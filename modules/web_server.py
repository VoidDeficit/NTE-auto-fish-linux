"""Experimental web dashboard for NTE Auto-Fish.

GUI mode:     python start_gui.py --web [--web-port 5000]
Headless:     python main.py start --web [--web-port 5000]
Then open:    http://localhost:5000

Requires flask:  pip install flask
"""
from __future__ import annotations

import json
import queue
import threading
import time
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from gui.bridge import BotBridge


def _fmt_roi(roi) -> Optional[str]:
    if not roi or (isinstance(roi, (tuple, list)) and all(v == 0 for v in roi)):
        return None
    return f"{roi[0]},{roi[1]}  {roi[2]}×{roi[3]}"


_SVG_PLACEHOLDER = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="60">'
    '<rect width="600" height="60" fill="#12122a"/>'
    '<text x="300" y="36" text-anchor="middle" fill="#334155"'
    ' font-family="sans-serif" font-size="13">No capture yet</text>'
    "</svg>"
).encode()


class WebServer:
    """SSE-based live dashboard with bot controls and a game-feed image.

    Status is read via bridge.peek_status() (non-destructive).
    Logs are received through a registered observer queue.
    Frame images are received through bridge.latest_frame().

    on_start / on_stop are optional callables invoked when the matching
    control button is pressed.  In headless mode pass None for both.
    """

    MAX_LOGS = 300

    def __init__(
        self,
        bridge: "BotBridge",
        port: int = 5000,
        on_start: Optional[Callable[[], None]] = None,
        on_stop: Optional[Callable[[], None]] = None,
    ) -> None:
        try:
            import flask  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "flask is required for the web dashboard.\n"
                "Install it with:  pip install flask"
            )

        self._bridge = bridge
        self._port = port
        self._on_start = on_start
        self._on_stop = on_stop

        self._log_q: queue.Queue[str] = queue.Queue(maxsize=500)
        bridge.register_log_observer(self._log_q)

        self._log_buf: list[str] = []
        self._lock = threading.Lock()

    def start(self) -> None:
        threading.Thread(target=self._drain_logs, daemon=True, name="web-log-drain").start()
        threading.Thread(target=self._serve, daemon=True, name="web-server").start()

    # ── Internal threads ───────────────────────────────────────────────────

    def _drain_logs(self) -> None:
        while True:
            try:
                msg = self._log_q.get(timeout=0.1)
                with self._lock:
                    self._log_buf.append(msg)
                    if len(self._log_buf) > self.MAX_LOGS:
                        del self._log_buf[: len(self._log_buf) - self.MAX_LOGS]
            except queue.Empty:
                pass

    def _serve(self) -> None:
        import logging
        import flask

        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        app = flask.Flask(__name__)
        server = self

        @app.route("/")
        def index():
            return _HTML

        @app.route("/stream")
        def stream():
            def generate():
                while True:
                    status = server._bridge.peek_status()
                    with server._lock:
                        logs_snap = list(server._log_buf)
                    data = {
                        "state": status.state.name,
                        "fish_count": status.fish_count,
                        "session_secs": status.session_secs,
                        "fps": round(status.fps, 1),
                        "pid_output": round(status.pid_output, 3),
                        "cursor_x": status.cursor_x,
                        "target_x": status.target_x,
                        "bar_width": status.bar_width,
                        "lost_frames": status.lost_frames,
                        "lost_cursor_frames": status.lost_cursor_frames,
                        "lost_target_frames": status.lost_target_frames,
                        "button_roi": _fmt_roi(status.button_roi),
                        "bar_roi": _fmt_roi(status.bar_roi),
                        "is_running": status.is_running,
                        "is_stopped": status.is_stopped,
                        "can_start": server._on_start is not None,
                        "logs": logs_snap,
                    }
                    yield f"data: {json.dumps(data)}\n\n"
                    time.sleep(0.1)

            return flask.Response(
                flask.stream_with_context(generate()),
                mimetype="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        @app.route("/frame")
        def frame():
            data = server._bridge.latest_frame()
            if not data:
                return flask.Response(_SVG_PLACEHOLDER, mimetype="image/svg+xml")
            return flask.Response(data, mimetype="image/jpeg",
                                  headers={"Cache-Control": "no-store"})

        @app.route("/cmd/<action>", methods=["POST"])
        def cmd(action):
            if action == "start":
                if server._on_start:
                    server._on_start()
                else:
                    flask.abort(501)
            elif action == "stop":
                if server._on_stop:
                    server._on_stop()
                else:
                    server._bridge.send_cmd("stop")
            elif action in ("pause", "resume"):
                server._bridge.send_cmd(action)
            else:
                flask.abort(400)
            return "", 204

        app.run(host="0.0.0.0", port=self._port, threaded=True, use_reloader=False)


# ── Embedded HTML dashboard ────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>NTE Auto-Fish Dashboard</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet"/>
  <style>
    :root {
      --bg:#0f0f1a; --surface:#1a1a2e; --border:#2a2a4a;
      --accent:#6c63ff; --amber:#f59e0b; --green:#10b981;
      --red:#ef4444; --text:#e2e8f0; --muted:#64748b;
    }
    *, *::before, *::after { box-sizing:border-box; margin:0; padding:0; }
    html { font-size:14px; }
    body { background:var(--bg); color:var(--text); font-family:'Inter',sans-serif; min-height:100vh; }

    .card { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:18px; }
    .label { font-size:.68rem; color:var(--muted); text-transform:uppercase;
             letter-spacing:.07em; margin-bottom:4px; }
    .val   { font-size:1.8rem; font-weight:700; line-height:1; }
    .mono  { font-family:'JetBrains Mono',monospace; font-size:.8rem; }

    /* Status badge */
    .badge { display:inline-flex; align-items:center; gap:5px; padding:3px 10px;
             border-radius:20px; font-size:.7rem; font-weight:600; }
    .badge-dot { width:6px; height:6px; border-radius:50%; }
    .badge-run   { background:rgba(16,185,129,.15); color:#10b981; }
    .badge-run   .badge-dot { background:#10b981; animation:blink 1.4s infinite; }
    .badge-pause { background:rgba(245,158,11,.15); color:#f59e0b; }
    .badge-pause .badge-dot { background:#f59e0b; }
    .badge-stop  { background:rgba(239,68,68,.15);  color:#ef4444; }
    .badge-stop  .badge-dot { background:#ef4444; }
    @keyframes blink { 0%,100%{opacity:1} 50%{opacity:.35} }

    /* Control buttons */
    .ctrl { padding:6px 16px; border:1px solid transparent; border-radius:7px;
            font-size:.8rem; font-weight:600; cursor:pointer; transition:opacity .1s, background .1s; }
    .ctrl:disabled { opacity:.28; cursor:default; }
    .ctrl-green { background:rgba(16,185,129,.12); color:#10b981; border-color:rgba(16,185,129,.3); }
    .ctrl-green:not(:disabled):hover { background:rgba(16,185,129,.22); }
    .ctrl-amber { background:rgba(245,158,11,.12); color:#f59e0b; border-color:rgba(245,158,11,.3); }
    .ctrl-amber:not(:disabled):hover { background:rgba(245,158,11,.22); }
    .ctrl-red   { background:rgba(239,68,68,.12);  color:#ef4444; border-color:rgba(239,68,68,.3); }
    .ctrl-red:not(:disabled):hover { background:rgba(239,68,68,.22); }

    /* Bar visualiser */
    .vis-track  { height:28px; border-radius:6px; background:#12122a; position:relative; overflow:hidden; }
    .vis-safe   { position:absolute; top:0; bottom:0; background:rgba(16,185,129,.2);
                  border-left:2px solid rgba(16,185,129,.5); border-right:2px solid rgba(16,185,129,.5); }
    .vis-cursor { position:absolute; top:50%; transform:translate(-50%,-50%);
                  width:7px; height:22px; background:#f59e0b; border-radius:2px;
                  box-shadow:0 0 8px rgba(245,158,11,.5); }

    /* Log colours */
    .log-line { font-family:'JetBrains Mono',monospace; font-size:.7rem;
                line-height:1.65; white-space:pre-wrap; word-break:break-all; }
    .lc-err  { color:#f87171; }
    .lc-warn { color:#fbbf24; }
    .lc-ok   { color:#34d399; }
    .lc-info { color:#94a3b8; }

    ::-webkit-scrollbar { width:5px; }
    ::-webkit-scrollbar-track { background:var(--surface); }
    ::-webkit-scrollbar-thumb { background:var(--border); border-radius:3px; }
  </style>
</head>
<body>
<div style="max-width:1120px;margin:0 auto;padding:20px 24px;">

  <!-- Header -->
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
    <div>
      <h1 style="font-size:1.3rem;font-weight:700;letter-spacing:-.01em;">NTE Auto-Fish</h1>
      <div style="color:var(--muted);font-size:.75rem;margin-top:2px;">Live Web Dashboard</div>
    </div>
    <div style="display:flex;align-items:center;gap:12px;">
      <span id="badge" class="badge badge-stop">
        <span class="badge-dot"></span><span id="badge-text">Stopped</span>
      </span>
      <span id="conn" style="color:var(--muted);font-size:.7rem;">Connecting…</span>
    </div>
  </div>

  <!-- Controls -->
  <div class="card" style="padding:12px 16px;margin-bottom:16px;
       display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
    <button id="btn-start"  class="ctrl ctrl-green" onclick="sendCmd('start')"
            disabled>&#9654; Start</button>
    <button id="btn-pause"  class="ctrl ctrl-amber" onclick="sendPauseResume()"
            data-action="pause" disabled>&#9646;&#9646; Pause</button>
    <button id="btn-stop"   class="ctrl ctrl-red"   onclick="sendCmd('stop')"
            disabled>&#9632; Stop</button>
    <div style="flex:1;"></div>
    <span style="color:var(--muted);font-size:.68rem;letter-spacing:.05em;text-transform:uppercase;">
      Bot Controls
    </span>
  </div>

  <!-- Stat cards -->
  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:16px;">
    <div class="card">
      <div class="label">State</div>
      <div class="val" id="c-state" style="font-size:1.15rem;">&#8212;</div>
    </div>
    <div class="card">
      <div class="label">Fish Caught</div>
      <div class="val" id="c-fish">0</div>
    </div>
    <div class="card">
      <div class="label">Session Time</div>
      <div class="val" id="c-time" style="font-size:1.4rem;">0:00:00</div>
    </div>
    <div class="card">
      <div class="label">FPS</div>
      <div class="val" id="c-fps">0.0</div>
    </div>
  </div>

  <!-- Game feed + right column -->
  <div style="display:grid;grid-template-columns:1fr 300px;gap:14px;margin-bottom:16px;">

    <!-- Game feed -->
    <div class="card" style="display:flex;flex-direction:column;gap:10px;">
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <div class="label" style="margin-bottom:0;">Game Feed</div>
        <div id="feed-label" style="color:var(--muted);font-size:.68rem;">&#8212;</div>
      </div>
      <div style="background:#0d0d1a;border-radius:8px;overflow:hidden;
                  display:flex;align-items:center;justify-content:center;min-height:72px;">
        <img id="feed-img" src="/frame" alt="No capture"
             style="width:100%;max-height:220px;object-fit:contain;display:block;"/>
      </div>
      <!-- Bar visualiser under feed -->
      <div>
        <div class="label">Bar Tracker</div>
        <div class="vis-track" id="vis-track">
          <div class="vis-safe"   id="vis-safe"   style="display:none;"></div>
          <div class="vis-cursor" id="vis-cursor" style="display:none;"></div>
        </div>
        <div style="display:flex;gap:16px;margin-top:8px;">
          <div><div class="label">Cursor X</div><div class="mono" id="t-curx">N/A</div></div>
          <div><div class="label">Target X</div><div class="mono" id="t-tgtx">N/A</div></div>
        </div>
      </div>
    </div>

    <!-- PID chart -->
    <div class="card" style="display:flex;flex-direction:column;gap:10px;">
      <div style="display:flex;align-items:center;justify-content:space-between;">
        <div class="label" style="margin-bottom:0;">PID Output</div>
        <div class="mono" style="color:var(--accent);" id="pid-val">0.000</div>
      </div>
      <div style="flex:1;min-height:120px;position:relative;">
        <canvas id="pid-chart"></canvas>
      </div>
      <!-- Telemetry inside right card -->
      <div style="border-top:1px solid var(--border);padding-top:10px;">
        <div class="label" style="margin-bottom:6px;">Lost Frames</div>
        <div style="display:flex;gap:16px;">
          <div><div class="mono" id="t-lost">0</div>
               <div class="label" style="font-size:.6rem;margin-top:2px;">Total</div></div>
          <div><div class="mono" id="t-lcur">0</div>
               <div class="label" style="font-size:.6rem;margin-top:2px;">Cursor</div></div>
          <div><div class="mono" id="t-ltgt">0</div>
               <div class="label" style="font-size:.6rem;margin-top:2px;">Target</div></div>
        </div>
      </div>
      <div style="border-top:1px solid var(--border);padding-top:10px;">
        <div class="label">Button ROI</div>
        <div class="mono" id="t-broi" style="margin-top:2px;font-size:.75rem;word-break:break-all;">N/A</div>
      </div>
      <div style="border-top:1px solid var(--border);padding-top:10px;">
        <div class="label">Bar ROI</div>
        <div class="mono" id="t-rroi" style="margin-top:2px;font-size:.75rem;word-break:break-all;">N/A</div>
      </div>
    </div>
  </div>

  <!-- Activity log -->
  <div class="card">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">
      <div class="label" style="margin-bottom:0;">Activity Log</div>
      <button onclick="clearLogs()"
        style="background:var(--border);border:none;color:var(--muted);
               padding:3px 9px;border-radius:6px;cursor:pointer;font-size:.7rem;">
        Clear
      </button>
    </div>
    <div id="log-box"
      style="height:220px;overflow-y:auto;background:#0d0d1a;
             border-radius:8px;padding:10px 12px;">
    </div>
  </div>

</div>
<script>
// PID chart
const N = 120;
const pidBuf = new Array(N).fill(0);
const pidChart = new Chart(document.getElementById('pid-chart').getContext('2d'), {
  type: 'line',
  data: {
    labels: new Array(N).fill(''),
    datasets: [{
      data: pidBuf,
      borderColor: '#6c63ff',
      borderWidth: 1.5,
      backgroundColor: 'rgba(108,99,255,0.08)',
      fill: true, pointRadius: 0, tension: 0.35,
    }]
  },
  options: {
    animation: false, responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { display: false },
      y: {
        grid: { color: 'rgba(42,42,74,0.5)', drawBorder: false },
        ticks: { color: '#64748b', font: { size: 10 }, maxTicksLimit: 5 },
        border: { display: false },
      }
    }
  }
});

// Helpers
function fmtTime(s) {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = Math.floor(s % 60);
  return h + ':' + String(m).padStart(2,'0') + ':' + String(ss).padStart(2,'0');
}
const stateMap = {
  IDLE:'Idle', WAITING:'Waiting', STRUGGLING:'Tracking', RESULT:'Result', UNKNOWN:'—'
};

// Badge
function setBadge(running, stopped) {
  const b = document.getElementById('badge'), t = document.getElementById('badge-text');
  b.className = 'badge ' + (stopped ? 'badge-stop' : running ? 'badge-run' : 'badge-pause');
  t.textContent = stopped ? 'Stopped' : running ? 'Running' : 'Paused';
}

// Controls
let canStart = false;
function updateControls(running, stopped) {
  const btnStart = document.getElementById('btn-start');
  const btnPause = document.getElementById('btn-pause');
  const btnStop  = document.getElementById('btn-stop');
  if (stopped) {
    btnStart.disabled = !canStart;
    btnPause.disabled = true;
    btnStop.disabled  = true;
    btnPause.innerHTML = '&#9646;&#9646; Pause';
    btnPause.dataset.action = 'pause';
    btnPause.className = 'ctrl ctrl-amber';
  } else if (running) {
    btnStart.disabled = true;
    btnPause.disabled = false;
    btnStop.disabled  = false;
    btnPause.innerHTML = '&#9646;&#9646; Pause';
    btnPause.dataset.action = 'pause';
    btnPause.className = 'ctrl ctrl-amber';
  } else {
    btnStart.disabled = true;
    btnPause.disabled = false;
    btnStop.disabled  = false;
    btnPause.innerHTML = '&#9654; Resume';
    btnPause.dataset.action = 'resume';
    btnPause.className = 'ctrl ctrl-green';
  }
}

function sendCmd(action) {
  fetch('/cmd/' + action, { method: 'POST' }).catch(() => {});
}
function sendPauseResume() {
  const action = document.getElementById('btn-pause').dataset.action || 'pause';
  sendCmd(action);
}

// Bar visualiser
function updateVis(cx, tx, bw) {
  const track = document.getElementById('vis-track');
  const safe  = document.getElementById('vis-safe');
  const cur   = document.getElementById('vis-cursor');
  const tw = track.clientWidth;
  if (!bw || !tw) { safe.style.display = cur.style.display = 'none'; return; }
  const sc = tw / bw;
  if (tx !== null && tx !== undefined) {
    const half = bw * 0.08 * sc;
    const mid  = tx * sc;
    safe.style.display = '';
    safe.style.left    = Math.max(0, mid - half) + 'px';
    safe.style.width   = (half * 2) + 'px';
  } else { safe.style.display = 'none'; }
  if (cx !== null && cx !== undefined) {
    cur.style.display = '';
    cur.style.left    = (cx * sc) + 'px';
  } else { cur.style.display = 'none'; }
}

// Game feed
let feedActive = false;
function startFeed() {
  if (feedActive) return;
  feedActive = true;
  (function tick() {
    const img = document.getElementById('feed-img');
    const next = new Image();
    next.onload = () => { img.src = next.src; };
    next.src = '/frame?' + Date.now();
    setTimeout(tick, 150);
  })();
}

// Logs
const logBox = document.getElementById('log-box');
let knownCount = 0;
function appendLogs(logs) {
  const fresh = logs.slice(knownCount);
  knownCount  = logs.length;
  if (!fresh.length) return;
  const atBot = logBox.scrollHeight - logBox.scrollTop <= logBox.clientHeight + 16;
  const frag  = document.createDocumentFragment();
  for (const line of fresh) {
    const d = document.createElement('div');
    d.className = 'log-line ' + (
      /error|fail|crash|exception/i.test(line) ? 'lc-err'  :
      /warn/i.test(line)                        ? 'lc-warn' :
      /caught|fish|success|complete/i.test(line)? 'lc-ok'  : 'lc-info'
    );
    d.textContent = line;
    frag.appendChild(d);
  }
  logBox.appendChild(frag);
  if (atBot) logBox.scrollTop = logBox.scrollHeight;
}
function clearLogs() { logBox.innerHTML = ''; knownCount = 0; }

// SSE
const es   = new EventSource('/stream');
const conn = document.getElementById('conn');
es.onopen  = () => { conn.textContent = '● Live'; conn.style.color = '#10b981'; startFeed(); };
es.onerror = () => { conn.textContent = 'Reconnecting…'; conn.style.color = '#f59e0b'; };

es.onmessage = (e) => {
  const d = JSON.parse(e.data);

  document.getElementById('c-state').textContent = stateMap[d.state] ?? d.state;
  document.getElementById('c-fish').textContent  = d.fish_count;
  document.getElementById('c-time').textContent  = fmtTime(d.session_secs);
  document.getElementById('c-fps').textContent   = d.fps.toFixed(1);
  document.getElementById('pid-val').textContent = d.pid_output.toFixed(3);
  document.getElementById('t-curx').textContent  = d.cursor_x  ?? 'N/A';
  document.getElementById('t-tgtx').textContent  = d.target_x  ?? 'N/A';
  document.getElementById('t-lost').textContent  = d.lost_frames;
  document.getElementById('t-lcur').textContent  = d.lost_cursor_frames;
  document.getElementById('t-ltgt').textContent  = d.lost_target_frames;
  document.getElementById('t-broi').textContent  = d.button_roi ?? 'N/A';
  document.getElementById('t-rroi').textContent  = d.bar_roi    ?? 'N/A';

  // Feed label
  const feedLbl = { WAITING:'Button ROI', STRUGGLING:'Bar ROI • tracking', RESULT:'Bar ROI' };
  document.getElementById('feed-label').textContent = feedLbl[d.state] ?? '';

  setBadge(d.is_running, d.is_stopped);
  updateVis(d.cursor_x, d.target_x, d.bar_width);

  // PID chart
  pidBuf.shift(); pidBuf.push(d.pid_output);
  pidChart.data.datasets[0].data = pidBuf.slice();
  pidChart.update('none');

  // Controls — set canStart once from first message
  canStart = !!d.can_start;
  updateControls(d.is_running, d.is_stopped);

  appendLogs(d.logs);
};
</script>
</body>
</html>"""
