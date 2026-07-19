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
ripchamp.py

Converts an ultrawide video to a centered 1920x1080 (16:9) output.

How it works:
  1. Scales the video so its height becomes 1080px, preserving aspect ratio.
  2. Crops the center 1920px-wide region out of that scaled frame.
Works for any ultrawide ratio (21:9, 32:9, etc.) -- ffmpeg's crop filter
centers automatically. If the source isn't wide enough to fill 1920px
after scaling, black bars are padded in instead of cropping/stretching.
Pass --keep-aspect-ratio to skip the crop/pad entirely and keep the
source's own aspect ratio (e.g. for portrait/vertical clips).

Trimming:
  Use --start/--end to cut the video down to a time range before
  processing. Accepts seconds ("90"), a trailing-s shorthand ("30s"), or
  colon time formats ("1:30", "1:02:30").

HDR tone-mapping:
  If the source is HDR (PQ/smpte2084 or HLG/arib-std-b67), a straight
  copy to SDR looks washed out/flat because the brightness curve is
  interpreted incorrectly. The script auto-detects HDR sources and
  applies proper tone-mapping (zscale+tonemap) down to SDR bt709 before
  encoding. Use --tonemap to force/disable this or pick a different
  algorithm, and --npl/--desat to tune the look.

GPU acceleration:
  By default the script auto-detects a hardware encoder your ffmpeg build
  supports (NVIDIA NVENC, Intel QuickSync, AMD AMF, or Apple VideoToolbox)
  and tries each in order, falling back to CPU (libx264) only if none work.

YouTube/Streamable + Discord auto-upload:
  By default (--video-host youtube), if a YouTube client secret has been
  saved via the setup page's YouTube Setup card, the finished video is
  uploaded to YouTube as Unlisted at full quality, and the resulting link
  is posted to your Discord webhook (which Discord auto-embeds as a
  playable video) -- no size limit to fight with. The first run opens a
  browser once for you to authorize; after that it's fully automatic.

  Pass --video-host streamable to upload to Streamable instead (requires
  streamable_credentials.txt next to this script, or STREAMABLE_USERNAME /
  STREAMABLE_PASSWORD env vars). Streamable uploads are capped at
  --streamable-max-mb (default 250MB); a copy is compressed to fit if the
  output is larger.

  If the chosen video host isn't configured (or --no-youtube is set),
  falls back to uploading the file directly to Discord via webhook (see
  --discord-webhook), compressing a copy to fit under --discord-max-mb
  (default 10, Discord's free-tier limit) if needed.

  Flags: --no-youtube / --no-discord to disable either, --video-host,
  --youtube-title, --youtube-privacy (unlisted/private/public).

Corruption tolerance:
  Decoding errors and corrupt packets are skipped rather than aborting the
  whole conversion. If ffprobe can't read a corrupted header, pass
  --width/--height manually.

Usage:
    python ripchamp.py input.mp4 [output.mp4]
    python ripchamp.py input.mp4 --start 30s --end 1:30
    python ripchamp.py input.mp4 --encoder nvenc
    python ripchamp.py input.mp4 --youtube-auth-only   (run once to authorize)

Requires: ffmpeg and ffprobe on PATH. Discord upload requires curl on PATH.
YouTube upload requires: pip install google-api-python-client google-auth-oauthlib
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

HW_ENCODER_MAP = {
    "nvenc": "h264_nvenc",
    "qsv": "h264_qsv",
    "amf": "h264_amf",
    "videotoolbox": "h264_videotoolbox",
}

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}  # PQ, HLG
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def check_dependencies():
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        print(f"Error: {' and '.join(missing)} not found on PATH. Install ffmpeg first.", file=sys.stderr)
        sys.exit(1)


def list_ffmpeg_encoders() -> str:
    result = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True)
    return result.stdout


def detect_hw_encoders() -> list[str]:
    """Return all available hardware encoder names, in try-order."""
    available = list_ffmpeg_encoders()
    found = []
    for key in ("nvenc", "qsv", "amf", "videotoolbox"):  # rough speed/quality priority
        if HW_ENCODER_MAP[key] in available:
            found.append(HW_ENCODER_MAP[key])
    return found


def get_dimensions(input_path: Path):
    """Read width/height, probing deeper than ffprobe's defaults to tolerate
    minor header corruption. Returns None if it still can't be read."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-analyzeduration", "200M", "-probesize", "200M",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            str(input_path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    # Some files (iPhone recordings with rotation/display-matrix metadata
    # have been seen to do this) make ffprobe emit a trailing empty field,
    # e.g. "1920x1080x" instead of "1920x1080" -- filter those out rather
    # than assuming the split produces exactly two parts.
    parts = [p for p in result.stdout.strip().split("x") if p]
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def get_duration(path: Path) -> float | None:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def get_color_transfer(input_path: Path) -> str | None:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=color_transfer",
            "-of", "csv=s=x:p=0",
            str(input_path),
        ],
        capture_output=True, text=True,
    )
    value = result.stdout.strip()
    return value if value and value != "unknown" else None


def parse_time(value: str) -> float:
    """Parse a time string into seconds. Accepts '90', '30s', '1:30', '1:02:30'."""
    original = value
    value = value.strip()
    if not value:
        print("Error: empty time value.", file=sys.stderr)
        sys.exit(1)

    if ":" in value:
        parts = value.split(":")
        try:
            parts = [float(p) for p in parts]
        except ValueError:
            print(f"Error: invalid time format '{original}'. Use formats like '90', '30s', '1:30', or '1:02:30'.", file=sys.stderr)
            sys.exit(1)
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + part
        return seconds

    text = value.lower()
    if text.endswith("s"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        print(f"Error: invalid time format '{original}'. Use formats like '90', '30s', '1:30', or '1:02:30'.", file=sys.stderr)
        sys.exit(1)


def build_crop_filter(src_w: int, src_h: int, keep_aspect: bool = False) -> str:
    if keep_aspect:
        # No crop/pad -- just force even width/height, which libx264 and
        # most hardware encoders require but odd-dimension sources don't
        # always have.
        return "scale=trunc(iw/2)*2:trunc(ih/2)*2"

    scaled_w = src_w * 1080 // src_h
    scaled_w -= scaled_w % 2  # keep it even, ffmpeg requires this

    if scaled_w < 1920:
        print(f"Note: scaled width ({scaled_w}px) is narrower than 1920px, so black bars")
        print("      will be added on the sides instead of cropping.")
        return "scale=-2:1080,pad=1920:1080:(1920-iw)/2:(1080-ih)/2:color=black"
    return "scale=-2:1080,crop=1920:1080"


def build_tonemap_filter(algo: str, npl: float, desat: float) -> str:
    return (
        f"zscale=t=linear:npl={npl},format=gbrpf32le,zscale=p=bt709,"
        f"tonemap=tonemap={algo}:desat={desat},"
        f"zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
    )


def encoder_args(encoder: str) -> list[str]:
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0"]
    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-global_quality", "19"]
    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-rc", "cqp", "-qp_i", "19", "-qp_p", "19", "-quality", "quality"]
    if encoder == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-q:v", "65"]
    return ["-c:v", "libx264", "-crf", "18", "-preset", "medium"]  # cpu fallback


def run_ffmpeg(
    input_path: Path, output_path: Path, vf_filter: str, encoder: str, tag_sdr: bool,
    trim_start: float, trim_duration: float | None,
) -> bool:
    cmd = ["ffmpeg", "-y"]
    if trim_start > 0:
        cmd += ["-ss", str(trim_start)]
    cmd += [
        "-err_detect", "ignore_err",
        "-fflags", "+discardcorrupt+genpts",
        "-i", str(input_path),
    ]
    if trim_duration is not None:
        cmd += ["-t", str(trim_duration)]
    cmd += ["-vf", vf_filter, *encoder_args(encoder)]
    if tag_sdr:
        cmd += ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]
    cmd += ["-c:a", "copy", str(output_path)]
    result = subprocess.run(cmd)
    return result.returncode == 0


# --- Discord ---

def load_discord_webhooks() -> dict[str, str]:
    """Named webhooks from the encrypted store (ripchamp_secrets.json, what
    the setup page's Discord Integration card writes)."""
    import ripchamp_secrets
    return ripchamp_secrets.get_discord_webhooks()


def get_webhook_url(args) -> str | None:
    if args.discord_webhook:
        return args.discord_webhook
    env_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if env_url:
        return env_url

    webhooks = load_discord_webhooks()
    if not webhooks:
        return None

    if args.discord_channel:
        url = webhooks.get(args.discord_channel)
        if url is None:
            print(f"Error: no Discord channel named '{args.discord_channel}' configured. "
                  f"Available: {', '.join(webhooks.keys())}", file=sys.stderr)
        return url

    if len(webhooks) == 1:
        return next(iter(webhooks.values()))

    print(f"Multiple Discord channels configured ({', '.join(webhooks.keys())}) but none specified "
          f"-- pass --discord-channel NAME.", file=sys.stderr)
    return None


def compress_to_target_size(path: Path, max_mb: float, tag: str) -> Path | None:
    duration = get_duration(path)
    if not duration or duration <= 0:
        print(f"Could not determine video duration -- skipping {tag} compression.")
        return None

    audio_kbps = 96
    safety_factor = 0.92
    total_kbps_budget = (max_mb * 8192 * safety_factor) / duration
    video_kbps = int(total_kbps_budget - audio_kbps)

    if video_kbps < 100:
        print(f"Warning: fitting under {max_mb}MB for a {duration:.0f}s clip only allows ~{max(video_kbps, 1)}kbps "
              f"video -- quality will be poor.")
        video_kbps = max(video_kbps, 50)

    compressed_path = path.with_name(f"{path.stem}_{tag}.mp4")
    cmd = [
        "ffmpeg", "-y", "-i", str(path),
        "-c:v", "libx264", "-preset", "medium",
        "-b:v", f"{video_kbps}k", "-maxrate", f"{int(video_kbps * 1.5)}k", "-bufsize", f"{video_kbps * 2}k",
        "-c:a", "aac", "-b:a", f"{audio_kbps}k",
        str(compressed_path),
    ]
    print(f"Compressing a copy for {tag} (~{video_kbps}kbps video) to fit under {max_mb}MB...")
    result = subprocess.run(cmd)
    if result.returncode != 0 or not compressed_path.exists():
        return None
    return compressed_path


def compress_for_discord(path: Path, max_mb: float) -> Path | None:
    return compress_to_target_size(path, max_mb, "discord")


def compress_for_streamable(path: Path, max_mb: float) -> Path | None:
    return compress_to_target_size(path, max_mb, "streamable")


def upload_file_to_discord(path: Path, webhook_url: str) -> bool:
    if shutil.which("curl") is None:
        print("Error: curl not found on PATH -- can't upload to Discord.", file=sys.stderr)
        return False
    print(f"Uploading {path.name} to Discord...")
    result = subprocess.run(
        ["curl", "-s", "-o", "-", "-w", "\\nHTTP_STATUS:%{http_code}", "-F", f"file=@{path}", webhook_url],
        capture_output=True, text=True,
    )
    output = result.stdout
    status = output.rsplit("HTTP_STATUS:", 1)[-1].strip() if "HTTP_STATUS:" in output else "?"
    if status.startswith("2"):
        print("Uploaded to Discord successfully.")
        return True
    print(f"Discord upload failed (HTTP {status}): {output.split('HTTP_STATUS:')[0].strip()}", file=sys.stderr)
    return False


def post_message_to_discord(content: str, webhook_url: str) -> bool:
    if shutil.which("curl") is None:
        print("Error: curl not found on PATH -- can't post to Discord.", file=sys.stderr)
        return False
    result = subprocess.run(
        ["curl", "-s", "-o", "-", "-w", "\\nHTTP_STATUS:%{http_code}",
         "-H", "Content-Type: application/json",
         "-d", json.dumps({"content": content}),
         webhook_url],
        capture_output=True, text=True,
    )
    output = result.stdout
    status = output.rsplit("HTTP_STATUS:", 1)[-1].strip() if "HTTP_STATUS:" in output else "?"
    if status.startswith("2"):
        print("Posted link to Discord successfully.")
        return True
    print(f"Discord message post failed (HTTP {status}): {output.split('HTTP_STATUS:')[0].strip()}", file=sys.stderr)
    return False


# --- YouTube ---

def get_youtube_service():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        print("Error: YouTube upload needs extra packages. Install with:", file=sys.stderr)
        print("  pip install google-api-python-client google-auth-oauthlib", file=sys.stderr)
        return None

    import ripchamp_secrets
    client_secret_json = ripchamp_secrets.get_youtube_client_secret()
    if not client_secret_json:
        return None
    client_config = json.loads(client_secret_json)

    creds = None
    token_json = ripchamp_secrets.get_youtube_token()
    if token_json:
        creds = Credentials.from_authorized_user_info(json.loads(token_json), YOUTUBE_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            print("Opening a browser to authorize YouTube access (one-time)...")
            flow = InstalledAppFlow.from_client_config(client_config, YOUTUBE_SCOPES)
            creds = flow.run_local_server(port=0)
        ripchamp_secrets.set_youtube_token(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def build_video_link(video_id: str, style: str, koutube_direct: bool, koutube_stock: bool) -> str:
    """koutube.com re-embeds YouTube videos so Discord plays them natively
    inline, instead of the usual click-through mini-player. See
    https://github.com/iGerman00/koutube.
    'direct' bypasses koutube's own cache/metadata and resolves straight to
    the video. 'stock' embeds YouTube's real player instead (full adaptive
    quality, but back to a click-to-play embed) -- native inline embedding
    requires a single muxed video+audio file, which YouTube only offers up
    to ~720p, so 'stock' is the fix when quality matters more than autoplay."""
    if style != "koutube":
        return f"https://youtu.be/{video_id}"
    params = []
    if koutube_direct:
        params.append("direct")
    if koutube_stock:
        params.append("stock")
    link = f"https://koutu.be/{video_id}"
    if params:
        link += "?" + "&".join(params)
    return link


def upload_to_youtube(path: Path, title: str, privacy: str) -> str | None:
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        return None

    youtube = get_youtube_service()
    if youtube is None:
        return None

    body = {
        "snippet": {"title": title, "description": "Uploaded automatically by ripchamp.py"},
        "status": {"privacyStatus": privacy},
    }
    media = MediaFileUpload(str(path), chunksize=-1, resumable=True, mimetype="video/mp4")
    print(f"Uploading {path.name} to YouTube ({privacy})...")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    try:
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"  Upload progress: {int(status.progress() * 100)}%")
    except Exception as e:
        print(f"YouTube upload failed: {e}", file=sys.stderr)
        return None

    video_id = response.get("id") if response else None
    if not video_id:
        return None
    print(f"Uploaded to YouTube: https://youtu.be/{video_id}")
    return video_id


# --- Streamable ---

def load_streamable_credentials() -> tuple[str, str] | None:
    """Load a Streamable account's login from streamable_credentials.txt
    (username=... / password=... on separate lines, next to this script) or
    STREAMABLE_USERNAME / STREAMABLE_PASSWORD env vars. An account is
    required -- anonymous uploads are heavily rate-limited and auto-deleted
    by Streamable after a short time."""
    env_user = os.environ.get("STREAMABLE_USERNAME")
    env_pass = os.environ.get("STREAMABLE_PASSWORD")
    if env_user and env_pass:
        return env_user, env_pass

    creds_path = Path(__file__).resolve().parent / "streamable_credentials.txt"
    if not creds_path.is_file():
        return None
    values = {}
    for line in creds_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        values[key.strip().lower()] = val.strip()
    username, password = values.get("username"), values.get("password")
    return (username, password) if username and password else None


def upload_to_streamable(path: Path, title: str, credentials: tuple[str, str]) -> str | None:
    if shutil.which("curl") is None:
        print("Error: curl not found on PATH -- can't upload to Streamable.", file=sys.stderr)
        return None
    username, password = credentials
    print(f"Uploading {path.name} to Streamable...")
    result = subprocess.run(
        [
            "curl", "-s", "-o", "-", "-w", "\\nHTTP_STATUS:%{http_code}",
            "-u", f"{username}:{password}",
            "-F", f"file=@{path}",
            f"https://api.streamable.com/upload?title={quote(title)}",
        ],
        capture_output=True, text=True,
    )
    output = result.stdout
    status = output.rsplit("HTTP_STATUS:", 1)[-1].strip() if "HTTP_STATUS:" in output else "?"
    body = output.split("HTTP_STATUS:")[0].strip()
    if not status.startswith("2"):
        print(f"Streamable upload failed (HTTP {status}): {body}", file=sys.stderr)
        return None
    try:
        shortcode = json.loads(body).get("shortcode")
    except json.JSONDecodeError:
        shortcode = None
    if not shortcode:
        print(f"Streamable upload succeeded but no shortcode in response: {body}", file=sys.stderr)
        return None
    # The bare streamable.com/<shortcode> link doesn't get Discord's inline
    # video embed -- the '?src=player-page-share' param (what Streamable's own
    # "Share" button appends) is what triggers it.
    link = f"https://streamable.com/{shortcode}?src=player-page-share"
    print(f"Uploaded to Streamable: {link}")
    return link


def main():
    parser = argparse.ArgumentParser(description="RIPChamp an ultrawide video to 1920x1080.")
    parser.add_argument("input", type=Path, nargs="?", help="Path to the input video")
    parser.add_argument("output", type=Path, nargs="?", help="Path to the output video (optional)")
    parser.add_argument("--width", type=int, help="Manually specify source width (if ffprobe can't read a corrupted header)")
    parser.add_argument("--height", type=int, help="Manually specify source height (if ffprobe can't read a corrupted header)")
    parser.add_argument("--keep-aspect-ratio", action="store_true",
        help="Skip the 1920x1080 (16:9) crop/pad and keep the source's own aspect ratio instead.")
    parser.add_argument("--start", type=str, default=None, help="Trim start point, e.g. '30s', '1:30'.")
    parser.add_argument("--end", type=str, default=None, help="Trim end point, e.g. '1:30'.")
    parser.add_argument(
        "--encoder", choices=["auto", "nvenc", "qsv", "amf", "videotoolbox", "cpu"], default="auto",
        help="Video encoder. 'auto' picks the best available GPU encoder, falling back to CPU.",
    )
    parser.add_argument(
        "--tonemap", choices=["auto", "hable", "reinhard", "mobius", "clip", "none"], default="auto",
        help="HDR-to-SDR tonemap algorithm. 'auto' (default) only tonemaps if the source is detected as HDR.",
    )
    parser.add_argument("--npl", type=float, default=75.0, help="Nominal peak luminance in nits for tonemapping (default 75).")
    parser.add_argument("--desat", type=float, default=0.0, help="Desaturation strength for tonemapped highlights (default 0).")
    parser.add_argument("--discord-webhook", type=str, default=None, help="Discord webhook URL (overrides configured channels).")
    parser.add_argument("--discord-channel", type=str, default=None, help="Named Discord channel to post to (see discord_webhooks.txt).")
    parser.add_argument("--discord-max-mb", type=float, default=10.0, help="Max direct-upload size in MB before compressing (default 10).")
    parser.add_argument("--no-discord", action="store_true", help="Skip Discord entirely.")
    parser.add_argument("--youtube-title", type=str, default=None, help="Video title (default: output filename). Used for both YouTube and Streamable.")
    parser.add_argument("--youtube-privacy", choices=["unlisted", "private", "public"], default="unlisted", help="YouTube privacy status (default unlisted).")
    parser.add_argument("--no-youtube", action="store_true", help="Skip YouTube upload even if configured.")
    parser.add_argument("--youtube-auth-only", action="store_true", help="Just run the YouTube auth flow once and exit (no video needed).")
    parser.add_argument("--video-host", choices=["youtube", "streamable"], default="youtube",
        help="Video hosting service to upload to before posting the link to Discord (default youtube).")
    parser.add_argument("--streamable-max-mb", type=float, default=250.0,
        help="Max Streamable upload size in MB before compressing a copy (default 250, Streamable's free-tier limit).")
    parser.add_argument("--delete-after-upload", action="store_true", help="Delete the local cropped file once it's been successfully uploaded.")
    parser.add_argument("--audio-only", action="store_true", help="Extract just the audio as an mp3 instead of producing a cropped video. Saved locally, no upload.")
    parser.add_argument("--embed-link", choices=["koutube", "youtube"], default="youtube",
        help="Link style posted to Discord after a YouTube upload. 'youtube' (default) posts a plain youtu.be "
             "link (standard YouTube embed, full quality); 'koutube' routes it through koutube.com for a "
             "lower-res auto-inline embed instead.")
    parser.add_argument("--no-koutube-direct", action="store_true",
        help="With --embed-link koutube, omit the '?direct' param (uses koutube's normal cached mode instead "
             "of resolving straight to Google's raw video CDN URL).")
    parser.add_argument("--koutube-stock", action="store_true",
        help="With --embed-link koutube, add the '?stock' param to embed YouTube's real player (full adaptive "
             "quality up to 1080p/4K) instead of the low-res auto-inline video. Trades autoplay-inline for quality.")
    parser.add_argument("--embed-delay", type=float, default=30.0,
        help="Seconds to wait after a YouTube upload finishes before posting the link to Discord (default 30), "
             "giving YouTube a moment to finish processing the video first.")
    args = parser.parse_args()

    if args.youtube_auth_only:
        svc = get_youtube_service()
        if not svc:
            print("YouTube authorization failed -- check a client secret has been saved via the setup page.")
            sys.exit(1)
        try:
            resp = svc.channels().list(part="snippet", mine=True).execute()
            items = resp.get("items", [])
            if items:
                print(f"Authorized as channel: {items[0]['snippet']['title']}")
                print("If that's the wrong channel: switch your active channel at youtube.com")
                print("(profile picture -> Switch account), reset the token via the setup page, and re-run this.")
            else:
                print("Authorized, but couldn't identify the channel name.")
        except Exception as e:
            print(f"Authorized, but the channel check failed: {e}")
        sys.exit(0)

    if args.input is None:
        parser.error("input is required (unless using --youtube-auth-only)")

    check_dependencies()

    if not args.input.is_file():
        print(f"Error: input file '{args.input}' not found.", file=sys.stderr)
        sys.exit(1)

    output_path = args.output or args.input.with_name(f"{args.input.stem}_1080p.mp4")

    # --- Trim range ---
    trim_start = parse_time(args.start) if args.start else 0.0
    trim_end = parse_time(args.end) if args.end else None
    trim_duration = None
    if trim_end is not None:
        trim_duration = trim_end - trim_start
        if trim_duration <= 0:
            print(f"Error: --end ({args.end}) must be later than --start ({args.start or '0'}).", file=sys.stderr)
            sys.exit(1)
    if trim_start > 0 or trim_duration is not None:
        end_desc = f"{trim_end:.2f}s" if trim_end is not None else "end of file"
        print(f"Trimming: {trim_start:.2f}s to {end_desc}")

    # --- Audio-only: extract mp3 and stop, skip all video/upload logic ---
    if args.audio_only:
        mp3_path = args.output or args.input.with_suffix(".mp3")
        print(f"Extracting audio to: {mp3_path}")
        cmd = ["ffmpeg", "-y"]
        if trim_start > 0:
            cmd += ["-ss", str(trim_start)]
        cmd += ["-err_detect", "ignore_err", "-fflags", "+discardcorrupt+genpts", "-i", str(args.input)]
        if trim_duration is not None:
            cmd += ["-t", str(trim_duration)]
        cmd += ["-vn", "-c:a", "libmp3lame", "-q:a", "2", str(mp3_path)]
        result = subprocess.run(cmd)
        if result.returncode == 0 and mp3_path.exists():
            print(f"Done: {mp3_path}")
            sys.exit(0)
        else:
            print("ffmpeg failed to extract audio.", file=sys.stderr)
            sys.exit(1)

    # --- Resolution ---
    if args.width and args.height:
        src_w, src_h = args.width, args.height
        print(f"Using manually specified resolution: {src_w}x{src_h}")
    else:
        dims = get_dimensions(args.input)
        if dims is None:
            print("Error: could not read video dimensions -- the file header may be corrupted.", file=sys.stderr)
            print("Retry with --width and --height set manually, e.g. --width 3440 --height 1440", file=sys.stderr)
            sys.exit(1)
        src_w, src_h = dims
        print(f"Source resolution: {src_w}x{src_h}")

    crop_filter = build_crop_filter(src_w, src_h, keep_aspect=args.keep_aspect_ratio)

    # --- HDR tonemap ---
    if args.tonemap == "none":
        is_hdr, algo = False, None
    elif args.tonemap == "auto":
        transfer = get_color_transfer(args.input)
        is_hdr = transfer in HDR_TRANSFERS
        algo = "hable" if is_hdr else None
        print(f"Color transfer: {transfer or 'unknown'} -> {'HDR detected, tonemapping' if is_hdr else 'treating as SDR'}")
    else:
        is_hdr, algo = True, args.tonemap
        print(f"Tonemapping forced: {algo}")

    if is_hdr:
        vf_filter = f"{crop_filter},{build_tonemap_filter(algo, args.npl, args.desat)}"
        print(f"Applying {algo} tonemap (npl={args.npl}, desat={args.desat})")
    else:
        vf_filter = crop_filter

    # --- Encoder selection ---
    if args.encoder == "auto":
        candidates = detect_hw_encoders()
        if candidates:
            print(f"Detected possible GPU encoders (in try-order): {', '.join(candidates)}")
    elif args.encoder == "cpu":
        candidates = []
    else:
        candidates = [HW_ENCODER_MAP[args.encoder]]

    print(f"Writing: {output_path}")

    start_time = time.time()
    success = False
    used_encoder = None

    for enc in candidates:
        print(f"Trying encoder: {enc} (GPU)...")
        if run_ffmpeg(args.input, output_path, vf_filter, enc, is_hdr, trim_start, trim_duration):
            success = True
            used_encoder = enc
            break
        print(f"{enc} failed (driver/hardware may not match) -- trying next option...")

    if not success:
        print("Using encoder: libx264 (CPU)")
        success = run_ffmpeg(args.input, output_path, vf_filter, "libx264", is_hdr, trim_start, trim_duration)
        used_encoder = "libx264"

    elapsed = time.time() - start_time

    if success:
        print(f"Done: {output_path}  [{used_encoder}, {elapsed:.1f}s]")
    elif output_path.exists() and output_path.stat().st_size > 0:
        print(f"ffmpeg reported errors, but a partial output was written: {output_path}")
        print("Check the file -- it may be missing a corrupted section but still usable.")
    else:
        print("ffmpeg failed and no usable output was produced.", file=sys.stderr)
        sys.exit(1)

    if not output_path.exists():
        return

    # --- Video host (YouTube or Streamable) + Discord ---
    uploaded = False
    webhook_url = None if args.no_discord else get_webhook_url(args)
    title = args.youtube_title or output_path.stem

    if args.video_host == "youtube":
        import ripchamp_secrets
        youtube_configured = ripchamp_secrets.get_youtube_client_secret() is not None
        if not args.no_youtube and youtube_configured:
            video_id = upload_to_youtube(output_path, title, args.youtube_privacy)
            if video_id:
                uploaded = True
                if webhook_url:
                    if args.embed_delay > 0:
                        print(f"Waiting {args.embed_delay:.0f}s for YouTube to finish processing before posting the link...")
                        time.sleep(args.embed_delay)
                    link = build_video_link(video_id, args.embed_link, not args.no_koutube_direct, args.koutube_stock)
                    post_message_to_discord(link, webhook_url)
            else:
                print("YouTube upload failed -- falling back to direct Discord upload." if webhook_url else "YouTube upload failed.")
    elif args.video_host == "streamable":
        streamable_creds = load_streamable_credentials()
        if not args.no_youtube and streamable_creds:
            upload_path = output_path
            size_mb = output_path.stat().st_size / (1024 * 1024)
            if size_mb > args.streamable_max_mb:
                print(f"Output is {size_mb:.1f}MB, over the {args.streamable_max_mb}MB Streamable limit.")
                upload_path = compress_for_streamable(output_path, args.streamable_max_mb)
                if upload_path is None:
                    print("Could not prepare a file under the size limit -- skipping Streamable upload.")
            if upload_path:
                link = upload_to_streamable(upload_path, title, streamable_creds)
                if link:
                    uploaded = True
                    if webhook_url:
                        if args.embed_delay > 0:
                            print(f"Waiting {args.embed_delay:.0f}s for Streamable to finish processing before posting the link...")
                            time.sleep(args.embed_delay)
                        post_message_to_discord(link, webhook_url)
                else:
                    print("Streamable upload failed -- falling back to direct Discord upload." if webhook_url else "Streamable upload failed.")
                if upload_path != output_path and upload_path.exists():
                    upload_path.unlink(missing_ok=True)
        elif not streamable_creds:
            print("Streamable not configured -- add streamable_credentials.txt next to this script (or STREAMABLE_USERNAME/STREAMABLE_PASSWORD env vars).")

    if not uploaded and webhook_url:
        upload_path = output_path
        size_mb = output_path.stat().st_size / (1024 * 1024)
        if size_mb > args.discord_max_mb:
            print(f"Output is {size_mb:.1f}MB, over the {args.discord_max_mb}MB Discord limit.")
            upload_path = compress_for_discord(output_path, args.discord_max_mb)
            if upload_path is None:
                print("Could not prepare a file under the size limit -- skipping Discord upload.")
        if upload_path:
            if upload_file_to_discord(upload_path, webhook_url):
                uploaded = True
            if upload_path != output_path and upload_path.exists():
                upload_path.unlink(missing_ok=True)  # clean up the temp compressed copy either way

    if args.delete_after_upload:
        if uploaded:
            output_path.unlink(missing_ok=True)
            print(f"Deleted local copy: {output_path}")
        else:
            print("No upload succeeded -- keeping local file.")


if __name__ == "__main__":
    main()