"""Accept verified game-directory Drop candidates while Twitch metadata catches up."""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from TwitchChannelPointsMiner import configured_drop_game_priority_patch as configured
from TwitchChannelPointsMiner import drop_game_main_list_preference_patch as main_preference
from TwitchChannelPointsMiner import drop_games_patch
from TwitchChannelPointsMiner import drop_priority_order_patch as priority_order
from TwitchChannelPointsMiner import finish_started_drops_patch
from TwitchChannelPointsMiner.classes.Twitch import Twitch

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_explicit_game_drop_discovery_patch"
_DASHBOARD_PATCH_MARKER = "_explicit_game_dashboard_status_patch"
_FALLBACK_SOURCES = {"drop_fallback", "game_drop"}
_LOGGED_DIRECTORY_ASSIGNMENTS: set[tuple[str, Any]] = set()

_ORIGINAL_CONFIGURED_ELIGIBILITY = configured._eligible_for_campaign
_ORIGINAL_MAIN_ELIGIBILITY = main_preference._eligible_for_campaign
_ORIGINAL_DROP_CANDIDATE = priority_order._drop_candidate
_ORIGINAL_DROPS_DASHBOARD = getattr(Twitch, "_Twitch__get_drops_dashboard", None)


def _source(streamer: Any) -> str:
    return str(getattr(streamer, "source", "list")).strip().lower()


def _fallback_campaign_ids(streamer: Any) -> set[Any]:
    return set(getattr(streamer, "fallback_campaign_ids", ()) or ())


def _campaign_label(campaign: Any) -> str:
    game = getattr(campaign, "game", {}) or {}
    if isinstance(game, dict):
        game_name = game.get("displayName") or game.get("name") or "unknown game"
    else:
        game_name = str(game or "unknown game")
    return f"{game_name} / {getattr(campaign, 'name', getattr(campaign, 'id', 'unknown campaign'))}"


def _dashboard_campaigns_without_status_loss(
    twitch: Twitch,
    status: Any = None,
) -> list[Any]:
    """Let campaign/drop time windows decide activity instead of a brittle status label."""
    if not callable(_ORIGINAL_DROPS_DASHBOARD):
        return []
    if status is None or str(status).upper() != "ACTIVE":
        return list(_ORIGINAL_DROPS_DASHBOARD(twitch, status=status) or [])

    campaigns = list(_ORIGINAL_DROPS_DASHBOARD(twitch, status=None) or [])
    status_counts = Counter(
        str(campaign.get("status") or "UNKNOWN").upper()
        for campaign in campaigns
        if isinstance(campaign, dict)
    )
    signature = tuple(sorted(status_counts.items()))
    config = drop_games_patch._CONFIG.get(id(twitch))
    if config is not None and config.get("dashboard_campaign_statuses") != signature:
        config["dashboard_campaign_statuses"] = signature
        logger.info(
            "Drop dashboard campaign statuses: %s; current campaign and Drop time windows will determine activity",
            ", ".join(f"{name}={count}" for name, count in signature) or "none",
        )
    return campaigns


def _directory_assignment_eligibility(
    streamer: Any,
    campaign: Any,
) -> tuple[bool, str]:
    if campaign is None:
        return False, "campaign details unavailable"
    if getattr(streamer, "is_online", False) is not True:
        return False, "offline"
    if not configured._watchable(streamer):
        return False, "warming up"

    settings = getattr(streamer, "settings", None)
    if getattr(settings, "claim_drops", False) is not True:
        return False, "claim_drops disabled"

    campaign_id = getattr(campaign, "id", None)
    if campaign_id is None:
        return False, "campaign id unavailable"
    if not configured._same_game(streamer, campaign):
        return False, "streaming another game"

    source = _source(streamer)
    if source not in _FALLBACK_SOURCES:
        return False, "Twitch campaign metadata missing"
    if campaign_id not in _fallback_campaign_ids(streamer):
        return False, "not assigned to this campaign"

    return True, "verified directory assignment"


def _log_directory_assignment(streamer: Any, campaign: Any) -> None:
    key = (str(getattr(streamer, "username", "unknown")), getattr(campaign, "id", None))
    if key in _LOGGED_DIRECTORY_ASSIGNMENTS:
        return
    _LOGGED_DIRECTORY_ASSIGNMENTS.add(key)
    logger.info(
        "Using verified DROPS_ENABLED directory assignment for %s and campaign %s while Twitch channel campaign metadata is pending",
        getattr(streamer, "username", "unknown"),
        _campaign_label(campaign),
    )


def _configured_eligibility(streamer: Any, campaign: Any) -> bool:
    if _ORIGINAL_CONFIGURED_ELIGIBILITY(streamer, campaign):
        return True
    eligible, _ = _directory_assignment_eligibility(streamer, campaign)
    if eligible:
        _log_directory_assignment(streamer, campaign)
    return eligible


def _main_preference_eligibility(
    streamer: Any,
    campaign: Any,
    *,
    main_only: bool = False,
) -> bool:
    if _ORIGINAL_MAIN_ELIGIBILITY(streamer, campaign, main_only=main_only):
        return True
    if main_only:
        return False
    eligible, _ = _directory_assignment_eligibility(streamer, campaign)
    if eligible:
        _log_directory_assignment(streamer, campaign)
    return eligible


def _relevant_to_campaign(streamer: Any, campaign: Any) -> bool:
    if _source(streamer) in _FALLBACK_SOURCES:
        return True
    if configured._same_game(streamer, campaign):
        return True
    campaign_id = getattr(campaign, "id", None)
    return campaign_id is not None and campaign_id in configured._campaign_ids(streamer)


def _rejection_summary(streamers: list[Any], campaign: Any) -> str:
    reasons: Counter[str] = Counter()
    relevant = 0
    for streamer in streamers:
        if not _relevant_to_campaign(streamer, campaign):
            continue
        relevant += 1
        if _configured_eligibility(streamer, campaign):
            continue
        _, reason = _directory_assignment_eligibility(streamer, campaign)
        reasons[reason] += 1

    if relevant == 0:
        return "no matching channel discovered"
    if not reasons:
        return "candidate state changed before selection"
    return ", ".join(
        f"{reason}: {count}"
        for reason, count in sorted(reasons.items())
    )


def _explicit_game_names(twitch: Any) -> tuple[str, ...]:
    return tuple(
        str(game).strip()
        for game in finish_started_drops_patch.get_explicit_drop_games(twitch)
        if str(game).strip()
    )


def _log_explicit_game_failure(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
) -> None:
    campaigns = config.get("campaigns_by_id", {}) or {}
    explicit_ids, _ = priority_order._explicit_first_campaigns(twitch, config)

    if not explicit_ids:
        games = _explicit_game_names(twitch)
        signature = ("no-campaign", games)
        if config.get("explicit_drop_diagnostic") == signature:
            return
        config["explicit_drop_diagnostic"] = signature
        logger.info(
            "No current open Drop campaign matched explicit drop_games [%s] after checking all Twitch dashboard status values; started Drop completion remains fallback",
            ", ".join(games) or "none",
        )
        return

    details = []
    signature_details = []
    for campaign_id in priority_order._prefer_existing_target(
        twitch,
        config,
        explicit_ids,
    ):
        campaign = campaigns.get(campaign_id)
        if campaign is None:
            continue
        summary = _rejection_summary(streamers, campaign)
        details.append(f"{_campaign_label(campaign)} ({summary})")
        signature_details.append((campaign_id, summary))

    signature = ("no-candidate", tuple(signature_details))
    if config.get("explicit_drop_diagnostic") == signature:
        return
    config["explicit_drop_diagnostic"] = signature
    logger.info(
        "No eligible channel for explicit Drop game: %s. Explicit games remain first priority; started Drop completion is considered only as fallback",
        "; ".join(details) or "candidate details unavailable",
    )


def _drop_candidate_with_directory_fallback(
    twitch: Any,
    streamers: list[Any],
    config: dict[str, Any],
):
    candidate = _ORIGINAL_DROP_CANDIDATE(twitch, streamers, config)
    if candidate is not None and candidate[2] == "game_drop":
        config.pop("explicit_drop_diagnostic", None)
        return candidate

    _log_explicit_game_failure(twitch, streamers, config)
    return candidate


def apply_patch() -> None:
    """Install status-tolerant campaign discovery and verified directory eligibility."""
    dashboard_name = "_Twitch__get_drops_dashboard"
    dashboard = getattr(Twitch, dashboard_name, None)
    if dashboard is not None and not getattr(dashboard, _DASHBOARD_PATCH_MARKER, False):
        setattr(
            _dashboard_campaigns_without_status_loss,
            _DASHBOARD_PATCH_MARKER,
            True,
        )
        setattr(Twitch, dashboard_name, _dashboard_campaigns_without_status_loss)

    if getattr(priority_order, _PATCH_MARKER, False):
        return

    configured._eligible_for_campaign = _configured_eligibility
    main_preference._eligible_for_campaign = _main_preference_eligibility
    priority_order._drop_candidate = _drop_candidate_with_directory_fallback
    setattr(priority_order, _PATCH_MARKER, True)
