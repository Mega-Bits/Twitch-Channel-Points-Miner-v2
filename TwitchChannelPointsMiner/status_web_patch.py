"""Optional read-only web dashboard backed by live status and SQLite history."""

from __future__ import annotations

import hmac
import logging
import threading
from typing import Any

from flask import Flask, Response, jsonify, render_template_string, request
from werkzeug.serving import make_server

from TwitchChannelPointsMiner import status_dashboard_patch, status_history_patch
from TwitchChannelPointsMiner.TwitchChannelPointsMiner import TwitchChannelPointsMiner

logger = logging.getLogger(__name__)
_PATCH_MARKER = "_status_web_patch"
_SERVERS_LOCK = threading.RLock()
_SERVERS: dict[int, "StatusWebServer"] = {}
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Twitch Miner Status</title>
<style>
:root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
body { margin: 0; background: #0b0d12; color: #eef1f7; }
main { max-width: 1180px; margin: auto; padding: 24px; }
header { display:flex; gap:16px; justify-content:space-between; align-items:center; margin-bottom:20px; }
h1 { font-size: 1.45rem; margin: 0; }
.muted { color:#9aa4b2; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:14px; }
.card { background:#151922; border:1px solid #252b38; border-radius:14px; padding:16px; box-shadow:0 10px 30px #0004; }
.card h2 { font-size: .95rem; margin:0 0 12px; color:#b9c2d0; }
.value { font-size:1.15rem; font-weight:700; }
.row { padding:9px 0; border-top:1px solid #252b38; }
.row:first-child { border-top:0; padding-top:0; }
.badge { display:inline-block; padding:3px 8px; border-radius:999px; background:#252b38; font-size:.78rem; }
.progress { height:7px; border-radius:99px; background:#252b38; overflow:hidden; margin-top:7px; }
.progress > span { display:block; height:100%; background:#8b5cf6; }
a { color:#a78bfa; text-decoration:none; }
pre { white-space:pre-wrap; word-break:break-word; font-family:inherit; margin:6px 0 0; }
table { width:100%; border-collapse:collapse; font-size:.88rem; }
th,td { text-align:left; padding:8px 6px; border-top:1px solid #252b38; vertical-align:top; }
th { color:#9aa4b2; font-weight:600; }
#status-dot { width:9px; height:9px; border-radius:50%; display:inline-block; background:#64748b; margin-right:7px; }
@media (max-width:600px) { main { padding:14px; } table { display:block; overflow:auto; } }
</style>
</head>
<body><main>
<header><div><h1><span id="status-dot"></span>Twitch Miner Status</h1><div class="muted" id="updated">Loading…</div></div><span class="badge">read only</span></header>
<section class="grid" id="summary"></section>
<section class="grid" style="margin-top:14px"><article class="card"><h2>Drop campaigns</h2><div id="campaigns"></div></article><article class="card"><h2>Recent events</h2><div id="events"></div></article></section>
<section class="card" style="margin-top:14px"><h2>Watch sessions</h2><div id="sessions"></div></section>
</main>
<script>
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dt = value => value ? new Date(value * 1000).toLocaleString() : 'pending';
const ago = value => { if(!value) return 'pending'; const s=Math.round(Date.now()/1000-value); if(s<60)return `${s}s ago`; if(s<3600)return `${Math.floor(s/60)}m ago`; if(s<86400)return `${Math.floor(s/3600)}h ago`; return `${Math.floor(s/86400)}d ago`; };
async function get(path){ const r=await fetch(path,{cache:'no-store'}); if(!r.ok) throw new Error(`${r.status} ${r.statusText}`); return r.json(); }
function renderStatus(s){
 document.getElementById('status-dot').style.background=s.running?'#22c55e':'#64748b';
 document.getElementById('updated').textContent=`Inventory sync: ${ago(s.last_inventory_sync)} · Started: ${dt(s.started_at)}`;
 const slots=(s.watch_slots||[]).map((x,i)=>`<div class="row"><div><b>${i+1}. <a href="https://twitch.tv/${esc(x.username)}">${esc(x.username)}</a></b> <span class="badge">${esc(x.reason)}</span></div>${x.campaign?`<div class="muted">${esc(x.campaign.game)} · ${esc(x.campaign.drop.name)} · ${esc(x.campaign.drop.current)}/${esc(x.campaign.drop.required)} min</div>`:''}</div>`).join('')||'<div class="muted">No active watch slot</div>';
 document.getElementById('summary').innerHTML=`<article class="card"><h2>Status</h2><div class="value">${s.running?'Running':'Stopped'}</div><div class="muted">${esc((s.priority||[]).join(' > '))}</div></article><article class="card"><h2>Watch slots</h2>${slots}</article><article class="card"><h2>History</h2><div class="value">${esc(s.history?.events||0)} events</div><div class="muted">${esc(s.history?.watch_sessions||0)} watch sessions · ${esc(s.history?.retention_days||0)} day retention</div></article>`;
 document.getElementById('campaigns').innerHTML=(s.campaigns||[]).map(c=>`<div class="row"><div><b>${esc(c.game)} — ${esc(c.name)}</b> <span class="badge">${c.locked?'active':'queued'}</span></div><div class="muted">Ends ${dt(c.end_at)} · ${(c.eligible||[]).map(x=>esc(x.username)).join(', ')||'searching'}</div>${(c.drops||[]).map(d=>`<div style="margin-top:8px">${esc(d.name)} · ${esc(d.current)}/${esc(d.required)} min · ${esc(d.percent)}%<div class="progress"><span style="width:${Math.max(0,Math.min(100,Number(d.percent)||0))}%"></span></div></div>`).join('')}</div>`).join('')||'<div class="muted">No open Drop campaign</div>';
}
function renderEvents(items){ document.getElementById('events').innerHTML=items.map(e=>`<div class="row"><b>${esc(e.event)}</b> <span class="muted">${ago(e.timestamp)}</span><pre>${esc(e.message)}</pre></div>`).join('')||'<div class="muted">No events</div>'; }
function renderSessions(items){ document.getElementById('sessions').innerHTML=`<table><thead><tr><th>Channel</th><th>Reason</th><th>Campaign</th><th>Started</th><th>Ended</th></tr></thead><tbody>${items.map(x=>`<tr><td><a href="https://twitch.tv/${esc(x.channel)}">${esc(x.channel)}</a><br><span class="muted">${esc(x.source)}</span></td><td>${esc(x.reason)}</td><td>${esc(x.campaign_name||'—')}</td><td>${esc(dt(x.started_at))}</td><td>${esc(x.ended_at?dt(x.ended_at):'active')}</td></tr>`).join('')}</tbody></table>`; }
async function refresh(){ try { const [s,e,w]=await Promise.all([get('/api/status'),get('/api/events?limit=20'),get('/api/watch-sessions?limit=30')]); renderStatus(s); renderEvents(e.items); renderSessions(w.items); } catch(err){ document.getElementById('updated').textContent=`Unavailable: ${err.message}`; } }
refresh(); setInterval(refresh,10000);
</script></body></html>"""


class StatusWebServer:
    def __init__(
        self,
        miner: TwitchChannelPointsMiner,
        host: str,
        port: int,
        token: str | None,
    ) -> None:
        self.miner = miner
        self.host = host
        self.port = int(port)
        self.token = token or None
        if host not in _LOOPBACK_HOSTS and not self.token:
            raise ValueError(
                "status_web_token is required when status_web_host is not loopback"
            )
        self.app = self._create_app()
        self._server = make_server(host, self.port, self.app, threaded=True)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="Miner status web server",
            daemon=True,
        )

    def _authorized(self) -> bool:
        if not self.token:
            return True
        auth = request.authorization
        return bool(
            auth
            and auth.password
            and hmac.compare_digest(auth.password, self.token)
        )

    def _create_app(self) -> Flask:
        app = Flask("twitch-miner-status")
        app.config["JSON_SORT_KEYS"] = False

        @app.before_request
        def require_auth():
            if request.path == "/healthz" or self._authorized():
                return None
            return Response(
                "Authentication required",
                401,
                {"WWW-Authenticate": 'Basic realm="Twitch Miner Status"'},
            )

        @app.after_request
        def security_headers(response):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; connect-src 'self'"
            )
            return response

        @app.get("/")
        def index():
            return render_template_string(_PAGE)

        @app.get("/healthz")
        def health():
            return jsonify({"ok": True})

        @app.get("/api/status")
        def status():
            return jsonify(
                status_dashboard_patch.get_status_snapshot(self.miner.twitch)
            )

        @app.get("/api/events")
        def events():
            history = status_history_patch.get_history(self.miner.twitch)
            limit = request.args.get("limit", 100, type=int)
            event = request.args.get("event") or None
            items = history.recent_events(limit, event) if history else []
            return jsonify({"items": items})

        @app.get("/api/claims")
        def claims():
            history = status_history_patch.get_history(self.miner.twitch)
            limit = request.args.get("limit", 20, type=int)
            items = history.recent_claims(limit) if history else []
            return jsonify({"items": items})

        @app.get("/api/watch-sessions")
        def watch_sessions():
            history = status_history_patch.get_history(self.miner.twitch)
            limit = request.args.get("limit", 100, type=int)
            items = history.recent_watch_sessions(limit) if history else []
            return jsonify({"items": items})

        @app.get("/api/snapshots")
        def snapshots():
            history = status_history_patch.get_history(self.miner.twitch)
            limit = request.args.get("limit", 100, type=int)
            kind = request.args.get("kind") or None
            items = history.recent_snapshots(kind, limit) if history else []
            return jsonify({"items": items})

        return app

    def start(self) -> None:
        self._thread.start()
        logger.info(
            "Status web dashboard listening on http://%s:%s",
            self.host,
            self.port,
        )

    def stop(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()


def get_server(twitch: Any | None = None) -> StatusWebServer | None:
    with _SERVERS_LOCK:
        if twitch is not None:
            return _SERVERS.get(id(twitch))
        if len(_SERVERS) == 1:
            return next(iter(_SERVERS.values()))
        return None


def apply_patch() -> None:
    """Add optional web arguments to `run()` and `mine()`."""
    original_run = TwitchChannelPointsMiner.run
    if not getattr(original_run, _PATCH_MARKER, False):
        def run_with_status_web(
            self,
            *args,
            status_web=False,
            status_web_host="127.0.0.1",
            status_web_port=8080,
            status_web_token=None,
            **kwargs,
        ):
            if not status_web:
                return original_run(self, *args, **kwargs)
            server = StatusWebServer(
                self,
                host=str(status_web_host),
                port=int(status_web_port),
                token=status_web_token,
            )
            with _SERVERS_LOCK:
                previous = _SERVERS.pop(id(self.twitch), None)
                _SERVERS[id(self.twitch)] = server
            if previous is not None:
                previous.stop()
            server.start()
            try:
                return original_run(self, *args, **kwargs)
            finally:
                server.stop()
                with _SERVERS_LOCK:
                    _SERVERS.pop(id(self.twitch), None)

        setattr(run_with_status_web, _PATCH_MARKER, True)
        TwitchChannelPointsMiner.run = run_with_status_web

    original_mine = TwitchChannelPointsMiner.mine
    if not getattr(original_mine, _PATCH_MARKER, False):
        def mine_with_status_web(
            self,
            *args,
            status_web=False,
            status_web_host="127.0.0.1",
            status_web_port=8080,
            status_web_token=None,
            **kwargs,
        ):
            return self.run(
                *args,
                status_web=status_web,
                status_web_host=status_web_host,
                status_web_port=status_web_port,
                status_web_token=status_web_token,
                **kwargs,
            )

        setattr(mine_with_status_web, _PATCH_MARKER, True)
        TwitchChannelPointsMiner.mine = mine_with_status_web
