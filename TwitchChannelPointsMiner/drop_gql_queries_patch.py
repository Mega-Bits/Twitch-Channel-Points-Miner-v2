"""Refresh Twitch Drop GraphQL queries and report query-health failures."""

from __future__ import annotations

import logging
from typing import Any

from TwitchChannelPointsMiner.classes.Twitch import Twitch
from TwitchChannelPointsMiner.constants import GQLOperations

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_drop_gql_queries_patch"

_QUERY_DEFINITIONS = {
    "Inventory": {
        "hash": "8337eb8541b314040b0edde0c09c5c7a2783ba1960aa9edfbf3bac16d0fec404",
        "variables": {"fetchRewardCampaigns": False},
    },
    "ViewerDropsDashboard": {
        "hash": "d9cae7761dafab85908c85e6683cb4201b449e66ac3bb5e894f15ff12aeafaa7",
        "variables": {"fetchRewardCampaigns": False},
    },
    "DropCampaignDetails": {
        "hash": "039277bf98f3130929262cc7c6efd9c141ca3749cb6dca442fc8ead9a53f77c1",
    },
    "DropsHighlightService_AvailableDrops": {
        "hash": "782dad0f032942260171d2d80a654f88bdd0c5a9dddc392e9bc92218a0f42d20",
    },
}
_TRACKED_OPERATIONS = frozenset(_QUERY_DEFINITIONS)
_LAST_FAILURES: dict[tuple[int, tuple[str, ...]], tuple[str, ...]] = {}


def _refresh_queries() -> None:
    for operation_name, definition in _QUERY_DEFINITIONS.items():
        operation = getattr(GQLOperations, operation_name, None)
        if not isinstance(operation, dict):
            continue
        extensions = operation.setdefault("extensions", {})
        persisted_query = extensions.setdefault("persistedQuery", {})
        persisted_query["version"] = 1
        persisted_query["sha256Hash"] = definition["hash"]
        if "variables" in definition:
            operation["variables"] = dict(definition["variables"])


def _operation_names(payload: Any) -> tuple[str, ...]:
    items = payload if isinstance(payload, list) else [payload]
    names = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("operationName") or "").strip()
        if name and name in _TRACKED_OPERATIONS and name not in names:
            names.append(name)
    return tuple(names)


def _error_messages(response: Any) -> tuple[str, ...]:
    responses = response if isinstance(response, list) else [response]
    messages = []
    for item in responses:
        if not isinstance(item, dict):
            continue
        errors = item.get("errors")
        if not isinstance(errors, list):
            continue
        for error in errors:
            if isinstance(error, dict):
                message = str(
                    error.get("message")
                    or error.get("error")
                    or "unknown GraphQL error"
                )
                extensions = error.get("extensions")
                code = extensions.get("code") if isinstance(extensions, dict) else None
                if code:
                    message = f"{code}: {message}"
            else:
                message = str(error)
            if message not in messages:
                messages.append(message)
            if len(messages) >= 5:
                return tuple(messages)
    return tuple(messages)


def apply_patch() -> None:
    """Install current Drop queries and deduplicated GQL health logging."""
    _refresh_queries()

    original = Twitch.post_gql_request
    if getattr(original, _PATCH_MARKER, False):
        return

    def post_with_drop_query_health(self: Twitch, json_data: Any) -> Any:
        response = original(self, json_data)
        operation_names = _operation_names(json_data)
        if not operation_names:
            return response

        key = (id(self), operation_names)
        messages = _error_messages(response)
        if messages:
            if _LAST_FAILURES.get(key) != messages:
                logger.warning(
                    "Twitch Drop GQL operation %s failed: %s",
                    ", ".join(operation_names),
                    "; ".join(messages),
                )
                _LAST_FAILURES[key] = messages
            return response

        if response in ({}, []):
            signature = ("empty response",)
            if _LAST_FAILURES.get(key) != signature:
                logger.warning(
                    "Twitch Drop GQL operation %s returned an empty response",
                    ", ".join(operation_names),
                )
                _LAST_FAILURES[key] = signature
            return response

        if key in _LAST_FAILURES:
            logger.info(
                "Twitch Drop GQL operation %s recovered",
                ", ".join(operation_names),
            )
            _LAST_FAILURES.pop(key, None)
        return response

    setattr(post_with_drop_query_health, _PATCH_MARKER, True)
    Twitch.post_gql_request = post_with_drop_query_health
