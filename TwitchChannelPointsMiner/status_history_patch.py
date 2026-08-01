"""Connect the Discord dashboard state to the SQLite history store."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from TwitchChannelPointsMiner import status_dashboard_patch
from TwitchChannelPointsMiner.status_history import StatusHistory

_PATCH_MARKER = "_status_history_patch"
_HISTORY_LOCK = threading.RLock()
_HISTORIES: dict[int, StatusHistory] = {}


def _history_path(state: status_dashboard_patch.DashboardState) -> Path:
    cookies_file = Path(getattr(state.twitch, "cookies_file", "miner.pkl"))
    cookies_file.parent.mkdir(parents=True, exist_ok=True)
    return cookies_file.with_name(f"{cookies_file.stem}.miner-status.sqlite3")


def get_history(twitch: Any | None = None) -> StatusHistory | None:
    with _HISTORY_LOCK:
        if twitch is not None:
            return _HISTORIES.get(id(twitch))
        if len(_HISTORIES) == 1:
            return next(iter(_HISTORIES.values()))
        return None


def history_summary(twitch: Any | None = None) -> dict[str, Any]:
    history = get_history(twitch)
    return history.summary() if history else {}


def apply_patch() -> None:
    """Install persistence wrappers around the dashboard state."""
    dashboard_state = status_dashboard_patch.DashboardState

    original_init = dashboard_state.__init__
    if not getattr(original_init, _PATCH_MARKER, False):
        def init_with_history(self, twitch, streamers, priority):
            original_init(self, twitch, streamers, priority)
            history = StatusHistory(_history_path(self))
            with _HISTORY_LOCK:
                previous = _HISTORIES.pop(id(twitch), None)
                _HISTORIES[id(twitch)] = history
            if previous is not None:
                previous.close()
            self._history = history

        setattr(init_with_history, _PATCH_MARKER, True)
        dashboard_state.__init__ = init_with_history

    original_event = dashboard_state.record_event
    if not getattr(original_event, _PATCH_MARKER, False):
        def event_with_history(self, event, message, created):
            original_event(self, event, message, created)
            item = self.last_event if str(event) != "DROP_STATUS" else None
            payload = item if isinstance(item, dict) else None
            self._history.record_event(
                int(created),
                str(event),
                status_dashboard_patch._trim(message, 4000),
                payload,
            )

        setattr(event_with_history, _PATCH_MARKER, True)
        dashboard_state.record_event = event_with_history

    original_inventory = dashboard_state.update_inventory
    if not getattr(original_inventory, _PATCH_MARKER, False):
        def inventory_with_history(self, streamers, campaigns):
            original_inventory(self, streamers, campaigns)
            self._history.record_snapshot("inventory", self.snapshot())

        setattr(inventory_with_history, _PATCH_MARKER, True)
        dashboard_state.update_inventory = inventory_with_history

    original_watch = dashboard_state.update_watch
    if not getattr(original_watch, _PATCH_MARKER, False):
        def watch_with_history(self, streamers, selected_indexes):
            previous = list(self.watch_slots)
            original_watch(self, streamers, selected_indexes)
            current = list(self.watch_slots)
            if current != previous:
                self._history.record_watch_slots(current)
                self._history.record_snapshot(
                    "watch_slots",
                    {
                        "timestamp": self.last_inventory_sync,
                        "watch_slots": current,
                    },
                )

        setattr(watch_with_history, _PATCH_MARKER, True)
        dashboard_state.update_watch = watch_with_history

    original_snapshot = dashboard_state.snapshot
    if not getattr(original_snapshot, _PATCH_MARKER, False):
        def snapshot_with_history(self):
            snapshot = original_snapshot(self)
            history = getattr(self, "_history", None)
            snapshot["history"] = history.summary() if history else {}
            return snapshot

        setattr(snapshot_with_history, _PATCH_MARKER, True)
        dashboard_state.snapshot = snapshot_with_history

    original_stop = dashboard_state.stop
    if not getattr(original_stop, _PATCH_MARKER, False):
        def stop_with_history(self):
            try:
                return original_stop(self)
            finally:
                history = getattr(self, "_history", None)
                if history is not None:
                    history.record_snapshot("shutdown", self.snapshot())
                    history.close()
                with _HISTORY_LOCK:
                    _HISTORIES.pop(id(self.twitch), None)

        setattr(stop_with_history, _PATCH_MARKER, True)
        dashboard_state.stop = stop_with_history
