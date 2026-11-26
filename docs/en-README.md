# Power Peasant — Power Queen / Redodo / LiTime battery monitor

Unofficial monitor for Power Queen, Redodo and compatible LiTime LiFePO4 SMART
batteries. It shows per-cell voltages — the data the official app parses but never
displays — plus current, power, SOC, temperature, charge/discharge time, groups
and charts.

The monitor talks to the BMS directly over Bluetooth LE and only sends the `Inquire`
request (`0x13`); no control commands are used.

![Dashboard](images/dashboard.jpg)

## Features

- per-cell voltages with the highest/lowest cell highlighted, delta and balancing state;
- live voltage, current, power, SOC, SOH, temperatures and time to empty/full;
- groups of batteries (parallel or series) with aggregated values and capacity;
- charts in five styles (line, area, step, bars, dots) backed by CSV history;
  windows 5m–2y plus All (measured back from the newest record, downsampled with
  a step label); drag to zoom into any range, Ctrl+wheel to zoom around the cursor,
  Shift+drag to pan, double-click or ↺ to reset; a zoom that reaches the newest
  sample follows incoming data, older ranges stay fixed; gaps in the log are
  drawn as gaps — no extrapolated points;
- scan list with discovery history, manual add/remove and a batteries-only filter;
- settings in `data/config.json` (created on first start) or via the ⚙ dialog;
- Bluetooth status banner (off / no adapter) and an About dialog with logo, version
  and sponsor links from `.github/FUNDING.yml`;
- theme switcher: auto (follows the OS), dark or light;
- update check on start (cached for 24 h) and a manual check in About;
  an available release opens its GitHub page;
- UI in Ukrainian, English and German (English by default); no phone or cloud required.

## Supported batteries

Verified on real hardware:

- Power Queen 12.8 V 100 Ah (`P-12100BNNA70`);
- Power Queen 12 V 140 Ah;
- Power Queen 24 V / 25.6 V 100 Ah;
- Redodo 12.8 V 280 Ah (`R-12280BNN200`);
- LiTime 12.8 V 100 Ah;
- LiTime 24 V 50 Ah;
- LiTime 12 V and 36 V.

Likely compatible, but not tested yet:

- other capacities of Power Queen, Redodo and LiTime on the same BMS;
- Starry Sea batteries with `S-…` / `SS-…` names;
- 36 V and 48 V packs.

Batteries from other brands may use a different Bluetooth protocol and are not
supported.

Protocol details and the compatibility rules are in
[protocol.md](protocol.md).

## Requirements

- Python **3.10+** (tested with 3.13)
- Bluetooth LE adapter (Windows 10/11, Linux with BlueZ)
- Windows binaries target **Windows 10/11 x64** (Windows 7 is not supported)
- `pip install -r requirements.txt` (`bleak`, `pywebview`)

### Linux prerequisites

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip bluez
```

## Install

```bash
git clone https://github.com/moleculafree/power-peasant
cd power-peasant
python3 -m venv .venv
```

Activate the environment:

```powershell
# Windows
.venv\Scripts\activate
```

```bash
# Linux / macOS
source .venv/bin/activate
```

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## Run and stop

### Windows (PowerShell)

```powershell
.\scripts\run.ps1 start            # background, prints the URL
.\scripts\run.ps1 start -Port 8800
.\scripts\run.ps1 start -BindHost 0.0.0.0   # reachable from LAN (no auth!)
.\scripts\run.ps1 status
.\scripts\run.ps1 logs -Follow
.\scripts\run.ps1 restart
.\scripts\run.ps1 stop
.\scripts\run.ps1 foreground       # runs in this console, Ctrl+C to stop
```

If PowerShell blocks scripts, use the batch wrapper:

```powershell
.\scripts\run.cmd start
.\scripts\run.cmd status
.\scripts\run.cmd stop
```

### Linux / macOS

```bash
chmod +x scripts/run.sh
./scripts/run.sh start
./scripts/run.sh start --port 8800
./scripts/run.sh start --host 0.0.0.0     # reachable from LAN (no auth!)
./scripts/run.sh status
./scripts/run.sh logs --follow
./scripts/run.sh stop
./scripts/run.sh foreground
```

### Direct run

```bash
python scripts/pq_monitor.py --port 8791
python scripts/pq_monitor.py --window          # embedded window
python scripts/pq_monitor.py --host 0.0.0.0    # LAN access (no auth)
```

Open **http://127.0.0.1:8791/**

> The server listens on `127.0.0.1` only by default. With `--host 0.0.0.0`
> (or `-BindHost 0.0.0.0` on Windows) it is reachable from other machines —
> there is **no authentication**, so use it only on a trusted network.

## What it shows

- pack voltage, current, power, SOC;
- estimated time to empty/full — a single value based on the absolute current;
- per-cell voltages (mV and V), delta max−min, balancing state;
- the highest cell is green, the lowest is orange, a critically low cell (<3.05 V) is red;
- for groups: pack spread between members;
- remaining/full capacity, SOH, cycles, “total discharged” Ah (a lifetime BMS
  counter — Ah delivered since manufacture; it does not change while charging);
- cell and MOSFET temperatures, BMS protection/fault flags.

## Scan and devices

The scan dialog shows the latest radio scan (every 20 s): name, MAC, RSSI, BMS flag,
and a history of devices seen earlier. From there you can connect a new battery
immediately, clear the history, or enable the **batteries only** filter. The **Scan**
button (in the scan dialog and in **＋ Battery**) runs a radio scan right away instead of
waiting for the background cycle; devices from the history can be added even when they
are not in range at the moment.

Known batteries can be removed from monitoring with **✕ Remove** on their page. The name
is stored in `data/ignored.json`, so auto-discovery will not add it again; CSV history is
kept. Return it via the scan list or the **＋ Battery** button.

## Groups

- the name is free text (**✎ Edit**);
- capacity is added automatically from the connected members (BMS FCC), and updated when
  a battery disappears (`100 / 201 Ah`);
- parallel: voltage is averaged, current and capacity are summed;
- series: voltage is summed, current/capacity follow the weakest battery;
- group SOC is capacity-weighted: `Σ remaining / Σ FCC`.

## Settings

`data/config.json` can be edited manually or with the ⚙ button (changes apply
immediately; a manual edit needs a page reload). The file is created on the first
start; `assets/config.json` is only the shipped template.

| Key | Meaning |
|---|---|
| `ui.colors.cellMax` / `cellMin` / `cellNormal` / `cellCritical` | cell colors |
| `ui.colors.chartPalette` | chart line colors |
| `ui.colors.chartNegative` | negative current color (bars go down from zero; shared by both themes) |
| `ui.cells.scaleMinV` / `scaleMaxV` | cell bar scale |
| `ui.cells.criticalMv` | critically low threshold, mV |
| `ui.thresholds.cellDeltaOkMv` / `cellDeltaWarnMv` | cell delta thresholds |
| `ui.thresholds.packSpreadOkMv` / `packSpreadBadMv` | group pack-spread thresholds |
| `ui.chart.defaultWindowSec` | default chart window, s (0 = all) |
| `ui.view.metric` / `chartStyle` / `windowSec` / `selected` | last chart and filter state (saved automatically) |
| `ui.scan.onlyBatteries` | “show only batteries” in the scan dialog |
| `server.pollIntervalSec` | BLE poll interval (after restart) |
| `server.logIntervalSec` | CSV row interval (after restart) |
| `server.historyHours` | history preloaded on start |
| `ui.about.repo` | GitHub link in the About dialog |
| `ui.about.donate` | extra donation link (FUNDING.yml links are shown anyway) |

Invalid values fall back to defaults; colors must be `#rgb` / `#rrggbb`.
The selected battery, chart tab, style and window are written to `ui.view` right
after each change and restored on restart; every write is verified by reading the
file back, and the panel shows “Saved” or “Could not save”.
CLI options (`--interval`, `--log-interval`, `--history-hours`) override `server.*`.

## Structure

```
scripts/pq_monitor.py    # BLE polling + HTTP server
assets/ui/               # dashboard (HTML/JS/CSS), served from disk
assets/config.json       # settings template (runtime copy: data/config.json)
data/                    # CSV history, config.json, groups, scan history, logs,
                         # WebView2 profile (gitignored)
packaging/               # Windows build: spec, icon, build.ps1, BUILD.md
docs/                    # documentation, screenshots
tests/                   # unittest suite: protocol, config, groups, FUNDING.yml
```

## Tests

```bash
python -m unittest discover -s tests
```

They cover protocol frames, config merging/clamping, group aggregation, funding
parsing and version metadata.

## Windows app

Prebuilt Windows builds are on the
[Releases](https://github.com/moleculafree/power-peasant/releases) page:

- `PowerPeasant-<version>-win64-setup.exe` — installer: defaults to
  `C:\Program Files\PowerPeasant`, creates Start-menu and desktop shortcuts and an
  uninstaller (asks for administrator rights);
- `PowerPeasant-<version>-win64.exe` — portable self-extracting archive, shipped
  instead of the installer only when the build machine had no NSIS: run it, choose
  any folder (a USB stick works), then start `PowerPeasant.exe` inside.

Building from source: [packaging/BUILD.md](../packaging/BUILD.md). The installer keeps
everything in its folder: history, `config.json` and the WebView2 cache live in
`<install folder>\data` (that subfolder is made writable during installation, even
under `C:\Program Files`). Only if the app runs from a read-only folder without that
permission does it fall back to `%LOCALAPPDATA%\PowerPeasant`.


## How it works

- PID `data/monitor.pid`; logs `data/monitor.log` (rotated to `.prev` on Windows starts);
- scripts stop only `pq_monitor.py` processes;
- one BLE connection per battery: the phone app cannot be connected at the same time;
- if a battery stops advertising, the monitor still retries a direct connection to
  its MAC address; each attempt and the full exception are written to
  `data/monitor.log`, and the reason is shown in the status line tooltip;
- the newest received frame of every battery is kept in `data/last_values.json`, so
  after a restart an offline battery keeps its card with the last values marked as
  “archive” instead of an empty dashboard;
- if `8791` is busy with a different kind of instance (source vs packaged), the app
  starts on the next free port instead of attaching to it; same-kind instances share
  one window;
- on a cold start the splash stays until the first radio scan and the first live
  frame (at most 30 s; a page reload always shows the dashboard at once), and a
  "No connection" dialog appears if the server stops answering for 10 s;
- history lives in monthly `data/<name>-YYYY-MM.csv` files (new month = new file);
  older single-file `data/<name>.csv` logs are imported once on first start and
  renamed to `*.csv.imported`; the last CSV column `balance` lists balancing cells
  (e.g. `1 3`).

## More

- [BLE protocol reference](protocol.md)
- [Documentation index](README.md)
- [Main README](../README.md)
