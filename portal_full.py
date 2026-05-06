import time
import cv2
import numpy as np
import gi
gi.require_version('Gst', '1.0')
gi.require_version('GLib', '2.0')
from gi.repository import Gst, GLib
from jeepney import DBusAddress, new_method_call, MessageType
from jeepney.io.blocking import open_dbus_connection

Gst.init(None)

conn = open_dbus_connection(bus='SESSION')
sender = conn.unique_name.replace(':', '').replace('.', '_')

PORTAL = DBusAddress(
    '/org/freedesktop/portal/desktop',
    bus_name='org.freedesktop.portal.Desktop',
    interface='org.freedesktop.portal.ScreenCast'
)

def call(method, body, signature):
    msg = new_method_call(PORTAL, method, signature, body)
    return conn.send_and_get_reply(msg)

def wait_for_response(handle):
    while True:
        msg = conn.receive()
        if (msg.header.message_type == MessageType.signal and
            msg.header.fields.get(1) == handle):
            return msg.body

# Step 1: CreateSession
print("Step 1: Creating session...")
reply = call('CreateSession', (
    {'handle_token': ('s', 'tok1'), 'session_handle_token': ('s', 'sess1')},
), 'a{sv}')
handle = reply.body[0]
response = wait_for_response(handle)
session = response[1]['session_handle'][1]
print(f"Session: {session}")

# Step 2: SelectSources — window picker popup appears here
print("\nStep 2: SelectSources (pick your game window in the popup)...")
reply = call('SelectSources', (
    session,
    {
        'handle_token': ('s', 'tok2'),
        'types': ('u', 1),          # 1=monitor, 2=window, 3=both
        'multiple': ('b', False),
        'cursor_mode': ('u', 2),    # embed cursor
    }
), 'oa{sv}')
handle = reply.body[0]
wait_for_response(handle)
print("Sources selected!")

# Step 3: Start
print("\nStep 3: Starting stream...")
reply = call('Start', (
    session,
    '',
    {'handle_token': ('s', 'tok3')},
), 'osa{sv}')
handle = reply.body[0]
response = wait_for_response(handle)
streams = response[1].get('streams', ('a(ua{sv})', []))[1]
node_id = streams[0][0]
print(f"PipeWire node ID: {node_id}")

# Step 4: Open with GStreamer
print(f"\nOpening stream with GStreamer (node {node_id})...")
pipeline_str = (
    f"pipewiresrc path={node_id} ! "
    f"videoconvert ! "
    f"appsink name=sink max-buffers=1 drop=true sync=false"
)
pipeline = Gst.parse_launch(pipeline_str)
sink = pipeline.get_by_name('sink')
pipeline.set_state(Gst.State.PLAYING)
time.sleep(2)

# Grab a test frame
sample = sink.emit('pull-sample')
if sample:
    buf = sample.get_buffer()
    caps = sample.get_caps()
    s = caps.get_structure(0)
    w = s.get_value('width')
    h = s.get_value('height')
    ok, mapinfo = buf.map(Gst.MapFlags.READ)
    frame = np.frombuffer(mapinfo.data, dtype=np.uint8).reshape((h, w, 4))
    buf.unmap(mapinfo)
    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
    cv2.imwrite('portal_test.png', frame)
    print(f"Frame captured: {w}x{h} → saved portal_test.png")
else:
    print("No frame yet!")

pipeline.set_state(Gst.State.NULL)
