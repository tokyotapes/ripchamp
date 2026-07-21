<#
RIPChamp
Copyright (C) 2026  NoOrg

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
#>

<#
ripchamp_tools.ps1

All-in-one control script for the ripchamp pipeline: the folder watcher,
scheduled-task install/uninstall/status for running it hidden at logon,
and one-time setup for Discord and YouTube. Everyday clip processing
itself happens entirely through the browser (the queue page at
http://127.0.0.1:8787/, including its own "Browse for a file..." button
for anything not auto-detected by the watcher) -- this script's job is
just keeping that watcher/queue server running, not processing clips
directly.

Usage:
  powershell -File ripchamp_tools.ps1 -Mode Watch [-WatchPath "C:\..."]
      Keeps the queue server running and, once the setup page's "Let us
      watch for new videos to clip?" is set to Yes and a folder has been
      chosen and saved (ripchamp_config.json's watch_enabled/
      watch_directory), watches that folder (and subfolders) for new .mp4
      files and adds each one to the persistent queue server
      (ripchamp_queue_server.py, http://127.0.0.1:8787/ by default) --
      bookmark that page and process clips whenever you're ready, so a
      clip finishing mid-game doesn't yank focus away from it. Leave
      running -- Ctrl+C to stop.

      Nothing is watched (not even -WatchPath's own default) until both
      of those are configured -- a fresh install with folder watching
      still turned off just keeps the queue server alive. Both settings
      are polled every 5s while running, so enabling/disabling watching or
      changing the folder later all take effect live, no restart needed.

  powershell -File ripchamp_tools.ps1 -Mode InstallTask [-WatchPath "C:\..."]
      Registers a scheduled task to run -Mode Watch hidden at every logon.

  powershell -File ripchamp_tools.ps1 -Mode UninstallTask
      Removes that scheduled task and stops the watcher and queue server
      if either is running.

  powershell -File ripchamp_tools.ps1 -Mode DisableTask
      Removes the logon-start scheduled task only -- unlike UninstallTask,
      doesn't touch anything currently running. Used by the setup page's
      "Let RIPChamp Start Automatically?" -> No.

  powershell -File ripchamp_tools.ps1 -Mode Status
      Reports whether the watcher and queue server are currently running.

  powershell -File ripchamp_tools.ps1 -Mode AddDiscordChannel
      Prompts for a channel name + webhook URL, saves to discord_webhooks.txt.

  powershell -File ripchamp_tools.ps1 -Mode SetupYoutube
      Installs required pip packages and runs the one-time YouTube
      authorization flow (have the right channel active on youtube.com first).
#>

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("Watch", "InstallTask", "UninstallTask", "DisableTask", "Status", "AddDiscordChannel", "SetupYoutube")]
    [string]$Mode,

    [string]$WatchPath = "C:\Users\evan\Videos\NVIDIA",
    [string]$ScriptDir = $PSScriptRoot
)

$TaskName = "RIPChampWatcher"

function Get-ConfiguredWatchPath {
    # Set via the setup page's "Browse for Folder..." button (saved to
    # ripchamp_config.json's watch_directory key by ripchamp_queue_server.py).
    # None/missing means no folder has been chosen yet.
    if (-not (Test-Path $ConfigPath)) { return $null }
    try {
        $config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
        if ($config.watch_directory) { return $config.watch_directory }
    } catch { }
    return $null
}

function Get-ConfiguredWatchEnabled {
    # The setup page's "Let us watch for new videos to clip?" choice
    # (saved to ripchamp_config.json's watch_enabled key by
    # ripchamp_queue_server.py). Defaults to $false -- mirrors
    # get_watch_enabled()'s own default there, so a fresh install never
    # starts watching any folder (including the -WatchPath fallback below)
    # until the user has explicitly opted in, picked a folder, and saved.
    if (-not (Test-Path $ConfigPath)) { return $false }
    try {
        $config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
        if ($null -ne $config.watch_enabled) { return [bool]$config.watch_enabled }
    } catch { }
    return $false
}

function Get-ConfiguredPort {
    # Chosen at install time (installer/ripchamp_installer.py) and saved to
    # ripchamp_config.json's port key -- mirrors ripchamp_queue_server.py's
    # own get_port() default so every launch path agrees on the same port.
    if (Test-Path $ConfigPath) {
        try {
            $config = Get-Content $ConfigPath -Raw | ConvertFrom-Json
            if ($config.port) { return $config.port }
        } catch { }
    }
    return 8787
}

function Ensure-QueueServer {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$QueuePort/status.json" -TimeoutSec 2 -ErrorAction Stop | Out-Null
        return $true
    } catch {
        # not reachable yet -- fall through and try to start it
    }

    try {
        Start-Process -FilePath "python" `
            -ArgumentList "`"$(Join-Path $ScriptDir 'ripchamp_queue_server.py')`" --port $QueuePort" `
            -WindowStyle Hidden
    } catch {
        Write-Host "Could not start the queue server: $_" -ForegroundColor Red
        return $false
    }

    for ($i = 0; $i -lt 10; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$QueuePort/status.json" -TimeoutSec 2 -ErrorAction Stop | Out-Null
            return $true
        } catch { }
    }
    return $false
}

if (-not $ScriptDir) {
    $ScriptDir = Split-Path -Parent $PSCommandPath
}
$ConfigPath = Join-Path $ScriptDir "ripchamp_config.json"
$QueuePort = Get-ConfiguredPort

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class RIPChampWin32 {
    [DllImport("kernel32.dll")]
    public static extern IntPtr GetConsoleWindow();
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
"@

function Invoke-Watch {
    # Hide this console window directly -- launch-time window-style flags
    # (like -WindowStyle Hidden) aren't always honored depending on what's
    # hosting the console, so hide it explicitly instead of relying on that.
    $consoleHandle = [RIPChampWin32]::GetConsoleWindow()
    if ($consoleHandle -ne [IntPtr]::Zero) {
        [RIPChampWin32]::ShowWindow($consoleHandle, 0) | Out-Null  # SW_HIDE
    }

    if (Ensure-QueueServer) {
        Write-Host "Queue server ready at http://127.0.0.1:$QueuePort/ -- bookmark it and process clips whenever you're ready."
    } else {
        Write-Host "Queue server unavailable -- new clips won't be queued until it's running. Try start_ripchamp.bat." -ForegroundColor Yellow
    }

    $watcher = $null
    $activePath = $null
    $watcherSubId = $null

    $action = {
        $filePath = $Event.SourceEventArgs.FullPath
        $queuePort = $Event.MessageData.QueuePort

        if ($filePath -like "*_1080p.mp4" -or $filePath -like "*_discord.mp4") { return }

        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] New file detected: $filePath"
        Start-Sleep -Seconds 3

        $ready = $false
        for ($i = 0; $i -lt 120; $i++) {
            try {
                $stream = [System.IO.File]::Open($filePath, 'Open', 'Read', 'None')
                $stream.Close()
                $ready = $true
                break
            } catch {
                Start-Sleep -Seconds 5
            }
        }

        if (-not $ready) {
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Gave up waiting for file to be ready: $filePath" -ForegroundColor Yellow
            return
        }

        $queued = $false
        try {
            $encodedPath = [uri]::EscapeDataString($filePath)
            Invoke-RestMethod -Uri "http://127.0.0.1:$queuePort/add?path=$encodedPath" -TimeoutSec 5 -ErrorAction Stop | Out-Null
            $queued = $true
        } catch {
            $queued = $false
        }

        if ($queued) {
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Added to queue: $filePath"
        } else {
            # No fallback path anymore -- everything goes through the queue page.
            # The file is still on disk; once the server's back up, add it with
            # the queue page's "Browse for a file..." button.
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Queue server unreachable -- couldn't add: $filePath" -ForegroundColor Yellow
        }
    }

    # Starts, stops, or re-points the actual FileSystemWatcher based on the
    # setup page's live "watch_enabled"/watch_directory config -- called
    # once up front and then every 5s, so enabling/disabling/changing the
    # watch folder all take effect without restarting this process, and
    # nothing gets watched at all (not even a fallback path) until the
    # user has explicitly opted in, picked a folder, and saved.
    function Sync-Watcher {
        $enabled = Get-ConfiguredWatchEnabled
        $configuredPath = Get-ConfiguredWatchPath
        $shouldWatch = $enabled -and $configuredPath -and (Test-Path $configuredPath)

        if ($shouldWatch -and $configuredPath -ne $script:activePath) {
            if ($script:watcher) {
                $script:watcher.EnableRaisingEvents = $false
                Unregister-Event -SourceIdentifier $script:watcherSubId -ErrorAction SilentlyContinue
                $script:watcher.Dispose()
            }
            $script:watcher = New-Object System.IO.FileSystemWatcher
            $script:watcher.Path = $configuredPath
            $script:watcher.IncludeSubdirectories = $true
            $script:watcher.Filter = "*.mp4"
            $script:watcher.NotifyFilter = [System.IO.NotifyFilters]::FileName
            $subscription = Register-ObjectEvent -InputObject $script:watcher -EventName Created -Action $action `
                -MessageData @{ QueuePort = $QueuePort }
            $script:watcherSubId = $subscription.Name
            $script:watcher.EnableRaisingEvents = $true
            $script:activePath = $configuredPath
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Watching '$configuredPath' and subfolders for new .mp4 files."
        } elseif (-not $shouldWatch -and $script:watcher) {
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Folder watching turned off -- no longer watching '$($script:activePath)'."
            $script:watcher.EnableRaisingEvents = $false
            Unregister-Event -SourceIdentifier $script:watcherSubId -ErrorAction SilentlyContinue
            $script:watcher.Dispose()
            $script:watcher = $null
            $script:activePath = $null
        }
    }

    if (-not (Get-ConfiguredWatchEnabled) -or -not (Get-ConfiguredWatchPath)) {
        Write-Host "Folder watching isn't turned on yet -- enable it and choose a folder on the setup page, then save."
    }
    Sync-Watcher

    Write-Host "Press Ctrl+C to stop."
    while ($true) {
        Start-Sleep -Seconds 5
        Sync-Watcher
    }
}

function Install-WatcherTask {
    $scriptPath = Join-Path $ScriptDir "ripchamp_tools.ps1"
    $trValue = "`"powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -NoProfile -File $scriptPath -Mode Watch -WatchPath $WatchPath`""

    schtasks /create /tn $TaskName /tr $trValue /sc onlogon /rl limited /f

    if ($LASTEXITCODE -eq 0) {
        Write-Host "Done. The watcher will start automatically and hidden next time you log in."
        Write-Host "To start it right now: schtasks /run /tn `"$TaskName`""
    } else {
        Write-Host "Something went wrong creating the scheduled task (exit code $LASTEXITCODE)." -ForegroundColor Red
        if ($scriptPath -match '\s' -or $WatchPath -match '\s') {
            Write-Host "Note: your script or watch folder path contains spaces, which schtasks handles poorly -- let me know if this is the case, there's a workaround." -ForegroundColor Yellow
        }
    }
}

function Uninstall-WatcherTask {
    schtasks /delete /tn $TaskName /f 2>$null

    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
        Where-Object { $_.CommandLine -like "*ripchamp_tools.ps1*-Mode Watch*" -and $_.ProcessId -ne $PID } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -like "*ripchamp_queue_server.py*" -and $_.ProcessId -ne $PID } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

    Write-Host "Removed the scheduled task and stopped the watcher and queue server (if either was active)."
}

function Disable-WatcherTask {
    # Lighter-weight than UninstallTask -- only removes the logon trigger
    # (idempotent: a no-op if it wasn't installed), does NOT touch any
    # currently-running watcher/queue server. Used by the setup page's
    # "Let RIPChamp Start Automatically?" -> No, which must not kill the
    # very server process the setup page itself is being served from.
    schtasks /delete /tn $TaskName /f 2>$null
    Write-Host "RIPChamp will no longer start automatically at login. Anything currently running is untouched."
}

function Get-WatcherStatus {
    $proc = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
        Where-Object { $_.CommandLine -like "*ripchamp_tools.ps1*-Mode Watch*" -and $_.ProcessId -ne $PID }
    if ($proc) {
        Write-Host "Watcher is running (PID $($proc.ProcessId))."
    } else {
        Write-Host "Watcher is NOT running."
    }

    try {
        $status = Invoke-RestMethod -Uri "http://127.0.0.1:$QueuePort/status.json" -TimeoutSec 2 -ErrorAction Stop
        Write-Host "Queue server is running at http://127.0.0.1:$QueuePort/ -- $($status.pending.Count) pending, $($status.active.Count) processing."
    } catch {
        Write-Host "Queue server is NOT running."
    }
}

function Add-DiscordChannel {
    $name = Read-Host "Short name for this channel (e.g. highlights, clips)"
    if (-not $name) { Write-Host "No name entered -- nothing saved."; return }
    $url = Read-Host "Paste the Discord webhook URL for that channel"
    if (-not $url) { Write-Host "No URL entered -- nothing saved."; return }

    Add-Content -Path (Join-Path $ScriptDir "discord_webhooks.txt") -Value "$name=$url"
    Write-Host "Saved '$name'."
}

function Install-YoutubeAuth {
    Write-Host "Installing required packages..."
    pip install google-api-python-client google-auth-oauthlib

    Write-Host ""
    Write-Host "Starting YouTube authorization -- a browser window will open..."
    python (Join-Path $ScriptDir "ripchamp.py") --youtube-auth-only
}

switch ($Mode) {
    "Watch"             { Invoke-Watch }
    "InstallTask"       { Install-WatcherTask }
    "UninstallTask"     { Uninstall-WatcherTask }
    "DisableTask"       { Disable-WatcherTask }
    "Status"            { Get-WatcherStatus }
    "AddDiscordChannel" { Add-DiscordChannel }
    "SetupYoutube"      { Install-YoutubeAuth }
}