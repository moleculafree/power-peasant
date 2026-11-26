# power-peasant

Monitor for LiFePO4 SMART BMS batteries: per-cell voltages, current,
power, SOC, temperature, charge/discharge time, groups and charts. The BMS is read
directly over Bluetooth.

No cloud, No phone, No account. Free.

![Dashboard](docs/images/dashboard.jpg)

**Read in:** [English](docs/en-README.md) · [Українська](docs/uk-README.md)

## Supported batteries

Verified on real hardware:

- **Power Queen** 12.8 V 100 Ah (`P-12100BNNA70`)
- **Power Queen** 12 V 140 Ah
- **Power Queen** 24 V / 25.6 V 100 Ah
- **Redodo** 12.8 V 280 Ah (`R-12280BNN200`)
- **LiTime** 12.8 V 100 Ah
- **LiTime** 24 V 50 Ah
- **LiTime** 12 V and 36 V

Likely compatible, but not tested yet:

- other capacities of Power Queen, Redodo and LiTime on the same BMS;
- **Starry Sea** batteries with `S-…` / `SS-…` names;
- 36 V and 48 V packs.

Batteries from other brands may use a different Bluetooth protocol and are not
supported. The monitor only reads data and never changes battery settings or
switches.

## Windows Installation
Windows users can download the installer
`PowerPeasant-<version>-win64-setup.exe` (defaults to `C:\Program Files\PowerPeasant`)
from [Releases](https://github.com/moleculafree/power-peasant/releases) (Windows
10/11 x64). When a build has no NSIS, the release contains the portable
self-extracting exe instead.


## Quick start from sources (linux/windows)

```bash
git clone https://github.com/moleculafree/power-peasant
cd power-peasant
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/pq_monitor.py
```

Open **http://127.0.0.1:8791/**


## Documentation

- [English manual](docs/en-README.md)
- [Українська інструкція](docs/uk-README.md)
- [Documentation index](docs/README.md)
