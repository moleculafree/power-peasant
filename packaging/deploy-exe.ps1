<#
.SYNOPSIS
  One-command release: bump version, commit, tag, push, wait for GitHub Actions.

.DESCRIPTION
  By default this always publishes: commit, tag, push, then CI creates the
  GitHub release. Flags only modify that flow; none of them turns it off.
  Use -NoPublish (-LocalOnly) to stop after the local build with git untouched.

  -Build is not a mode: it only adds a local build before publishing.

.EXAMPLE
  .\packaging\deploy-exe.cmd

  Patch bump from the highest tag; commit, tag, push, wait for CI.

.EXAMPLE
  .\packaging\deploy-exe.cmd -Bump minor

  Minor bump (same as -Minor). -Bump alone changes nothing: patch is the default.

.EXAMPLE
  .\packaging\deploy-exe.cmd -Version v1.5.0 -Build

  Explicit version and a local build first; the release still happens.

.EXAMPLE
  .\packaging\deploy-exe.cmd -NoPublish

  Local build of the next version only (installer, or the portable
  self-extracting exe when NSIS is unavailable); git and GitHub are not touched.

.EXAMPLE
  .\packaging\deploy-exe.cmd -DryRun

  Print the plan and exit.

.PARAMETER Version
  Explicit version (v1.2.3). Without it the version is bumped from the highest
  semver tag (patch by default).

.PARAMETER Bump
  Optional prefix for `-Bump <patch|minor|major>`; the type is the positional
  argument. On its own it changes nothing, since patch is the default.

.PARAMETER BumpType
  Bump kind used when -Version is not given: patch (default), minor or major.

.PARAMETER Minor
  Shortcut for -Bump minor.

.PARAMETER Major
  Shortcut for -Bump major.

.PARAMETER Build
  Build the Windows package locally before publishing (installer, or the
  portable self-extracting exe when NSIS is unavailable). Publishing still
  happens.

.PARAMETER NoPublish
  Local build only: resolve the version exactly like a release, run
  build.ps1 -Installer, then stop. No commit, tag, push or release.
  Implies -Build. The exe contains the current working tree, including
  uncommitted changes.

.PARAMETER SkipWait
  Do not wait for the build-windows workflow. The release is still created.

.PARAMETER DryRun
  Print what would happen and exit. Nothing is built, committed or pushed.

.PARAMETER Yes
  Skip the confirmation prompt for uncommitted changes.
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
    [Alias('LocalOnly')]
    [switch]$NoPublish,
    [switch]$SkipWait,
    [switch]$DryRun,
    [switch]$Yes
)

# Below the help block on purpose: #Requires on line 1 hides comment-based help
# (Get-Help) in Windows PowerShell 5.1.
#Requires -Version 5.1

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$ReleaseDir = Join-Path $Root 'dist\release'
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
$tagExists = ($LASTEXITCODE -eq 0)
if ($tagExists -and -not $NoPublish) {
    Fail "Tag $v already exists. Use -Version vX.Y.Z, -Bump minor|major, or delete the tag first."
}

# ---- working tree ---------------------------------------------------------
$status = @(& git status --porcelain)
$dirty = $status.Count -gt 0

$bumpLabel = if ($Version) { 'explicit' } else { "$bumpKind bump" }
$modeLabel = if ($NoPublish) { 'local build only (-NoPublish)' } else { 'release (publish)' }
Write-Host "Branch:  $branch" -ForegroundColor Cyan
Write-Host "Mode:    $modeLabel" -ForegroundColor Cyan
Write-Host "Version: $v ($bumpLabel)" -ForegroundColor Cyan
if ($tagExists) {
    Write-Host "Tag $v already exists; rebuilding it locally, git stays untouched." -ForegroundColor Yellow
}
if ($dirty) {
    if ($NoPublish) {
        Write-Host 'Working tree changes (they go into the local build, nothing is committed):' -ForegroundColor Yellow
    } else {
        Write-Host 'Changes to commit:' -ForegroundColor Yellow
    }
    $status | ForEach-Object { Write-Host "  $_" }
}

if ($DryRun) {
    Write-Host '[dry run] no changes will be made' -ForegroundColor Yellow
    if ($NoPublish) {
        Write-Host "would run: packaging\build.ps1 -Installer -Version $v"
        Write-Host 'would not commit, tag, push or publish'
        return
    }
    if ($dirty) { Write-Host "would run: git add -A; git commit -m 'release: $v'" }
    Write-Host "would run: git tag $v; git push origin HEAD; git push origin $v"
    if ($Build) { Write-Host "would run: packaging\build.ps1 -Installer -Version $v" }
    if ($hasGh -and -not $SkipWait) { Write-Host "would wait for the build-windows workflow" }
    return
}

if ($NoPublish -and $SkipWait) {
    Write-Host '-SkipWait has no effect with -NoPublish: nothing is published.' -ForegroundColor Yellow
}

if ($dirty -and -not $Yes -and -not $NoPublish) {
    $answer = Read-Host 'Commit all changes and start the release? [y/N]'
    if ($answer -notin @('y', 'Y', 'yes', 'Yes')) { Fail 'Aborted.' }
}

# ---- local build (-Build adds it to a release, -NoPublish needs it) -------
if ($Build -or $NoPublish) {
    if ($NoPublish) {
        Write-Host 'Local build only: nothing will be committed, tagged, pushed or published.' -ForegroundColor Cyan
    } else {
        Write-Host 'Local build before publishing...' -ForegroundColor Cyan
    }
    & (Join-Path $PSScriptRoot 'build.ps1') -Installer -Version $v
    if ($LASTEXITCODE -ne 0) { Fail 'Local build failed; nothing was committed, tagged or pushed.' }
}

if ($NoPublish) {
    $setupPath = Join-Path $ReleaseDir "PowerPeasant-$v-win64-setup.exe"
    $sfxPath = Join-Path $ReleaseDir "PowerPeasant-$v-win64.exe"
    Write-Host 'Local build finished. Git and GitHub were not touched.' -ForegroundColor Yellow
    if (Test-Path $setupPath) { Write-Host "Installer: $setupPath" -ForegroundColor Green }
    if (Test-Path $sfxPath) { Write-Host "SFX:       $sfxPath" -ForegroundColor Green }
    return
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
