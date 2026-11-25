#Requires -Version 5.1
<#
.SYNOPSIS
  Build the portable Windows app (icon and version included).

.EXAMPLE
  .\packaging\build.ps1
  .\packaging\build.ps1 -Sfx -Version v1.0.0
#>
[CmdletBinding()]
param(
    [switch]$Sfx,
    [string]$Version = ''
)

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
$DataDir = Join-Path $AppDir 'data'
if (Test-Path $DataDir) { Remove-Item $DataDir -Recurse -Force }
New-Item -ItemType Directory $DataDir -Force | Out-Null

$about = [ordered]@{
    version = $ver
    build   = $displayVersion
    date    = (Get-Date).ToString('yyyy-MM-dd')
}
$about | ConvertTo-Json | Set-Content -Path (Join-Path $AppDir 'assets\version.json') -Encoding ascii

if ($Sfx) {
    $seven = "C:\Program Files\7-Zip\7z.exe"
    if (-not (Test-Path $seven)) { $seven = "C:\Program Files (x86)\7-Zip\7z.exe" }
    if (-not (Test-Path $seven)) {
        $cmd = Get-Command 7z.exe -ErrorAction SilentlyContinue
        if ($cmd) { $seven = $cmd.Source }
    }
    if (-not (Test-Path $seven)) { throw "7-Zip not found. Install it or add 7z.exe to PATH." }
    $module = Join-Path (Split-Path $seven) "7z.sfx"
    if (-not (Test-Path $module)) { throw "7z.sfx not found next to $seven" }

    $name = "PowerPeasant-$displayVersion-win64.exe"
    $outPath = Join-Path $Release $name
    if (Test-Path $outPath) { Remove-Item $outPath -Force }
    $runtime = @(Get-ChildItem $DataDir -Force -ErrorAction SilentlyContinue)
    if ($runtime.Count -gt 0) {
        throw "Refusing to pack runtime data from $DataDir. Stop the app and rebuild."
    }
    Write-Host "Packing SFX (asks for the target folder when run): $outPath" -ForegroundColor Cyan
    Push-Location $Dist
    try {
        & $seven a "-sfx$module" $outPath "PowerPeasant"
        if ($LASTEXITCODE -ne 0) { throw "7-Zip failed (exit code $LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
    if (-not (Test-Path $outPath)) { throw "SFX was not created: $outPath" }
    $mb = [math]::Round((Get-Item $outPath).Length / 1MB, 1)
    Write-Host "Self-extracting exe: $outPath ($mb MB)" -ForegroundColor Green
} else {
    Write-Host ("Portable folder: " + $AppDir) -ForegroundColor Green
}
