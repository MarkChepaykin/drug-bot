import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

VERSION = "v15"
status = "starting"

# Хук для входящих фраз из войса (ставит bot.py). Вызывается из потока HTTP-сервера.
on_utterance = None


def full_status() -> str:
    return f"{status} {VERSION}"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(full_status().encode())

    def do_POST(self):
        if self.path != "/utterance":
            self.send_response(404)
            self.end_headers()
            return
        if self.headers.get("X-Ears-Token") != os.environ.get("EARS_TOKEN", "dev"):
            self.send_response(403)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(length) or b"{}")
        if on_utterance:
            on_utterance(data)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def start():
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(
        target=HTTPServer(("0.0.0.0", port), _Handler).serve_forever,
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
