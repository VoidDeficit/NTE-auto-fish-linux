import threading
import time
import subprocess
from jeepney import DBusAddress, new_method_call, MessageType
from jeepney.io.blocking import open_dbus_connection

conn = open_dbus_connection(bus='SESSION')

sender = conn.unique_name.replace(':', '').replace('.', '_')

def make_portal_call(method, body, signature):
    msg = new_method_call(
        DBusAddress(
            '/org/freedesktop/portal/desktop',
            bus_name='org.freedesktop.portal.Desktop',
            interface='org.freedesktop.portal.ScreenCast'
        ),
        method, signature, body
    )
    return conn.send_and_get_reply(msg)

# Step 1: CreateSession
print("Creating session...")
reply = make_portal_call(
    'CreateSession',
    ({'handle_token': ('s', 'token1'), 'session_handle_token': ('s', 'sess1')},),
    'a{sv}'
)
print("Session reply:", reply)

# Listen for signals
print("\nListening for Response signal (approve the popup!)...")
deadline = time.time() + 30
while time.time() < deadline:
    msg = conn.receive()
    if msg.header.message_type == MessageType.signal:
        print("Signal:", msg.header.fields, msg.body)
        break
