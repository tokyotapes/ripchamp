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
ripchamp_secrets.py

Encrypted-at-rest storage for sensitive values RIPChamp needs to hang onto
long-term: Discord webhook URLs and the YouTube client secret/OAuth token.

Encrypted with Windows DPAPI (CryptProtectData/CryptUnprotectData via
ctypes -- no extra dependency needed), scoped to the current Windows user
account. There's no master password to prompt for or key file to protect
separately -- but it also means a copied ripchamp_secrets.json won't
decrypt on another machine or under another Windows account, which is a
deliberate tradeoff, not a bug.

Storage: ripchamp_secrets.json next to this file (gitignored), holding
base64-encoded encrypted blobs grouped by category and name, e.g.
{"discord_webhooks": {"my-channel": {"value": "<base64 blob>", "added": "<ISO
date>"}}}. "added" is plaintext (not sensitive) -- just when the entry was
first saved, for display in the setup page's list.
"""

import base64
import ctypes
import ctypes.wintypes as wintypes
import json
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SECRETS_PATH = SCRIPT_DIR / "ripchamp_secrets.json"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _encrypt(plaintext: bytes) -> bytes:
    blob_in = _to_blob(plaintext)
    blob_out = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), "RIPChamp", None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _decrypt(ciphertext: bytes) -> bytes:
    blob_in = _to_blob(ciphertext)
    blob_out = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _load_all() -> dict:
    if not SECRETS_PATH.is_file():
        return {}
    try:
        return json.loads(SECRETS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_all(data: dict):
    SECRETS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _raw_value_and_added(entry) -> tuple:
    """Entries used to be stored as a bare base64 string before the "added"
    date field existed -- accept both shapes so pre-existing entries saved
    under the old format don't silently go unreadable."""
    if isinstance(entry, str):
        return entry, None
    return entry["value"], entry.get("added")


def _get_category(category: str) -> dict[str, str]:
    """Decrypted {name: value} for a category, skipping any entry that
    fails to decrypt (e.g. the file was copied from another machine or
    Windows account -- DPAPI blobs don't travel)."""
    raw = _load_all().get(category, {})
    out = {}
    for name, entry in raw.items():
        try:
            encoded, _ = _raw_value_and_added(entry)
            out[name] = _decrypt(base64.b64decode(encoded)).decode("utf-8")
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


def _get_category_with_added(category: str) -> dict[str, dict]:
    """Decrypted {name: {"value": ..., "added": ...}} for a category, same
    decrypt-failure skipping as _get_category."""
    raw = _load_all().get(category, {})
    out = {}
    for name, entry in raw.items():
        try:
            encoded, added = _raw_value_and_added(entry)
            value = _decrypt(base64.b64decode(encoded)).decode("utf-8")
        except (OSError, ValueError, KeyError, TypeError):
            continue
        out[name] = {"value": value, "added": added}
    return out


def _set_secret(category: str, name: str, value: str):
    data = _load_all()
    bucket = data.setdefault(category, {})
    existing = bucket.get(name)
    _, added = _raw_value_and_added(existing) if existing is not None else (None, None)
    added = added or datetime.now(timezone.utc).isoformat()
    bucket[name] = {
        "value": base64.b64encode(_encrypt(value.encode("utf-8"))).decode("ascii"),
        "added": added,
    }
    _save_all(data)


def _delete_secret(category: str, name: str):
    data = _load_all()
    bucket = data.get(category, {})
    if name in bucket:
        del bucket[name]
        _save_all(data)


# --- Discord webhooks ---

def get_discord_webhooks() -> dict[str, str]:
    """{channel_name: webhook_url}, decrypted."""
    return _get_category("discord_webhooks")


def get_discord_webhooks_with_added() -> dict[str, dict]:
    """{channel_name: {"value": webhook_url, "added": ISO date}}, decrypted."""
    return _get_category_with_added("discord_webhooks")


def set_discord_webhook(name: str, url: str):
    _set_secret("discord_webhooks", name, url)


def delete_discord_webhook(name: str):
    _delete_secret("discord_webhooks", name)


# --- YouTube ---
# Uses the same category/name bucket as Discord webhooks, just with a
# single fixed category ("youtube") and two fixed entry names, since
# there's only ever one client secret and one token at a time (unlike
# webhooks, which are per-channel).

def get_youtube_client_secret() -> str | None:
    """Raw contents of the downloaded client_secret JSON, decrypted."""
    return _get_category("youtube").get("client_secret")


def set_youtube_client_secret(json_text: str):
    _set_secret("youtube", "client_secret", json_text)


def delete_youtube_client_secret():
    _delete_secret("youtube", "client_secret")


def get_youtube_token() -> str | None:
    """Raw contents of the OAuth token JSON (creds.to_json()), decrypted."""
    return _get_category("youtube").get("token")


def set_youtube_token(json_text: str):
    _set_secret("youtube", "token", json_text)


def delete_youtube_token():
    _delete_secret("youtube", "token")


def get_youtube_status() -> dict:
    """{"client_secret_added": ISO date or None, "token_added": ISO date or
    None} -- for the setup page to show what's configured without exposing
    the actual secret contents."""
    entries = _get_category_with_added("youtube")
    return {
        "client_secret_added": entries.get("client_secret", {}).get("added"),
        "token_added": entries.get("token", {}).get("added"),
    }


# --- Streamable ---
# Same bucket pattern as YouTube: one fixed category, two fixed entry names
# (username/password), since there's only ever one Streamable account.

def get_streamable_credentials() -> tuple[str, str] | None:
    """(username, password), decrypted, or None if not saved."""
    entries = _get_category("streamable")
    username, password = entries.get("username"), entries.get("password")
    return (username, password) if username and password else None


def set_streamable_credentials(username: str, password: str):
    _set_secret("streamable", "username", username)
    _set_secret("streamable", "password", password)


def delete_streamable_credentials():
    _delete_secret("streamable", "username")
    _delete_secret("streamable", "password")


def get_streamable_status() -> dict:
    """{"added": ISO date or None, "username": saved username or None} --
    for the setup page to show what's configured without exposing the
    password."""
    entries = _get_category_with_added("streamable")
    return {
        "added": entries.get("username", {}).get("added"),
        "username": entries.get("username", {}).get("value"),
    }
