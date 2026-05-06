import gi
gi.require_version('Gst', '1.0')
gi.require_version('GLib', '2.0')
from gi.repository import Gst, GLib
import subprocess
import sys

Gst.init(None)

# Step 1: get node_id via portal (run this separately to get the ID)
# For now hardcode a test with pw-cli
result = subprocess.run(['pw-cli', 'list-objects', 'PipeWire:Interface:Node'],
    capture_output=True, text=True)

for line in result.stdout.split('\n'):
    if any(x in line.lower() for x in ['game', 'nte', 'steam', 'proton', 'wine']):
        print(line)

print("\nAll nodes:")
print(result.stdout[:3000])
