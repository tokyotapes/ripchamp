#!/usr/bin/env python3
# RIPChamp
# Copyright (C) 2026  NoOrg
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""
ripchamp_picker.py

Shared trim/options picker helpers, used by ripchamp_queue_server.py to
build and serve the picker page for each queued item.

The actual picker page markup/CSS/JS lives in static/html/picker.html,
static/css/picker.css, and static/js/picker.js as plain static files
(served via serve_static_file() below) -- this module only builds the per-item
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

from ripchamp import HDR_TRANSFERS, build_tonemap_filter, get_color_transfer

RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")
FASTSTART_CACHE_DIR = Path(tempfile.gettempdir()) / "ripchamp_faststart_cache"
PREVIEW_PROXY_CACHE_DIR = Path(tempfile.gettempdir()) / "ripchamp_preview_proxy_cache"

def build_picker_config(
    filename: str, channel_names: list, preview_path: Path = None, video_url: str = "/video",
    confirm_url: str = "/confirm", queue_url: str = None, open_file_url: str = "/open-file",
    open_folder_url: str = "/open-folder", youtube_available: bool = False,
    streamable_available: bool = False, clip_directory_name: str | None = None,
) -> dict:
    """Per-item config static/picker.js fetches at load (from
    "<page-path>/config.json", resolved client-side relative to wherever
    the picker page itself is served) to fill in dynamic values --
    static/html/picker.html is a plain static file with no server-side
    templating. queue_url: if set, the page redirects there after
    Confirm/Cancel instead of showing a static "you can close this tab"
    message -- used by ripchamp_queue_server.py to bounce back to the
    queue list. preview_path: the actual source file, used only to check
    whether the browser preview needs a lower-quality proxy (see
    needs_preview_proxy) -- the real crop/encode always runs against the
    original file regardless of this flag. youtube_available/
    streamable_available: whether the setup page has each host fully
    configured (YouTube needs both a client secret and an authorized
    token; Streamable needs saved credentials) -- picker.js uses these to
    hide the Upload option entirely (falling back to local-only) when
    neither is set up, and to only offer hosts that are actually usable
    when at least one is. clip_directory_name: basename of the configured
    "Clips Directory" setting, or None if unset -- lets the Local card's
    "Create Clip" button caption say where the file will actually land."""
    return {
        "filename": filename,
        "channels": channel_names,
        "videoUrl": video_url,
        "confirmUrl": confirm_url,
        "queueUrl": queue_url,
        "openFileUrl": open_file_url,
        "clipDirectoryName": clip_directory_name,
        "openFolderUrl": open_folder_url,
        "usingPreviewProxy": needs_preview_proxy(preview_path) if preview_path else False,
        "youtubeAvailable": youtube_available,
        "streamableAvailable": streamable_available,
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


def needs_preview_proxy(path: Path) -> bool:
    """True when the browser's <video> tag likely can't decode path
    directly -- 10-bit HEVC/H.265 HDR captures (common on HDR/ultrawide
    monitor recordings) aren't supported by Chrome's built-in decoder
    without OS-level HEVC codec support, which most Windows installs don't
    have. HDR detection mirrors ripchamp.py's own tonemap step."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,pix_fmt", "-of", "csv=s=x:p=0", str(path),
        ],
        capture_output=True, text=True,
    )
    codec_pix = result.stdout.strip().lower()
    is_hevc = codec_pix.startswith("hevc")
    is_10bit = "10le" in codec_pix or "10be" in codec_pix
    is_hdr = get_color_transfer(path) in HDR_TRANSFERS
    return is_hevc or is_10bit or is_hdr


def ensure_preview_proxy(path: Path) -> tuple[Path, bool]:
    """(path_to_stream, used_proxy). If path needs a preview proxy (see
    needs_preview_proxy), transcode a small cached H.264 SDR copy for the
    browser to play -- tone-mapped the same way as ripchamp.py's own HDR
    handling, downscaled since it's preview-only. The real crop/encode
    still runs against the original file, so final output quality/HDR is
    unaffected. Cached by content hash + mtime, same pattern as
    ensure_faststart_video's cache below."""
    if not needs_preview_proxy(path):
        return path, False

    PREVIEW_PROXY_CACHE_DIR.mkdir(exist_ok=True)
    stat = path.stat()
    key = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:16]
    cache_path = PREVIEW_PROXY_CACHE_DIR / f"{key}_{int(stat.st_mtime)}.mp4"
    if cache_path.is_file():
        return cache_path, True

    for stale in PREVIEW_PROXY_CACHE_DIR.glob(f"{key}_*.mp4"):
        stale.unlink(missing_ok=True)

    print(f"Transcoding a browser-preview proxy for {path.name} (10-bit HEVC/HDR source)...")
    vf = "scale=-2:720"
    if get_color_transfer(path) in HDR_TRANSFERS:
        vf += "," + build_tonemap_filter("hable", 75.0, 0.0)
    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-err_detect", "ignore_err", "-fflags", "+discardcorrupt+genpts",
            "-i", str(path), "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(cache_path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not cache_path.is_file():
        print(f"Preview proxy transcode failed, streaming original file as-is: {result.stderr.strip()[-500:]}")
        return path, False
    return cache_path, True


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
    proxy_path, used_proxy = ensure_preview_proxy(video_path)
    video_path = proxy_path if used_proxy else ensure_faststart_video(video_path)
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

    if output["type"] == "audio":
        file_name = (data.get("fileName") or "").strip()
        if file_name:
            output["fileName"] = file_name
    elif output["type"] == "video":
        title = (data.get("title") or "").strip()
        if title:
            output["title"] = title
        if data.get("aspect") == "original":
            output["aspect"] = "original"
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


def sanitize_filename(name: str) -> str:
    """Strip characters Windows won't allow in a filename, so a user-typed
    audio filename can't produce an invalid path."""
    return re.sub(r'[<>:"/\\|?*]', "", name).strip().strip(".")


def build_ripchamp_args(input_path: str, result: dict, clip_dir: str | None = None) -> list:
    """Build the ripchamp.py argv (excluding the python/script prefix) from
    a build_result() dict. clip_dir, if set, overrides where local
    (non-upload) output lands -- audio extractions always use it; cropped
    video only uses it for the "local" (no-upload) destination, since
    uploaded clips are deleted from their default location after upload
    anyway. For a local-destination video, result["title"] (labeled "File
    Name" on the picker page in this case -- see picker.js's isLocalOnly)
    doubles as the output filename, same as fileName does for audio."""
    args = [input_path]

    if result.get("type") == "audio":
        out_dir = Path(clip_dir) if clip_dir else Path(input_path).parent
        sanitized = sanitize_filename(result.get("fileName") or "")
        if sanitized:
            out_name = Path(sanitized).with_suffix(".mp3").name
            args.append(str(out_dir / out_name))
        elif clip_dir:
            out_name = Path(input_path).with_suffix(".mp3").name
            args.append(str(out_dir / out_name))
    elif result.get("destination") == "local":
        out_dir = Path(clip_dir) if clip_dir else Path(input_path).parent
        sanitized = sanitize_filename(result.get("title") or "")
        if sanitized:
            out_name = Path(sanitized).with_suffix(".mp4").name
            args.append(str(out_dir / out_name))
        elif clip_dir:
            out_name = f"{Path(input_path).stem}_1080p.mp4"
            args.append(str(out_dir / out_name))

    if "start" in result:
        args += ["--start", str(result["start"])]
    if "end" in result:
        args += ["--end", str(result["end"])]

    if result.get("type") == "audio":
        args += ["--audio-only", "--no-youtube", "--no-discord"]
        return args

    if result.get("title"):
        args += ["--youtube-title", result["title"]]

    if result.get("aspect") == "original":
        args += ["--keep-aspect-ratio"]

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
