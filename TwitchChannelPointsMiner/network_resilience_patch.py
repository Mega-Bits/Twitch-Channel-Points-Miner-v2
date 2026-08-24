"""Harden transient Twitch network operations without changing miner settings."""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Any

import requests

import TwitchChannelPointsMiner.classes.Twitch as twitch_module
import TwitchChannelPointsMiner.classes.WebSocketsPool as websocket_pool_module
import TwitchChannelPointsMiner.utils as utils_module
from TwitchChannelPointsMiner.classes.Twitch import Twitch
from TwitchChannelPointsMiner.classes.WebSocketsPool import WebSocketsPool
from TwitchChannelPointsMiner.constants import CLIENT_ID, GQLOperations, URL

logger = logging.getLogger(__name__)

_PATCH_MARKER = "_network_resilience_patch"
_CLIENT_VERSION_TTL_SECONDS = 30 * 60
_CLIENT_VERSION_FAILURE_BACKOFF_SECONDS = 2 * 60
_CLIENT_VERSION_TIMEOUT = (5, 10)
_GQL_TIMEOUT = (8, 30)
_DEFAULT_CONNECTIVITY_PROBES = (
    ("1.1.1.1", 443),
    ("8.8.8.8", 53),
    ("www.twitch.tv", 443),
)
_TRANSIENT_WEBSOCKET_ERRORS = (
    "connection to remote host was lost",
    "ping pong failed",
    "bad_length",
    "bad length",
    "connection reset",
    "connection is already closed",
    "remote end closed",
    "timed out",
    "timeout",
)
_TRANSIENT_HTTP_ERRORS = (
    "read timed out",
    "connect timeout",
    "connection aborted",
    "connection reset",
    "remote end closed",
    "invalidchunklength",
    "invalid chunk length",
)

_client_version_lock = threading.Lock()
_client_version_value: str | None = None
_client_version_checked_at = 0.0
_client_version_retry_after = 0.0
_client_version_last_failure: str | None = None


def _operation_label(payload: Any) -> str:
    items = payload if isinstance(payload, list) else [payload]
    names: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("operationName") or "").strip()
        if name and name not in names:
            names.append(name)
    return ", ".join(names) if names else "unknown"


def _is_transient_message(message: str, needles: tuple[str, ...]) -> bool:
    lowered = message.lower()
    return any(needle in lowered for needle in needles)


def _internet_connection_available(
    host: str = "8.8.8.8", port: int = 53, timeout: float = 3
) -> bool:
    """Probe more than one route and avoid changing the process-wide socket timeout."""
    if host == "8.8.8.8" and port == 53:
        targets = _DEFAULT_CONNECTIVITY_PROBES
    else:
        targets = ((host, port),)

    probe_timeout = max(0.25, float(timeout))
    for target_host, target_port in targets:
        try:
            with socket.create_connection(
                (target_host, target_port), timeout=probe_timeout
            ):
                return True
        except OSError:
            continue
    return False


def _update_client_version(self: Twitch) -> str:
    """Refresh Twitch's build ID at most twice per hour and back off after failures."""
    global _client_version_checked_at
    global _client_version_last_failure
    global _client_version_retry_after
    global _client_version_value

    now = time.monotonic()
    if (
        _client_version_value is not None
        and now - _client_version_checked_at < _CLIENT_VERSION_TTL_SECONDS
    ):
        self.client_version = _client_version_value
        return self.client_version
    if now < _client_version_retry_after:
        if _client_version_value is not None:
            self.client_version = _client_version_value
        return self.client_version

    with _client_version_lock:
        now = time.monotonic()
        if (
            _client_version_value is not None
            and now - _client_version_checked_at < _CLIENT_VERSION_TTL_SECONDS
        ):
            self.client_version = _client_version_value
            return self.client_version
        if now < _client_version_retry_after:
            if _client_version_value is not None:
                self.client_version = _client_version_value
            return self.client_version

        failure = "unknown failure"
        for attempt in range(2):
            try:
                response = requests.get(URL, timeout=_CLIENT_VERSION_TIMEOUT)
                if response.status_code != 200:
                    failure = f"HTTP {response.status_code}"
                    break

                matcher = self.twilight_build_id_pattern.search(response.text)
                if not matcher:
                    failure = "build ID not found"
                    break

                _client_version_value = matcher.group(1)
                _client_version_checked_at = time.monotonic()
                _client_version_retry_after = 0.0
                recovered = _client_version_last_failure is not None
                _client_version_last_failure = None
                self.client_version = _client_version_value
                twitch_module.logger.debug("Client version: %s", self.client_version)
                if recovered:
                    logger.info("Twitch client-version refresh recovered")
                return self.client_version
            except requests.exceptions.RequestException as exc:
                failure = f"{type(exc).__name__}: {exc}"
                if attempt == 0:
                    time.sleep(0.25)
                    continue

        _client_version_retry_after = (
            time.monotonic() + _CLIENT_VERSION_FAILURE_BACKOFF_SECONDS
        )
        if failure != _client_version_last_failure:
            logger.warning(
                "Unable to refresh Twitch client version (%s); using cached version %s",
                failure,
                self.client_version,
            )
            _client_version_last_failure = failure
        else:
            logger.debug("Twitch client-version refresh still failing: %s", failure)
        if _client_version_value is not None:
            self.client_version = _client_version_value
        return self.client_version


def _post_gql_request(self: Twitch, json_data: Any) -> Any:
    """Keep GraphQL requests bounded without changing caller error semantics."""
    response = requests.post(
        GQLOperations.url,
        json=json_data,
        headers={
            "Authorization": f"OAuth {self.twitch_login.get_auth_token()}",
            "Client-Id": CLIENT_ID,
            "Client-Session-Id": self.client_session,
            "Client-Version": self.update_client_version(),
            "User-Agent": self.user_agent,
            "X-Device-Id": self.device_id,
        },
        timeout=_GQL_TIMEOUT,
    )
    twitch_module.logger.debug(
        "Data: %s, Status code: %s, Content: %s",
        json_data,
        response.status_code,
        response.text,
    )
    return response.json()


def _websocket_on_error(ws: Any, error: Any) -> None:
    message = str(error)
    log = (
        websocket_pool_module.logger.warning
        if _is_transient_message(message, _TRANSIENT_WEBSOCKET_ERRORS)
        else websocket_pool_module.logger.error
    )
    log("#%s - WebSocket error: %s", ws.index, error)


class _TransientMinuteWatchedFilter(logging.Filter):
    """Treat expected stream transport interruptions as warnings, not crashes."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.ERROR:
            return True
        message = record.getMessage()
        if message.startswith("Error while trying to send minute watched:") and (
            _is_transient_message(message, _TRANSIENT_HTTP_ERRORS)
        ):
            record.levelno = logging.WARNING
            record.levelname = "WARNING"
        return True


def apply_patch() -> None:
    """Install bounded HTTP calls, cached client metadata, and safer connectivity checks."""
    if getattr(Twitch.update_client_version, _PATCH_MARKER, False):
        return

    setattr(_update_client_version, _PATCH_MARKER, True)
    setattr(_post_gql_request, _PATCH_MARKER, True)
    setattr(_websocket_on_error, _PATCH_MARKER, True)

    Twitch.update_client_version = _update_client_version
    Twitch.post_gql_request = _post_gql_request
    WebSocketsPool.on_error = staticmethod(_websocket_on_error)

    utils_module.internet_connection_available = _internet_connection_available
    twitch_module.internet_connection_available = _internet_connection_available
    websocket_pool_module.internet_connection_available = _internet_connection_available

    if not any(
        isinstance(item, _TransientMinuteWatchedFilter)
        for item in twitch_module.logger.filters
    ):
        twitch_module.logger.addFilter(_TransientMinuteWatchedFilter())
