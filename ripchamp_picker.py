#!/usr/bin/env python3
"""
ripchamp_picker.py

Shared trim/options picker page + helpers, used by both:
  - ripchamp_trim_ui.py    (single-shot picker for one file, opened directly
                             by ripchamp_launcher.vbs for drag-and-drop /
                             right-click use)
  - ripchamp_queue_server.py (persistent bookmarkable queue -- same picker
                             page, one per queued item)

Not meant to be run directly.
"""

import hashlib
import json
import mimetypes
import os
import re
import struct
import subprocess
import tempfile
from pathlib import Path

RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")
FASTSTART_CACHE_DIR = Path(tempfile.gettempdir()) / "ripchamp_faststart_cache"

PAGE_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>RIPChamp</title>
<link rel="icon" href="/favicon.ico">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Workbench&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Poppins:ital,wght@0,100;0,200;0,300;0,400;0,500;0,600;0,700;0,800;0,900;1,100;1,200;1,300;1,400;1,500;1,600;1,700;1,800;1,900&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:ital,wght@0,100..800;1,100..800&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://www.nerdfonts.com/assets/css/webfont.css">
<style>
  :root {
    --bg: #0b0c0f;
    --bg-elev: #15171c;
    --border: #262931;
    --text: #e7e9ee;
    --text-dim: #8b909c;
    --accent: #8e54ee;
    --accent-hover: #729bff;
    --danger: #e5484d;
    --radius: 14px;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; background: var(--bg); color: var(--text); }
  body {
    display: flex; justify-content: center;
    font-family: "Poppins", sans-serif;
    padding: 40px 24px;
  }
  .page { width: 100%; max-width: 1300px; }
  .brand { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 20px; }
  .brand-left { display: flex; align-items: center; gap: 14px; }
  .file-info { display: flex; flex-direction: column; align-items: flex-end; text-align: right; }
  .brand .logo { height: 40px; width: auto; }
  .brand-name {
    font-family: "JetBrains Mono", monospace;
    font-optical-sizing: auto;
    font-weight: 400;
    font-style: normal;
    font-variation-settings: "BLED" 0, "SCAN" 0;
    font-size: 42px;
    letter-spacing: 0.1rem;
    margin: 0;
  }
  .brand-name .champ-part { color: #8E54EE; }
  .brand-name .clip-part { color: #FF0000; }
  .brand-name .trim-part { color: #5865F2; }
  .cursor-blink { display: inline-block; animation: blink 1.6s ease-in-out infinite; }
  @keyframes blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0; }
  }
  .filename { color: var(--text-dim); font-size: 14px; overflow-wrap: anywhere; }
  .file-actions { display: flex; gap: 10px; margin-top: 3px; }
  .file-actions button {
    padding: 6px 12px; font-size: 11px; background: #8E54EE; color: #fff;
    border: none;
  }
  .file-actions button:hover { background: #9d6bf0; }
  h1 { font-size: 20px; font-weight: 600; margin: 0 0 20px; letter-spacing: -0.01em; }
  h2 {
    font-size: 11px; font-weight: 700; color: var(--text-dim);
    text-transform: uppercase; letter-spacing: 0.06em; margin: 0 0 16px;
  }
  .card {
    background: var(--bg-elev);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 24px;
    margin-bottom: 25px;
    box-shadow: 5px 5px 0px -3px #8E54EE;
  }
  #outputCard, #outputCard input[type=text], #outputCard select { font-family: "JetBrains Mono", monospace; }
  video { width: 100%; max-height: 55vh; background: #000; display: block; border-radius: 8px; }
  .video-wrap { position: relative; }
  .play-overlay {
    position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    cursor: pointer; opacity: 0.5; transition: opacity 0.15s ease;
  }
  .play-overlay:hover { opacity: 0.75; }
  .play-overlay.hidden { opacity: 0; pointer-events: none; }
  .play-overlay svg { filter: drop-shadow(0 2px 6px rgba(0,0,0,0.6)); }
  .row { display: flex; align-items: center; gap: 12px; margin-top: 14px; flex-wrap: wrap; }
  .times { font-variant-numeric: tabular-nums; font-size: 13px; color: var(--text-dim); min-width: 320px; }
  .slider-wrap { position: relative; height: 48px; margin-top: 16px; width: 98%; margin-left: auto; margin-right: auto; }
  .slider-wrap input[type=range] {
    position: absolute; top: 0; left: 0; width: 100%; height: 48px; margin: 0; z-index: 1;
    -webkit-appearance: none; background: transparent; pointer-events: none;
  }
  .slider-wrap input[type=range]::-webkit-slider-thumb {
    -webkit-appearance: none; pointer-events: auto;
    width: 10px; height: 28px; border-radius: 3px; margin-top: -12px;
    background: var(--accent); border: none;
    box-shadow: 0 1px 3px rgba(0,0,0,0.5); cursor: ew-resize; transition: background 0.15s ease;
  }
  .slider-wrap input[type=range]::-webkit-slider-thumb:hover { background: var(--accent-hover); }
  #startSlider::-webkit-slider-thumb { transform: translateX(-4px); }
  #endSlider::-webkit-slider-thumb { transform: translateX(4px); }
  #startSlider.at-edge::-webkit-slider-thumb { transform: translateX(-13px); }
  #endSlider.at-edge::-webkit-slider-thumb { transform: translateX(13px); }
  .slider-wrap input[type=range]::-webkit-slider-runnable-track { height: 4px; background: transparent; }
  .volume-row { display: flex; align-items: center; gap: 8px; margin-left: auto; }
  .volume-row span { font-size: 12px; color: var(--text-dim); }
  input[type=range].volume-slider {
    width: 100px; height: 3px; accent-color: var(--accent); cursor: pointer;
  }
  .track-bg { position: absolute; top: 22px; left: 0; right: 0; height: 4px; background: var(--border); border-radius: 2px; }
  .track-sel {
    position: absolute; top: 22px; height: 4px; opacity: 0.85;
    background: magenta;
    border-radius: 2px;
  }
  .playhead {
    position: absolute; top: 18px; width: 12px; height: 12px; border-radius: 50%;
    background: #b3b8c2; border: 2px solid var(--bg-elev);
    box-shadow: 0 0 0 1px #b3b8c2, 0 1px 3px rgba(0,0,0,0.5);
    pointer-events: none; z-index: 10; transform: translateX(-50%);
  }
  button {
    background: #23262e; color: var(--text); border: 1px solid var(--border); border-radius: 8px;
    padding: 9px 16px; font-size: 13px; font-weight: 400; cursor: pointer;
    transition: background 0.15s ease, border-color 0.15s ease;
  }
  button:hover { background: #2c2f38; border-color: #3a3e49; }
  button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  button.primary:hover { background: var(--accent-hover); border-color: var(--accent-hover); }
  button.danger { background: transparent; border-color: var(--danger); color: var(--danger); }
  button.danger:hover { background: rgba(229, 72, 77, 0.12); }
  a.button { display: inline-block; }
  .controls { margin-top: 4px; display: flex; justify-content: flex-end; gap: 12px; }
  .hint { font-size: 12px; color: var(--text-dim); margin-top: 12px; }
  label { display: flex; align-items: center; gap: 8px; font-size: 14px; cursor: pointer; }
  input[type=radio], input[type=checkbox] { accent-color: var(--accent); }
  input[type=text], select {
    background: #1c1e24; color: var(--text); border: 1px solid var(--border); border-radius: 8px;
    padding: 8px 10px; font-size: 14px; flex: 1;
  }
  input[type=text]:focus, select:focus { outline: none; border-color: var(--accent); }
</style>
</head>
<body>
<div class="page">
<div class="brand">
  <div class="brand-left">
    <img src="/logo2.png" alt="RIPChamp logo" class="logo">
    <h1 class="brand-name"><span class="rip-part">RIP</span><span class="champ-part">Champ</span>(<span class="clip-part">Clip</span>).<span class="trim-part">Trim</span><span class="cursor-blink">_</span></h1>
  </div>
  <div class="file-info">
    <span class="filename">__FILENAME__</span>
    <div class="file-actions">
      <button id="openFileBtn" type="button">Open This File</button>
      <button id="openFolderBtn" type="button">Browse Local Folder</button>
    </div>
  </div>
</div>

<div class="card">
  <div class="video-wrap">
    <video id="v" src="__VIDEO_URL__" preload="metadata"></video>
    <div class="play-overlay" id="playOverlay">
      <svg viewBox="0 0 100 100" width="72" height="72">
        <polygon points="32,20 32,80 82,50" fill="#fff"/>
      </svg>
    </div>
  </div>

  <div class="row">
    <span class="times" id="times">start 0:00.0 &nbsp;·&nbsp; end 0:00.0 &nbsp;·&nbsp; duration 0:00.0</span>
    <div class="volume-row">
      <span id="volumeIcon">🔊</span>
      <input type="range" id="volumeSlider" class="volume-slider" min="0" max="1" step="0.01" value="1">
    </div>
  </div>

  <div class="slider-wrap">
    <div class="track-bg"></div>
    <div class="track-sel" id="trackSel"></div>
    <div class="playhead" id="playhead"></div>
    <input type="range" id="startSlider" min="0" max="1000" value="0" step="1">
    <input type="range" id="endSlider" min="0" max="1000" value="1000" step="1">
  </div>
  <div class="hint">Click on the video to play, click the video again to pause.  Drag sliders to where you want your new clip to start and stop.</div>
</div>

<div class="card" id="outputCard">
  <h2>Output</h2>
  <div class="row">
    <label><input type="radio" name="type" value="video" checked> Video</label>
    <label><input type="radio" name="type" value="audio"> Audio only (extract mp3, saved locally)</label>
  </div>

  <div id="videoOptions">
    <div class="row">
      <label for="titleInput" style="min-width:40px">Title</label>
      <input type="text" id="titleInput" placeholder="blank for default">
    </div>
    <div class="row">
      <label><input type="radio" name="dest" value="upload" checked> Upload</label>
      <label><input type="radio" name="dest" value="local"> Local only (just crop, don't upload)</label>
    </div>
    <div class="row" id="hostRow" style="display:none">
      <label><input type="radio" name="videoHost" value="youtube" checked> YouTube</label>
      <label><input type="radio" name="videoHost" value="streamable"> Streamable (max 250MB)</label>
    </div>
    <div class="row" id="channelRow" style="display:none">
      <label for="channelSelect" style="min-width:40px">Discord</label>
      <select id="channelSelect"></select>
    </div>
  </div>
</div>

<div class="controls">
  <button class="danger" id="cancelBtn">Cancel</button>
  <button class="primary" id="confirmBtn">Confirm</button>
</div>
</div>

<script>
const CHANNELS = __CHANNELS_JSON__;
const CONFIRM_URL = "__CONFIRM_URL__";
const QUEUE_URL = __QUEUE_URL_JSON__;
const OPEN_FILE_URL = "__OPEN_FILE_URL__";
const OPEN_FOLDER_URL = "__OPEN_FOLDER_URL__";

const v = document.getElementById('v');
const startSlider = document.getElementById('startSlider');
const endSlider = document.getElementById('endSlider');
const trackSel = document.getElementById('trackSel');
const playhead = document.getElementById('playhead');
const timesEl = document.getElementById('times');
const playOverlay = document.getElementById('playOverlay');
const volumeSlider = document.getElementById('volumeSlider');
const volumeIcon = document.getElementById('volumeIcon');
const videoOptions = document.getElementById('videoOptions');
const hostRow = document.getElementById('hostRow');
const channelRow = document.getElementById('channelRow');
const channelSelect = document.getElementById('channelSelect');
const titleInput = document.getElementById('titleInput');
const openFileBtn = document.getElementById('openFileBtn');
const openFolderBtn = document.getElementById('openFolderBtn');

let duration = 0;

CHANNELS.forEach(name => {
  const opt = document.createElement('option');
  opt.value = name; opt.textContent = name;
  channelSelect.appendChild(opt);
});

function fmt(s) {
  if (!isFinite(s)) return '0:00.0';
  const m = Math.floor(s / 60);
  const sec = (s - m * 60).toFixed(1).padStart(4, '0');
  return m + ':' + sec;
}

function startTime() { return (startSlider.value / 1000) * duration; }
function endTime() { return (endSlider.value / 1000) * duration; }

function updateTimes() {
  timesEl.textContent = `start ${fmt(startTime())}  ·  end ${fmt(endTime())}  ·  duration ${fmt(duration)}  ·  selection: ${fmt(endTime() - startTime())}`;
  const s = (startSlider.value / 1000) * 100;
  const e = (endSlider.value / 1000) * 100;
  trackSel.style.left = s + '%';
  trackSel.style.width = Math.max(0, e - s) + '%';
  startSlider.classList.toggle('at-edge', parseInt(startSlider.value) === 0);
  endSlider.classList.toggle('at-edge', parseInt(endSlider.value) === 1000);
  updatePlayhead();
}

function updatePlayhead() {
  const pct = duration ? (v.currentTime / duration) * 100 : 0;
  playhead.style.left = pct + '%';
}

function updateVisibility() {
  const type = document.querySelector('input[name=type]:checked').value;
  videoOptions.style.display = (type === 'video') ? 'block' : 'none';
  const dest = document.querySelector('input[name=dest]:checked').value;
  hostRow.style.display = (type === 'video' && dest === 'upload') ? 'flex' : 'none';
  channelRow.style.display = (type === 'video' && dest === 'upload' && CHANNELS.length > 1) ? 'flex' : 'none';
}

document.querySelectorAll('input[name=type]').forEach(r => r.addEventListener('change', updateVisibility));
document.querySelectorAll('input[name=dest]').forEach(r => r.addEventListener('change', updateVisibility));
updateVisibility();

v.addEventListener('loadedmetadata', () => {
  duration = v.duration;
  updateTimes();
  updatePlayhead();
});

v.addEventListener('seeked', updatePlayhead);

startSlider.addEventListener('input', () => {
  if (parseInt(startSlider.value) >= parseInt(endSlider.value)) {
    startSlider.value = Math.max(0, parseInt(endSlider.value) - 1);
  }
  v.currentTime = startTime();
  updateTimes();
});

endSlider.addEventListener('input', () => {
  if (parseInt(endSlider.value) <= parseInt(startSlider.value)) {
    endSlider.value = Math.min(1000, parseInt(startSlider.value) + 1);
  }
  v.currentTime = endTime();
  updateTimes();
});

function playFromCorrectPosition() {
  if (v.currentTime >= endTime() - 0.01) {
    v.currentTime = startTime();
  }
  v.play();
}

playOverlay.addEventListener('click', playFromCorrectPosition);
v.addEventListener('click', () => {
  if (!v.paused) { v.pause(); }
});

v.addEventListener('play', () => { playOverlay.classList.add('hidden'); });
v.addEventListener('pause', () => { playOverlay.classList.remove('hidden'); });

openFileBtn.addEventListener('click', () => { fetch(OPEN_FILE_URL); });
openFolderBtn.addEventListener('click', () => { fetch(OPEN_FOLDER_URL); });

v.volume = parseFloat(volumeSlider.value);
volumeSlider.addEventListener('input', () => {
  v.volume = parseFloat(volumeSlider.value);
  volumeIcon.textContent = v.volume === 0 ? '🔇' : (v.volume < 0.5 ? '🔉' : '🔊');
});

v.addEventListener('timeupdate', () => {
  if (v.currentTime >= endTime() && !v.paused) {
    v.pause();
    v.currentTime = endTime();
  }
  updatePlayhead();
});

document.getElementById('confirmBtn').addEventListener('click', async () => {
  const type = document.querySelector('input[name=type]:checked').value;
  const body = { start: startTime(), end: endTime(), duration: duration, canceled: false, type: type };
  if (type === 'video') {
    body.title = titleInput.value.trim();
    body.destination = document.querySelector('input[name=dest]:checked').value;
    if (body.destination === 'upload') {
      body.videoHost = document.querySelector('input[name=videoHost]:checked').value;
      if (CHANNELS.length > 1) {
        body.discordChannel = channelSelect.value;
      }
    }
  }
  await fetch(CONFIRM_URL, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
  if (QUEUE_URL) { window.location.href = QUEUE_URL; }
  else { document.body.innerHTML = '<h1>Confirmed -- you can close this tab.</h1>'; }
});

document.getElementById('cancelBtn').addEventListener('click', async () => {
  await fetch(CONFIRM_URL, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({canceled: true}) });
  if (QUEUE_URL) { window.location.href = QUEUE_URL; }
  else { document.body.innerHTML = '<h1>Canceled -- you can close this tab.</h1>'; }
});
</script>
</body>
</html>
"""


def render_page(
    filename: str, channel_names: list, video_url: str = "/video", confirm_url: str = "/confirm",
    queue_url: str = None, open_file_url: str = "/open-file", open_folder_url: str = "/open-folder",
) -> str:
    """queue_url: if set, the page redirects there after Confirm/Cancel
    instead of showing a static "you can close this tab" message -- used by
    ripchamp_queue_server.py to bounce back to the queue list."""
    return (
        PAGE_TEMPLATE
        .replace("__FILENAME__", filename)
        .replace("__CHANNELS_JSON__", json.dumps(channel_names))
        .replace("__VIDEO_URL__", video_url)
        .replace("__CONFIRM_URL__", confirm_url)
        .replace("__QUEUE_URL_JSON__", json.dumps(queue_url))
        .replace("__OPEN_FILE_URL__", open_file_url)
        .replace("__OPEN_FOLDER_URL__", open_folder_url)
    )


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
        if result.get("discordChannel"):
            args += ["--discord-channel", result["discordChannel"]]

    return args
