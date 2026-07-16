#!/usr/bin/env python3
"""
ripchamp_secrets.py

Encrypted-at-rest storage for sensitive values RIPChamp needs to hang onto
long-term: Discord webhook URLs today, YouTube client secret/token later.

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


def _get_category(category: str) -> dict[str, str]:
    """Decrypted {name: value} for a category, skipping any entry that
    fails to decrypt (e.g. the file was copied from another machine or
    Windows account -- DPAPI blobs don't travel)."""
    raw = _load_all().get(category, {})
    out = {}
    for name, entry in raw.items():
        try:
            out[name] = _decrypt(base64.b64decode(entry["value"])).decode("utf-8")
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
            value = _decrypt(base64.b64decode(entry["value"])).decode("utf-8")
        except (OSError, ValueError, KeyError, TypeError):
            continue
        out[name] = {"value": value, "added": entry.get("added")}
    return out


def _set_secret(category: str, name: str, value: str):
    data = _load_all()
    bucket = data.setdefault(category, {})
    added = bucket.get(name, {}).get("added") or datetime.now(timezone.utc).isoformat()
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
