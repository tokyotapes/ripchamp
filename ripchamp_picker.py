#!/usr/bin/env python3
"""
ripchamp_picker.py

Shared trim/options picker helpers, used by ripchamp_queue_server.py to
build and serve the picker page for each queued item.

The actual picker page markup/CSS/JS lives in static/picker.html,
static/picker.css, and static/picker.js as plain static files (served
via serve_static_file() below) -- this module only builds the per-item
config JSON that picker.js fetches at load (see build_picker_config()),
plus the video-streaming/Explorer-integration/arg-building helpers.

Not meant to be run directly.
"""

import hashlib
import mimetypes
import os
import re
import struct
import subprocess
import tempfile
from pathlib import Path

RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")
FASTSTART_CACHE_DIR = Path(tempfile.gettempdir()) / "ripchamp_faststart_cache"

def build_picker_config(
    filename: str, channel_names: list, video_url: str = "/video", confirm_url: str = "/confirm",
    queue_url: str = None, open_file_url: str = "/open-file", open_folder_url: str = "/open-folder",
) -> dict:
    """Per-item config static/picker.js fetches at load (from
    "<page-path>/config.json", resolved client-side relative to wherever
    the picker page itself is served) to fill in dynamic values --
    static/picker.html is a plain static file with no server-side
    templating. queue_url: if set, the page redirects there after
    Confirm/Cancel instead of showing a static "you can close this tab"
    message -- used by ripchamp_queue_server.py to bounce back to the
    queue list."""
    return {
        "filename": filename,
        "channels": channel_names,
        "videoUrl": video_url,
        "confirmUrl": confirm_url,
        "queueUrl": queue_url,
        "openFileUrl": open_file_url,
        "openFolderUrl": open_folder_url,
    }


def _moov_is_first(path: Path) -> bool:
    """Scan top-level MP4 boxes and return True if 'moov' comes before
    'mdat' -- i.e. the file is already faststart / progressively
    streamable. Returns True (don't touch the file) if the layout can't be
    read, so a parsing hiccup never blocks playback outright."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            pos = 0
            while pos < size:
                f.seek(pos)
                header = f.read(8)
                if len(header) < 8:
                    break
                box_size, box_type = struct.unpack(">I4s", header)
                if box_type == b"moov":
                    return True
                if box_type == b"mdat":
                    return False
                if box_size == 1:
                    big = f.read(8)
                    if len(big) < 8:
                        break
                    box_size = struct.unpack(">Q", big)[0]
                if box_size < 8:
                    break
                pos += box_size
    except OSError:
        pass
    return True


def ensure_faststart_video(path: Path) -> Path:
    """If path's moov atom is at the end of the file (not "faststart"),
    remux -- not re-encode -- a cached copy with it moved to the front.

    Some older clips (e.g. from capture software that doesn't finalize
    with faststart) have their moov box, which holds every track's sample
    table including audio, written after the video data instead of before
    it. Chrome's HTML5 <video> engine streams progressively from the front
    of the file and doesn't reliably fetch the moov box from the tail
    before it starts decoding, so it plays the video track but silently
    drops the audio track -- even though the audio is intact (confirmed
    via ffprobe and playback in VLC, which reads the whole file first).
    Remuxing to move moov to the front fixes this with no re-encode."""
    if path.suffix.lower() != ".mp4":
        return path
    if _moov_is_first(path):
        return path

    FASTSTART_CACHE_DIR.mkdir(exist_ok=True)
    stat = path.stat()
    key = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:16]
    cache_path = FASTSTART_CACHE_DIR / f"{key}_{int(stat.st_mtime)}.mp4"
    if cache_path.is_file():
        return cache_path

    for stale in FASTSTART_CACHE_DIR.glob(f"{key}_*.mp4"):
        stale.unlink(missing_ok=True)

    print(f"Remuxing {path.name} for faststart preview playback (moov atom was at the end of the file)...")
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(path), "-c", "copy", "-movflags", "+faststart", str(cache_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not cache_path.is_file():
        print(f"Faststart remux failed, streaming original file as-is: {result.stderr.strip()[-500:]}")
        return path
    return cache_path


def serve_video_range(handler, video_path: Path):
    """Stream video_path to an http.server request handler, honoring Range
    requests so <video> seeking works."""
    video_path = ensure_faststart_video(video_path)
    file_size = video_path.stat().st_size
    mime = mimetypes.guess_type(str(video_path))[0] or "video/mp4"
    range_header = handler.headers.get("Range")

    start, end = 0, file_size - 1
    status = 200
    if range_header:
        match = RANGE_RE.match(range_header)
        if match:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
            status = 206

    length = end - start + 1
    handler.send_response(status)
    handler.send_header("Content-Type", mime)
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(length))
    if status == 206:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
    handler.end_headers()

    with open(video_path, "rb") as f:
        f.seek(start)
        remaining = length
        chunk_size = 1024 * 1024
        while remaining > 0:
            chunk = f.read(min(chunk_size, remaining))
            if not chunk:
                break
            try:
                handler.wfile.write(chunk)
            except (BrokenPipeError, ConnectionAbortedError):
                return
            remaining -= len(chunk)


def open_file_in_default_app(file_path: Path):
    """"Open Local File" button -- launch it with whatever Windows has
    associated with its extension."""
    os.startfile(str(file_path))  # noqa: S606 -- Windows-only, launches the user's own file


def reveal_file_in_folder(file_path: Path):
    """"Browse Local Folder" button -- open the containing folder in
    Explorer with the file pre-selected. explorer.exe often returns a
    non-zero exit code even on success, so its return code is ignored.

    Must go through shell=True with an exactly-quoted `/select,"path"`
    string -- explorer.exe does its own non-standard command-line parsing,
    and subprocess's list-based argv quoting (correct for normal programs)
    mangles it, silently falling back to opening the Documents folder
    instead of the target whenever the path contains unusual whitespace
    (e.g. a folder name with a double space). Safe from shell injection
    here since Windows forbids '"' in file/folder names, so it can't be
    used to break out of the quotes."""
    subprocess.run(f'explorer /select,"{file_path}"', shell=True, capture_output=True)


def serve_static_file(handler, file_path: Path):
    """Serve a small static file's full bytes with an appropriate
    Content-Type header -- used for the favicon and (on the queue page)
    the logo. No Range support needed for these (unlike video)."""
    if not file_path.is_file():
        handler.send_response(404)
        handler.end_headers()
        return
    mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    data = file_path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def build_result(data: dict) -> dict:
    """Turn the JSON posted by the picker page's Confirm/Cancel button into
    the compact result dict (fields omitted when they don't apply)."""
    if data.get("canceled"):
        return {"canceled": True}

    start = data.get("start", 0.0)
    end = data.get("end")
    duration = data.get("duration", 0.0)

    output = {"canceled": False, "type": data.get("type", "video")}
    if start and start > 0.05:
        output["start"] = round(start, 2)
    if end is not None and end < duration - 0.05:
        output["end"] = round(end, 2)

    if output["type"] == "video":
        title = (data.get("title") or "").strip()
        if title:
            output["title"] = title
        output["destination"] = data.get("destination", "upload")
        if output["destination"] == "upload":
            output["videoHost"] = data.get("videoHost", "youtube")
            if data.get("postToDiscord") is False:
                output["postToDiscord"] = False
            else:
                discord_channel = data.get("discordChannel")
                if discord_channel:
                    output["discordChannel"] = discord_channel

    return output


def build_ripchamp_args(input_path: str, result: dict, clip_dir: str | None = None) -> list:
    """Build the ripchamp.py argv (excluding the python/script prefix) from
    a build_result() dict. clip_dir, if set, overrides where local
    (non-upload) output lands -- audio extractions always use it; cropped
    video only uses it for the "local" (no-upload) destination, since
    uploaded clips are deleted from their default location after upload
    anyway."""
    args = [input_path]

    if result.get("type") == "audio":
        if clip_dir:
            out_name = Path(input_path).with_suffix(".mp3").name
            args.append(str(Path(clip_dir) / out_name))
    elif result.get("destination") == "local" and clip_dir:
        out_name = f"{Path(input_path).stem}_1080p.mp4"
        args.append(str(Path(clip_dir) / out_name))

    if "start" in result:
        args += ["--start", str(result["start"])]
    if "end" in result:
        args += ["--end", str(result["end"])]

    if result.get("type") == "audio":
        args += ["--audio-only", "--no-youtube", "--no-discord"]
        return args

    if result.get("title"):
        args += ["--youtube-title", result["title"]]

    if result.get("destination") == "local":
        args += ["--no-youtube", "--no-discord"]
    else:
        args += ["--delete-after-upload"]
        if result.get("videoHost") == "streamable":
            args += ["--video-host", "streamable"]
        if result.get("postToDiscord") is False:
            args += ["--no-discord"]
        elif result.get("discordChannel"):
            args += ["--discord-channel", result["discordChannel"]]

    return args
