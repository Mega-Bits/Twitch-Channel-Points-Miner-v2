"""Opt-in authenticated operational controls for the status web dashboard."""

from __future__ import annotations

import secrets
import threading
import time
from typing import Any, Callable

from flask import abort, jsonify, render_template_string, request

from TwitchChannelPointsMiner import (
    status_dashboard_patch,
    status_history_patch,
    status_web_patch,
)
from TwitchChannelPointsMiner.TwitchChannelPointsMiner import TwitchChannelPointsMiner

_PATCH_MARKER = "_status_web_controls_patch"
_CONFIG_LOCK = threading.RLock()
_CONFIG: dict[int, dict[str, Any]] = {}

_CONTROLS_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Miner Controls</title>
<style>
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif}body{margin:0;background:#0b0d12;color:#eef1f7}main{max-width:760px;margin:auto;padding:24px}.card{background:#151922;border:1px solid #252b38;border-radius:14px;padding:18px;margin-bottom:14px}h1{font-size:1.45rem}h2{font-size:1rem;color:#b9c2d0}button{width:100%;text-align:left;background:#252b38;color:#fff;border:1px solid #343c4c;border-radius:10px;padding:12px;margin:6px 0;cursor:pointer}button:hover{background:#303849}button:disabled{opacity:.5;cursor:wait}.muted{color:#9aa4b2}a{color:#a78bfa}pre{white-space:pre-wrap;word-break:break-word}
</style></head><body><main>
<h1>Miner Controls</h1><p><a href="/">Back to status</a></p>
<section class="card"><h2>Safe operational actions</h2><button data-action="refresh-dashboard"><b>Refresh Discord dashboard</b><br><span class="muted">Immediately schedule an edit of the persistent status message.</span></button><button data-action="claim-drops"><b>Claim completed Drops</b><br><span class="muted">Run the existing inventory claim pass now.</span></button><button data-action="refresh-streamers"><b>Refresh streamers</b><br><span class="muted">Recheck online state, current game, and Drop eligibility.</span></button><button data-action="clear-dashboard-activity"><b>Clear compact dashboard activity</b><br><span class="muted">Clear recent claims and last-event fields from Discord only. SQLite history is retained.</span></button></section>
<section class="card"><h2>Result</h2><pre id="result" class="muted">Ready.</pre></section>
</main><script>
let csrf=null; const result=document.getElementById('result');
async function loadCsrf(){const r=await fetch('/api/control/csrf',{cache:'no-store'});if(!r.ok)throw new Error(`${r.status} ${r.statusText}`);csrf=(await r.json()).csrf_token;}
async function run(action){document.querySelectorAll('button').forEach(x=>x.disabled=true);try{if(!csrf)await loadCsrf();const r=await fetch(`/api/control/${action}`,{method:'POST',headers:{'X-CSRF-Token':csrf}});const body=await r.json();result.textContent=JSON.stringify(body,null,2);if(!r.ok)throw new Error(body.error||`${r.status} ${r.statusText}`);}catch(err){result.textContent=`Error: ${err.message}`;}finally{document.querySelectorAll('button').forEach(x=>x.disabled=false);}}
document.querySelectorAll('button[data-action]').forEach(x=>x.addEventListener('click',()=>run(x.dataset.action)));loadCsrf().catch(err=>result.textContent=`Error: ${err.message}`);
</script></body></html>"""


def _record_action(miner: TwitchChannelPointsMiner, action: str, result: str) -> None:
    history = status_history_patch.get_history(miner.twitch)
    if history is not None:
        history.record_event(
            int(time.time()),
            "WEB_CONTROL",
            f"{action}: {result}",
            {"action": action, "result": result},
        )


def _run_background(
    server: status_web_patch.StatusWebServer,
    action: str,
    callback: Callable[[], Any],
):
    if not server._control_action_lock.acquire(blocking=False):
        return jsonify(
            {"ok": False, "error": "another control action is running"}
        ), 409

    def worker():
        try:
            callback()
            _record_action(server.miner, action, "completed")
        except Exception as exc:
            _record_action(server.miner, action, f"failed: {exc}")
        finally:
            server._control_action_lock.release()

    threading.Thread(
        target=worker,
        name=f"Miner web control: {action}",
        daemon=True,
    ).start()
    return jsonify({"ok": True, "action": action, "status": "accepted"}), 202


def _check_csrf(server: status_web_patch.StatusWebServer) -> None:
    supplied = request.headers.get("X-CSRF-Token", "")
    if not supplied or not secrets.compare_digest(
        supplied, server._control_csrf
    ):
        abort(403)


def apply_patch() -> None:
    """Add disabled-by-default controls to the optional status server."""
    server_class = status_web_patch.StatusWebServer

    original_create_app = server_class._create_app
    if not getattr(original_create_app, _PATCH_MARKER, False):
        def create_app_with_controls(self):
            app = original_create_app(self)

            @app.get("/controls")
            def controls_page():
                if not getattr(self, "controls_enabled", False):
                    abort(404)
                return render_template_string(_CONTROLS_PAGE)

            @app.get("/api/control/csrf")
            def control_csrf():
                if not getattr(self, "controls_enabled", False):
                    abort(404)
                return jsonify({"csrf_token": self._control_csrf})

            @app.get("/api/control/status")
            def control_status():
                if not getattr(self, "controls_enabled", False):
                    abort(404)
                return jsonify(
                    {
                        "enabled": True,
                        "busy": self._control_action_lock.locked(),
                    }
                )

            @app.post("/api/control/refresh-dashboard")
            def refresh_dashboard():
                if not getattr(self, "controls_enabled", False):
                    abort(404)
                _check_csrf(self)
                refreshed = status_dashboard_patch.request_dashboard_refresh(
                    self.miner.twitch
                )
                _record_action(
                    self.miner,
                    "refresh-dashboard",
                    "scheduled" if refreshed else "dashboard unavailable",
                )
                return jsonify({"ok": refreshed, "status": "scheduled"})

            @app.post("/api/control/claim-drops")
            def claim_drops():
                if not getattr(self, "controls_enabled", False):
                    abort(404)
                _check_csrf(self)
                return _run_background(
                    self,
                    "claim-drops",
                    self.miner.twitch.claim_all_drops_from_inventory,
                )

            @app.post("/api/control/refresh-streamers")
            def refresh_streamers():
                if not getattr(self, "controls_enabled", False):
                    abort(404)
                _check_csrf(self)

                def refresh():
                    for streamer in list(self.miner.streamers):
                        self.miner.twitch.check_streamer_online(streamer)
                    status_dashboard_patch.request_dashboard_refresh(
                        self.miner.twitch
                    )

                return _run_background(self, "refresh-streamers", refresh)

            @app.post("/api/control/clear-dashboard-activity")
            def clear_dashboard_activity():
                if not getattr(self, "controls_enabled", False):
                    abort(404)
                _check_csrf(self)
                with status_dashboard_patch._STATE_LOCK:
                    state = status_dashboard_patch._STATES.get(
                        id(self.miner.twitch)
                    )
                if state is None:
                    return jsonify(
                        {"ok": False, "error": "dashboard unavailable"}
                    ), 409
                with state._lock:
                    state.recent_claims.clear()
                    state.last_points_event = None
                    state.last_event = None
                    state._save()
                state.mark_dirty()
                _record_action(
                    self.miner,
                    "clear-dashboard-activity",
                    "completed",
                )
                return jsonify({"ok": True, "status": "completed"})

            return app

        setattr(create_app_with_controls, _PATCH_MARKER, True)
        server_class._create_app = create_app_with_controls

    original_init = server_class.__init__
    if not getattr(original_init, _PATCH_MARKER, False):
        def init_with_controls(self, miner, host, port, token):
            with _CONFIG_LOCK:
                config = dict(_CONFIG.get(id(miner), {}))
            self.controls_enabled = bool(config.get("controls_enabled", False))
            if self.controls_enabled and not token:
                raise ValueError(
                    "status_web_token is required when status_web_controls is enabled"
                )
            self._control_csrf = secrets.token_urlsafe(32)
            self._control_action_lock = threading.Lock()
            original_init(self, miner, host, port, token)

        setattr(init_with_controls, _PATCH_MARKER, True)
        server_class.__init__ = init_with_controls

    original_run = TwitchChannelPointsMiner.run
    if not getattr(original_run, _PATCH_MARKER, False):
        def run_with_controls(
            self,
            *args,
            status_web_controls=False,
            **kwargs,
        ):
            if status_web_controls and not kwargs.get("status_web", False):
                raise ValueError(
                    "status_web=True is required when status_web_controls is enabled"
                )
            if status_web_controls and not kwargs.get("status_web_token"):
                raise ValueError(
                    "status_web_token is required when status_web_controls is enabled"
                )
            with _CONFIG_LOCK:
                _CONFIG[id(self)] = {
                    "controls_enabled": bool(status_web_controls)
                }
            try:
                return original_run(self, *args, **kwargs)
            finally:
                with _CONFIG_LOCK:
                    _CONFIG.pop(id(self), None)

        setattr(run_with_controls, _PATCH_MARKER, True)
        TwitchChannelPointsMiner.run = run_with_controls

    original_mine = TwitchChannelPointsMiner.mine
    if not getattr(original_mine, _PATCH_MARKER, False):
        def mine_with_controls(
            self,
            *args,
            status_web_controls=False,
            **kwargs,
        ):
            return self.run(
                *args,
                status_web_controls=status_web_controls,
                **kwargs,
            )

        setattr(mine_with_controls, _PATCH_MARKER, True)
        TwitchChannelPointsMiner.mine = mine_with_controls
