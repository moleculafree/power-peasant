#Requires -Version 5.1
<#
.SYNOPSIS
  Power Peasant console control for Windows.

.DESCRIPTION
  start       run the monitor in the background (logs + PID in data\)
  stop        stop all monitor processes
  restart     stop + start
  status      PID, port, HTTP health and URL
  logs        tail data\monitor.log (-Follow to keep watching)
  foreground  run in this console, Ctrl+C to stop

.EXAMPLES
  .\scripts\run.ps1 start
  .\scripts\run.ps1 start -Port 8800
  .\scripts\run.ps1 status
  .\scripts\run.ps1 logs -Follow
  .\scripts\run.ps1 stop
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status', 'logs', 'foreground')]
    [string]$Action = 'start',

    [int]$Port = 8791,

    [string]$BindHost = '127.0.0.1',

    [string]$Python = '',

    [switch]$Follow
)

$ErrorActionPreference = 'Stop'

$Root     = Split-Path -Parent $PSScriptRoot
$Entry    = Join-Path $Root 'scripts\pq_monitor.py'
$DataDir  = Join-Path $Root 'data'
$PidFile  = Join-Path $DataDir 'monitor.pid'
$LogFile  = Join-Path $DataDir 'monitor.log'
$ErrFile  = Join-Path $DataDir 'monitor.err'
$BaseUrl  = "http://$(if ($BindHost -in @('0.0.0.0', '::')) { '127.0.0.1' } else { $BindHost }):$Port"
$Url      = "$BaseUrl/"

function Resolve-Python {
    if ($Python) { return $Python }
    $venv = Join-Path $Root '.venv\Scripts\python.exe'
    if (Test-Path $venv) { return $venv }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "Python not found. Install Python 3 or create .venv (python -m venv .venv)."
}

function Get-MonitorProcesses {
    $found = @()
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'pq_monitor\.py' } |
        ForEach-Object {
            $p = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
            if ($p) { $found += $p }
        }
    @($found | Sort-Object Id -Unique)
}

function Test-PortOpen([int]$PortNumber) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect('127.0.0.1', $PortNumber, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(400)) { return $false }
        $client.EndConnect($iar)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-Health {
    try {
        $data = Invoke-RestMethod -Uri "$BaseUrl/api/data" -TimeoutSec 3
        $connected = @($data.devices | Where-Object { $_.connected }).Count
        $total = @($data.devices).Count
        return "HTTP OK - batteries connected: $connected/$total"
    } catch {
        return "no HTTP response"
    }
}

function Wait-Health([int]$Attempts = 30) {
    for ($i = 0; $i -lt $Attempts; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            Invoke-RestMethod -Uri "$BaseUrl/api/config" -TimeoutSec 2 | Out-Null
            return $true
        } catch { }
    }
    return $false
}

function Start-Monitor {
    $existing = Get-MonitorProcesses
    if ($existing.Count -gt 0) {
        Write-Host "Already running (PID $($existing.Id -join ', '))." -ForegroundColor Yellow
        Write-Host "URL: $Url" -ForegroundColor Cyan
        return
    }
    if (Test-PortOpen $Port) {
        throw "Port $Port is already used by another program. Try: .\scripts\run.ps1 start -Port 8800"
    }
    if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir | Out-Null }
    if (Test-Path $LogFile) { Move-Item $LogFile "$LogFile.prev" -Force }
    if (Test-Path $ErrFile) { Move-Item $ErrFile "$ErrFile.prev" -Force }

    $py = Resolve-Python
    $argList = @('-u', $Entry, '--no-browser', '--host', $BindHost, '--port', "$Port")
    $proc = Start-Process -FilePath $py -ArgumentList $argList -WorkingDirectory $Root `
        -RedirectStandardOutput $LogFile -RedirectStandardError $ErrFile `
        -WindowStyle Hidden -PassThru
    Set-Content -Path $PidFile -Value $proc.Id -Encoding ascii

    if (Wait-Health) {
        Write-Host "Started (PID $($proc.Id))." -ForegroundColor Green
        Write-Host "URL: $Url" -ForegroundColor Cyan
        if ($BindHost -in @('0.0.0.0', '::')) {
            Write-Host "Bound to $BindHost - open http://<this-pc-ip>:$Port/ from other devices (no auth!)" -ForegroundColor Yellow
        }
        Write-Host "Log: $LogFile"
    } elseif ($proc.HasExited) {
        Write-Host "Process exited. stderr:" -ForegroundColor Red
        Get-Content $ErrFile -Tail 20 -ErrorAction SilentlyContinue
    } else {
        Write-Host "Started (PID $($proc.Id)), but HTTP is not answering yet." -ForegroundColor Yellow
        Write-Host "URL: $Url" -ForegroundColor Cyan
        Write-Host "Log: $LogFile"
    }
}

function Stop-Monitor {
    $procs = Get-MonitorProcesses
    if ($procs.Count -eq 0) {
        Write-Host "Not running." -ForegroundColor Yellow
        Remove-Item $PidFile -ErrorAction SilentlyContinue
        return
    }
    foreach ($p in $procs) {
        Write-Host "Stopping PID $($p.Id) ..."
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    }
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 300
        if ((Get-MonitorProcesses).Count -eq 0) { break }
    }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
    if ((Get-MonitorProcesses).Count -eq 0) {
        Write-Host "Stopped." -ForegroundColor Green
    } else {
        Write-Host "Some processes did not stop." -ForegroundColor Red
    }
}

function Show-Status {
    $procs = Get-MonitorProcesses
    if ($procs.Count -eq 0) {
        Write-Host "Status: not running" -ForegroundColor Yellow
        Write-Host "URL: $Url (not reachable)"
        return
    }
    Write-Host "Status: running (PID $($procs.Id -join ', '))" -ForegroundColor Green
    Write-Host "Port $Port open: $(Test-PortOpen $Port)"
    Write-Host (Get-Health)
    Write-Host "URL: $Url" -ForegroundColor Cyan
}

function Show-Logs {
    if (-not (Test-Path $LogFile)) {
        Write-Host "No log yet: $LogFile"
        return
    }
    if ($Follow) { Get-Content $LogFile -Tail 40 -Wait }
    else { Get-Content $LogFile -Tail 40 }
    if ((Test-Path $ErrFile) -and (Get-Item $ErrFile).Length -gt 0) {
        Write-Host "`n--- stderr ---" -ForegroundColor DarkYellow
        Get-Content $ErrFile -Tail 20
    }
}

function Start-Foreground {
    if ((Get-MonitorProcesses).Count -gt 0) {
        throw "Already running in the background. Run: .\scripts\run.ps1 stop"
    }
    $py = Resolve-Python
    Write-Host "URL: $Url" -ForegroundColor Cyan
    Write-Host "Ctrl+C to stop.`n"
    & $py -u $Entry --no-browser --host $BindHost --port $Port
}

switch ($Action) {
    'start'      { Start-Monitor }
    'stop'       { Stop-Monitor }
    'restart'    { Stop-Monitor; Start-Sleep -Seconds 1; Start-Monitor }
    'status'     { Show-Status }
    'logs'       { Show-Logs }
    'foreground' { Start-Foreground }
}
