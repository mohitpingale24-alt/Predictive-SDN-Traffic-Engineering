"""Double-click entry point: reuse this app or start it, then open its local page."""
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

URL = 'http://127.0.0.1:8765/'


def ready():
    try:
        with urlopen(URL + 'api/health', timeout=1) as response:
            return json.load(response).get('application') == 'sdn-traffic-lab'
    except (URLError, ValueError, OSError): return False


def main():
    if ready():
        webbrowser.open(URL)
        return
    process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('server.py'))])
    try:
        for _ in range(30):
            if process.poll() is not None:
                raise SystemExit('Could not start SDN Traffic Lab. Check whether port 8765 is used by another app.')
            if ready():
                webbrowser.open(URL)
                print('SDN Traffic Lab is running. Leave this window open; press Ctrl+C to stop the dashboard.')
                process.wait()
                return
            time.sleep(.2)
        raise SystemExit('The dashboard did not become ready. Check the messages above.')
    except KeyboardInterrupt:
        pass
    finally:
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()


if __name__ == '__main__': main()
