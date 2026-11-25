#Requires -Version 5.1
<#
.SYNOPSIS
  One-command release: bump version, commit, tag, push, wait for GitHub Actions.

.EXAMPLE
  .\packaging\deploy-exe.cmd                  # patch bump, wait for the release
  .\packaging\deploy-exe.cmd -Bump            # patch bump (same as above)
  .\packaging\deploy-exe.cmd -Bump minor      # or -Minor
  .\packaging\deploy-exe.cmd -Major
  .\packaging\deploy-exe.cmd -Version v1.5.0 -Build
  .\packaging\deploy-exe.cmd -DryRun
#>
[CmdletBinding()]
param(
    [string]$Version = '',
    [switch]$Bump,
    [Parameter(Position = 0)]
    [ValidateSet('patch', 'minor', 'major')]
    [string]$BumpType = 'patch',
    [switch]$Minor,
    [switch]$Major,
    [switch]$Build,
    [switch]$SkipWait,
    [switch]$DryRun,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Fail([string]$message) {
    Write-Host "ERROR: $message" -ForegroundColor Red
    exit 1
}

function Get-RepoSlug {
    $remote = (& git remote get-url origin 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $remote) { return $null }
    if ($remote -match 'github\.com[:/](?<owner>[^/]+)/(?<repo>[^/]+?)(?:\.git)?$') {
        return "$($Matches.owner)/$($Matches.repo)"
    }
    return $null
}

if (-not (Test-Path (Join-Path $Root '.git'))) { Fail 'Not a git repository.' }
$branch = (& git rev-parse --abbrev-ref HEAD).Trim()
$repo = Get-RepoSlug
$hasGh = [bool](Get-Command gh -ErrorAction SilentlyContinue)

# ---- version -------------------------------------------------------------
if ($Minor -and $Major) { Fail 'Use either -Minor or -Major, not both.' }
if (($Minor -or $Major) -and $BumpType -ne 'patch') {
    Fail 'Use either -Minor/-Major or -Bump <patch|minor|major>, not both.'
}
$bumpKind = if ($Major) { 'major' } elseif ($Minor) { 'minor' } else { $BumpType }

if ($Version) {
    $v = $Version.Trim()
    if ($v -notmatch '^v?\d+\.\d+\.\d+$') { Fail "Version must look like v1.2.3 (got '$Version')." }
    if ($v -notmatch '^v') { $v = "v$v" }
} else {
    # Do not use `git describe`: it fails when the newest tag is not an
    # ancestor of HEAD (e.g. after a history rewrite), so pick the highest
    # semver tag instead.
    $tags = @(& git tag --list | Where-Object { $_ -match '^v\d+\.\d+\.\d+$' })
    if ($tags.Count -eq 0) {
        $v = 'v0.1.0'
    } else {
        $last = ($tags | Sort-Object { [version]($_ -replace '^v', '') } | Select-Object -Last 1)
        if ($last -notmatch '^v(\d+)\.(\d+)\.(\d+)$') { Fail "Latest tag '$last' is not semver." }
        $vMajor = [int]$Matches[1]; $vMinor = [int]$Matches[2]; $vPatch = [int]$Matches[3]
        switch ($bumpKind) {
            'major' { $vMajor++; $vMinor = 0; $vPatch = 0 }
            'minor' { $vMinor++; $vPatch = 0 }
            default { $vPatch++ }
        }
        $v = "v$vMajor.$vMinor.$vPatch"
    }
}

& git rev-parse -q --verify "refs/tags/$v" 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    Fail "Tag $v already exists. Use -Version vX.Y.Z, -Bump minor|major, or delete the tag first."
}

# ---- working tree ---------------------------------------------------------
$status = @(& git status --porcelain)
$dirty = $status.Count -gt 0

$bumpLabel = if ($Version) { 'explicit' } else { "$bumpKind bump" }
Write-Host "Branch:  $branch" -ForegroundColor Cyan
Write-Host "Version: $v ($bumpLabel)" -ForegroundColor Cyan
if ($dirty) {
    Write-Host 'Changes to commit:' -ForegroundColor Yellow
    $status | ForEach-Object { Write-Host "  $_" }
}

if ($DryRun) {
    Write-Host '[dry run] no changes will be made' -ForegroundColor Yellow
    if ($dirty) { Write-Host "would run: git add -A; git commit -m 'release: $v'" }
    Write-Host "would run: git tag $v; git push origin HEAD; git push origin $v"
    if ($Build) { Write-Host "would run: packaging\build.ps1 -Sfx -Version $v" }
    if ($hasGh -and -not $SkipWait) { Write-Host "would wait for the build-windows workflow" }
    return
}

if ($dirty -and -not $Yes) {
    $answer = Read-Host 'Commit all changes and start the release? [y/N]'
    if ($answer -notin @('y', 'Y', 'yes', 'Yes')) { Fail 'Aborted.' }
}

# ---- optional local build -------------------------------------------------
if ($Build) {
    Write-Host 'Local build before publishing...' -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot 'build.ps1') -Sfx -Version $v
    if ($LASTEXITCODE -ne 0) { Fail 'Local build failed; nothing was pushed.' }
}

# ---- commit, tag, push ----------------------------------------------------
if ($dirty) {
    & git add -A
    & git commit -m "release: $v"
    if ($LASTEXITCODE -ne 0) { Fail 'git commit failed.' }
} else {
    Write-Host 'No changes to commit; tagging the current HEAD.' -ForegroundColor Yellow
}

& git tag $v
if ($LASTEXITCODE -ne 0) { Fail "git tag $v failed." }

& git push origin HEAD
if ($LASTEXITCODE -ne 0) {
    & git tag -d $v | Out-Null
    Fail 'git push failed; the local tag was removed.'
}

& git push origin $v
if ($LASTEXITCODE -ne 0) {
    Fail "Pushing the tag failed. Clean up with: git tag -d $v"
}

Write-Host "Pushed $v." -ForegroundColor Green

# ---- wait for CI ----------------------------------------------------------
$actionsUrl = if ($repo) { "https://github.com/$repo/actions/workflows/build-windows.yml" } else { $null }
if ($actionsUrl) { Write-Host "Actions: $actionsUrl" }

if ($SkipWait -or -not $hasGh) {
    if (-not $hasGh) { Write-Host 'gh CLI not found - check the Actions page for progress.' -ForegroundColor Yellow }
    return
}

Write-Host 'Waiting for the build-windows workflow...' -ForegroundColor Cyan
$started = (Get-Date).AddSeconds(-5)
$runId = $null
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 3
    try {
        $runs = & gh run list --workflow build-windows --limit 10 --json databaseId,createdAt 2>$null | ConvertFrom-Json
    } catch { continue }
    $run = $runs | Where-Object {
        $_.createdAt -and ([datetime]$_.createdAt) -ge $started
    } | Select-Object -First 1
    if ($run) { $runId = $run.databaseId; break }
}

if (-not $runId) {
    Write-Host 'Workflow run not found yet - open the Actions page.' -ForegroundColor Yellow
    return
}

& gh run watch $runId --exit-status
if ($LASTEXITCODE -ne 0) {
    Write-Host "Workflow failed. Logs: gh run view $runId --log-failed" -ForegroundColor Red
    exit 1
}

Write-Host 'Release published.' -ForegroundColor Green
if ($repo) { Write-Host "https://github.com/$repo/releases/tag/$v" }
& gh release view $v
