"""Experimental web dashboard for NTE Auto-Fish.

Launch:  python start_gui.py --web [--web-port 5000]
Then open http://localhost:5000 in any browser on the same machine.

Requires flask:  pip install flask
"""
from __future__ import annotations

import json
import queue
import threading
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from gui.bridge import BotBridge


def _fmt_roi(roi) -> Optional[str]:
    if not roi or (isinstance(roi, (tuple, list)) and all(v == 0 for v in roi)):
        return None
    return f"{roi[0]},{roi[1]}  {roi[2]}×{roi[3]}"


class WebServer:
    """SSE-based live dashboard.

    The server reads bot status via bridge.peek_status() (non-destructive) and
    receives log copies through its own observer queue registered on the bridge.
    Both run on daemon threads so they never block shutdown.
    """

    MAX_LOGS = 300

    def __init__(self, bridge: "BotBridge", port: int = 5000) -> None:
        try:
            import flask  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "flask is required for the web dashboard.\n"
                "Install it with:  pip install flask"
            )

        self._bridge = bridge
        self._port = port

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
                        "logs": logs_snap,
                    }
                    yield f"data: {json.dumps(data)}\n\n"
                    time.sleep(0.1)

            return flask.Response(
                flask.stream_with_context(generate()),
                mimetype="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

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
    .label { font-size:.68rem; color:var(--muted); text-transform:uppercase; letter-spacing:.07em; margin-bottom:4px; }
    .val   { font-size:1.8rem; font-weight:700; line-height:1; }
    .mono  { font-family:'JetBrains Mono',monospace; font-size:.8rem; }

    .badge { display:inline-flex; align-items:center; gap:5px; padding:3px 10px; border-radius:20px; font-size:.7rem; font-weight:600; }
    .badge-dot { width:6px; height:6px; border-radius:50%; }
    .badge-run   { background:rgba(16,185,129,.15); color:#10b981; }
    .badge-run   .badge-dot { background:#10b981; animation:blink 1.4s infinite; }
    .badge-pause { background:rgba(245,158,11,.15); color:#f59e0b; }
    .badge-pause .badge-dot { background:#f59e0b; }
    .badge-stop  { background:rgba(239,68,68,.15);  color:#ef4444; }
    .badge-stop  .badge-dot { background:#ef4444; }
    @keyframes blink { 0%,100%{opacity:1} 50%{opacity:.35} }

    .vis-track  { height:36px; border-radius:8px; background:#12122a; position:relative; overflow:hidden; }
    .vis-safe   { position:absolute; top:0; bottom:0; background:rgba(16,185,129,.2);
                  border-left:2px solid rgba(16,185,129,.5); border-right:2px solid rgba(16,185,129,.5); }
    .vis-cursor { position:absolute; top:50%; transform:translate(-50%,-50%);
                  width:8px; height:26px; background:#f59e0b; border-radius:3px;
                  box-shadow:0 0 10px rgba(245,158,11,.5); }

    .log-line { font-family:'JetBrains Mono',monospace; font-size:.7rem; line-height:1.65;
                white-space:pre-wrap; word-break:break-all; }
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
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;">
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

  <!-- Stat cards -->
  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:16px;">
    <div class="card">
      <div class="label">State</div>
      <div class="val" id="c-state" style="font-size:1.2rem;">—</div>
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

  <!-- Bar visualiser + PID chart -->
  <div style="display:grid;grid-template-columns:280px 1fr;gap:14px;margin-bottom:16px;">
    <div class="card" style="display:flex;flex-direction:column;gap:12px;">
      <div class="label" style="margin-bottom:0;">Bar Tracker</div>
      <div class="vis-track" id="vis-track">
        <div class="vis-safe"   id="vis-safe"   style="display:none;"></div>
        <div class="vis-cursor" id="vis-cursor" style="display:none;"></div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:4px;">
        <div><div class="label">Cursor X</div><div class="mono" id="t-curx">N/A</div></div>
        <div><div class="label">Target X</div><div class="mono" id="t-tgtx">N/A</div></div>
      </div>
    </div>

    <div class="card">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">
        <div class="label" style="margin-bottom:0;">PID Output</div>
        <div class="mono" style="color:var(--accent);" id="pid-val">0.000</div>
      </div>
      <div style="height:110px;position:relative;">
        <canvas id="pid-chart"></canvas>
      </div>
    </div>
  </div>

  <!-- Telemetry row -->
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:16px;">
    <div class="card" style="padding:14px;">
      <div class="label">Lost Frames</div>
      <div style="display:flex;gap:20px;margin-top:6px;">
        <div>
          <div class="mono" id="t-lost">0</div>
          <div class="label" style="margin-top:2px;font-size:.62rem;">Total</div>
        </div>
        <div>
          <div class="mono" id="t-lcur">0</div>
          <div class="label" style="margin-top:2px;font-size:.62rem;">Cursor</div>
        </div>
        <div>
          <div class="mono" id="t-ltgt">0</div>
          <div class="label" style="margin-top:2px;font-size:.62rem;">Target</div>
        </div>
      </div>
    </div>
    <div class="card" style="padding:14px;">
      <div class="label">Button ROI</div>
      <div class="mono" id="t-broi" style="margin-top:6px;">N/A</div>
    </div>
    <div class="card" style="padding:14px;">
      <div class="label">Bar ROI</div>
      <div class="mono" id="t-rroi" style="margin-top:6px;">N/A</div>
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
      style="height:220px;overflow-y:auto;background:#0d0d1a;border-radius:8px;padding:10px 12px;">
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
      fill: true,
      pointRadius: 0,
      tension: 0.35,
    }]
  },
  options: {
    animation: false,
    responsive: true,
    maintainAspectRatio: false,
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

function fmtTime(s) {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = Math.floor(s % 60);
  return h + ':' + String(m).padStart(2,'0') + ':' + String(ss).padStart(2,'0');
}

const stateMap = { IDLE:'Idle', WAITING:'Waiting', STRUGGLING:'Tracking', RESULT:'Result', UNKNOWN:'\\u2014' };

function setBadge(running, stopped) {
  const b = document.getElementById('badge');
  const t = document.getElementById('badge-text');
  b.className = 'badge ' + (stopped ? 'badge-stop' : running ? 'badge-run' : 'badge-pause');
  t.textContent = stopped ? 'Stopped' : running ? 'Running' : 'Paused';
}

function updateVis(cx, tx, bw) {
  const track = document.getElementById('vis-track');
  const safe  = document.getElementById('vis-safe');
  const cur   = document.getElementById('vis-cursor');
  const tw = track.clientWidth;
  if (!bw || !tw) { safe.style.display = cur.style.display = 'none'; return; }
  const sc = tw / bw;
  if (tx !== null && tx !== undefined) {
    const half = bw * 0.1 * sc;
    const mid  = tx * sc;
    safe.style.display = '';
    safe.style.left    = Math.max(0, mid - half) + 'px';
    safe.style.width   = (half * 2) + 'px';
  } else {
    safe.style.display = 'none';
  }
  if (cx !== null && cx !== undefined) {
    cur.style.display = '';
    cur.style.left    = (cx * sc) + 'px';
  } else {
    cur.style.display = 'none';
  }
}

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

// SSE stream
const es   = new EventSource('/stream');
const conn = document.getElementById('conn');

es.onopen  = () => { conn.textContent = '\\u25cf Live'; conn.style.color = '#10b981'; };
es.onerror = () => { conn.textContent = 'Reconnecting\\u2026'; conn.style.color = '#f59e0b'; };

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

  setBadge(d.is_running, d.is_stopped);
  updateVis(d.cursor_x, d.target_x, d.bar_width);

  pidBuf.shift();
  pidBuf.push(d.pid_output);
  pidChart.data.datasets[0].data = pidBuf.slice();
  pidChart.update('none');

  appendLogs(d.logs);
};
</script>
</body>
</html>"""
