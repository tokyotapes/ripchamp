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
ripchamp_installer.py

Standalone installer for RIPChamp -- asks where to install (default
%USERPROFILE%\\ripchamp), copies the app's files there, and launches the
queue server for the first time (which opens a browser to /setup).

Built into a standalone .exe via PyInstaller (see build.ps1) so end
users don't need Python to run the *installer* -- the *installed app*
still needs Python and ffmpeg already on the machine. Before copying
anything, this installer checks for both; if either is missing, it offers
a one-click "Install via winget" per dependency (falling back to a manual
download link if winget isn't available or the install fails) -- no
partial install left behind either way. Once Python/ffmpeg are confirmed
present, it also pip-installs RIPChamp's own Python package dependencies
(see PIP_PACKAGES) into that Python before launching the server, so the
end user never has to run pip themselves.

Usage (dev, unfrozen -- run from inside installer/):
    python ripchamp_installer.py

Usage (built):
    RIPChampInstaller.exe

To test the missing-dependency dialog without actually uninstalling
anything, set RIPCHAMP_SIMULATE_MISSING to a comma-separated list of
names from DEPENDENCIES before launching, e.g. (PowerShell):
    $env:RIPCHAMP_SIMULATE_MISSING = "ffmpeg"
    .\\dist\\RIPChampInstaller.exe
"""

import os
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
import winreg
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox

# Files/dirs copied into the install directory. Kept as an explicit list
# rather than derived from git so the manifest is obvious to read/update,
# and mirrors exactly what's tracked in git (i.e. no secrets/local config
# -- see .gitignore in the project root).
PAYLOAD_FILES = [
    "ripchamp.py",
    "ripchamp_picker.py",
    "ripchamp_queue_server.py",
    "ripchamp_secrets.py",
    "ripchamp_tools.ps1",
    "start_ripchamp.bat",
    "stop_ripchamp.bat",
    "favicon.ico",
    "logo.png",
    "logo2.png",
    "COPYING",
]
PAYLOAD_DIRS = ["static"]

PYTHON_CANDIDATES = ["python", "py", "python3"]

# Third-party packages RIPChamp's own code imports (psutil for watcher-status
# detection in ripchamp_queue_server.py, the two google-* packages for
# YouTube upload in ripchamp.py) -- installed into whichever Python
# find_python() resolves, so they're present for the copied app without the
# user having to run pip themselves.
PIP_PACKAGES = ["psutil", "google-api-python-client", "google-auth-oauthlib"]


@dataclass
class Dependency:
    name: str
    candidates: list       # PATH names to check for
    winget_id: str          # winget package id, installed via `winget install --id <id> -e`
    fallback_url: str      # manual download page, shown if winget isn't available or fails


# Checked before any files are copied. Windows machines vary on which
# PATH name actually resolves, so each entry tries a few. Python.Python.3.13
# matches what this project already requires (uses `X | None` type hints,
# PEP 604, needing 3.10+) and is a current stable release.
DEPENDENCIES = [
    Dependency("Python", PYTHON_CANDIDATES, "Python.Python.3.13", "https://www.python.org/downloads/"),
    Dependency("ffmpeg", ["ffmpeg"], "Gyan.FFmpeg", "https://ffmpeg.org/download.html"),
]


class MissingDependencyError(Exception):
    """Raised when one or more required programs aren't on PATH.
    Carries [Dependency, ...] so the caller can show install options."""
    def __init__(self, missing: list):
        self.missing = missing
        names = ", ".join(dep.name for dep in missing)
        super().__init__(f"Missing requirements: {names}")


def resource_path(rel: str) -> Path:
    """Resolve a bundled payload file/dir, whether running as a frozen
    PyInstaller exe (files live under sys._MEIPASS) or as a plain script
    during development (files live at the project root, one level up
    from this installer/ folder)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / rel


def find_on_path(candidates: list) -> str | None:
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def find_python() -> str | None:
    return find_on_path(PYTHON_CANDIDATES)


def check_dependencies() -> list:
    """Return [Dependency, ...] for every dependency in DEPENDENCIES that
    isn't found on PATH -- or that's listed in the RIPCHAMP_SIMULATE_MISSING
    env var, for testing the missing-dependency view without actually
    uninstalling anything (see module docstring)."""
    simulated = {
        n.strip().lower()
        for n in os.environ.get("RIPCHAMP_SIMULATE_MISSING", "").split(",")
        if n.strip()
    }
    return [
        dep for dep in DEPENDENCIES
        if dep.name.lower() in simulated or not find_on_path(dep.candidates)
    ]


def _registry_path(root_key, subkey: str) -> str:
    try:
        with winreg.OpenKey(root_key, subkey) as key:
            value, _ = winreg.QueryValueEx(key, "Path")
            return value
    except OSError:
        return ""


def refresh_path_env():
    """winget updates PATH in the registry, but this already-running
    process's PATH (captured at launch) won't see it -- merge the current
    machine + user PATH from the registry into os.environ so a
    freshly-winget-installed program can be found immediately, without
    restarting the installer."""
    machine_path = _registry_path(
        winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")
    user_path = _registry_path(winreg.HKEY_CURRENT_USER, "Environment")
    combined = ";".join(p for p in (machine_path, user_path) if p)
    if combined:
        os.environ["PATH"] = combined


def install_via_winget(winget_id: str) -> tuple:
    """Run `winget install --id <winget_id> -e` silently. Returns
    (success, output) -- output is the failure detail if not successful."""
    winget = shutil.which("winget")
    if not winget:
        return False, "winget isn't available on this machine."
    try:
        result = subprocess.run(
            [winget, "install", "--id", winget_id, "-e",
             "--silent", "--accept-package-agreements", "--accept-source-agreements"],
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return False, "winget install timed out."
    if result.returncode != 0:
        return False, (result.stdout + result.stderr).strip()[-800:]
    return True, ""


def _copy_with_retry(copy_fn, src, dst, attempts=6, delay=0.5):
    """Windows Defender (and other AV) commonly holds a brief lock on a
    freshly-extracted PyInstaller onefile temp file the moment a new,
    unsigned exe starts (scanning it) -- retry transient file-lock errors
    a few times before giving up, rather than failing the whole install
    over what's usually a race that clears within a second."""
    last_error = None
    for _ in range(attempts):
        try:
            copy_fn(src, dst)
            return
        except OSError as e:
            last_error = e
            time.sleep(delay)
    raise RuntimeError(f"Couldn't copy {src} to {dst}: {last_error}") from last_error


def install_pip_packages(python_exe: str, status_callback=None):
    """Install PIP_PACKAGES into whichever Python find_python() resolved --
    the same interpreter the copied ripchamp_queue_server.py will run
    under. Raises RuntimeError with pip's own output on failure, same
    pattern as _copy_with_retry's failure surfacing."""
    if status_callback:
        status_callback("Installing Python packages...")
    result = subprocess.run(
        [python_exe, "-m", "pip", "install", "--quiet", *PIP_PACKAGES],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).strip()[-800:]
        raise RuntimeError(f"Couldn't install required Python packages:\n{detail}")


def install(target_dir: Path, status_callback=None) -> str:
    """Copy the payload and install pip dependencies. Does NOT launch the
    server -- the caller does that after showing its own success message,
    so the server (and its --open-setup browser tab) doesn't pop up behind
    a still-open "Installed!" dialog. Returns the resolved python_exe path,
    needed to launch the server afterward."""
    # Check everything's present before touching disk -- no partial
    # install left behind if something's missing.
    missing = check_dependencies()
    if missing:
        raise MissingDependencyError(missing)

    target_dir.mkdir(parents=True, exist_ok=True)

    for rel in PAYLOAD_FILES:
        _copy_with_retry(shutil.copy2, resource_path(rel), target_dir / rel)
    for rel in PAYLOAD_DIRS:
        _copy_with_retry(
            lambda s, d: shutil.copytree(s, d, dirs_exist_ok=True),
            resource_path(rel), target_dir / rel,
        )

    python_exe = find_python()
    install_pip_packages(python_exe, status_callback)
    return python_exe


def launch_server(python_exe: str, target_dir: Path):
    """Start the queue server for the first time, opening a browser to
    /setup. No visible console -- matches how Ensure-QueueServer in
    ripchamp_tools.ps1 already launches the server hidden."""
    subprocess.Popen(
        [python_exe, "-u", str(target_dir / "ripchamp_queue_server.py"), "--open-setup"],
        cwd=str(target_dir),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


class InstallerApp:
    """One window whose content swaps between two views: a missing-
    dependencies view (shown first if anything's missing) and the normal
    choose-directory/install view. Never both onscreen at once, and no
    separate popup -- "Try Again" on the missing-deps view just re-checks
    and swaps to the install view if everything's present now."""

    def __init__(self, root):
        self.root = root
        self.root.resizable(False, False)
        self.show_current_view()

    def _clear(self):
        for widget in self.root.winfo_children():
            widget.destroy()

    def show_current_view(self):
        missing = check_dependencies()
        if missing:
            self._build_missing_view(missing)
        else:
            self._build_install_view()

    def _build_missing_view(self, missing):
        self._clear()
        self.root.title("RIPChamp Installer - Missing Dependencies")

        tk.Label(
            self.root,
            text="Install the missing dependencies below, then click Try Again.",
            justify="left", wraplength=360, padx=20, pady=16,
        ).pack()

        for dep in missing:
            row = tk.Frame(self.root)
            row.pack(fill="x", padx=20, pady=4)
            tk.Label(row, text=f"{dep.name}:", width=10, anchor="w").pack(side="left")
            install_btn = tk.Button(row, text="Install via winget", width=16)
            install_btn.pack(side="left")
            install_btn.config(command=lambda d=dep, b=install_btn: self.on_winget_install(d, b))
            link = tk.Label(row, text="or download manually", fg="#2952e3", cursor="hand2")
            link.pack(side="left", padx=(10, 0))
            link.bind("<Button-1>", lambda e, u=dep.fallback_url: webbrowser.open(u))

        btn_row = tk.Frame(self.root)
        btn_row.pack(pady=16)
        tk.Button(btn_row, text="Try Again", width=14, command=self.show_current_view).pack(
            side="left", padx=6)
        tk.Button(btn_row, text="Cancel", width=14, command=self.root.destroy).pack(
            side="left", padx=6)

    def on_winget_install(self, dep, button):
        button.config(state="disabled", text="Installing...")

        def worker():
            success, output = install_via_winget(dep.winget_id)
            self.root.after(0, self._on_winget_done, dep, success, output)

        threading.Thread(target=worker, daemon=True).start()

    def _on_winget_done(self, dep, success, output):
        if success:
            refresh_path_env()
        else:
            messagebox.showerror(
                "RIPChamp Setup",
                f"Couldn't install {dep.name} via winget:\n{output}\n\n"
                f"You can also install it manually: {dep.fallback_url}",
            )
        self.show_current_view()

    def _build_install_view(self):
        self._clear()
        self.root.title("RIPChamp Setup")

        default_dir = str(Path.home() / "ripchamp")
        pad = {"padx": 16, "pady": 8}

        tk.Label(self.root, text="Choose a directory to install RIPChamp:").grid(
            row=0, column=0, columnspan=2, sticky="w", **pad)

        self.path_var = tk.StringVar(value=default_dir)
        tk.Entry(self.root, textvariable=self.path_var, width=48).grid(
            row=1, column=0, padx=(16, 4), pady=(0, 8))

        tk.Button(self.root, text="Browse...", command=self.browse).grid(
            row=1, column=1, padx=(4, 16), pady=(0, 8))

        self.status_var = tk.StringVar(value="")
        tk.Label(self.root, textvariable=self.status_var, fg="#555").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=16)

        self.install_btn = tk.Button(self.root, text="Install", command=self.on_install, width=14)
        self.install_btn.grid(row=3, column=0, columnspan=2, pady=16)

    def browse(self):
        chosen = filedialog.askdirectory(initialdir=self.path_var.get() or str(Path.home()))
        if chosen:
            self.path_var.set(chosen)

    def on_install(self):
        target_dir_str = self.path_var.get().strip()
        if not target_dir_str:
            messagebox.showerror("RIPChamp Setup", "Choose a directory first.")
            return

        self.install_btn.config(state="disabled")
        self.status_var.set("Installing...")
        self.root.update_idletasks()

        def report_status(text):
            self.status_var.set(text)
            self.root.update_idletasks()

        target_dir = Path(target_dir_str)
        try:
            python_exe = install(target_dir, status_callback=report_status)
        except MissingDependencyError:
            # A dependency vanished between the startup check and clicking
            # Install (rare) -- swap back to the missing-deps view.
            self.show_current_view()
            return
        except Exception as e:
            self.install_btn.config(state="normal")
            self.status_var.set("")
            messagebox.showerror("RIPChamp Setup", str(e))
            return

        # Show the success message and wait for it to be dismissed before
        # launching the server -- otherwise the browser tab opens behind
        # this still-open dialog.
        messagebox.showinfo("RIPChamp Setup", "Installed!")
        launch_server(python_exe, target_dir)
        self.root.destroy()


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--diag-install":
        # Headless diagnostic path: run install() with a full traceback on
        # failure, no GUI. Temporary -- for tracking down a WinError 3 report.
        import traceback
        try:
            install(Path(sys.argv[2]))
            print("OK")
        except Exception:
            traceback.print_exc()
        return

    root = tk.Tk()
    InstallerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
