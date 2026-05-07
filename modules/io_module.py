"""
Screen capture and input helpers — backend selected automatically by platform.
  Windows : MSS capture  +  PyDirectInput
  Linux   : XDG Desktop Portal + PipeWire + GStreamer  +  pynput
"""
import os
import sys
import threading
import time

import cv2
import numpy as np

_WINDOWS = sys.platform == 'win32'

# ── Windows-only imports ─────────────────────────────────────────────────────
if _WINDOWS:
    import mss as _mss
    import pydirectinput as _pdi

# ── Linux-only imports ───────────────────────────────────────────────────────
else:
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    from jeepney import DBusAddress, new_method_call, MessageType
    from jeepney.io.blocking import open_dbus_connection
    from pynput.keyboard import Controller as _KbController, Key as _Key
    from pynput.mouse import Controller as _MouseController, Button as _Button

    # When frozen by PyInstaller, --collect-all gi causes the gi hook to set
    # GST_PLUGIN_PATH to the (empty) bundle dir. Clear it so GStreamer falls
    # back to the system plugin directory where pipewiresrc lives.
    if getattr(sys, 'frozen', False):
        os.environ.pop('GST_PLUGIN_PATH', None)
        os.environ['GST_PLUGIN_SYSTEM_PATH'] = '/usr/lib/gstreamer-1.0'

    Gst.init(None)

    # Map pyautogui-style string names to pynput Key objects
    _KEY_MAP: dict[str, _Key] = {
        'left': _Key.left, 'right': _Key.right,
        'up': _Key.up, 'down': _Key.down,
        'space': _Key.space, 'enter': _Key.enter,
        'esc': _Key.esc, 'escape': _Key.esc,
        'tab': _Key.tab, 'backspace': _Key.backspace,
        'shift': _Key.shift, 'ctrl': _Key.ctrl, 'alt': _Key.alt,
        'f1': _Key.f1, 'f2': _Key.f2, 'f3': _Key.f3, 'f4': _Key.f4,
        'f5': _Key.f5, 'f6': _Key.f6, 'f7': _Key.f7, 'f8': _Key.f8,
        'f9': _Key.f9, 'f10': _Key.f10, 'f11': _Key.f11, 'f12': _Key.f12,
    }

    def _resolve(key: str):
        return _KEY_MAP.get(key.lower(), key)

    _conn = open_dbus_connection(bus='SESSION')
    _PORTAL = DBusAddress(
        '/org/freedesktop/portal/desktop',
        bus_name='org.freedesktop.portal.Desktop',
        interface='org.freedesktop.portal.ScreenCast'
    )

    def _call(method, body, signature):
        msg = new_method_call(_PORTAL, method, signature, body)
        return _conn.send_and_get_reply(msg)

    def _wait_for_response(handle):
        while True:
            msg = _conn.receive()
            if (msg.header.message_type == MessageType.signal and
                    msg.header.fields.get(1) == handle):
                return msg.body

    def open_portal_stream() -> int:
        """XDG ScreenCast portal handshake. Shows monitor picker. Returns PipeWire node_id."""
        print("[Portal] Creating session...")
        reply = _call('CreateSession', (
            {'handle_token': ('s', 'tok1'), 'session_handle_token': ('s', 'sess1')},
        ), 'a{sv}')
        handle = reply.body[0]
        response = _wait_for_response(handle)
        session = response[1]['session_handle'][1]
        print(f"[Portal] Session: {session}")

        print("[Portal] Select your monitor in the popup...")
        reply = _call('SelectSources', (
            session,
            {
                'handle_token': ('s', 'tok2'),
                'types': ('u', 1),
                'multiple': ('b', False),
                'cursor_mode': ('u', 2),
            }
        ), 'oa{sv}')
        handle = reply.body[0]
        _wait_for_response(handle)
        print("[Portal] Sources selected!")

        print("[Portal] Starting stream...")
        reply = _call('Start', (
            session, '',
            {'handle_token': ('s', 'tok3')},
        ), 'osa{sv}')
        handle = reply.body[0]
        response = _wait_for_response(handle)
        streams = response[1].get('streams', ('a(ua{sv})', []))[1]
        if not streams:
            raise RuntimeError("No streams returned from portal")
        node_id = streams[0][0]
        print(f"[Portal] PipeWire node ID: {node_id}")
        return node_id


class CaptureModule:
    """
    Screen capture — MSS on Windows, PipeWire/GStreamer on Linux.
    Same public interface on both platforms.
    """

    def __init__(self, node_id: int | None = None) -> None:
        if _WINDOWS:
            self._sct = _mss.mss()
            mon = self._sct.monitors[1]
            self._width = mon['width']
            self._height = mon['height']
        else:
            self._pipeline = None
            self._sink = None
            self._width = 0
            self._height = 0
            self._lock = threading.Lock()
            self._latest_frame: np.ndarray | None = None
            self._running = False
            self._node_id = node_id
            self._init_pipeline()

    # ── Linux pipeline ───────────────────────────────────────────────────────

    def _init_pipeline(self) -> None:
        if self._node_id is None:
            self._node_id = open_portal_stream()

        print(f"[Capture] Starting pipeline (node {self._node_id})...")

        pipeline_str = (
            f"pipewiresrc path={self._node_id} ! "
            f"videoconvert ! "
            f"appsink name=sink max-buffers=1 drop=true sync=false"
        )

        self._pipeline = Gst.parse_launch(pipeline_str)
        self._sink = self._pipeline.get_by_name('sink')
        self._pipeline.set_state(Gst.State.PLAYING)

        time.sleep(2)
        sample = self._sink.emit('pull-sample')
        if sample:
            frame = self._decode_sample(sample)
            if frame is not None:
                self._height, self._width = frame.shape[:2]
                with self._lock:
                    self._latest_frame = frame

        if self._latest_frame is None:
            raise RuntimeError("No frame received — check PipeWire node")

        print(f"[Capture] Ready! Resolution: {self._width}x{self._height}")

        self._running = True
        threading.Thread(target=self._capture_loop, daemon=True).start()

    def _decode_sample(self, sample) -> np.ndarray | None:
        try:
            buf = sample.get_buffer()
            caps = sample.get_caps()
            s = caps.get_structure(0)
            w = s.get_value('width')
            h = s.get_value('height')
            ok, mapinfo = buf.map(Gst.MapFlags.READ)
            if not ok:
                return None
            arr = np.frombuffer(mapinfo.data, dtype=np.uint8)
            buf.unmap(mapinfo)
            channels = len(arr) // (w * h)
            frame = arr.reshape((h, w, channels))
            if channels == 4:
                return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            if channels == 3:
                return np.ascontiguousarray(frame)
            return np.ascontiguousarray(frame[:, :, :3])
        except Exception as e:
            print(f"[Capture] Decode error: {e}")
            return None

    def _capture_loop(self) -> None:
        while self._running:
            sample = self._sink.emit('pull-sample')
            if sample:
                frame = self._decode_sample(sample)
                if frame is not None:
                    with self._lock:
                        self._latest_frame = frame

    def _get_frame(self) -> np.ndarray:
        with self._lock:
            if self._latest_frame is None:
                raise RuntimeError("No frame available yet")
            return self._latest_frame.copy()

    # ── Public interface (both platforms) ────────────────────────────────────

    def grab_bgr(self, roi: dict) -> np.ndarray:
        if _WINDOWS:
            region = {k: roi[k] for k in ('top', 'left', 'width', 'height')}
            img = np.array(self._sct.grab(region))
            return np.ascontiguousarray(cv2.cvtColor(img, cv2.COLOR_BGRA2BGR))
        else:
            frame = self._get_frame()
            t, l, h, w = roi['top'], roi['left'], roi['height'], roi['width']
            return np.ascontiguousarray(frame[t:t+h, l:l+w])

    def grab_full_screen(self) -> np.ndarray:
        if _WINDOWS:
            mon = self._sct.monitors[1]
            img = np.array(self._sct.grab(mon))
            return np.ascontiguousarray(cv2.cvtColor(img, cv2.COLOR_BGRA2BGR))
        else:
            return self._get_frame()

    def get_screen_size(self) -> tuple[int, int]:
        return self._width, self._height

    def close(self) -> None:
        if _WINDOWS:
            self._sct.close()
        else:
            self._running = False
            if self._pipeline:
                self._pipeline.set_state(Gst.State.NULL)
                self._pipeline = None


class InputModule:
    """
    Input module — PyDirectInput on Windows, pynput on Linux.
    Same public interface on both platforms. Thread-safe key tracking.
    """

    def __init__(self) -> None:
        self._held: set[str] = set()
        self._lock = threading.Lock()
        if not _WINDOWS:
            self._kb = _KbController()
            self._mouse = _MouseController()

    def press(self, key: str, duration: float = 0.05) -> None:
        if _WINDOWS:
            _pdi.keyDown(key)
            time.sleep(duration)
            _pdi.keyUp(key)
        else:
            k = _resolve(key)
            self._kb.press(k)
            time.sleep(duration)
            self._kb.release(k)

    def hold(self, key: str) -> None:
        with self._lock:
            if key in self._held:
                return
            self._held.add(key)
        try:
            if _WINDOWS:
                _pdi.keyDown(key)
            else:
                self._kb.press(_resolve(key))
        except Exception:
            with self._lock:
                self._held.discard(key)
            raise

    def release(self, key: str) -> None:
        with self._lock:
            if key not in self._held:
                return
            self._held.discard(key)
        if _WINDOWS:
            _pdi.keyUp(key)
        else:
            self._kb.release(_resolve(key))

    def release_all(self) -> None:
        with self._lock:
            keys = list(self._held)
            self._held.clear()
        for key in keys:
            try:
                if _WINDOWS:
                    _pdi.keyUp(key)
                else:
                    self._kb.release(_resolve(key))
            except Exception:
                pass

    def click(self, x: int, y: int) -> None:
        if _WINDOWS:
            _pdi.click(x, y)
        else:
            self._mouse.position = (x, y)
            self._mouse.click(_Button.left)
