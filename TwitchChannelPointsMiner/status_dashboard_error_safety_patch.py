"""Keep Discord dashboard errors useful without exposing webhook credentials."""

from __future__ import annotations

import logging
import re
from typing import Any

import requests

from TwitchChannelPointsMiner import status_dashboard_enhancements_patch as enhancements
from TwitchChannelPointsMiner import status_dashboard_patch as dashboard

_PATCH_MARKER = "_status_dashboard_error_safety_patch"
_FILTER_MARKER = "_status_dashboard_secret_filter"
_WEBHOOK_URL = re.compile(
    r"https://(?:canary\.|ptb\.)?discord(?:app)?\.com/"
    r"api(?:/v\d+)?/webhooks/\d+/[^\s]+",
    re.IGNORECASE,
)


def _redact(value: Any) -> str:
    """Remove Discord webhook IDs, tokens, message IDs, and query strings."""
    return _WEBHOOK_URL.sub(
        "https://discord.com/api/webhooks/<redacted>",
        str(value),
    )


def _failure_reason(exc: BaseException | None) -> str:
    if exc is None:
        return "request failed"

    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        return f"HTTP {status_code}"
    if isinstance(exc, requests.Timeout):
        return "request timed out"
    if isinstance(exc, requests.ConnectionError):
        return "connection error"
    if isinstance(exc, requests.RequestException):
        return type(exc).__name__
    return type(exc).__name__


class _SecretRedactionFilter(logging.Filter):
    """Redact webhook URLs and suppress credential-bearing publish tracebacks."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:
            rendered = str(record.msg)

        redacted = _redact(rendered)
        publish_failure = (
            "Unable to update Discord status dashboard" in redacted
            or "Discord dashboard update failed" in redacted
        )
        contains_secret = redacted != rendered

        if publish_failure and record.exc_info:
            redacted = f"{redacted}: {_failure_reason(record.exc_info[1])}"

        if contains_secret or publish_failure:
            record.msg = redacted
            record.args = ()
            if record.exc_info:
                record.exc_info = None
                record.exc_text = None
        return True


def _install_filter(target: logging.Logger) -> None:
    if any(getattr(item, _FILTER_MARKER, False) for item in target.filters):
        return
    redaction_filter = _SecretRedactionFilter()
    setattr(redaction_filter, _FILTER_MARKER, True)
    target.addFilter(redaction_filter)


def _safe_publish_failure(self: Any, exc: Exception) -> bool:
    self._publish_backoff = (
        enhancements._BASE_RETRY_SECONDS
        if self._publish_backoff <= 0
        else min(
            self._publish_backoff * 2,
            enhancements._MAX_RETRY_SECONDS,
        )
    )
    enhancements._schedule_retry(self, self._publish_backoff)
    enhancements.logger.warning(
        "Discord dashboard update failed (%s); retrying in %.0f seconds",
        _failure_reason(exc),
        self._publish_backoff,
    )
    return False


def apply_patch() -> None:
    """Install sanitized dashboard failure reporting once."""
    if getattr(enhancements, _PATCH_MARKER, False):
        return

    _install_filter(dashboard.logger)
    _install_filter(enhancements.logger)
    enhancements._handle_publish_failure = _safe_publish_failure
    setattr(enhancements, _PATCH_MARKER, True)
