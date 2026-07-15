#!/usr/bin/env python3
"""
ripchamp_trim_ui.py

Single-shot browser picker for one file: trim sliders + loop preview,
video/audio choice, title, upload destination, and Discord channel
picker (see ripchamp_picker.py for the shared page/logic). Used for the
drag-and-drop / right-click flow (ripchamp_launcher.vbs -> Invoke-Prompt).

For clips detected by the folder watcher, see ripchamp_queue_server.py
instead -- that queues clips on a persistent, bookmarkable page so
opening a browser doesn't interrupt whatever you're doing the instant a
clip is recorded.

Opens a browser tab automatically. Once you hit Confirm (or Cancel), the
server shuts itself down and this process prints a single line to stdout:

    RESULT:{"canceled": false, "type": "video", "start": 12.3, "end": 45.6,
             "title": "clutch", "destination": "upload", "discordChannel": "clips"}

so a caller (ripchamp_tools.ps1) can parse it and build ripchamp.py's
args directly, with no further prompts.

Usage:
    python ripchamp_trim_ui.py input.mp4
"""

import argparse
import json
import socket
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ripchamp_picker import (
    build_picker_config, serve_video_range, serve_static_file, build_result,
    open_file_in_default_app, reveal_file_in_folder,
)
try:
    from ripchamp import load_discord_webhooks
except ImportError:
    load_discord_webhooks = None

STATIC_DIR = Path(__file__).resolve().parent / "static"


class TrimHandler(BaseHTTPRequestHandler):
    video_path: Path = None
    channel_names: list = []
    result_holder: dict = None
    shutdown_event: threading.Event = None

    def log_message(self, fmt, *args):
        pass  # keep stdout clean for the RESULT: line

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            serve_static_file(self, STATIC_DIR / "picker.html")
        elif self.path == "/config.json":
            config = build_picker_config(self.video_path.name, self.channel_names)
            body = json.dumps(config).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/static/picker.css":
            serve_static_file(self, STATIC_DIR / "picker.css")
        elif self.path == "/static/picker.js":
            serve_static_file(self, STATIC_DIR / "picker.js")
        elif self.path == "/video":
            serve_video_range(self, self.video_path)
        elif self.path == "/favicon.ico":
            serve_static_file(self, Path(__file__).resolve().parent / "favicon.ico")
        elif self.path == "/logo.png":
            serve_static_file(self, Path(__file__).resolve().parent / "logo.png")
        elif self.path == "/logo2.png":
            serve_static_file(self, Path(__file__).resolve().parent / "logo2.png")
        elif self.path == "/open-file":
            open_file_in_default_app(self.video_path)
            self.send_response(204)
            self.end_headers()
        elif self.path == "/open-folder":
            reveal_file_in_folder(self.video_path)
            self.send_response(204)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/confirm":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length))
            self.result_holder.update(data)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")
            self.shutdown_event.set()
        else:
            self.send_response(404)
            self.end_headers()


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description="Browser-based trim + upload-options picker for a video file.")
    parser.add_argument("input", type=Path, help="Path to the video file")
    parser.add_argument("--no-open", action="store_true", help="Don't auto-launch a browser (just print the URL).")
    args = parser.parse_args()

    if not args.input.is_file():
        print(f"Error: input file '{args.input}' not found.", file=sys.stderr)
        sys.exit(1)

    channel_names = list(load_discord_webhooks().keys()) if load_discord_webhooks else []

    result: dict = {}
    shutdown_event = threading.Event()

    TrimHandler.video_path = args.input.resolve()
    TrimHandler.channel_names = channel_names
    TrimHandler.result_holder = result
    TrimHandler.shutdown_event = shutdown_event

    port = find_free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), TrimHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    url = f"http://127.0.0.1:{port}/"
    print(f"Picker running at {url}", file=sys.stderr)
    if not args.no_open:
        webbrowser.open(url)

    shutdown_event.wait()
    server.shutdown()

    print("RESULT:" + json.dumps(build_result(result)))


if __name__ == "__main__":
    main()
