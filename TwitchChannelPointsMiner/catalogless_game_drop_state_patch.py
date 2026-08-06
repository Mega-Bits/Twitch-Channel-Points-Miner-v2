"""Persist completed Drop evidence and stabilize catalogless game selections."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from TwitchChannelPointsMiner import catalogless_game_drop_patch as catalogless
from TwitchChannelPointsMiner import completed_game_drop_guard_patch as completed_guard
from TwitchChannelPointsMiner import configured_drop_game_priority_patch as configured
from TwitchChannelPointsMiner import drop_games_patch
from TwitchChannelPointsMiner import finish_started_drops_patch
from TwitchChannelPointsMiner import inventory_campaign_recovery_patch as inventory_recovery
from TwitchChannelPointsMiner import status_dashboard_patch as dashboard
from TwitchChannelPointsMiner.classes.Twitch import Twitch

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_catalogless_game_drop_state_patch"
_REFRESH_MARKER = "_catalogless_active_refresh_stability"
_INVENTORY_MARKER = "_catalogless_terminal_inventory_ledger"
_LEDGER_LOCK = RLock()
_LEDGER_CACHE: dict[int, dict[str, dict[str, Any]]] = {}
_HEURISTIC_SHORT_SECONDS = 6 * 60 * 60
_HEURISTIC_STRONG_SECONDS = 24 * 60 * 60
_UNKNOWN_TERMINAL_SECONDS = 6 * 60 * 60
_CLAIM_LOOKBACK_SECONDS = 7 * 24 * 60 * 60

_GAME_RE = re.compile(r"(?:\*\*)?Game:(?:\*\*)?\s*([^\n·]+)", re.IGNORECASE)
_DROP_RE = re.compile(r"(?:\*\*)?Drop:(?:\*\*)?\s*([^\n·]+)", re.IGNORECASE)
_PROGRESS_RE = re.compile(
    r"(?:\*\*)?Progress:(?:\*\*)?\s*`?\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*`?"
    r"(?:\s*·\s*`?\s*(\d+(?:\.\d+)?)%\s*`?)?",
    re.IGNORECASE,
)


def _normalize(value: Any) -> str:
    return drop_games_patch._normalize(value)


def _safe_number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _game_values(value: Any) -> set[str]:
    game = value or {}
    if not isinstance(game, dict):
        normalized = _normalize(game)
        return {normalized} if normalized else set()
    return {
        _normalize(item)
        for item in (game.get("id"), game.get("name"), game.get("displayName"))
        if item not in (None, "")
    }


def _game_name(value: Any) -> str:
    game = value or {}
    if isinstance(game, dict):
        return str(game.get("displayName") or game.get("name") or game.get("id") or "")
    return str(game or "")


def _parse_epoch(value: Any) -> float | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _ledger_path(twitch: Any) -> Path:
    cookies = Path(getattr(twitch, "cookies_file", "miner.pkl"))
    return cookies.with_name(f"{cookies.stem}.drop-terminal-state.json")


def _prune_ledger(ledger: dict[str, dict[str, Any]], now: float) -> bool:
    changed = False
    for key, state in list(ledger.items()):
        if not isinstance(state, dict) or _safe_number(state.get("until")) <= now:
            ledger.pop(key, None)
            changed = True
    return changed


def _load_ledger(twitch: Any) -> dict[str, dict[str, Any]]:
    twitch_id = id(twitch)
    with _LEDGER_LOCK:
        cached = _LEDGER_CACHE.get(twitch_id)
        if cached is not None:
            _prune_ledger(cached, time.time())
            return cached
        try:
            raw = json.loads(_ledger_path(twitch).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raw = {}
        ledger = {
            str(key): value
            for key, value in (raw.items() if isinstance(raw, dict) else ())
            if isinstance(value, dict)
        }
        _prune_ledger(ledger, time.time())
        _LEDGER_CACHE[twitch_id] = ledger
        return ledger


def _save_ledger(twitch: Any, ledger: dict[str, dict[str, Any]]) -> None:
    path = _ledger_path(twitch)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError:
        logger.warning("Unable to persist completed Drop state", exc_info=True)


def _campaign_terminal_state(campaign: dict[str, Any], now: float) -> dict[str, Any] | None:
    campaign_id = str(campaign.get("id") or "").strip()
    game = _game_name(campaign.get("game"))
    game_key = _normalize(game)
    if not campaign_id or not game_key:
        return None

    campaign_end = _parse_epoch(campaign.get("endAt"))
    drops = [
        drop
        for drop in (campaign.get("timeBasedDrops") or [])
        if isinstance(drop, dict)
    ]
    states = [completed_guard._drop_state(drop, campaign_end, now) for drop in drops]
    status = str(campaign.get("status") or "").upper()
    farmable = any(state.get("finishable") for state in states)
    if farmable and status not in completed_guard._TERMINAL_STATUSES:
        return None

    if status in completed_guard._TERMINAL_STATUSES:
        reason = status.lower()
    elif states and all(state.get("claimed") for state in states):
        reason = "all rewards claimed"
    elif any(state.get("claimable") for state in states):
        reason = "reward ready to claim; no watching required"
    elif states:
        reason = "no reward can still be completed"
    else:
        reason = "no open reward"

    until = (
        campaign_end + completed_guard._EXPIRY_GRACE_SECONDS
        if campaign_end is not None
        else now + _UNKNOWN_TERMINAL_SECONDS
    )
    return {
        "campaign_id": campaign_id,
        "game": game,
        "game_key": game_key,
        "name": str(campaign.get("name") or campaign_id),
        "reason": reason,
        "source": "inventory",
        "observed_at": int(now),
        "until": int(until),
    }


def _record_inventory(twitch: Any, inventory: Any) -> None:
    if not isinstance(inventory, dict):
        return
    now = time.time()
    campaigns = inventory_recovery._inventory_campaigns(inventory)
    current_ids = {
        str(campaign.get("id"))
        for campaign in campaigns
        if campaign.get("id") not in (None, "")
    }
    ledger = _load_ledger(twitch)
    changed = _prune_ledger(ledger, now)

    for campaign in campaigns:
        campaign_id = str(campaign.get("id") or "").strip()
        if not campaign_id:
            continue
        key = f"campaign:{campaign_id}"
        terminal = _campaign_terminal_state(campaign, now)
        if terminal is None:
            if key in ledger:
                ledger.pop(key, None)
                changed = True
            continue
        if ledger.get(key) != terminal:
            ledger[key] = terminal
            changed = True

    # Exact campaign records intentionally survive disappearance from
    # dropCampaignsInProgress until their own deadline. Twitch removes fully
    # completed campaigns from that list, which is the state this ledger fixes.
    for key, state in list(ledger.items()):
        campaign_id = str(state.get("campaign_id") or "")
        if (
            state.get("source") == "inventory"
            and campaign_id in current_ids
            and _safe_number(state.get("until")) <= now
        ):
            ledger.pop(key, None)
            changed = True

    if changed:
        _save_ledger(twitch, ledger)


def _install_inventory_ledger() -> None:
    name = "_Twitch__get_inventory"
    current = getattr(Twitch, name, None)
    if current is None or getattr(current, _INVENTORY_MARKER, False):
        return

    def get_inventory_with_terminal_ledger(self: Twitch) -> Any:
        inventory = current(self)
        _record_inventory(self, inventory)
        return inventory

    setattr(get_inventory_with_terminal_ledger, _INVENTORY_MARKER, True)
    setattr(Twitch, name, get_inventory_with_terminal_ledger)


def _dashboard_snapshot(twitch: Any) -> dict[str, Any]:
    try:
        snapshot = dashboard.get_status_snapshot(twitch)
    except Exception:
        snapshot = {}
    if isinstance(snapshot, dict) and snapshot:
        return snapshot

    cookies = Path(getattr(twitch, "cookies_file", "miner.pkl"))
    path = cookies.with_name(f"{cookies.stem}.discord-dashboard.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _completed_claims_for_game(
    twitch: Any,
    game_key: str,
    now: float,
) -> list[dict[str, Any]]:
    snapshot = _dashboard_snapshot(twitch)
    claims: list[dict[str, Any]] = []
    for item in snapshot.get("recent_claims", ()) or ():
        if not isinstance(item, dict):
            continue
        message = str(item.get("message") or "")
        game_match = _GAME_RE.search(message)
        progress_match = _PROGRESS_RE.search(message)
        if not game_match or not progress_match:
            continue
        if _normalize(game_match.group(1)) != game_key:
            continue
        current = _safe_number(progress_match.group(1))
        required = _safe_number(progress_match.group(2))
        percent = _safe_number(progress_match.group(3))
        if required <= 0 or (current < required and percent < 100):
            continue
        timestamp = _safe_number(item.get("timestamp"))
        if timestamp <= 0 or timestamp < now - _CLAIM_LOOKBACK_SECONDS:
            continue
        timestamp = min(timestamp, now + 5 * 60)
        drop_match = _DROP_RE.search(message)
        claims.append(
            {
                "timestamp": timestamp,
                "drop": drop_match.group(1).strip() if drop_match else "completed Drop",
            }
        )
    return claims


def _inventory_has_game(twitch: Any, game_key: str) -> bool:
    if id(twitch) not in inventory_recovery._LAST_INVENTORY:
        return True
    inventory = inventory_recovery._LAST_INVENTORY.get(id(twitch), {})
    return any(
        game_key in _game_values(campaign.get("game"))
        for campaign in inventory_recovery._inventory_campaigns(inventory)
    )


def _tracked_campaign_has_game(config: dict[str, Any], game_key: str) -> bool:
    for campaign in (config.get("campaigns_by_id", {}) or {}).values():
        if game_key in _game_values(getattr(campaign, "game", {}) or {}):
            return True
    return False


def _bootstrap_claim_tombstone(
    twitch: Any,
    config: dict[str, Any],
    game_key: str,
    game_name: str,
    now: float,
) -> None:
    if _inventory_has_game(twitch, game_key) or _tracked_campaign_has_game(config, game_key):
        return
    claims = _completed_claims_for_game(twitch, game_key, now)
    if not claims:
        return
    latest = max(item["timestamp"] for item in claims)
    duration = (
        _HEURISTIC_STRONG_SECONDS
        if len({item["drop"] for item in claims}) >= 3
        else _HEURISTIC_SHORT_SECONDS
    )
    until = latest + duration
    if until <= now:
        return

    ledger = _load_ledger(twitch)
    key = f"claims:{game_key}"
    state = {
        "campaign_id": "",
        "game": game_name,
        "game_key": game_key,
        "name": "recent completed Drop claims",
        "reason": (
            f"{len(claims)} fully completed Drop claim(s) remain in the "
            "persisted dashboard while Twitch no longer lists an open inventory campaign"
        ),
        "source": "dashboard_claims",
        "observed_at": int(now),
        "latest_claim_at": int(latest),
        "until": int(until),
    }
    if ledger.get(key) != state:
        ledger[key] = state
        _save_ledger(twitch, ledger)


def _terminal_states(
    twitch: Any,
    config: dict[str, Any],
    game_key: str,
    game_name: str,
    now: float,
) -> list[dict[str, Any]]:
    _bootstrap_claim_tombstone(twitch, config, game_key, game_name, now)
    ledger = _load_ledger(twitch)
    if _prune_ledger(ledger, now):
        _save_ledger(twitch, ledger)
    return [
        state
        for state in ledger.values()
        if state.get("game_key") == game_key and _safe_number(state.get("until")) > now
    ]


def _progress_rejected(
    config: dict[str, Any],
    game_key: str,
    username: str,
    now: float,
) -> bool:
    for key, deadline in (config.get("game_drop_progress_rejections", {}) or {}).items():
        if not isinstance(key, tuple) or len(key) != 2 or key[1] != username:
            continue
        if key[0] == f"game:{game_key}" and _safe_number(deadline) > now:
            return True
    return False


def _active_catalogless_candidate(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
    now: float,
) -> tuple[str, int, str] | None:
    campaign_id = str(config.get("active_selection_campaign_id") or "")
    username = str(config.get("active_selection_streamer") or "")
    kind = str(config.get("active_selection_kind") or "")
    if (
        kind != "game_drop"
        or not campaign_id.startswith(catalogless._CATALOGLESS_PREFIX)
        or not username
    ):
        return None
    game_key = campaign_id[len(catalogless._CATALOGLESS_PREFIX):]
    explicit = {
        _normalize(game)
        for game in finish_started_drops_patch.get_explicit_drop_games(twitch)
    }
    if game_key not in explicit:
        return None
    index = next(
        (
            index
            for index, streamer in enumerate(streamers)
            if getattr(streamer, "username", None) == username
        ),
        None,
    )
    if index is None:
        return None
    streamer = streamers[index]
    if not configured._watchable(streamer):
        return None
    if getattr(getattr(streamer, "settings", None), "claim_drops", False) is not True:
        return None
    stream_games = catalogless._stream_game_values(streamer)
    if stream_games and game_key not in stream_games:
        return None
    if _progress_rejected(config, game_key, username, now):
        return None
    return campaign_id, index, "game_drop"


def _campaign_ids(streamer: Any) -> set[str]:
    return {
        str(value)
        for value in (
            getattr(getattr(streamer, "stream", None), "campaigns_ids", None) or []
        )
        if value not in (None, "")
    }


def _new_exact_campaign_visible(
    streamers: list[Any],
    game_key: str,
    states: list[dict[str, Any]],
) -> bool:
    terminal_ids = {
        str(state.get("campaign_id"))
        for state in states
        if state.get("campaign_id")
    }
    if not terminal_ids:
        return False
    for streamer in streamers:
        stream_games = catalogless._stream_game_values(streamer)
        if stream_games and game_key not in stream_games:
            continue
        if _campaign_ids(streamer).difference(terminal_ids):
            return True
    return False


def _fallback_without_catalogless(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
):
    mapping = config.pop("catalogless_streamer_games", None)
    active_game = config.pop("active_catalogless_game", None)
    try:
        return _ORIGINAL_DROP_CANDIDATE(twitch, streamers, config)
    finally:
        if mapping is not None:
            config["catalogless_streamer_games"] = mapping
        if active_game is not None:
            config["active_catalogless_game"] = active_game


def _clear_catalogless_selection(config: dict[str, Any]) -> None:
    for key in (
        "active_catalogless_game",
        "game_drop_progress_state",
        "sticky_game_drop_handoff",
    ):
        config.pop(key, None)


def _log_terminal_guard(
    config: dict[str, Any],
    game_name: str,
    states: list[dict[str, Any]],
) -> None:
    until = max(_safe_number(state.get("until")) for state in states)
    reasons = "; ".join(
        dict.fromkeys(str(state.get("reason") or "completed") for state in states)
    )
    signature = (_normalize(game_name), reasons, int(until))
    if config.get("catalogless_persisted_terminal_diagnostic") == signature:
        return
    config["catalogless_persisted_terminal_diagnostic"] = signature
    deadline = datetime.fromtimestamp(until, timezone.utc).isoformat()
    logger.info(
        "Skipping game-directory Drop fallback for %s because persisted completion evidence is terminal: %s. Rechecking automatically after %s or immediately when a real new campaign is discovered",
        game_name,
        reasons,
        deadline,
    )


def _candidate_with_persisted_completion(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
):
    candidate = _ORIGINAL_DROP_CANDIDATE(twitch, streamers, config)

    # A fully identified campaign always wins over provisional state and old
    # completion evidence.
    if (
        candidate is not None
        and candidate[2] == "game_drop"
        and not str(candidate[0]).startswith(catalogless._CATALOGLESS_PREFIX)
    ):
        config.pop("catalogless_persisted_terminal_diagnostic", None)
        return candidate

    now = time.time()
    active = _active_catalogless_candidate(twitch, streamers, config, now)
    selected = active or candidate
    if (
        selected is None
        or selected[2] != "game_drop"
        or not str(selected[0]).startswith(catalogless._CATALOGLESS_PREFIX)
    ):
        return selected

    campaign_id, _, _ = selected
    game_key = str(campaign_id)[len(catalogless._CATALOGLESS_PREFIX):]
    game_name = str(config.get("active_catalogless_game") or game_key)
    states = _terminal_states(twitch, config, game_key, game_name, now)
    if not states:
        # Keep the active provisional channel even if it disappeared from the
        # latest paginated directory response. Progress verification, an actual
        # offline/game change, or a real campaign handoff decides when to move.
        return active or candidate

    if _new_exact_campaign_visible(streamers, game_key, states):
        config.pop("catalogless_persisted_terminal_diagnostic", None)
        return active or candidate

    _clear_catalogless_selection(config)
    config["completed_game_drop_guard"] = {
        "game": game_name,
        "reason": "; ".join(
            str(state.get("reason") or "completed") for state in states
        ),
        "until": int(max(_safe_number(state.get("until")) for state in states)),
    }
    _log_terminal_guard(config, game_name, states)
    return _fallback_without_catalogless(twitch, streamers, config)


def _active_identity(config: dict[str, Any]) -> tuple[str, str, str] | None:
    campaign_id = str(config.get("active_selection_campaign_id") or "")
    username = str(config.get("active_selection_streamer") or "")
    if not campaign_id.startswith(catalogless._CATALOGLESS_PREFIX) or not username:
        return None
    game_key = campaign_id[len(catalogless._CATALOGLESS_PREFIX):]
    game_name = str(config.get("active_catalogless_game") or game_key)
    return username, game_key, game_name


def _install_refresh_stability() -> None:
    current = finish_started_drops_patch._ORIGINAL_REFRESH
    if getattr(current, _REFRESH_MARKER, False):
        return

    def refresh_without_directory_page_churn(
        twitch: Any,
        streamers: list[Any],
        campaigns: list[Any],
    ) -> Any:
        config = drop_games_patch._CONFIG.get(id(twitch)) or {}
        identity = _active_identity(config)
        previous: dict[str, Any] | None = None
        if identity is not None:
            username, _, _ = identity
            streamer = next(
                (
                    item
                    for item in streamers
                    if getattr(item, "username", None) == username
                ),
                None,
            )
            if streamer is not None:
                previous = {
                    "streamer": streamer,
                    "is_online": bool(getattr(streamer, "is_online", False)),
                    "online_at": getattr(streamer, "online_at", 0),
                    "offline_at": getattr(streamer, "offline_at", 0),
                    "fallback_campaign_ids": getattr(
                        streamer, "fallback_campaign_ids", frozenset()
                    ),
                }

        result = current(twitch, streamers, campaigns)
        if identity is None or previous is None or not previous["is_online"]:
            return result

        username, game_key, game_name = identity
        mapping = config.get("catalogless_streamer_games", {}) or {}
        if username in mapping:
            return result
        if _progress_rejected(config, game_key, username, time.time()):
            return result

        streamer = previous["streamer"]
        stream_games = catalogless._stream_game_values(streamer)
        if stream_games and game_key not in stream_games:
            return result

        streamer.is_online = True
        streamer.online_at = previous["online_at"]
        streamer.offline_at = previous["offline_at"]
        streamer.fallback_campaign_ids = previous[
            "fallback_campaign_ids"
        ] or frozenset({catalogless._CATALOGLESS_FALLBACK_ID})
        mapping = dict(mapping)
        mapping[username] = game_name
        config["catalogless_streamer_games"] = mapping
        try:
            streamer.toggle_chat()
        except (AttributeError, TypeError):
            pass
        return result

    setattr(refresh_without_directory_page_churn, _REFRESH_MARKER, True)
    finish_started_drops_patch._ORIGINAL_REFRESH = refresh_without_directory_page_churn


def apply_patch() -> None:
    """Install restart-safe completion evidence and stable provisional channels."""
    from TwitchChannelPointsMiner import drop_priority_order_patch as priority_order

    candidate = priority_order._drop_candidate
    if getattr(candidate, _PATCH_MARKER, False):
        return

    global _ORIGINAL_DROP_CANDIDATE
    _ORIGINAL_DROP_CANDIDATE = candidate
    setattr(_candidate_with_persisted_completion, _PATCH_MARKER, True)
    priority_order._drop_candidate = _candidate_with_persisted_completion
    _install_inventory_ledger()
    _install_refresh_stability()
