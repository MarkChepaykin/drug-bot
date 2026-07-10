import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

VERSION = "v30"
status = "starting"

# Хуки для событий войса (ставит bot.py). Вызываются из потока HTTP-сервера.
on_utterance = None
on_speaking = None
on_music_state = None


def full_status() -> str:
    return f"{status} {VERSION}"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(full_status().encode())

    def do_POST(self):
        if self.path not in ("/utterance", "/speaking", "/music_state"):
            self.send_response(404)
            self.end_headers()
            return
        if self.headers.get("X-Ears-Token") != os.environ.get("EARS_TOKEN", "dev"):
            self.send_response(403)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/utterance" and on_utterance:
            on_utterance(data)
        elif self.path == "/speaking" and on_speaking:
            on_speaking(data)
        elif self.path == "/music_state" and on_music_state:
            on_music_state(data)
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
