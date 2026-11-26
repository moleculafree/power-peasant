# Building Power Peasant for Windows

Maintainer notes: end users only need the prebuilt Windows package from
[GitHub Releases](https://github.com/moleculafree/power-peasant/releases).

## Requirements

- Python 3.10+ (CI uses 3.13)
- build dependencies: `pip install -r requirements.txt -r packaging/requirements-build.txt`
- NSIS (`makensis`) — the normal packaging path. CI installs it with Chocolatey and
  passes the absolute path via the `PP_MAKENSIS` environment variable; locally
  `choco install nsis` (or set `PP_MAKENSIS` to a custom `makensis.exe`)
- 7-Zip with the `7z.sfx` module — only used as the fallback when NSIS is missing
  (preinstalled on GitHub runners)

## Local build

```powershell
pip install -r requirements.txt -r packaging/requirements-build.txt
python -m unittest discover -s tests              # optional pre-flight check
.\packaging\build.ps1                             # icon + exe + portable folder
.\packaging\build.ps1 -Installer -Version v1.0.0  # + installer (or SFX without NSIS)
```

If PowerShell blocks the script (`running scripts is disabled on this system`):

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Installer -Version v1.2.3
```

Output:

- `dist\PowerPeasant\` — app folder (`PowerPeasant.exe`, `_internal`, `assets`, `data`);
- with `-Installer` — `dist\release\PowerPeasant-<version>-win64-setup.exe` (NSIS
  installer, default folder `C:\Program Files\PowerPeasant`, Start-menu/desktop
  shortcuts, uninstaller, admin rights required; close a running instance before
  installing over it);
- with `-Installer` and no NSIS — `dist\release\PowerPeasant-<version>-win64.exe`
  (7-Zip SFX that asks for the target folder when run). The SFX is never built
  next to the installer; it is a fallback only.

Version rules:

| Input | exe version | package name |
|---|---|---|
| `-Version v1.2.3` | `1.2.3` | `PowerPeasant-v1.2.3-win64-setup.exe` |
| `-Version dev-13` | `0.0.13` | `PowerPeasant-dev-13-win64-setup.exe` |
| no `-Version`, nearest tag `v1.2.3` (even with commits after it) | `1.2.3` (via `git describe`) | `PowerPeasant-v1.2.3-win64-setup.exe` |
| no `-Version`, no tags | `0.0.0` | `PowerPeasant-dev-win64-setup.exe` |

Without NSIS the same names end in `-win64.exe` instead of `-win64-setup.exe`.

Other details:

- the icon is regenerated from `packaging/app-icon.png` into `packaging/app.ico`
  (also used by the installer);
- `packaging/PowerPeasant.spec` is the single build configuration (PyInstaller);
- `packaging/installer.nsi` is the NSIS script; `build.ps1` passes the source folder,
  output path, version and icon as defines, so no manual editing is needed;
- everything the app writes (history, `config.json`, `data\webview\<user>` WebView2
  profile) stays in `<install folder>\data`; the installer grants `Users` modify
  rights on that one subfolder with `icacls`, so a normal launch from the shortcut
  works even under `C:\Program Files` — the `%LOCALAPPDATA%\PowerPeasant` fallback is
  only for read-only locations without that permission;
- `assets/config.json` is not shipped in the package: the runtime config starts as
  defaults in `data/config.json` (an existing `assets/config.json` from an older
  build is migrated once);
- the installer does not start the app after installation (an elevated first run
  would write into Program Files); start it from the shortcut;
- the uninstaller asks whether to delete `data\` (history and settings); silent
  uninstalls keep it;
- `SetRegView 64` puts the uninstall entry in the 64-bit Apps & Features list;
- `packaging/version_info.txt` is a template: the build writes a version-specific copy to
  `build/version_info.txt`, so the template does not need editing for releases;
- `build.ps1` also copies `assets` and `README.txt`, and creates `data\`;
- `dist/` and `build/` are generated and gitignored; `dist\release` holds the
  package artifacts.

## One-command deploy

`deploy-exe.cmd` publishes by default: it resolves the next version, commits, tags
and pushes, then CI creates the GitHub release. Flags only adjust that flow — none
of them turns publishing off except `-NoPublish`. A local build makes the **NSIS
installer**; only when NSIS is unavailable does it fall back to the portable
self-extracting exe.

**Modes — pick one:**

| Command | What it does |
|---|---|
| `.\packaging\deploy-exe.cmd` | full release: commit, tag, push, wait for CI |
| `.\packaging\deploy-exe.cmd -NoPublish` | local package build only — git and GitHub untouched |
| `.\packaging\deploy-exe.cmd -DryRun` | print the plan, change nothing (works with either mode) |

`-NoPublish` (alias `-LocalOnly`) resolves the version exactly like a release
(patch bump by default, or `-Version`), runs `build.ps1 -Installer`, then stops. It
implies `-Build`, no longer refuses an existing tag (so `-Version v1.0.8` can be
rebuilt), and packages the working tree **as is**: uncommitted changes end up in
the package because nothing is committed. It labels the result with the version a
release *would* get, e.g. `dist\release\PowerPeasant-v1.0.9-win64-setup.exe` (or
`...-win64.exe` when NSIS was missing). For a plain build without release numbering
use `packaging\build.ps1` directly.

**Modifiers — combine with a mode:**

| Flag | Effect |
|---|---|
| `-Version v1.5.0` | explicit version instead of the automatic bump |
| `-Bump minor` / `-Minor` | minor bump (`v1.0.0` → `v1.1.0`) |
| `-Major` | major bump (`v1.0.0` → `v2.0.0`) |
| `-Build` | build the package locally **before** publishing — the release still happens |
| `-SkipWait` | do not wait for GitHub Actions; the release is still created |
| `-Yes` | no confirmation prompt for uncommitted changes |

`-Bump` without a type is a no-op: `patch` is already the default.

The script carries the same option reference in its comment-based help:
`Get-Help packaging\deploy-exe.ps1 -Detailed` (PowerShell 5.1 needs
`-ExecutionPolicy Bypass` to read scripts).

What release mode does:

- takes the highest semver tag and bumps `patch` by default (no tags → `v0.1.0`);
- works even when the newest tag is not an ancestor of `HEAD` (no `git describe`);
- refuses to reuse an existing tag;
- commits everything as `release: <tag>`, creates the tag, pushes `HEAD` and the tag;
- if a push fails, removes the local tag; if the workflow fails, prints the log command;
- waits for the `build-windows` workflow when `gh` is installed (otherwise prints the
  Actions URL).

## Publish a release to GitHub

1. Commit and push (no manual version bump — the tag sets the exe version):

   ```bash
   git add -A
   git commit -m "release: v1.1.0"
   git push origin main
   ```

2. Create and push a tag (the tag becomes the release name and the exe version):

   ```bash
   git tag v1.1.0
   git push origin v1.1.0
   ```

3. GitHub → **Actions → build-windows** runs automatically:
   - installs dependencies on `windows-latest`;
   - runs `.\packaging\build.ps1 -Installer -Version <tag>`;
   - packs the folder into `PowerPeasant-<tag>-win64-setup.exe` (the SFX
     `PowerPeasant-<tag>-win64.exe` is substituted only if NSIS is missing);
   - creates/updates the **Release** and attaches the package;
   - also uploads it as a workflow artifact.

Manual build without a tag: **Actions → build-windows → Run workflow**; download the
artifact `PowerPeasant-win64` (`PowerPeasant-dev-<run>-win64-setup.exe`).

## Notes

- No secrets are needed: the workflow uses the built-in `GITHUB_TOKEN` with
  `permissions: contents: write`.
- CI runners have no Bluetooth, so BLE can only be tested on a real machine.
- The build is unsigned → SmartScreen warning on other PCs.
