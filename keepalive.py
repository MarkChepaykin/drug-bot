import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer


VERSION = "v8"
status = "starting"


def full_status() -> str:
    return f"{status} {VERSION}"


class _Ping(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(full_status().encode())

    def log_message(self, *args):
        pass


def start():
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(
        target=HTTPServer(("0.0.0.0", port), _Ping).serve_forever,
        daemon=True,
    ).start()

    url = os.environ.get("RENDER_EXTERNAL_URL")
    if url:
        def ping():
            while True:
                time.sleep(600)
                try:
                    urllib.request.urlopen(url, timeout=30)
                except Exception:
                    pass
        threading.Thread(target=ping, daemon=True).start()
