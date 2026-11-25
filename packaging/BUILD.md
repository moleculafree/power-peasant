# Building Power Peasant for Windows

Maintainer notes: end users only need the prebuilt portable exe from
[GitHub Releases](https://github.com/moleculafree/power-peasant/releases).

## Requirements

- Python 3.10+ (CI uses 3.13)
- build dependencies: `pip install -r requirements.txt -r packaging/requirements-build.txt`
- 7-Zip with the `7z.sfx` module — only needed for `-Sfx` (preinstalled on GitHub runners)

## Local build

```powershell
pip install -r requirements.txt -r packaging/requirements-build.txt
python -m unittest discover -s tests           # optional pre-flight check
.\packaging\build.ps1                          # icon + exe + portable folder
.\packaging\build.ps1 -Sfx -Version v1.0.0     # also a self-extracting .exe
```

If PowerShell blocks the script (`running scripts is disabled on this system`):

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Sfx -Version v1.2.3
```

Output:

- `dist\PowerPeasant\` — app folder (`PowerPeasant.exe`, `_internal`, `assets`, `data`);
- with `-Sfx` — `dist\release\PowerPeasant-<version>-win64.exe` (7-Zip SFX that asks for
  the target folder when run).

Version rules:

| Input | exe version | SFX name |
|---|---|---|
| `-Version v1.2.3` | `1.2.3` | `PowerPeasant-v1.2.3-win64.exe` |
| `-Version dev-13` | `0.0.13` | `PowerPeasant-dev-13-win64.exe` |
| no `-Version`, nearest tag `v1.2.3` (even with commits after it) | `1.2.3` (via `git describe`) | `PowerPeasant-v1.2.3-win64.exe` |
| no `-Version`, no tags | `0.0.0` | `PowerPeasant-dev-win64.exe` |

Other details:

- the icon is regenerated from `packaging/app-icon.png` into `packaging/app.ico`;
- `packaging/PowerPeasant.spec` is the single build configuration (PyInstaller);
- `packaging/version_info.txt` is a template: the build writes a version-specific copy to
  `build/version_info.txt`, so the template does not need editing for releases;
- `build.ps1` also copies `assets` and `README.txt`, and creates `data\`;
- `dist/` and `build/` are generated and gitignored; `dist\release` contains only the SFX.

## One-command deploy

```powershell
.\packaging\deploy-exe.cmd                  # patch bump, commit, tag, push, wait for CI
.\packaging\deploy-exe.cmd -Bump            # patch bump (same as no flag)
.\packaging\deploy-exe.cmd -Bump minor      # v1.0.0 -> v1.1.0
.\packaging\deploy-exe.cmd -Minor           # same as above
.\packaging\deploy-exe.cmd -Major           # v1.0.0 -> v2.0.0
.\packaging\deploy-exe.cmd -Version v1.5.0  # explicit version
.\packaging\deploy-exe.cmd -Build           # local build before pushing
.\packaging\deploy-exe.cmd -DryRun          # print the plan, change nothing
.\packaging\deploy-exe.cmd -SkipWait        # do not wait for GitHub Actions
.\packaging\deploy-exe.cmd -Yes             # no confirmation prompt
```

What it does:

- takes the highest semver tag and bumps `patch` by default (no tags → `v0.1.0`);
- works even when the newest tag is not an ancestor of `HEAD` (no `git describe`);
- refuses to reuse an existing tag;
- optionally builds locally (`-Build`) to catch errors before publishing;
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
   - runs `.\packaging\build.ps1 -Sfx -Version <tag>`;
   - packs the portable folder into `PowerPeasant-<tag>-win64.exe`;
   - creates/updates the **Release** and attaches that file;
   - also uploads it as a workflow artifact.

Manual build without a tag: **Actions → build-windows → Run workflow**; download the
artifact `PowerPeasant-win64` (`PowerPeasant-dev-<run>-win64.exe`).

## Notes

- No secrets are needed: the workflow uses the built-in `GITHUB_TOKEN` with
  `permissions: contents: write`.
- CI runners have no Bluetooth, so BLE can only be tested on a real machine.
- The build is unsigned → SmartScreen warning on other PCs.
