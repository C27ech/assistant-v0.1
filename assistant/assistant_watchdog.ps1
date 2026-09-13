# assistant_watchdog.ps1
# Watchdog for the Assistant AI process: auto-restart on crash.
#
# INVARIANT: this script NEVER kills any process (no Stop-Process, no taskkill anywhere).
# Process identity comes from the PID registry (runtime\assistant.pid.json) written by
# main.py, NOT from command-line text matching. Rationale: Win32_Process does not expose
# the working directory, so `python main.py open-wechat` (plugin child, cwd=controller_v2) is
# textually indistinguishable from the real entrypoint. Guessing identity by regex once
# made this watchdog force-kill plugin children (their exit code was 0xFFFFFFFF).
#
# Run: powershell -NoProfile -ExecutionPolicy Bypass -File assistant_watchdog.ps1

$ErrorActionPreference = 'SilentlyContinue'

$assistantDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$logFile = Join-Path $assistantDir 'watchdog.log'
$pidRecordFile = Join-Path $assistantDir 'runtime\assistant.pid.json'
$heartbeatFile = Join-Path $assistantDir 'runtime\assistant.heartbeat'
$heartbeatTimeoutSec = 120   # heartbeat file older than this => assistant is stuck
$creationSkewSec = 5         # max allowed diff between record started_at and process CreationDate
# Liveness-only fallback pattern, used ONLY when the registry is missing/unparsable.
# `--channel qq` is the discriminator: the desktop plugin children do not accept --channel.
# It is never used to decide on a kill -- this script has no kill path at all.
$fallbackProcPattern = '(?i)main\.py["\s]+--channel\s+qq\b'
$checkInterval = 15   # seconds between checks
$startupWait = 15     # seconds to wait after starting the assistant
$maxRestarts = 100    # max restarts per watchdog run (avoid infinite loop)

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $logFile -Value "$ts $Message" -Encoding UTF8
}

function ConvertTo-LocalDateTime {
    # Three input shapes must all land on the same local wall-clock value:
    #   - [datetime] from Get-CimInstance Win32_Process.CreationDate (Kind=Local)
    #   - ISO8601 string from the PID record (e.g. 2026-09-11T14:08:56.699001+08:00)
    #   - DMTF string from Get-WmiObject (yyyyMMddHHmmss.ffffff+UUU), whose wall-clock
    #     part is already local time and whose offset is in MINUTES.
    # ManagementDateTimeConverter must never be used on ISO8601 values (it throws).
    param($Value)
    if ($null -eq $Value) { return $null }
    if ($Value -is [datetime]) {
        $dt = [datetime]$Value
        if ($dt.Kind -eq [System.DateTimeKind]::Utc) { return $dt.ToLocalTime() }
        return $dt
    }
    $text = [string]$Value
    if ($text -match '^\d{14}(\.\d+)?([+-]\d{3})?$') {
        return [System.Management.ManagementDateTimeConverter]::ToDateTime($text)
    }
    $parsed = [datetime]::Parse(
        $text,
        [System.Globalization.CultureInfo]::InvariantCulture,
        [System.Globalization.DateTimeStyles]::RoundtripKind)
    if ($parsed.Kind -eq [System.DateTimeKind]::Utc) { return $parsed.ToLocalTime() }
    return $parsed
}

function Get-AssistantRecord {
    # Read the PID registry. Missing / empty / unparsable JSON -> $null (= "no registry").
    if (-not (Test-Path -LiteralPath $pidRecordFile -PathType Leaf)) { return $null }
    try {
        $raw = Get-Content -LiteralPath $pidRecordFile -Raw -Encoding UTF8
        if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
        return ($raw | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Get-AssistantProcFallback {
    # FALLBACK, liveness only (used when the registry is missing / unparsable).
    # Strict pattern + python.exe absolute path: the plugin children
    # (`python main.py open-wechat`) never match, so they can never be mistaken for the main
    # process. The result is only ever turned into "alive / not alive" -- the watchdog has
    # no code path that kills anything.
    return @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object {
            ($_.ExecutablePath -like '*python.exe') -and
            ([string]$_.CommandLine -match $fallbackProcPattern)
        })
}

function Get-AssistantFallbackStatus {
    param([string]$Reason)
    $found = Get-AssistantProcFallback
    if ($found.Count -ge 1) {
        $pids = ($found | Select-Object -ExpandProperty ProcessId) -join ', '
        return @{ Alive = $true; Reason = "fallback regex matched PID $pids ($Reason)" }
    }
    return @{ Alive = $false; Reason = "no registry ($Reason) and fallback regex matched nothing" }
}

function Get-AssistantStatus {
    # Read-only liveness probe driven by the registry (never kills anything).
    # Returns @{ Alive = <bool>; Reason = <string> }.
    $rec = Get-AssistantRecord
    if ($null -eq $rec) {
        return (Get-AssistantFallbackStatus 'pid record missing or invalid')
    }

    $pidValue = 0
    if (-not [int]::TryParse([string]$rec.pid, [ref]$pidValue) -or $pidValue -le 0) {
        return (Get-AssistantFallbackStatus 'pid record has no usable pid')
    }

    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue"
    if ($null -eq $proc) {
        return @{ Alive = $false; Reason = "pid $pidValue not running" }
    }

    # Check 1: ExecutablePath must equal the exe recorded by the assistant.
    if ($rec.exe -and $proc.ExecutablePath -and ($proc.ExecutablePath -ne $rec.exe)) {
        return @{ Alive = $false; Reason = "pid $pidValue exe mismatch" }
    }

    # Check 2: CreationDate within <=5s of the recorded started_at (defeats PID reuse).
    try {
        $started = ConvertTo-LocalDateTime $rec.started_at
        $created = ConvertTo-LocalDateTime $proc.CreationDate
        if (($null -eq $started) -or ($null -eq $created)) {
            return @{ Alive = $false; Reason = 'pid record started_at unparsable' }
        }
        if ([math]::Abs(($created - $started).TotalSeconds) -gt $creationSkewSec) {
            return @{ Alive = $false; Reason = "pid $pidValue start time mismatch (recycled pid)" }
        }
    } catch {
        return @{ Alive = $false; Reason = 'pid record started_at unparsable' }
    }

    # Check 3: command line must still be the assistant entrypoint.
    $cmd = [string]$proc.CommandLine
    if (($cmd -notlike '*main.py*') -or ($cmd -notlike '*--channel*')) {
        return @{ Alive = $false; Reason = "pid $pidValue is not the assistant entrypoint" }
    }

    # Check 4: heartbeat file mtime older than 120s => the main process is stuck.
    if (Test-Path -LiteralPath $heartbeatFile -PathType Leaf) {
        $hb = Get-Item -LiteralPath $heartbeatFile
        $age = (New-TimeSpan -Start $hb.LastWriteTime -End (Get-Date)).TotalSeconds
        if ($age -gt $heartbeatTimeoutSec) {
            return @{ Alive = $false; Reason = "heartbeat stale ($([int]$age)s > ${heartbeatTimeoutSec}s)" }
        }
    }

    return @{ Alive = $true; Reason = "pid $pidValue alive" }
}

function Write-DuplicateAdvisory {
    # ADVISORY ONLY -- the result of this function never influences any decision and it
    # never kills anything. Identity is decided by the registry (see Get-AssistantStatus);
    # this is merely one warning line so stray *main-entrypoint* processes stay visible.
    # Plugin children (`python main.py open-wechat`) do not match, so it produces no log spam.
    $cands = Get-AssistantProcFallback
    if ($cands.Count -gt 1) {
        $pids = ($cands | Select-Object -ExpandProperty ProcessId) -join ', '
        Write-Log "[WARN] $($cands.Count) python processes look like the main entrypoint (PIDs: $pids). Watchdog does NOT kill any process."
    }
}

function Start-Assistant {
    $out = Join-Path $assistantDir 'assistant_run.log'
    $err = Join-Path $assistantDir 'assistant_err.log'
    Start-Process -FilePath 'python' -ArgumentList (Join-Path $assistantDir 'main.py'), '--channel', 'qq' `
        -WorkingDirectory $assistantDir `
        -RedirectStandardOutput $out -RedirectStandardError $err `
        -WindowStyle Hidden
}

function Test-NapCat {
    $c = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -eq 3001 }
    return [bool]$c
}

Write-Log '===== watchdog started ====='
$restarts = 0
$napcatWasUp = $null

while ($true) {
    # Track NapCat (OneBot 3001) status; only log on change (assistant depends on it,
    # but we do NOT auto-restart NapCat because it needs admin/UAC).
    $napcatUp = Test-NapCat
    if ($napcatUp -ne $napcatWasUp) {
        if ($napcatUp) {
            Write-Log 'OneBot 3001 is up (NapCat/QQ OK).'
        } else {
            Write-Log '[WARN] OneBot 3001 is DOWN (NapCat/QQ not running). Assistant cannot connect to QQ channel.'
        }
        $napcatWasUp = $napcatUp
    }

    # Advisory log only (never a kill, never a restart trigger).
    Write-DuplicateAdvisory

    # Identity comes from the registry, never from command-line guessing.
    # There is exactly one decision branch below: restart when the owner is not alive.
    $status = Get-AssistantStatus

    if (-not $status.Alive) {
        if ($restarts -ge $maxRestarts) {
            Write-Log "Reached max restarts ($maxRestarts), watchdog exiting."
            break
        }
        $restarts++
        Write-Log "Assistant down ($($status.Reason)). Starting (attempt $restarts)..."
        Start-Assistant
        Start-Sleep -Seconds $startupWait
    }

    Start-Sleep -Seconds $checkInterval
}
