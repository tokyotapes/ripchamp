<#
ripchamp_tools.ps1

All-in-one control script for the ripchamp pipeline. Replaces what used
to be several separate .bat files: the interactive prompt, the folder
watcher, scheduled-task install/uninstall/status for running the watcher
hidden at logon, and one-time setup for Discord and YouTube.

Usage:
  powershell -File ripchamp_tools.ps1 -Mode Prompt -Path "video.mp4"
      Interactive: opens a single browser page (ripchamp_trim_ui.py) with
      scrub sliders + loop preview, video/audio choice, title, upload
      destination, and Discord channel picker, then runs ripchamp.py with
      the choices made there. Falls back to old-style console Read-Host
      prompts only if the picker can't launch. (This is what
      ripchamp_launcher.vbs calls for you -- you normally won't run this
      mode directly.)

  powershell -File ripchamp_tools.ps1 -Mode Watch [-WatchPath "C:\..."]
      Watches WatchPath (and subfolders) for new .mp4 files. Once a file
      finishes writing, it's added to the persistent queue server
      (ripchamp_queue_server.py, http://127.0.0.1:8787/ by default) instead
      of popping up a prompt immediately -- bookmark that page and process
      clips whenever you're ready, so a clip finishing mid-game doesn't
      yank focus away from it. Falls back to the old immediate popup
      (ripchamp_launcher.vbs) only if the queue server can't be reached.
      Leave running -- Ctrl+C to stop.

  powershell -File ripchamp_tools.ps1 -Mode InstallTask [-WatchPath "C:\..."]
      Registers a scheduled task to run -Mode Watch hidden at every logon.

  powershell -File ripchamp_tools.ps1 -Mode UninstallTask
      Removes that scheduled task and stops the watcher and queue server
      if either is running.

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
    [ValidateSet("Prompt", "Watch", "InstallTask", "UninstallTask", "Status", "AddDiscordChannel", "SetupYoutube")]
    [string]$Mode,

    [string]$Path,
    [string]$WatchPath = "C:\Users\evan\Videos\NVIDIA",
    [string]$ScriptDir = $PSScriptRoot
)

$TaskName = "RIPChampWatcher"
$QueuePort = 8787

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

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class RIPChampWin32 {
    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("kernel32.dll")]
    public static extern IntPtr GetConsoleWindow();
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
"@

function Invoke-PromptFallback {
    # Used only if ripchamp_trim_ui.py can't run at all (e.g. python missing).
    param([string]$FilePath)

    $start = Read-Host "Trim start time, blank for none (e.g. 30s or 1:30)"
    $end = Read-Host "Trim end time, blank for none (e.g. 1:30)"

    Write-Host "1. Video"
    Write-Host "2. Audio only (extract mp3, saved locally)"
    $typeChoice = Read-Host "Choice, blank for Video"

    $pyArgs = @($FilePath)
    if ($start) { $pyArgs += @("--start", $start) }
    if ($end) { $pyArgs += @("--end", $end) }

    if ($typeChoice -eq "2") {
        Write-Host "Audio only -- extracting mp3, saving locally."
        $pyArgs += @("--audio-only", "--no-youtube", "--no-discord")
        python (Join-Path $ScriptDir "ripchamp.py") @pyArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Host ""
            Write-Host "ripchamp.py reported an error -- see above." -ForegroundColor Yellow
            Read-Host "Press Enter to close"
        }
        return
    }

    $title = Read-Host "Video title, blank for default"
    if ($title) { $pyArgs += @("--youtube-title", $title) }

    Write-Host "1. Upload"
    Write-Host "2. Local only (just crop, don't upload anywhere)"
    $destChoice = Read-Host "Choice, blank for Upload"

    if ($destChoice -eq "2") {
        Write-Host "Local only -- skipping upload."
        $pyArgs += @("--no-youtube", "--no-discord")
    } else {
        $pyArgs += @("--delete-after-upload")

        # Numbered Discord channel picker, if more than one is configured
        $webhooksFile = Join-Path $ScriptDir "discord_webhooks.txt"
        if (Test-Path $webhooksFile) {
            $names = Get-Content $webhooksFile | Where-Object { $_ -match "=" } | ForEach-Object { ($_ -split "=", 2)[0] }
            if ($names.Count -gt 1) {
                Write-Host "Discord channels available:"
                for ($i = 0; $i -lt $names.Count; $i++) { Write-Host "  $($i + 1). $($names[$i])" }
                $choice = Read-Host "Post to which channel number, blank to skip"
                if ($choice) {
                    $idx = [int]$choice - 1
                    if ($idx -ge 0 -and $idx -lt $names.Count) {
                        $pyArgs += @("--discord-channel", $names[$idx])
                    } else {
                        Write-Host "Not a valid choice -- skipping Discord for this file."
                    }
                }
            }
        }
    }

    python (Join-Path $ScriptDir "ripchamp.py") @pyArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "ripchamp.py reported an error -- see above." -ForegroundColor Yellow
        Read-Host "Press Enter to close"
    }
}

function Invoke-Prompt {
    param([string]$FilePath)

    if (-not $FilePath) {
        Write-Host "Error: no file path given." -ForegroundColor Red
        return
    }

    Write-Host "============================================"
    Write-Host "Processing: $(Split-Path $FilePath -Leaf)"
    Write-Host "============================================"
    Write-Host "Opening picker in your browser..."

    $pickerJson = $null
    try {
        $pickerOutput = & python (Join-Path $ScriptDir "ripchamp_trim_ui.py") $FilePath 2>$null
        $resultLine = $pickerOutput | Where-Object { $_ -like "RESULT:*" } | Select-Object -Last 1
        if ($resultLine) {
            $pickerJson = $resultLine.Substring(7) | ConvertFrom-Json
        }
    } catch {
        $pickerJson = $null
    }

    if (-not $pickerJson) {
        Write-Host "Picker unavailable -- falling back to manual entry." -ForegroundColor Yellow
        Invoke-PromptFallback -FilePath $FilePath
        return
    }

    if ($pickerJson.canceled) {
        Write-Host "Canceled -- aborting."
        return
    }

    $pyArgs = @($FilePath)
    if ($pickerJson.PSObject.Properties.Name -contains "start") { $pyArgs += @("--start", "$($pickerJson.start)") }
    if ($pickerJson.PSObject.Properties.Name -contains "end") { $pyArgs += @("--end", "$($pickerJson.end)") }

    if ($pickerJson.type -eq "audio") {
        Write-Host "Audio only -- extracting mp3, saving locally."
        $pyArgs += @("--audio-only", "--no-youtube", "--no-discord")
        python (Join-Path $ScriptDir "ripchamp.py") @pyArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Host ""
            Write-Host "ripchamp.py reported an error -- see above." -ForegroundColor Yellow
            Read-Host "Press Enter to close"
        }
        return
    }

    if ($pickerJson.PSObject.Properties.Name -contains "title" -and $pickerJson.title) {
        $pyArgs += @("--youtube-title", $pickerJson.title)
    }

    if ($pickerJson.destination -eq "local") {
        Write-Host "Local only -- skipping upload."
        $pyArgs += @("--no-youtube", "--no-discord")
    } else {
        $pyArgs += @("--delete-after-upload")
        if ($pickerJson.PSObject.Properties.Name -contains "discordChannel" -and $pickerJson.discordChannel) {
            $pyArgs += @("--discord-channel", $pickerJson.discordChannel)
        }
    }

    python (Join-Path $ScriptDir "ripchamp.py") @pyArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "ripchamp.py reported an error -- see above." -ForegroundColor Yellow
        Read-Host "Press Enter to close"
    }
}

function Invoke-Watch {
    # Hide this console window directly -- launch-time window-style flags
    # (like -WindowStyle Hidden) aren't always honored depending on what's
    # hosting the console, so hide it explicitly instead of relying on that.
    $consoleHandle = [RIPChampWin32]::GetConsoleWindow()
    if ($consoleHandle -ne [IntPtr]::Zero) {
        [RIPChampWin32]::ShowWindow($consoleHandle, 0) | Out-Null  # SW_HIDE
    }

    if (-not (Test-Path $WatchPath)) {
        Write-Host "Error: watch path not found: $WatchPath" -ForegroundColor Red
        exit 1
    }

    if (Ensure-QueueServer) {
        Write-Host "Queue server ready at http://127.0.0.1:$QueuePort/ -- bookmark it and process clips whenever you're ready."
    } else {
        Write-Host "Queue server unavailable -- clips will fall back to an immediate popup prompt instead." -ForegroundColor Yellow
    }

    $watcher = New-Object System.IO.FileSystemWatcher
    $watcher.Path = $WatchPath
    $watcher.IncludeSubdirectories = $true
    $watcher.Filter = "*.mp4"
    $watcher.NotifyFilter = [System.IO.NotifyFilters]::FileName
    $watcher.EnableRaisingEvents = $true

    $action = {
        $filePath = $Event.SourceEventArgs.FullPath
        $scriptDir = $Event.MessageData.ScriptDir
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
            Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Queue server unreachable -- falling back to immediate prompt for: $filePath" -ForegroundColor Yellow
            $activeWindow = [RIPChampWin32]::GetForegroundWindow()
            Start-Process -FilePath "wscript.exe" -ArgumentList "`"$scriptDir\ripchamp_launcher.vbs`" `"$filePath`""
            Start-Sleep -Milliseconds 800
            [RIPChampWin32]::SetForegroundWindow($activeWindow) | Out-Null
        }
    }

    Register-ObjectEvent -InputObject $watcher -EventName Created -Action $action `
        -MessageData @{ ScriptDir = $ScriptDir; QueuePort = $QueuePort } | Out-Null

    Write-Host "Watching '$WatchPath' and subfolders for new .mp4 files. Press Ctrl+C to stop."
    while ($true) { Start-Sleep -Seconds 1 }
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
    "Prompt"            { Invoke-Prompt -FilePath $Path }
    "Watch"             { Invoke-Watch }
    "InstallTask"       { Install-WatcherTask }
    "UninstallTask"     { Uninstall-WatcherTask }
    "Status"            { Get-WatcherStatus }
    "AddDiscordChannel" { Add-DiscordChannel }
    "SetupYoutube"      { Install-YoutubeAuth }
}