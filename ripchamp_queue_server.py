#!/usr/bin/env python3
"""
ripchamp_queue_server.py

Persistent, bookmarkable queue for clips detected by the folder watcher
(or added manually via the page's "Browse for a file..." button -- this
is the only way clips get processed now, no separate drag-and-drop/prompt
flow). Instead of popping up a browser tab (stealing focus from your
game) the instant a clip finishes recording, the watcher just POSTs the
file path to this server's queue. Bookmark http://127.0.0.1:<PORT>/ and
process clips whenever you're ready: pick one from the list, scrub/trim/
set options, hit Confirm, and ripchamp.py runs in the background while
you move to the next one.

Runs on a fixed port (default 8787) so the bookmark stays valid across
restarts. Starting a second instance while one is already running on
that port is a no-op (it just exits) -- safe to call unconditionally
from Invoke-Watch each time the watcher starts.

The picker/queue page markup, CSS, and JS live as plain static files
under static/ (picker.html/css/js, queue.html/css/js) -- edit them
directly and refresh the browser, no restart needed, since they're
served fresh from disk on every request. ripchamp_picker.py's Python
logic is hot-reloaded automatically whenever the file changes on disk
(see _reload_if_changed()). Changes to this file's own routing/state
logic still require a restart.

Usage:
    python ripchamp_queue_server.py [--port 8787] [--open-setup]

    --open-setup opens a browser to /setup once the server is listening --
    only the installer passes this, for the first-run experience. Normal
    startups (the watcher's Ensure-QueueServer) never pass it, so logging
    in doesn't pop a browser tab every time.

Endpoints:
    GET  /                        queue page (bookmark this)
    GET  /setup                   first-run setup page (opened automatically with --open-setup)
    GET  /static/queue.css        queue page stylesheet
    GET  /static/queue.js         queue page script
    GET  /static/picker.css       picker page stylesheet
    GET  /static/picker.js        picker page script
    GET  /static/youtube-icon.gif   tiny YouTube icon
    GET  /static/streamable-icon.gif tiny Streamable icon
    GET  /static/discord-icon.gif   tiny Discord icon
    GET  /favicon.ico             browser tab icon
    GET  /logo.png                logo shown next to the page title
    GET  /status.json             pending/active/history as JSON (polled by the page)
    GET  /browse                  pop a native file-open dialog (multi-select), return the chosen paths
    GET  /set-clip-directory      pop a native folder-choose dialog, save it as the local (non-upload) output dir
    GET  /set-watch-directory     pop a native folder-choose dialog, save it as the watcher's watch folder (setup page)
    GET  /save-setup-settings?startAtStartup=yes|no&watchEnabled=yes|no&clipFolderEnabled=yes|no
                                   save the setup page's choices to ripchamp_config.json and
                                   install/remove the logon-start task (UAC prompt)
    GET  /discord-webhooks        saved Discord webhooks (name + date added), from the encrypted store
    GET  /save-discord-webhook?name=<channel>&url=<webhook url>
                                   validate and save a Discord webhook, encrypted at rest (setup page)
    GET  /delete-discord-webhook?name=<channel>  remove a saved Discord webhook
    GET  /youtube-status          whether a YouTube client secret/token are saved, and when
    GET  /browse-youtube-client-secret  pop a native file-open dialog, validate and save the chosen client_secret JSON, encrypted
    POST /authorize-youtube       run the OAuth flow (opens a browser) using the saved client secret, saves the resulting token
    GET  /reset-youtube-client-secret  remove the saved YouTube client secret
    GET  /reset-youtube-token     remove the saved YouTube OAuth token (re-authorize / switch channel)
    GET  /add?path=<abs path>     add a file to the queue (called by the watcher, or the Browse button)
    GET  /clear-pending           drop every pending (not-yet-started) item, recorded as canceled in history
    GET  /clear-history           wipe the finished-clips history list (doesn't touch files on disk)
    GET  /item/<id>               picker page for one queued item
    GET  /item/<id>/config.json   per-item config picker.js fetches at load (filename, video URL, etc.)
    GET  /item/<id>/video         range-streamed video for that item
    GET  /item/<id>/open-file     open the source file in its default app
    GET  /item/<id>/open-folder   reveal the source file in Explorer
    GET  /history-open-folder?finished=<ts>  reveal a finished local (non-upload) job's output file in Explorer
    POST /item/<id>/confirm       picker page's Confirm/Cancel -> processes or drops the item
    POST /item/<id>/cancel-processing  kill an in-progress job and delete anything it created
"""

import argparse
import importlib
import json
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ripchamp_picker
import ripchamp_secrets
try:
    from ripchamp import load_discord_webhooks, get_youtube_service
except ImportError:
    load_discord_webhooks = None
    get_youtube_service = None
try:
    import psutil
except ImportError:
    psutil = None

SCRIPT_DIR = Path(__file__).resolve().parent
STATIC_DIR = SCRIPT_DIR / "static"
DEFAULT_PORT = 8787
HISTORY_LIMIT = 20
CONFIG_PATH = SCRIPT_DIR / "ripchamp_config.json"

_module_mtimes = {}


def load_config() -> dict:
    if CONFIG_PATH.is_file():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_config(config: dict):
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def get_clip_directory() -> str | None:
    """Where local (non-upload) crops and mp3s are saved, if the user has
    set one via the "Set Clip Directory" button -- None means the default
    of saving next to the original file."""
    return load_config().get("clip_directory") or None


def set_clip_directory(path: str):
    config = load_config()
    config["clip_directory"] = path
    save_config(config)


def get_watch_directory() -> str | None:
    """The folder the watcher (ripchamp_tools.ps1 -Mode Watch) should watch
    for new clips, if set via the setup page's "Browse for Folder..."
    button -- None means the watcher's own hardcoded default. Read by
    Invoke-Watch at startup and polled periodically for live changes (see
    ripchamp_tools.ps1), so changing this here takes effect without
    restarting the watcher."""
    return load_config().get("watch_directory") or None


def set_watch_directory(path: str):
    config = load_config()
    config["watch_directory"] = path
    save_config(config)


def get_start_at_startup() -> bool:
    """The setup page's last-saved "Let RIPChamp Start Automatically?"
    choice -- reflects what was saved, not the live Task Scheduler state.
    Defaults to True, matching the radio's own default when unset."""
    value = load_config().get("start_at_startup")
    return True if value is None else bool(value)


def set_start_at_startup(enabled: bool):
    config = load_config()
    config["start_at_startup"] = enabled
    save_config(config)


def get_watch_enabled() -> bool:
    """The setup page's last-saved "Let us watch for new videos to clip?"
    choice. Defaults to True, matching the radio's own default when unset."""
    value = load_config().get("watch_enabled")
    return True if value is None else bool(value)


def set_watch_enabled(enabled: bool):
    config = load_config()
    config["watch_enabled"] = enabled
    save_config(config)


def get_clip_folder_enabled() -> bool:
    """The setup page's last-saved "Choose a folder for local clips?"
    choice. Defaults to False (unset clip_directory already means "save
    next to the original file" -- this just remembers whether the user
    deliberately opted into a custom folder)."""
    value = load_config().get("clip_folder_enabled")
    return False if value is None else bool(value)


def set_clip_folder_enabled(enabled: bool):
    config = load_config()
    config["clip_folder_enabled"] = enabled
    save_config(config)


def is_valid_discord_webhook_url(url: str) -> bool:
    return bool(re.match(
        r"^https://(discord\.com|discordapp\.com)/api/webhooks/\d+/[\w-]+/?$", url))


def is_valid_youtube_client_secret(text: str) -> bool:
    """Loose shape-check on a Google OAuth client secret JSON -- just
    enough to catch "wrong file" mistakes, not a full schema validation."""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return False
    inner = parsed.get("installed") or parsed.get("web")
    return bool(inner and "client_id" in inner and "client_secret" in inner)


def run_elevated_tools_mode(mode: str, timeout: float = 90) -> tuple:
    """Run `ripchamp_tools.ps1 -Mode <mode>` elevated, via a UAC prompt.

    `schtasks /create` (used by -Mode InstallTask) fails with Access
    Denied from this server's own normal, non-elevated process on this
    machine, confirmed the hard way -- so InstallTask/DisableTask (from
    the setup page's "Let RIPChamp Start Automatically?" toggle) need to
    run in an elevated child process instead. Uses `Start-Process -Verb
    RunAs -Wait -PassThru` from a throwaway (non-elevated) outer
    PowerShell so it can wait for the elevated child and relay its exit
    code -- passing arguments as a real PowerShell array avoids the
    nested-quoting problems schtasks' own /tr argument is prone to.
    Returns (success, error_detail)."""
    script_path = SCRIPT_DIR / "ripchamp_tools.ps1"
    wrapper = (
        "try { "
        f"$p = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
        f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','{script_path}','-Mode','{mode}'; "
        "exit $p.ExitCode "
        "} catch { exit 1 }"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", wrapper],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "Timed out waiting for the elevation (UAC) prompt to be approved."
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).strip()[-500:]
        return False, detail or f"Failed or the UAC prompt was declined (exit code {result.returncode})."
    return True, ""


def _reload_if_changed(module):
    """Hot-reload a module if its source file has changed on disk since we
    last loaded it, so Python-logic edits to ripchamp_picker.py take
    effect on next request without needing to kill and restart this
    long-running server process. (HTML/CSS/JS under static/ don't need
    this -- serve_static_file() already re-reads them from disk on every
    request.)"""
    path = Path(module.__file__)
    mtime = path.stat().st_mtime
    if _module_mtimes.get(module.__name__) != mtime:
        importlib.reload(module)
        _module_mtimes[module.__name__] = mtime
    return module


def get_watcher_status():
    """Detect whether Invoke-Watch (ripchamp_tools.ps1 -Mode Watch) is
    currently running, and which folder it's watching, by scanning process
    command lines -- mirrors the check Get-WatcherStatus does in
    ripchamp_tools.ps1 itself, just from the Python side."""
    empty = {"running": False, "watch_path": None, "watch_folder_name": None}
    if psutil is None:
        return empty
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline = proc.info["cmdline"] or []
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        if not any("ripchamp_tools.ps1" in part for part in cmdline):
            continue
        if "-Mode" not in cmdline:
            continue
        mode_idx = cmdline.index("-Mode")
        if mode_idx + 1 >= len(cmdline) or cmdline[mode_idx + 1] != "Watch":
            continue
        watch_path = None
        if "-WatchPath" in cmdline:
            wp_idx = cmdline.index("-WatchPath")
            if wp_idx + 1 < len(cmdline):
                watch_path = cmdline[wp_idx + 1]
        if not watch_path:
            # -WatchPath won't be on the command line when the watcher was
            # started directly (e.g. start_ripchamp.bat's fallback when no
            # scheduled task exists yet) rather than via the scheduled task,
            # which bakes -WatchPath in at InstallTask time. Fall back to
            # the configured value -- Invoke-Watch reads the same config at
            # startup, so this reflects what it's actually watching.
            watch_path = get_watch_directory()
        # Compute the folder's basename here, in Python, rather than in the
        # embedded JS -- QUEUE_PAGE is a plain (non-raw) string, so a "\\"
        # meant for a JS regex silently collapses to a single "\" before the
        # JS ever sees it, breaking any client-side Windows-path splitting.
        folder_name = Path(watch_path).name if watch_path else None
        return {"running": True, "watch_path": watch_path, "watch_folder_name": folder_name}
    return empty


def open_file_dialog(title="Choose a video to process", filetypes=None, multiple=False):
    """Pop a native "Open File" dialog on the machine running this server
    (it's always the user's own desktop, never remote) and return the chosen
    path (or list of paths if multiple=True), or None (or [] if multiple)
    if canceled. Runs on the request-handling thread, which is fine since
    ThreadingHTTPServer gives each request its own thread."""
    import tkinter as tk
    from tkinter import filedialog

    if filetypes is None:
        filetypes = [("Video files", "*.mp4 *.mov *.mkv *.avi *.webm"), ("All files", "*.*")]

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if multiple:
            return list(filedialog.askopenfilenames(title=title, filetypes=filetypes))
        path = filedialog.askopenfilename(title=title, filetypes=filetypes)
    finally:
        root.destroy()
    return path or None


def open_directory_dialog(title: str) -> str | None:
    """Pop a native "choose folder" dialog -- used for both the Clips
    Directory setting (local, non-upload output) and the setup page's
    watch-folder picker."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        path = filedialog.askdirectory(title=title)
    finally:
        root.destroy()
    return path or None


class QueueState:
    def __init__(self):
        self.lock = threading.Lock()
        self.next_id = 1
        self.pending: dict[int, dict] = {}   # id -> {"path": Path, "added": float}
        self.active: dict[int, dict] = {}    # id -> {"path": Path, "started": float}
        self.history: list[dict] = []        # most-recent-first

    def add(self, path: Path):
        with self.lock:
            for item in self.pending.values():
                if item["path"] == path:
                    return
            for item in self.active.values():
                if item["path"] == path:
                    return
            item_id = self.next_id
            self.next_id += 1
            self.pending[item_id] = {"path": path, "added": time.time()}

    def get(self, item_id: int):
        with self.lock:
            return self.pending.get(item_id)

    def start_processing(self, item_id: int):
        with self.lock:
            item = self.pending.pop(item_id, None)
            if item:
                item["stage"] = "Starting"
                item["proc"] = None
                item["cancel_requested"] = False
                self.active[item_id] = item
            return item

    def set_stage(self, item_id: int, stage: str):
        with self.lock:
            if item_id in self.active:
                self.active[item_id]["stage"] = stage

    def set_proc(self, item_id: int, proc):
        with self.lock:
            if item_id in self.active:
                self.active[item_id]["proc"] = proc

    def request_cancel(self, item_id: int):
        """Mark an active item as cancel-requested and return its subprocess
        (if one has started yet) so the caller can kill it outside the lock."""
        with self.lock:
            item = self.active.get(item_id)
            if not item:
                return None
            item["cancel_requested"] = True
            return item.get("proc")

    def was_canceled(self, item_id: int) -> bool:
        with self.lock:
            item = self.active.get(item_id)
            return bool(item and item.get("cancel_requested"))

    def finish_processing(self, item_id: int, filename: str, status: str, detail: str = "",
                           destination: str | None = None, output_path: str | None = None,
                           title: str | None = None, upload_url: str | None = None):
        with self.lock:
            self.active.pop(item_id, None)
            self.history.insert(0, {
                "filename": filename, "status": status, "detail": detail, "finished": time.time(),
                "destination": destination, "output_path": output_path,
                "title": title, "upload_url": upload_url,
            })
            self.history = self.history[:HISTORY_LIMIT]

    def drop(self, item_id: int, filename: str, status: str):
        with self.lock:
            self.pending.pop(item_id, None)
            self.history.insert(0, {
                "filename": filename, "status": status, "detail": "", "finished": time.time(),
                "destination": None, "output_path": None, "title": None, "upload_url": None,
            })
            self.history = self.history[:HISTORY_LIMIT]

    def clear_pending(self) -> int:
        """Drop every pending (not yet started) item at once -- each one
        recorded in history as "canceled", same as canceling them
        individually from the picker page. Doesn't touch active/in-progress
        jobs. Returns how many were cleared."""
        with self.lock:
            cleared = list(self.pending.items())
            self.pending.clear()
            for _, item in cleared:
                self.history.insert(0, {
                    "filename": item["path"].name, "status": "canceled", "detail": "", "finished": time.time(),
                    "destination": None, "output_path": None, "title": None, "upload_url": None,
                })
            self.history = self.history[:HISTORY_LIMIT]
            return len(cleared)

    def clear_history(self) -> int:
        """Wipe the finished-clips history list. Doesn't touch pending/active
        jobs or any files on disk -- just the queue page's own record of what
        already finished. Returns how many entries were cleared."""
        with self.lock:
            count = len(self.history)
            self.history = []
            return count

    def snapshot(self):
        with self.lock:
            pending = sorted(
                ({"id": i, "name": it["path"].name} for i, it in self.pending.items()),
                key=lambda x: x["id"],
            )
            active = [{"id": i, "name": it["path"].name, "stage": it.get("stage", "")} for i, it in self.active.items()]
            history = list(self.history)
        return pending, active, history

    def find_history_by_finished(self, finished: float):
        with self.lock:
            return next((dict(h) for h in self.history if h.get("finished") == finished), None)


STATE = QueueState()

# Ordered (pattern, label) pairs matched against ripchamp.py's stdout, in
# priority order, to translate its progress prints into a friendly stage
# name for the queue page. First match wins.
STAGE_PATTERNS = [
    (re.compile(r"^Extracting audio"), "Extracting audio"),
    (re.compile(r"^(Trimming:|Source resolution:|Color transfer:|Tonemapping|Applying .* tonemap|"
                r"Detected possible GPU encoders|Writing:|Trying encoder:|Using encoder:|.* failed \(driver/hardware)"),
     "Rendering clip"),
    (re.compile(r"to YouTube|^  Upload progress:"), "Uploading to YouTube"),
    (re.compile(r"^Waiting .*before posting"), "Waiting before posting link"),
    (re.compile(r"^Compressing a copy for Discord"), "Compressing for Discord"),
    (re.compile(r"^Uploading .* to Discord"), "Uploading to Discord"),
    (re.compile(r"^Deleted local copy"), "Cleaning up"),
]


def _detect_stage(line: str):
    for pattern, label in STAGE_PATTERNS:
        if pattern.search(line):
            return label
    return None


def expected_output_paths(path: Path, result: dict) -> list:
    """Paths ripchamp.py may have created for this job, mirroring its own
    naming (see output_path/compressed_path in ripchamp.py) -- used to clean
    up after a canceled job, including a partial file ffmpeg was still
    writing to when killed."""
    clip_dir = get_clip_directory()
    if result.get("type") == "audio":
        base_dir = Path(clip_dir) if clip_dir else path.parent
        # Mirror build_ripchamp_args' naming exactly -- a custom fileName
        # (see ripchamp_picker.build_result) changes where the finished
        # file actually lands, so this has to track that or "open in
        # Explorer" points at a file that was never created.
        sanitized = ripchamp_picker.sanitize_filename(result.get("fileName") or "")
        out_name = Path(sanitized).with_suffix(".mp3").name if sanitized else path.with_suffix(".mp3").name
        return [base_dir / out_name]
    # A custom clip directory only applies to the "local" (no-upload) branch --
    # uploaded clips still land next to the source until deleted post-upload.
    base_dir = Path(clip_dir) if (clip_dir and result.get("destination") == "local") else path.parent
    cropped = base_dir / f"{path.stem}_1080p.mp4"
    discord_copy = cropped.with_name(f"{cropped.stem}_discord.mp4")
    return [cropped, discord_copy]


def resolve_local_output_path(path: Path, result: dict) -> Path | None:
    """Where a "local" (non-upload) job's finished file ends up -- used to
    offer an "open in Explorer" action for it on the queue page's history
    list. None for uploaded clips, since those get deleted after upload."""
    if result.get("type") == "audio" or result.get("destination") == "local":
        return expected_output_paths(path, result)[0]
    return None


def _delete_with_retry(file_path: Path, attempts: int = 5, delay: float = 0.3):
    """A file ffmpeg just had open can stay briefly locked on Windows right
    after the process is killed -- retry a few times before giving up."""
    for _ in range(attempts):
        try:
            if file_path.exists():
                file_path.unlink()
            return
        except OSError:
            time.sleep(delay)


def run_and_record(item_id: int, path: Path, result: dict):
    try:
        args = _reload_if_changed(ripchamp_picker).build_ripchamp_args(str(path), result, get_clip_directory())
        proc = subprocess.Popen(
            [sys.executable, "-u", str(SCRIPT_DIR / "ripchamp.py"), *args],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        STATE.set_proc(item_id, proc)

        tail_lines = []
        upload_url = None
        for line in proc.stdout:
            line = line.rstrip("\n")
            tail_lines.append(line)
            del tail_lines[:-40]
            stage = _detect_stage(line)
            if stage:
                STATE.set_stage(item_id, stage)
            # ripchamp.py prints this exact line right after a successful
            # YouTube/Streamable upload (upload_to_youtube/upload_to_streamable) --
            # captured here so the queue page's history list can link straight
            # to it instead of just showing the local filename (which gets
            # deleted after a successful upload anyway).
            m = re.match(r"Uploaded to (?:YouTube|Streamable): (https://\S+)", line)
            if m:
                upload_url = m.group(1)
        proc.wait()

        destination = "local" if (result.get("type") == "audio" or result.get("destination") == "local") else "upload"

        if STATE.was_canceled(item_id):
            for out_path in expected_output_paths(path, result):
                _delete_with_retry(out_path)
            STATE.finish_processing(item_id, path.name, "canceled", destination=destination)
        elif proc.returncode == 0:
            output_path = resolve_local_output_path(path, result)
            # Mirrors ripchamp.py's own default: `args.youtube_title or output_path.stem`,
            # where output_path there is the cropped "<stem>_1080p.mp4" file.
            title = (result.get("title") or f"{path.stem}_1080p") if destination == "upload" else None
            STATE.finish_processing(
                item_id, path.name, "done", destination=destination,
                output_path=str(output_path) if output_path else None,
                title=title, upload_url=upload_url,
            )
        else:
            STATE.finish_processing(item_id, path.name, "error", "\n".join(tail_lines), destination=destination)
    except Exception as e:
        # Always clear the item from "active" even on an unexpected failure
        # (e.g. the source file got deleted mid-run) -- otherwise it's stuck
        # showing "processing" forever with no way to clear it from the UI.
        STATE.finish_processing(item_id, path.name, "error", str(e))


class QueueHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep stdout clean

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _not_found(self):
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]

        if parsed.path in ("/", "/index.html"):
            _reload_if_changed(ripchamp_picker).serve_static_file(self, STATIC_DIR / "queue.html")
            return

        if parsed.path == "/setup":
            _reload_if_changed(ripchamp_picker).serve_static_file(self, STATIC_DIR / "setup.html")
            return

        if parsed.path == "/static/queue.css":
            _reload_if_changed(ripchamp_picker).serve_static_file(self, STATIC_DIR / "queue.css")
            return

        if parsed.path == "/static/queue.js":
            _reload_if_changed(ripchamp_picker).serve_static_file(self, STATIC_DIR / "queue.js")
            return

        if parsed.path == "/static/picker.css":
            _reload_if_changed(ripchamp_picker).serve_static_file(self, STATIC_DIR / "picker.css")
            return

        if parsed.path == "/static/picker.js":
            _reload_if_changed(ripchamp_picker).serve_static_file(self, STATIC_DIR / "picker.js")
            return

        if parsed.path == "/static/youtube-icon.gif":
            _reload_if_changed(ripchamp_picker).serve_static_file(self, STATIC_DIR / "youtube-icon.gif")
            return

        if parsed.path == "/static/streamable-icon.gif":
            _reload_if_changed(ripchamp_picker).serve_static_file(self, STATIC_DIR / "streamable-icon.gif")
            return

        if parsed.path == "/static/discord-icon.gif":
            _reload_if_changed(ripchamp_picker).serve_static_file(self, STATIC_DIR / "discord-icon.gif")
            return

        if parsed.path == "/favicon.ico":
            _reload_if_changed(ripchamp_picker).serve_static_file(self, SCRIPT_DIR / "favicon.ico")
            return

        if parsed.path == "/logo.png":
            _reload_if_changed(ripchamp_picker).serve_static_file(self, SCRIPT_DIR / "logo.png")
            return

        if parsed.path == "/logo2.png":
            _reload_if_changed(ripchamp_picker).serve_static_file(self, SCRIPT_DIR / "logo2.png")
            return

        if parsed.path == "/status.json":
            pending, active, history = STATE.snapshot()
            clip_dir = get_clip_directory()
            watch_dir = get_watch_directory()
            # Compute the folder's basename here in Python, not via a client-side
            # split -- QUEUE_PAGE is a plain (non-raw) string, so a "\\" meant for
            # a JS regex silently collapses to "\" before the JS ever sees it,
            # breaking Windows path splitting (bit us once already for the watcher).
            clip_dir_name = Path(clip_dir).name if clip_dir else None
            watch_dir_name = Path(watch_dir).name if watch_dir else None
            # Compute the finished output's basename here too, same reason --
            # lets the queue page show "original -> renamed" when the output
            # filename differs (custom audio filename, "_1080p" suffix, etc.)
            # without splitting a Windows path in JS.
            history = [
                {**h, "output_filename": Path(h["output_path"]).name if h.get("output_path") else None}
                for h in history
            ]
            self._send_json({
                "pending": pending, "active": active, "history": history,
                "watcher": get_watcher_status(),
                "clip_directory": clip_dir, "clip_directory_name": clip_dir_name,
                "watch_directory": watch_dir, "watch_directory_name": watch_dir_name,
                "start_at_startup": get_start_at_startup(), "watch_enabled": get_watch_enabled(),
                "clip_folder_enabled": get_clip_folder_enabled(),
            })
            return

        if parsed.path == "/browse":
            self._send_json({"paths": open_file_dialog(
                title="Choose one or more videos to process", multiple=True)})
            return

        if parsed.path == "/set-clip-directory":
            chosen = open_directory_dialog("Choose a folder for local (non-upload) clips and mp3s")
            if chosen:
                set_clip_directory(chosen)
            self._send_json({"path": get_clip_directory()})
            return

        if parsed.path == "/set-watch-directory":
            chosen = open_directory_dialog("Choose a folder to watch for new clips")
            if chosen:
                set_watch_directory(chosen)
            self._send_json({"path": get_watch_directory()})
            return

        if parsed.path == "/save-setup-settings":
            qs = urllib.parse.parse_qs(parsed.query)
            start_at_startup = qs.get("startAtStartup", [None])[0]
            watch_enabled = qs.get("watchEnabled", [None])[0]
            clip_folder_enabled = qs.get("clipFolderEnabled", [None])[0]
            if (start_at_startup not in ("yes", "no") or watch_enabled not in ("yes", "no")
                    or clip_folder_enabled not in ("yes", "no")):
                self.send_response(400)
                self.end_headers()
                return

            set_start_at_startup(start_at_startup == "yes")
            set_watch_enabled(watch_enabled == "yes")
            set_clip_folder_enabled(clip_folder_enabled == "yes")

            mode = "InstallTask" if start_at_startup == "yes" else "DisableTask"
            ok, error = run_elevated_tools_mode(mode)
            self._send_json({"ok": ok, "error": error})
            return

        if parsed.path == "/discord-webhooks":
            webhooks = ripchamp_secrets.get_discord_webhooks_with_added()
            self._send_json({
                "webhooks": [
                    {"name": name, "added": entry["added"]}
                    for name, entry in webhooks.items()
                ]
            })
            return

        if parsed.path == "/save-discord-webhook":
            qs = urllib.parse.parse_qs(parsed.query)
            name = (qs.get("name", [None])[0] or "").strip()
            url = (qs.get("url", [None])[0] or "").strip()
            if not name or not url:
                self._send_json({"ok": False, "error": "Channel name and webhook URL are both required."})
                return
            if not is_valid_discord_webhook_url(url):
                self._send_json({"ok": False, "error": "That doesn't look like a Discord webhook URL."})
                return
            ripchamp_secrets.set_discord_webhook(name, url)
            self._send_json({"ok": True})
            return

        if parsed.path == "/delete-discord-webhook":
            qs = urllib.parse.parse_qs(parsed.query)
            name = (qs.get("name", [None])[0] or "").strip()
            if name:
                ripchamp_secrets.delete_discord_webhook(name)
            self._send_json({"ok": True})
            return

        if parsed.path == "/youtube-status":
            status = ripchamp_secrets.get_youtube_status()
            self._send_json({
                "clientSecretAdded": status["client_secret_added"],
                "tokenAdded": status["token_added"],
            })
            return

        if parsed.path == "/browse-youtube-client-secret":
            chosen = open_file_dialog(
                title="Choose your downloaded client_secret JSON file",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            )
            if not chosen:
                self._send_json({"ok": False, "error": None})
                return
            try:
                text = Path(chosen).read_text(encoding="utf-8")
            except OSError as e:
                self._send_json({"ok": False, "error": f"Couldn't read that file: {e}"})
                return
            if not is_valid_youtube_client_secret(text):
                self._send_json({"ok": False, "error": "That doesn't look like a Google OAuth client secret file."})
                return
            ripchamp_secrets.set_youtube_client_secret(text)
            self._send_json({"ok": True})
            return

        if parsed.path == "/reset-youtube-client-secret":
            ripchamp_secrets.delete_youtube_client_secret()
            self._send_json({"ok": True})
            return

        if parsed.path == "/reset-youtube-token":
            ripchamp_secrets.delete_youtube_token()
            self._send_json({"ok": True})
            return

        if parsed.path == "/history-open-folder":
            qs = urllib.parse.parse_qs(parsed.query)
            finished_str = qs.get("finished", [None])[0]
            entry = None
            if finished_str:
                try:
                    entry = STATE.find_history_by_finished(float(finished_str))
                except ValueError:
                    entry = None
            if entry and entry.get("output_path"):
                _reload_if_changed(ripchamp_picker).reveal_file_in_folder(Path(entry["output_path"]))
            self.send_response(204)
            self.end_headers()
            return

        if parsed.path == "/add":
            qs = urllib.parse.parse_qs(parsed.query)
            path_str = qs.get("path", [None])[0]
            if not path_str:
                self.send_response(400)
                self.end_headers()
                return
            STATE.add(Path(path_str))
            self._send_json({"ok": True})
            return

        if parsed.path == "/clear-pending":
            cleared = STATE.clear_pending()
            self._send_json({"ok": True, "cleared": cleared})
            return

        if parsed.path == "/clear-history":
            cleared = STATE.clear_history()
            self._send_json({"ok": True, "cleared": cleared})
            return

        if len(parts) == 2 and parts[0] == "item":
            item_id = int(parts[1])
            item = STATE.get(item_id)
            if not item:
                self._not_found()
                return
            _reload_if_changed(ripchamp_picker).serve_static_file(self, STATIC_DIR / "picker.html")
            return

        if len(parts) == 3 and parts[0] == "item" and parts[2] == "config.json":
            item_id = int(parts[1])
            item = STATE.get(item_id)
            if not item:
                self._not_found()
                return
            channel_names = list(load_discord_webhooks().keys()) if load_discord_webhooks else []
            config = _reload_if_changed(ripchamp_picker).build_picker_config(
                item["path"].name, channel_names,
                video_url=f"/item/{item_id}/video", confirm_url=f"/item/{item_id}/confirm",
                queue_url="/", open_file_url=f"/item/{item_id}/open-file",
                open_folder_url=f"/item/{item_id}/open-folder",
            )
            self._send_json(config)
            return

        if len(parts) == 3 and parts[0] == "item" and parts[2] == "video":
            item_id = int(parts[1])
            item = STATE.get(item_id)
            if not item:
                self._not_found()
                return
            _reload_if_changed(ripchamp_picker).serve_video_range(self, item["path"])
            return

        if len(parts) == 3 and parts[0] == "item" and parts[2] == "open-file":
            item_id = int(parts[1])
            item = STATE.get(item_id)
            if not item:
                self._not_found()
                return
            _reload_if_changed(ripchamp_picker).open_file_in_default_app(item["path"])
            self.send_response(204)
            self.end_headers()
            return

        if len(parts) == 3 and parts[0] == "item" and parts[2] == "open-folder":
            item_id = int(parts[1])
            item = STATE.get(item_id)
            if not item:
                self._not_found()
                return
            _reload_if_changed(ripchamp_picker).reveal_file_in_folder(item["path"])
            self.send_response(204)
            self.end_headers()
            return

        self._not_found()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]

        if len(parts) == 3 and parts[0] == "item" and parts[2] == "confirm":
            item_id = int(parts[1])
            item = STATE.get(item_id)
            if not item:
                self._not_found()
                return
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length))
            result = _reload_if_changed(ripchamp_picker).build_result(data)
            self._send_json({"ok": True})

            if result.get("canceled"):
                STATE.drop(item_id, item["path"].name, "canceled")
            else:
                STATE.start_processing(item_id)
                threading.Thread(target=run_and_record, args=(item_id, item["path"], result), daemon=True).start()
            return

        if parsed.path == "/authorize-youtube":
            if get_youtube_service is None:
                self._send_json({"ok": False, "error": "YouTube packages aren't installed on this machine."})
                return
            try:
                svc = get_youtube_service()
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)})
                return
            if not svc:
                self._send_json({"ok": False, "error": "Save a client secret first."})
                return
            channel_name = None
            try:
                resp = svc.channels().list(part="snippet", mine=True).execute()
                items = resp.get("items", [])
                if items:
                    channel_name = items[0]["snippet"]["title"]
            except Exception:
                pass
            self._send_json({"ok": True, "channel": channel_name})
            return

        if len(parts) == 3 and parts[0] == "item" and parts[2] == "cancel-processing":
            item_id = int(parts[1])
            proc = STATE.request_cancel(item_id)
            if proc is not None and proc.poll() is None:
                try:
                    # proc.terminate() only kills ripchamp.py itself on Windows --
                    # its ffmpeg child would keep running as an orphan. "/T" kills
                    # the whole process tree.
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
                except Exception:
                    pass
            self._send_json({"ok": True})
            return

        self._not_found()


class SingleInstanceServer(ThreadingHTTPServer):
    # HTTPServer defaults allow_reuse_address=True, which on Windows lets a
    # second process silently bind the same port instead of raising --
    # defeating the "already running, exit quietly" check below.
    allow_reuse_address = False


def main():
    parser = argparse.ArgumentParser(description="Persistent bookmarkable queue for ripchamp clips.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open-setup", action="store_true",
        help="Open a browser to /setup once listening -- used by the installer for the first-run experience.")
    args = parser.parse_args()

    try:
        server = SingleInstanceServer(("127.0.0.1", args.port), QueueHandler)
    except OSError:
        print(f"Queue server already running (or port {args.port} is in use) -- not starting another.", file=sys.stderr)
        sys.exit(0)

    print(f"Queue server running at http://127.0.0.1:{args.port}/", file=sys.stderr)
    if args.open_setup:
        webbrowser.open(f"http://127.0.0.1:{args.port}/setup")
    server.serve_forever()


if __name__ == "__main__":
    main()
