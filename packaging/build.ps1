<#
.SYNOPSIS
  Build the portable Windows app (icon and version included).

.EXAMPLE
  .\packaging\build.ps1

  Icon + exe + portable folder, version from `git describe`.

.EXAMPLE
  .\packaging\build.ps1 -Installer -Version v1.0.0

  Same, plus the Windows package: the NSIS installer
  dist\release\PowerPeasant-v1.0.0-win64-setup.exe (default folder
  C:\Program Files\PowerPeasant, requires admin rights). When NSIS is not
  available, the portable self-extracting exe is built instead.

.PARAMETER Installer
  Build the Windows package: the NSIS installer
  dist\release\PowerPeasant-<version>-win64-setup.exe (requires makensis,
  e.g. `choco install nsis`). Without NSIS the portable self-extracting
  dist\release\PowerPeasant-<version>-win64.exe is built instead (requires
  7-Zip with 7z.sfx).

.PARAMETER Version
  Version for the exe and the package names (v1.2.3 or dev-13). Without it the
  version comes from `git describe` (v1.2.3 -> 1.2.3, otherwise dev).
#>
[CmdletBinding()]
param(
    [switch]$Installer,
    [string]$Version = ''
)

# Below the help block on purpose: #Requires on line 1 hides comment-based help
# (Get-Help) in Windows PowerShell 5.1.
#Requires -Version 5.1

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Spec    = Join-Path $PSScriptRoot 'PowerPeasant.spec'
$Dist    = Join-Path $Root 'dist'
$Release = Join-Path $Dist 'release'
$Work    = Join-Path $Root 'build'

New-Item -ItemType Directory $Release -Force | Out-Null
New-Item -ItemType Directory $Work -Force | Out-Null

Write-Host "[1/4] icon from packaging/app-icon.png" -ForegroundColor Cyan
python packaging\make_icon.py

Write-Host "[2/4] version" -ForegroundColor Cyan
$displayVersion = $Version
if (-not $displayVersion) {
    $desc = ''
    try { $desc = ((git describe --tags --always --dirty 2>$null) | Out-String).Trim() } catch {}
    if ($desc -match '^v?(\d+\.\d+\.\d+)') { $displayVersion = "v$($Matches[1])" }
    else { $displayVersion = 'dev' }
}

$ver = '0.0.0'
if ($displayVersion -match '^v?(\d+\.\d+\.\d+)$') { $ver = $Matches[1] }
elseif ($displayVersion -match '^dev-(\d+)$') { $ver = "0.0.$($Matches[1])" }

$tuple = (($ver.Split('.') + @('0')) -join ', ')
$text = Get-Content (Join-Path $PSScriptRoot 'version_info.txt') -Raw
$text = $text -replace 'filevers=\([0-9, ]+\)', "filevers=($tuple)"
$text = $text -replace 'prodvers=\([0-9, ]+\)', "prodvers=($tuple)"
$text = $text -replace "FileVersion', '[^']*'", "FileVersion', '$ver'"
$text = $text -replace "ProductVersion', '[^']*'", "ProductVersion', '$ver'"
$versionFile = Join-Path $Work 'version_info.txt'
Set-Content -Path $versionFile -Value $text -Encoding utf8
$env:PP_VERSION_FILE = $versionFile
Write-Host "      exe version $ver (build $displayVersion)"

Write-Host "[3/4] PyInstaller (packaging/PowerPeasant.spec)" -ForegroundColor Cyan
python -m PyInstaller --noconfirm --clean --distpath $Dist --workpath $Work $Spec

Write-Host "[4/4] assemble portable folder" -ForegroundColor Cyan
$AppDir = Join-Path $Dist 'PowerPeasant'
$AssetsDir = Join-Path $AppDir 'assets'
if (Test-Path $AssetsDir) { Remove-Item $AssetsDir -Recurse -Force }
Copy-Item assets -Destination $AssetsDir -Recurse -Force
$funding = Join-Path $Root '.github\FUNDING.yml'
if (Test-Path $funding) {
    Copy-Item $funding (Join-Path $AppDir 'assets\FUNDING.yml') -Force
}
Copy-Item packaging\README-portable.txt (Join-Path $AppDir 'README.txt') -Force
# config.json is runtime state now (data\config.json); do not ship the dev copy
$cfgCopy = Join-Path $AppDir 'assets\config.json'
if (Test-Path $cfgCopy) { Remove-Item $cfgCopy -Force }
$DataDir = Join-Path $AppDir 'data'
if (Test-Path $DataDir) { Remove-Item $DataDir -Recurse -Force }
New-Item -ItemType Directory $DataDir -Force | Out-Null

$about = [ordered]@{
    version = $ver
    build   = $displayVersion
    date    = (Get-Date).ToString('yyyy-MM-dd')
}
$about | ConvertTo-Json | Set-Content -Path (Join-Path $AppDir 'assets\version.json') -Encoding ascii

$runtime = @(Get-ChildItem $DataDir -Force -ErrorAction SilentlyContinue)
if ($runtime.Count -gt 0) {
    throw "Refusing to pack runtime data from $DataDir. Stop the app and rebuild."
}

function Find-MakeNsis {
    if ($env:PP_MAKENSIS -and (Test-Path $env:PP_MAKENSIS)) { return $env:PP_MAKENSIS }
    $cmd = Get-Command makensis.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @()
    if (${env:ProgramFiles(x86)}) { $candidates += (Join-Path ${env:ProgramFiles(x86)} 'NSIS\makensis.exe') }
    if ($env:ProgramFiles) { $candidates += (Join-Path $env:ProgramFiles 'NSIS\makensis.exe') }
    if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA 'Programs\NSIS\makensis.exe') }
    return $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}

function Find-SevenZip {
    foreach ($p in @("C:\Program Files\7-Zip\7z.exe", "C:\Program Files (x86)\7-Zip\7z.exe")) {
        if (Test-Path $p) { return $p }
    }
    $cmd = Get-Command 7z.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function New-Sfx {
    $seven = Find-SevenZip
    if (-not $seven) { return $null }
    $module = Join-Path (Split-Path $seven) "7z.sfx"
    if (-not (Test-Path $module)) { throw "7z.sfx not found next to $seven" }

    $outPath = Join-Path $Release "PowerPeasant-$displayVersion-win64.exe"
    if (Test-Path $outPath) { Remove-Item $outPath -Force }
    Write-Host "Packing SFX (asks for the target folder when run): $outPath" -ForegroundColor Cyan
    Push-Location $Dist
    try {
        & $seven a "-sfx$module" $outPath "PowerPeasant" | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "7-Zip failed (exit code $LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
    if (-not (Test-Path $outPath)) { throw "SFX was not created: $outPath" }
    $mb = [math]::Round((Get-Item $outPath).Length / 1MB, 1)
    Write-Host "Self-extracting exe: $outPath ($mb MB)" -ForegroundColor Green
    return $outPath
}

function New-Installer([string]$makensis) {
    $setupPath = Join-Path $Release "PowerPeasant-$displayVersion-win64-setup.exe"
    if (Test-Path $setupPath) { Remove-Item $setupPath -Force }
    $nsi = Join-Path $PSScriptRoot 'installer.nsi'
    $icon = Join-Path $PSScriptRoot 'app.ico'
    Write-Host "Building NSIS installer (default folder: %ProgramFiles%\PowerPeasant): $setupPath" -ForegroundColor Cyan
    & $makensis "/DPP_SOURCE=$AppDir" "/DPP_OUTFILE=$setupPath" "/DPP_VERSION=$ver" "/DPP_BUILD=$displayVersion" "/DPP_ICON=$icon" $nsi | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "NSIS failed (exit code $LASTEXITCODE)" }
    if (-not (Test-Path $setupPath)) { throw "Installer was not created: $setupPath" }
    $mb = [math]::Round((Get-Item $setupPath).Length / 1MB, 1)
    Write-Host "Windows installer: $setupPath ($mb MB)" -ForegroundColor Green
    return $setupPath
}

if ($Installer) {
    $makensis = Find-MakeNsis
    if ($makensis) {
        New-Installer $makensis | Out-Null
    } else {
        Write-Host 'NSIS (makensis) not found - falling back to the portable self-extracting exe.' -ForegroundColor Yellow
        if (-not (New-Sfx)) {
            throw 'Neither NSIS (makensis) nor 7-Zip is available. Install NSIS (choco install nsis) or 7-Zip.'
        }
    }
} else {
    Write-Host ("Portable folder: " + $AppDir) -ForegroundColor Green
}
