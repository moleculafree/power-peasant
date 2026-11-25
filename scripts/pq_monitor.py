#!/usr/bin/env python3
"""
Power Peasant - Power Queen battery monitor (BLE).
Shows per-cell voltages that the official app hides.
Several batteries at once, groups with aggregated values, live charts and CSV history.

Usage:
    python pq_monitor.py                       # all Power Queen batteries in range
    python pq_monitor.py B02556 B01607         # only these (name fragments)
    python pq_monitor.py --port 8791 --host 0.0.0.0   # LAN access
    python pq_monitor.py --list

Requires: pip install bleak
Protocol: service FFE0, write FFE2, notify FFE1, request "00 00 04 01 13 55 AA 17".
"""

import argparse
import asyncio
import mimetypes
import csv
import json
import math
import os
import re
import socket
import sys
import threading
import time
import uuid
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from urllib.request import urlopen

from bleak import BleakClient, BleakScanner

try:
    from bleak.exc import BleakBluetoothNotAvailableError, BleakBluetoothNotAvailableReason
except ImportError:  # older bleak
    BleakBluetoothNotAvailableError = None
    BleakBluetoothNotAvailableReason = None

CH_NOTIFY = "0000ffe1-0000-1000-8000-00805f9b34fb"
CH_WRITE = "0000ffe2-0000-1000-8000-00805f9b34fb"
FRAME_LEN = 105

HERE = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = HERE.parent if HERE.name.lower() in ("scripts", "src", "bin") else HERE


def _writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("1", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _fallback_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "PowerPeasant"


DATA_DIR = ROOT / "data" if _writable_dir(ROOT / "data") else _fallback_root() / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

GROUPS_FILE = DATA_DIR / "groups.json"
IGNORED_FILE = DATA_DIR / "ignored.json"
SEEN_FILE = DATA_DIR / "scan_history.json"
CONFIG_FILE = ROOT / "assets" / "config.json"
if not _writable_dir(ROOT / "assets"):
    CONFIG_FILE = DATA_DIR / "config.json"

HISTORY_MAX = 4000       # in-memory points per source (~2 h at 2 s)
MAX_POINTS = 700         # max points returned to the chart
LOG_INTERVAL = 10.0      # seconds between CSV rows
LOAD_HOURS = 2.0         # how much CSV history to preload

HISTORY_CACHE = {}       # (path, window) -> (expires_monotonic, payload)
HISTORY_CACHE_LOCK = threading.Lock()

DEVICES = {}             # address -> entry
GROUPS = []              # [{id,name,mode,members,history,logger,last_log,data}]
SCAN = {"ts": 0.0, "items": []}   # everything seen in the last radio scan
BT_STATUS = {"ok": None, "reason": None, "message": None, "checked": 0}
SEEN = {}                # address -> discovery history record
SEEN_MAX = 500
IGNORED = set()          # display names the user removed from monitoring
LOCK = threading.Lock()
POLL_INTERVAL = 2.0
SHOW_ALL = False         # --all: also treat unnamed FFE0 devices as batteries
MAIN_LOOP = None         # asyncio loop, used by the HTTP thread for manual adds

CSV_HEADER = (["epoch", "iso", "pack_v", "current_a", "power_w", "soc", "soh",
               "rem_ah", "fcc_ah", "cell_temp", "mos_temp", "cycles", "discharged_ah"]
              + [f"cell{i}" for i in range(1, 17)] + ["balance"])

DEFAULT_CONFIG = {
    "ui": {
        "language": "en",
        "colors": {
            "cellMax": "#35d07f",
            "cellMin": "#f2a33c",
            "cellNormal": "#58a6ff",
            "cellCritical": "#ef5b5b",
            "chartPalette": ["#58a6ff", "#35d07f", "#f2a33c", "#c792ea",
                             "#ef5b5b", "#4dd0e1", "#f78fb3", "#a3e635"],
        },
        "cells": {"scaleMinV": 3.00, "scaleMaxV": 3.45, "criticalMv": 3050},
        "thresholds": {"cellDeltaOkMv": 10, "cellDeltaWarnMv": 30,
                       "packSpreadOkMv": 60, "packSpreadBadMv": 200},
        "chart": {"defaultWindowSec": 600},
        "scan": {"onlyBatteries": True},
        "about": {"repo": "https://github.com/moleculafree/power-peasant", "donate": ""},
    },
    "server": {"pollIntervalSec": 2.0, "logIntervalSec": 10.0, "historyHours": 2.0},
}

_cfg_cache = {"mtime": None, "data": None}


def _merge_cfg(base: dict, over) -> dict:
    """Deep-merge user config over defaults, keeping only known keys and types."""
    out = {}
    for key, default in base.items():
        val = over.get(key, default) if isinstance(over, dict) else default
        if isinstance(default, dict):
            out[key] = _merge_cfg(default, val if isinstance(val, dict) else {})
        elif isinstance(default, list):
            items = [x for x in val if isinstance(x, str)] if isinstance(val, list) else []
            out[key] = items or default
        elif isinstance(default, bool):
            out[key] = val if isinstance(val, bool) else default
        elif isinstance(default, (int, float)):
            out[key] = val if isinstance(val, (int, float)) and not isinstance(val, bool) else default
        else:
            out[key] = val if isinstance(val, str) else default
    return out


_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _clean_config(cfg: dict) -> dict:
    """Clamp numbers and validate colors so a bad edit cannot break the UI."""
    ui = cfg["ui"]
    c = ui["colors"]
    for key in ("cellMax", "cellMin", "cellNormal", "cellCritical"):
        if not _HEX_RE.match(str(c.get(key, ""))):
            c[key] = DEFAULT_CONFIG["ui"]["colors"][key]
    palette = [x for x in c.get("chartPalette", []) if _HEX_RE.match(str(x))]
    c["chartPalette"] = palette or list(DEFAULT_CONFIG["ui"]["colors"]["chartPalette"])

    def num(section, key, lo, hi, default):
        val = section.get(key, default)
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            val = default
        return max(lo, min(hi, float(val)))

    cells, th, ch, srv = ui["cells"], ui["thresholds"], ui["chart"], cfg["server"]
    cells["scaleMinV"] = num(cells, "scaleMinV", 2.0, 4.0, 3.00)
    cells["scaleMaxV"] = num(cells, "scaleMaxV", 2.0, 5.0, 3.45)
    if cells["scaleMaxV"] <= cells["scaleMinV"] + 0.01:
        cells["scaleMinV"], cells["scaleMaxV"] = 3.00, 3.45
    cells["criticalMv"] = int(num(cells, "criticalMv", 2000, 4500, 3050))
    th["cellDeltaOkMv"] = int(num(th, "cellDeltaOkMv", 0, 1000, 10))
    th["cellDeltaWarnMv"] = max(th["cellDeltaOkMv"], int(num(th, "cellDeltaWarnMv", 0, 2000, 30)))
    th["packSpreadOkMv"] = int(num(th, "packSpreadOkMv", 0, 5000, 60))
    th["packSpreadBadMv"] = max(th["packSpreadOkMv"], int(num(th, "packSpreadBadMv", 0, 10000, 200)))
    ch["defaultWindowSec"] = int(num(ch, "defaultWindowSec", 0, 63072000, 600))
    srv["pollIntervalSec"] = num(srv, "pollIntervalSec", 0.5, 60, 2.0)
    srv["logIntervalSec"] = num(srv, "logIntervalSec", 0, 3600, 10.0)
    srv["historyHours"] = num(srv, "historyHours", 0.1, 48, 2.0)
    if ui.get("language") not in ("uk", "en", "de"):
        ui["language"] = "en"
    return cfg


def get_config() -> dict:
    """Read assets/config.json (cached by mtime); fall back to defaults on error."""
    try:
        mtime = CONFIG_FILE.stat().st_mtime
    except OSError:
        return DEFAULT_CONFIG
    if _cfg_cache["data"] is not None and _cfg_cache["mtime"] == mtime:
        return _cfg_cache["data"]
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("root must be a JSON object")
        cfg = _merge_cfg(DEFAULT_CONFIG, raw)
        cfg = _clean_config(cfg)
    except (OSError, ValueError) as exc:
        print(f"[-] config.json: {exc} (using defaults)")
        cfg = DEFAULT_CONFIG
    _cfg_cache["mtime"] = mtime
    _cfg_cache["data"] = cfg
    return cfg


_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_URL_FIND_RE = re.compile(r"https?://[^\s\"'\[\],]+", re.IGNORECASE)


def _parse_funding(text: str) -> list:
    """Extract sponsor URLs from a FUNDING.yml (the custom list)."""
    urls = []
    for raw in text.splitlines():
        line = re.sub(r"\s+#.*$", "", raw).strip()
        if not line or line.startswith("#"):
            continue
        for url in _URL_FIND_RE.findall(line):
            if url not in urls:
                urls.append(url)
    return urls


def _funding_label(url: str) -> str:
    """Short human label for a sponsor link, based on its host."""
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if "monobank" in host:
        return "Monobank"
    if "paypal" in host:
        return "PayPal"
    return host or url


def funding_links(extra: str = "") -> list:
    """Sponsor links from FUNDING.yml (assets first, then .github) plus an optional extra URL."""
    urls = []
    for path in (ROOT / "assets" / "FUNDING.yml", ROOT / ".github" / "FUNDING.yml"):
        try:
            if path.is_file():
                urls = _parse_funding(path.read_text(encoding="utf-8"))
                if urls:
                    break
        except (OSError, ValueError):
            pass
    extra = str(extra or "").strip()
    if _URL_RE.match(extra) and extra not in urls:
        urls.insert(0, extra)
    return [{"label": _funding_label(u), "url": u} for u in urls]


def about_info() -> dict:
    """App name, version/build info and links for the About dialog."""
    about = (get_config().get("ui") or {}).get("about") or {}
    donate = about.get("donate") or ""
    info = {
        "name": "Power Peasant",
        "version": "dev",
        "build": "dev",
        "date": None,
        "repo": about.get("repo") or "https://github.com/moleculafree/power-peasant",
        "donate": donate,
        "funding": funding_links(donate),
        "frozen": bool(getattr(sys, "frozen", False)),
    }
    try:
        data = json.loads((ROOT / "assets" / "version.json").read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            for key in ("version", "build", "date"):
                if data.get(key):
                    info[key] = data[key]
    except (OSError, ValueError):
        pass
    return info


# ---------------------------------------------------------------- protocol ---

def request_frame(cmd: int) -> bytes:
    body = bytes([0x00, 0x00, 0x04, 0x01, cmd, 0x55, 0xAA])
    return body + bytes([sum(body) & 0xFF])


def u16(fr, i):
    return int.from_bytes(fr[i:i + 2], "little")


def s16(fr, i):
    return int.from_bytes(fr[i:i + 2], "little", signed=True)


def u32(fr, i):
    return int.from_bytes(fr[i:i + 4], "little")


def s32(fr, i):
    v = u32(fr, i)
    return v - (1 << 32) if v & 0x80000000 else v


def valid(fr: bytes) -> bool:
    return (
        len(fr) == FRAME_LEN
        and fr[4] == 0x93
        and fr[5] == 0x55
        and fr[6] == 0xAA
        and (sum(fr[:-1]) & 0xFF) == fr[-1]
    )


def decode(fr: bytes) -> dict:
    cells = []
    for k in range(16):
        mv = u16(fr, 16 + 2 * k)
        if mv == 0:
            break
        cells.append(mv)

    current = s32(fr, 48) / 1000.0
    pack_mv = u32(fr, 12)
    balance_mask = u32(fr, 84)

    return {
        "pack_mv": pack_mv,
        "cells_mv": cells,
        "delta_mv": (max(cells) - min(cells)) if cells else None,
        "current_a": current,
        "power_w": (pack_mv / 1000.0) * current,
        "soc": fr[90],
        "soh": fr[92],
        "remaining_ah": u16(fr, 62) / 100.0,
        "fcc_ah": u16(fr, 64) / 100.0,
        "cell_temp_c": s16(fr, 52),
        "mos_temp_c": s16(fr, 54),
        "cycles": u32(fr, 96),
        "discharged_ah": u32(fr, 100),
        "protect": fr[76:80].hex().upper(),
        "failure": fr[80:83].hex().upper(),
        "balance_mask": balance_mask,
        "balance_cells": [i + 1 for i in range(len(cells)) if balance_mask & (1 << i)],
        "state": "charging" if current > 0.05 else ("discharging" if current < -0.05 else "idle"),
    }


# --------------------------------------------------------------- csv store ---

class CsvLog:
    def __init__(self, path: Path):
        self.dir = path.parent
        self.base = path.stem
        self.dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, ts: float | None = None) -> Path:
        """Monthly file for the given timestamp (<base>-YYYY-MM.csv)."""
        month = datetime.fromtimestamp(ts if ts is not None else time.time()).strftime("%Y-%m")
        return self.dir / f"{self.base}-{month}.csv"

    @property
    def path(self) -> Path:
        return self.path_for()

    def files(self) -> list:
        """Existing monthly files, oldest first."""
        return sorted(self.dir.glob(f"{self.base}-[0-9][0-9][0-9][0-9]-[0-9][0-9].csv"))

    def append(self, data: dict, ts: float):
        path = self.path_for(ts)
        new = (not path.exists()) or path.stat().st_size == 0
        cells = list(data.get("cells_mv") or [])
        cells = [round(c / 1000, 3) for c in cells][:16]
        cells += [""] * (16 - len(cells))
        balance = data.get("balance_cells")
        if balance is None:
            mask = int(data.get("balance_mask") or 0)
            balance = [i + 1 for i in range(len(data.get("cells_mv") or []))
                       if mask & (1 << i)]
        balance = " ".join(str(c) for c in (balance or []))
        row = [
            round(ts, 1),
            datetime.fromtimestamp(ts).isoformat(timespec="seconds"),
            round(data.get("pack_mv", 0) / 1000, 3),
            round(data.get("current_a", 0), 3),
            round(data.get("power_w", 0), 2),
            data.get("soc", ""),
            data.get("soh", ""),
            round(data.get("remaining_ah", 0), 2),
            round(data.get("fcc_ah", 0), 2),
            data.get("cell_temp_c", ""),
            data.get("mos_temp_c", ""),
            data.get("cycles", ""),
            data.get("discharged_ah", ""),
        ] + cells + [balance]
        try:
            with open(path, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(CSV_HEADER)
                w.writerow(row)
        except OSError as exc:
            print(f"[-] csv {path.name}: {exc}")


def load_points(path: Path, hours: float) -> list:
    if not path.exists():
        return []
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > 2_000_000:
                f.seek(size - 2_000_000)
                f.readline()  # drop partial line
            raw = f.read().decode("utf-8", "ignore")
    except OSError:
        return []
    cutoff = time.time() - hours * 3600
    pts = []
    for line in raw.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 4:
            continue
        try:
            ts = float(parts[0])
            if ts < cutoff:
                continue
            soc = int(float(parts[5])) if parts[5] else 0
            cells = [round(float(x), 3) for x in parts[13:29] if x != ""]
            pts.append([round(ts, 1), float(parts[2]), float(parts[3]), soc] + cells)
        except (ValueError, IndexError):
            continue
    return pts[-HISTORY_MAX:]


def load_log_points(logger, hours: float) -> list:
    """Recent points across monthly files (enough for the startup preload)."""
    pts = []
    for path in logger.files()[-2:]:
        pts.extend(load_points(path, hours))
    pts.sort(key=lambda p: p[0])
    return pts[-HISTORY_MAX:]


def _line_ts(line: str):
    try:
        return float(line.split(",", 1)[0])
    except (ValueError, IndexError):
        return None


def _csv_rows(path: Path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore", newline="") as f:
            f.readline()
            for line in f:
                if not line.endswith("\n"):
                    continue
                ts = _line_ts(line)
                if ts is not None:
                    yield ts, line
    except OSError:
        return


def _pad_csv_line(line: str, total: int = len(CSV_HEADER)) -> str:
    """Append empty fields so an older CSV row matches the current header."""
    if line.endswith("\r\n"):
        body, end = line[:-2], "\r\n"
    elif line.endswith("\n"):
        body, end = line[:-1], "\n"
    else:
        body, end = line, ""
    missing = total - (body.count(",") + 1)
    return body + ("," * max(0, missing)) + end


def _merge_month_file(temp: Path, target: Path) -> None:
    """Merge an import temp file into an existing monthly file, de-duplicating by ts."""
    merged = target.with_name(target.name + ".merge")
    try:
        with open(merged, "w", encoding="utf-8", newline="") as out:
            writer = csv.writer(out)
            writer.writerow(CSV_HEADER)
            old = _csv_rows(target)
            new = _csv_rows(temp)
            ro = next(old, None)
            rn = next(new, None)
            last_ts = None
            while ro is not None or rn is not None:
                if rn is None or (ro is not None and ro[0] <= rn[0]):
                    ts, line = ro
                    ro = next(old, None)
                else:
                    ts, line = rn
                    rn = next(new, None)
                if ts == last_ts:
                    continue
                last_ts = ts
                out.write(_pad_csv_line(line))
        merged.replace(target)
        temp.unlink(missing_ok=True)
    except OSError as exc:
        print(f"[-] import {target.name}: {exc}")
        try:
            merged.unlink()
        except OSError:
            pass


def migrate_legacy_csvs(directory: Path | None = None) -> list:
    """One-time import of <base>.csv into monthly <base>-YYYY-MM.csv files.

    Legacy files are renamed to *.imported after a successful import, so the
    migration is safe to re-run after a crash and never runs twice.
    """
    root = Path(directory) if directory else DATA_DIR
    if not root.is_dir():
        return []
    imported = []
    for legacy in sorted(root.glob("*.csv")):
        if re.match(r"^.+-\d{4}-\d{2}\.csv$", legacy.name):
            continue
        base = legacy.stem
        temps = {}
        try:
            with open(legacy, "r", encoding="utf-8", errors="ignore", newline="") as f:
                f.readline()
                for line in f:
                    if not line.strip():
                        continue
                    ts = _line_ts(line)
                    if ts is None:
                        continue
                    month = datetime.fromtimestamp(ts).strftime("%Y-%m")
                    item = temps.get(month)
                    if item is None:
                        tmp_path = root / f".{base}-{month}.import"
                        handle = open(tmp_path, "w", encoding="utf-8", newline="")
                        csv.writer(handle).writerow(CSV_HEADER)
                        item = (handle, tmp_path)
                        temps[month] = item
                    handle = item[0]
                    text = _pad_csv_line(line)
                    handle.write(text if text.endswith("\n") else text + "\n")
            for handle, _ in temps.values():
                handle.close()
            for month, (_, tmp_path) in temps.items():
                target = root / f"{base}-{month}.csv"
                if target.exists() and target.stat().st_size > 0:
                    _merge_month_file(tmp_path, target)
                else:
                    tmp_path.replace(target)
            backup = legacy.with_name(legacy.name + ".imported")
            if backup.exists():
                backup = legacy.with_name(f"{legacy.stem}.{int(time.time())}.imported")
            legacy.replace(backup)
            imported.append(legacy.name)
            print(f"[i] imported legacy CSV: {legacy.name} -> {len(temps)} month(s)")
        except OSError as exc:
            print(f"[-] import {legacy.name}: {exc}")
            for handle, _ in temps.values():
                try:
                    handle.close()
                except OSError:
                    pass
    return imported


def upgrade_monthly_csvs(directory: Path | None = None) -> list:
    """Add missing trailing columns (e.g. balance) to older monthly CSV files."""
    root = Path(directory) if directory else DATA_DIR
    if not root.is_dir():
        return []
    upgraded = []
    pattern = "*-[0-9][0-9][0-9][0-9]-[0-9][0-9].csv"
    for path in sorted(root.glob(pattern)):
        tmp = None
        try:
            with open(path, "r", encoding="utf-8", errors="ignore", newline="") as f:
                header = f.readline()
                if not header.strip():
                    continue
                try:
                    names = next(csv.reader([header]))
                except (csv.Error, StopIteration):
                    continue
                if names and names[-1] == "balance":
                    continue
                tmp = path.with_name(path.name + ".upgrade")
                with open(tmp, "w", encoding="utf-8", newline="") as out:
                    csv.writer(out).writerow(CSV_HEADER)
                    for line in f:
                        if line.strip():
                            text = _pad_csv_line(line)
                            out.write(text if text.endswith("\n") else text + "\n")
            if tmp is not None:
                tmp.replace(path)
                upgraded.append(path.name)
        except OSError as exc:
            print(f"[-] upgrade {path.name}: {exc}")
            if tmp is not None:
                try:
                    tmp.unlink()
                except OSError:
                    pass
    if upgraded:
        print(f"[i] upgraded {len(upgraded)} CSV file(s) with the balance column")
    return upgraded


def _csv_point(line: str):
    """Parse one CSV row into [ts, pack_v, current_a, soc] (no cells)."""
    parts = line.split(",")
    if len(parts) < 6:
        return None
    try:
        ts = float(parts[0])
        pack_v = float(parts[2])
        current = float(parts[3])
        soc = int(float(parts[5])) if parts[5] else 0
    except (ValueError, IndexError):
        return None
    return [round(ts, 1), pack_v, current, soc]


def _iter_csv_lines(path: Path):
    """Yield complete data lines (skip header and a partially written last line)."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore", newline="") as f:
            f.readline()
            for line in f:
                if line.endswith("\n"):
                    yield line
    except OSError:
        return


def _csv_bounds(path: Path):
    """First and last data timestamps without reading the whole file."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.readline()
            first = None
            for _ in range(8):
                raw = f.readline()
                if not raw:
                    break
                point = _csv_point(raw.decode("utf-8", "ignore"))
                if point:
                    first = point[0]
                    break
            if size > 65536:
                f.seek(size - 65536)
                f.readline()
            else:
                f.seek(0)
                f.readline()
            last = None
            for raw in f:
                if not raw.endswith(b"\n"):
                    continue
                point = _csv_point(raw.decode("utf-8", "ignore"))
                if point:
                    last = point[0]
    except OSError:
        return None, None
    return first, last


def _tail_text(path: Path, start_ts: float, chunk: int = 1_048_576) -> str:
    """Read only the tail of a CSV that can still contain rows newer than start_ts."""
    try:
        size = path.stat().st_size
        if size == 0:
            return ""
        blocks = []
        with open(path, "rb") as f:
            pos = size
            while pos > 0:
                n = min(chunk, pos)
                pos -= n
                f.seek(pos)
                blocks.append(f.read(n))
                if pos == 0:
                    break
                block = blocks[-1]
                nl1 = block.find(b"\n")
                nl2 = block.find(b"\n", nl1 + 1) if nl1 >= 0 else -1
                if nl2 < 0:
                    continue
                point = _csv_point(block[nl1 + 1:nl2].decode("utf-8", "ignore"))
                if point and point[0] < start_ts:
                    break
        return b"".join(reversed(blocks)).decode("utf-8", "ignore")
    except OSError:
        return ""


def _add_bucket(buckets: dict, point: list, start: float, step: float) -> None:
    idx = int((point[0] - start) / step)
    acc = buckets.get(idx)
    if acc is None:
        buckets[idx] = [1, point[1], point[2], point[3]]
    else:
        acc[0] += 1
        acc[1] += point[1]
        acc[2] += point[2]
        acc[3] += point[3]


def _collect_history(lines, start: float, end: float, max_points: int = MAX_POINTS):
    """Turn CSV rows into chart points; average buckets once there are too many.

    Returns (points, step_seconds, first_ts, last_ts). step is 0 when raw rows fit.
    """
    raw = []
    buckets = None
    step = 0.0
    seen_from = seen_to = None
    span = max(0.001, end - start)
    for line in lines:
        point = _csv_point(line)
        if point is None:
            continue
        ts = point[0]
        if ts < start or ts > end:
            continue
        if seen_from is None:
            seen_from = ts
        seen_to = ts
        if buckets is None:
            raw.append(point)
            if len(raw) > max_points:
                step = span / max(1, max_points - 1)
                buckets = {}
                for item in raw:
                    _add_bucket(buckets, item, start, step)
                raw = None
        else:
            _add_bucket(buckets, point, start, step)
    if buckets is None:
        return raw, 0.0, seen_from, seen_to
    pts = []
    for idx in sorted(buckets):
        n, pack_sum, cur_sum, soc_sum = buckets[idx]
        pts.append([round(start + (idx + 0.5) * step, 1),
                    round(pack_sum / n, 3), round(cur_sum / n, 3), round(soc_sum / n)])
    return pts, step, seen_from, seen_to


def _log_paths(paths) -> list:
    if isinstance(paths, (str, Path)):
        return [Path(paths)]
    return [Path(p) for p in paths]


def read_csv_history(paths, window: float) -> dict:
    """Read chart points across monthly CSV files. window=0 means all stored data."""
    paths = _log_paths(paths)
    usable = []
    for path in paths:
        first, last = _csv_bounds(path)
        if first is not None and last is not None:
            usable.append((path, first, last))
    if not usable:
        return {"points": [], "from": None, "to": None, "step": 0.0}
    first_ts = usable[0][1]
    last_ts = usable[-1][2]

    if window and window > 0:
        start = max(first_ts, time.time() - window)
        end = last_ts
        if end < start:
            return {"points": [], "from": None, "to": None, "step": 0.0}

        def window_lines():
            for path, first, last in usable:
                if last < start:
                    continue
                if first < start:
                    text = _tail_text(path, start)
                    lines = text.splitlines()
                    if text and not text.endswith("\n") and lines:
                        lines = lines[:-1]
                    yield from lines
                else:
                    yield from _iter_csv_lines(path)

        pts, step, seen_from, seen_to = _collect_history(
            window_lines(), start, end)
    else:
        def all_lines():
            for path, first, last in usable:
                yield from _iter_csv_lines(path)

        pts, step, seen_from, seen_to = _collect_history(
            all_lines(), first_ts, last_ts)
    return {"points": pts, "from": seen_from, "to": seen_to, "step": step}


def _history_ttl(window: float, paths) -> float:
    if window > 0:
        if window >= 365 * 86400:
            return 300.0
        if window >= 180 * 86400:
            return 120.0
        if window >= 30 * 86400:
            return 60.0
        if window >= 7 * 86400:
            return 45.0
        if window >= 86400:
            return 30.0
        if window >= 6 * 3600:
            return 15.0
        return 10.0
    try:
        size = sum(p.stat().st_size for p in _log_paths(paths) if p.exists())
    except OSError:
        return 60.0
    if size < 20_000_000:
        return 30.0
    if size < 200_000_000:
        return 120.0
    return 300.0


def history_from_csv(paths, window: float) -> dict:
    """Cached CSV read: long windows are expensive and change slowly."""
    paths = [p for p in _log_paths(paths) if p.exists()]
    if not paths:
        return {"points": [], "from": None, "to": None, "step": 0.0}
    key = (tuple(str(p) for p in paths), int(window))
    now = time.monotonic()
    with HISTORY_CACHE_LOCK:
        cached = HISTORY_CACHE.get(key)
        if cached and cached[0] > now:
            return cached[1]
    payload = read_csv_history(paths, window)
    with HISTORY_CACHE_LOCK:
        if len(HISTORY_CACHE) > 64:
            HISTORY_CACHE.clear()
        HISTORY_CACHE[key] = (time.monotonic() + _history_ttl(window, paths), payload)
    return payload


def get_history(ident: str, window: int) -> dict:
    """Chart payload for a device/group: memory for short windows, CSV for long/All."""
    with LOCK:
        if ident in DEVICES:
            entry = DEVICES[ident]
            mem = list(entry.get("history") or [])
            logger = entry.get("logger")
        else:
            group = next((g for g in GROUPS if g["id"] == ident), None)
            mem = list(group.get("history") or []) if group else []
            logger = group.get("logger") if group else None
        paths = logger.files() if logger else []

    def memory_payload(points):
        pts = points
        step = 0.0
        if len(pts) > MAX_POINTS:
            stride = math.ceil(len(pts) / MAX_POINTS)
            pts = pts[::stride] + ([pts[-1]] if (len(pts) - 1) % stride else [])
            if len(pts) >= 2:
                step = (pts[-1][0] - pts[0][0]) / max(1, len(pts) - 1)
        return {"points": pts,
                "from": pts[0][0] if pts else None,
                "to": pts[-1][0] if pts else None,
                "step": step}

    if window > 0:
        cutoff = time.time() - window
        fresh = [p for p in mem if p[0] >= cutoff]
        covered = bool(fresh) and fresh[0][0] <= cutoff + max(30.0, LOG_INTERVAL * 2)
        if covered or not paths:
            return memory_payload(fresh)
        payload = history_from_csv(paths, window)
    else:
        if paths:
            payload = history_from_csv(paths, 0)
        else:
            return memory_payload(mem)
    if not payload["points"] and mem:
        fresh = [p for p in mem if not window or p[0] >= time.time() - window]
        if fresh:
            return memory_payload(fresh)
    return payload


# ----------------------------------------------------------------- groups ---

def sanitize_id(v) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]", "", str(v or ""))[:64]
    return s or uuid.uuid4().hex


def make_group(g: dict, base: dict | None = None) -> dict:
    gid = sanitize_id(g.get("id"))
    base = base or {}
    return {
        "id": gid,
        "name": str(g.get("name") or "Group")[:60],
        "mode": g.get("mode") if g.get("mode") in ("parallel", "series") else "parallel",
        "members": [str(m) for m in (g.get("members") or [])],
        "history": base.get("history") or deque(maxlen=HISTORY_MAX),
        "logger": base.get("logger") or CsvLog(DATA_DIR / f"group-{gid}.csv"),
        "last_log": base.get("last_log", 0.0),
        "data": base.get("data", {}),
    }


def groups_payload() -> list:
    return [{"id": g["id"], "name": g["name"], "mode": g["mode"],
             "members": g["members"]} for g in GROUPS]


def save_groups_file(payload=None):
    data = groups_payload() if payload is None else payload
    try:
        GROUPS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"[-] groups.json: {exc}")


def set_groups(items: list):
    with LOCK:
        old = {g["id"]: g for g in GROUPS}
        GROUPS[:] = [make_group(g, old.get(sanitize_id(g.get("id")))) for g in items]
        payload = groups_payload()
    save_groups_file(payload)
    return payload


def load_groups():
    if not GROUPS_FILE.exists():
        return
    try:
        items = json.loads(GROUPS_FILE.read_text(encoding="utf-8"))
        if isinstance(items, list):
            entries = []
            for g in items:
                e = make_group(g)
                e["history"].extend(load_log_points(e["logger"], LOAD_HOURS))
                entries.append(e)
            GROUPS[:] = entries
    except (OSError, ValueError) as exc:
        print(f"[-] groups.json: {exc}")


def aggregate(g: dict) -> dict:
    """Aggregate current data of a group's members. Call with LOCK held."""
    by_name = {e.get("name"): e for e in DEVICES.values()}
    members, ds = [], []
    for name in g["members"]:
        e = by_name.get(name)
        if not e:
            members.append({"name": name, "id": None, "connected": False, "missing": True})
            continue
        m = {"name": e.get("name"), "id": e.get("id"), "connected": bool(e.get("connected"))}
        d = e.get("data") or {}
        if d:
            m.update(
                pack_v=round(d["pack_mv"] / 1000, 3),
                current_a=round(d["current_a"], 2),
                soc=d["soc"],
                delta_mv=d["delta_mv"],
                cell_temp_c=d["cell_temp_c"],
                remaining_ah=round(d["remaining_ah"], 2),
            )
        members.append(m)
        if m["connected"] and d:
            ds.append(d)

    # configured capacity: last known values of all members, even offline ones
    known_fcc = []
    for name in g["members"]:
        e = by_name.get(name) or {}
        d = e.get("data") or {}
        if d.get("fcc_ah"):
            known_fcc.append(d["fcc_ah"])
    cap_total = (min(known_fcc) if g["mode"] == "series" else sum(known_fcc)) if known_fcc else 0.0

    base = {"kind": "group", "mode": g["mode"], "members": members,
            "members_total": len(g["members"]), "online": len(ds),
            "partial": len(ds) != len(g["members"]),
            "cap_online_ah": 0.0, "cap_total_ah": round(cap_total, 2),
            "cap_known": len(known_fcc)}
    if not ds:
        return base

    pack = sum(d["pack_mv"] for d in ds) / 1000.0
    cur = sum(d["current_a"] for d in ds)
    rem = sum(d["remaining_ah"] for d in ds)
    fcc = sum(d["fcc_ah"] for d in ds)
    if g["mode"] == "series":
        cur /= len(ds)
        rem = min(d["remaining_ah"] for d in ds)
        fcc = min(d["fcc_ah"] for d in ds)
    else:
        pack /= len(ds)
    soc = round(rem / fcc * 100) if fcc > 0 else 0

    base.update(
        cap_online_ah=round(fcc, 2),
        pack_mv=round(pack * 1000),
        current_a=round(cur, 2),
        power_w=round(pack * cur, 1),
        soc=soc,
        remaining_ah=round(rem, 2),
        fcc_ah=round(fcc, 2),
        cell_temp_c=round(sum(d["cell_temp_c"] for d in ds) / len(ds)),
        mos_temp_c=round(sum(d["mos_temp_c"] for d in ds) / len(ds)),
        soh=round(sum(d["soh"] for d in ds) / len(ds)),
        cycles=max(d["cycles"] for d in ds),
        discharged_ah=sum(d["discharged_ah"] for d in ds),
        delta_mv=max((d["delta_mv"] or 0) for d in ds),
        cells_mv=[],
        balance_cells=[],
        protect="00000000",
        failure="000000",
    )
    return base


def refresh_groups(ts: float):
    """Recompute group data after a device update. Call with LOCK held.
    Returns list of (logger, data, ts) for CSV logging (outside the lock)."""
    logs = []
    for g in GROUPS:
        d = aggregate(g)
        g["data"] = d
        if d.get("online"):
            g["history"].append([round(ts, 1), d["pack_mv"] / 1000.0,
                                 d["current_a"], d["soc"]])
            if ts - g["last_log"] >= LOG_INTERVAL:
                g["last_log"] = ts
                logs.append((g["logger"], d, ts))
    return logs


def reload_group_history():
    for g in GROUPS:
        if not g["history"]:
            g["history"].extend(load_log_points(g["logger"], LOAD_HOURS))


# -------------------------------------------------------------------- ble ---

class Reader:
    def __init__(self):
        self.buf = bytearray()
        self.event = asyncio.Event()

    def on_notify(self, _char, data: bytearray):
        self.buf.extend(data)
        if len(self.buf) >= FRAME_LEN:
            self.event.set()

    async def inquire(self, client: BleakClient, timeout=4.0) -> bytes | None:
        for _attempt in range(2):
            self.buf.clear()
            self.event.clear()
            frame = request_frame(0x13)
            try:
                await client.write_gatt_char(CH_WRITE, frame, response=False)
            except Exception:
                await client.write_gatt_char(CH_WRITE, frame, response=True)
            try:
                await asyncio.wait_for(self.event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                continue
            fr = bytes(self.buf[:FRAME_LEN])
            if valid(fr):
                return fr
        return None


def pretty(name: str | None) -> str:
    n = name or "battery"
    return n[2:] if n.startswith("P-") else n


def load_ignored():
    if not IGNORED_FILE.exists():
        return
    try:
        data = json.loads(IGNORED_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            IGNORED.update(str(x) for x in data)
    except (OSError, ValueError) as exc:
        print(f"[-] ignored.json: {exc}")


def save_ignored():
    try:
        IGNORED_FILE.write_text(json.dumps(sorted(IGNORED), ensure_ascii=False, indent=2),
                                encoding="utf-8")
    except OSError as exc:
        print(f"[-] ignored.json: {exc}")


_last_seen_save = 0.0


def load_seen():
    if not SEEN_FILE.exists():
        return
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            for rec in data:
                if isinstance(rec, dict) and rec.get("address"):
                    SEEN[rec["address"]] = rec
    except (OSError, ValueError) as exc:
        print(f"[-] scan_history.json: {exc}")


def save_seen(force=False):
    global _last_seen_save
    now = time.time()
    if not force and now - _last_seen_save < 20:
        return
    _last_seen_save = now
    with LOCK:
        data = sorted(SEEN.values(), key=lambda r: -r.get("last_seen", 0))
    try:
        SEEN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        print(f"[-] scan_history.json: {exc}")


async def scan_all() -> list:
    """One radio scan; remembers everything seen so the UI can show it."""
    try:
        found = await BleakScanner.discover(timeout=8, return_adv=True)
    except Exception as exc:
        reason, message = "error", str(exc)
        if BleakBluetoothNotAvailableError is not None and isinstance(exc, BleakBluetoothNotAvailableError):
            r = getattr(exc, "reason", None)
            if BleakBluetoothNotAvailableReason is not None:
                if r == BleakBluetoothNotAvailableReason.POWERED_OFF:
                    reason = "off"
                elif r == BleakBluetoothNotAvailableReason.NO_BLUETOOTH:
                    reason = "no_adapter"
                elif r in (BleakBluetoothNotAvailableReason.DENIED_BY_SYSTEM,
                           BleakBluetoothNotAvailableReason.DENIED_BY_USER):
                    reason = "denied"
                else:
                    reason = "unavailable"
            message = getattr(r, "name", str(r or exc))
        else:
            low = message.lower()
            if "powered off" in low or "radio is not powered" in low:
                reason = "off"
            elif "no bluetooth" in low or "no adapter" in low:
                reason = "no_adapter"
        with LOCK:
            BT_STATUS.update(ok=False, reason=reason, message=message, checked=time.time())
        raise
    with LOCK:
        BT_STATUS.update(ok=True, reason=None, message=None, checked=time.time())
    items = []
    for addr, (dev, adv) in found.items():
        uuids = [str(u).lower() for u in (adv.service_uuids or [])]
        items.append({
            "address": addr,
            "name": (dev.name or "").strip(),
            "rssi": adv.rssi,
            "ffe0": any("ffe0" in u for u in uuids),
        })
    items.sort(key=lambda x: -x["rssi"])
    now = time.time()
    with LOCK:
        SCAN["ts"] = now
        SCAN["items"] = items
        for it in items:
            rec = SEEN.get(it["address"])
            if not rec:
                rec = {"address": it["address"], "first_seen": now, "count": 0,
                       "best_rssi": it["rssi"]}
            rec["name"] = it["name"] or rec.get("name", "")
            rec["ffe0"] = it["ffe0"]
            rec["last_seen"] = now
            rec["last_rssi"] = it["rssi"]
            rec["best_rssi"] = max(rec.get("best_rssi") or it["rssi"], it["rssi"])
            rec["count"] = rec.get("count", 0) + 1
            SEEN[it["address"]] = rec
        if len(SEEN) > SEEN_MAX:
            keep = sorted(SEEN.values(), key=lambda r: -r.get("last_seen", 0))[:SEEN_MAX]
            SEEN.clear()
            SEEN.update({r["address"]: r for r in keep})
    save_seen()
    return items


async def discover(needles) -> list:
    out = []
    for it in await scan_all():
        name = it["name"]
        if not name:
            if not (SHOW_ALL and it["ffe0"]):
                continue
            name = it["address"]
        if pretty(name) in IGNORED:
            continue
        if needles:
            if not any(n.upper() in name.upper() for n in needles):
                continue
        elif not (it["ffe0"] or "P-12100" in name or "POWER" in name.upper()):
            continue
        out.append({"address": it["address"], "name": pretty(name), "rssi": it["rssi"]})
    out.sort(key=lambda d: d["name"])
    return out


async def worker(addr: str, name: str):
    reader = Reader()
    label = name
    retries = 0
    while True:
        try:
            set_entry(addr, connected=False, error="scanning")
            dev = await BleakScanner.find_device_by_address(addr, timeout=6)
            if dev is None:
                found = await discover([name])
                retries += 1
                if not found:
                    set_entry(addr, error="not in range")
                    await asyncio.sleep(min(30, 4 * retries))
                    continue
                if found[0]["address"] != addr:
                    with LOCK:
                        if addr in DEVICES and found[0]["address"] not in DEVICES:
                            DEVICES[found[0]["address"]] = DEVICES.pop(addr)
                    addr = found[0]["address"]
                dev = await BleakScanner.find_device_by_address(addr, timeout=6)
                if dev is None:
                    await asyncio.sleep(4)
                    continue

            async with BleakClient(dev, timeout=20) as client:
                await client.start_notify(CH_NOTIFY, reader.on_notify)
                retries = 0
                set_entry(addr, connected=True, error=None)
                print(f"[+] {label}: connected ({dev.address})")
                fails = 0
                while client.is_connected:
                    fr = await reader.inquire(client)
                    if fr:
                        data = decode(fr)
                        ts = time.time()
                        with LOCK:
                            e = DEVICES[addr]
                            e.update(connected=True, error=None, data=data, updated=ts,
                                     polls=e.get("polls", 0) + 1)
                            e["history"].append(
                                [round(ts, 1), round(data["pack_mv"] / 1000, 3),
                                 round(data["current_a"], 2), data["soc"]]
                                + [round(c / 1000, 3) for c in data["cells_mv"]])
                            dev_log = None
                            if ts - e.get("last_log", 0) >= LOG_INTERVAL:
                                e["last_log"] = ts
                                dev_log = (e["logger"], data, ts)
                            group_logs = refresh_groups(ts)
                        if dev_log:
                            dev_log[0].append(dev_log[1], dev_log[2])
                        for lg, gd, gts in group_logs:
                            lg.append(gd, gts)
                        print(f"[{time.strftime('%H:%M:%S')}] {label}: "
                              f"{data['pack_mv']/1000:.3f}V {data['current_a']:+.2f}A "
                              f"SOC {data['soc']}% "
                              f"cells {','.join(f'{c/1000:.3f}' for c in data['cells_mv'])} "
                              f"d={data['delta_mv']}mV")
                        fails = 0
                    else:
                        fails += 1
                        set_entry(addr, error="no/short response from BMS")
                        if fails >= 3:
                            break
                    await asyncio.sleep(POLL_INTERVAL)
        except Exception as exc:
            set_entry(addr, connected=False, error=f"{type(exc).__name__}: {exc}")
            print(f"[-] {label}: {type(exc).__name__}: {exc}")
        await asyncio.sleep(4)


def set_entry(addr, **kw):
    with LOCK:
        DEVICES.setdefault(addr, {}).update(kw)


async def register_device(address: str, name: str, rssi=None) -> bool:
    """Create a device entry and start its worker. Must be called from the event loop."""
    name = pretty(name)
    if name in IGNORED:
        with LOCK:
            IGNORED.discard(name)
        save_ignored()
    with LOCK:
        if address in DEVICES:
            return False
    entry = {
        "id": address, "name": name, "connected": False, "error": "starting",
        "updated": None, "polls": 0, "data": {},
        "history": deque(maxlen=HISTORY_MAX),
        "logger": CsvLog(DATA_DIR / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', name)}.csv"),
        "last_log": 0.0,
    }
    entry["history"].extend(load_log_points(entry["logger"], LOAD_HOURS))
    with LOCK:
        if address in DEVICES:
            return False
        DEVICES[address] = entry
    print(f"[i] {name}  {address}" + (f"  {rssi} dBm" if rssi is not None else "  (added manually)"))
    task = asyncio.create_task(worker(address, name))
    with LOCK:
        if address in DEVICES:
            DEVICES[address]["task"] = task
        else:
            task.cancel()
    return True


async def remove_device(address: str) -> bool:
    """Stop monitoring a battery and drop it from every group."""
    with LOCK:
        e = DEVICES.pop(address, None)
        if not e:
            return False
        name = e.get("name")
        changed = False
        for g in GROUPS:
            if name in g["members"]:
                g["members"] = [m for m in g["members"] if m != name]
                changed = True
        payload = groups_payload() if changed else None
        for g in GROUPS:
            g["data"] = aggregate(g)
        IGNORED.add(name)
    save_ignored()
    if payload is not None:
        save_groups_file(payload)
    task = e.get("task")
    if task:
        task.cancel()
    print(f"[i] removed from monitoring: {name} ({address})")
    return True


# ------------------------------------------------------------------- http ---

UI_FILE = ROOT / "assets" / "ui" / "index.html"
UI_DIR = UI_FILE.parent


def read_ui() -> bytes:
    """Serve the dashboard from disk, so edits show up on a page reload."""
    try:
        return UI_FILE.read_bytes()
    except OSError as exc:
        return f"<h1>UI file is missing</h1><p>{UI_FILE}</p><p>{exc}</p>".encode()


def serve_static(path: str):
    """Return (bytes, content-type) for a file inside assets/ui, or None."""
    rel = path.lstrip("/")
    if not rel or rel.startswith("api/"):
        return None
    target = (UI_DIR / rel).resolve()
    base = UI_DIR.resolve()
    if base not in target.parents or not target.is_file():
        return None
    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
        ctype += "; charset=utf-8"
    return target.read_bytes(), ctype



class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype="application/json", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)

        if url.path == "/api/config":
            self._send(json.dumps(get_config()).encode())
            return

        if url.path == "/api/data":
            with LOCK:
                devs = [{
                    "id": addr, "kind": "device",
                    "name": e.get("name") or addr,
                    "connected": bool(e.get("connected")),
                    "error": e.get("error"),
                    "updated": e.get("updated"),
                    "polls": e.get("polls", 0),
                    "data": e.get("data", {}),
                } for addr, e in DEVICES.items()]
                grps = [{
                    "id": g["id"], "kind": "group", "name": g["name"],
                    "members": g["members"],
                    "connected": bool((g.get("data") or {}).get("online")),
                    "error": None,
                    "updated": time.time(),
                    "polls": 0,
                    "data": g.get("data") or {},
                } for g in GROUPS]
                scan_items = []
                for it in SCAN["items"]:
                    e = DEVICES.get(it["address"])
                    scan_items.append({**it, "known": bool(e),
                                       "ignored": pretty(it["name"]) in IGNORED,
                                       "connected": bool(e and e.get("connected")),
                                       "label": (e.get("name") if e else None)})
                air = {it["address"] for it in SCAN["items"]}
                seen = []
                for rec in sorted(SEEN.values(), key=lambda r: -r.get("last_seen", 0)):
                    e = DEVICES.get(rec["address"])
                    seen.append({**rec, "known": bool(e),
                                 "ignored": pretty(rec.get("name")) in IGNORED,
                                 "connected": bool(e and e.get("connected")),
                                 "in_air": rec["address"] in air,
                                 "label": (e.get("name") if e else None)})
                scan_payload = {"ts": SCAN["ts"], "items": scan_items, "seen": seen}
                bt_payload = dict(BT_STATUS)
            devs.sort(key=lambda d: d["name"])
            self._send(json.dumps({"ts": time.time(), "devices": devs,
                                   "groups": grps, "scan": scan_payload,
                                   "bluetooth": bt_payload}).encode())
            return

        if url.path == "/api/about":
            self._send(json.dumps(about_info()).encode())
            return

        if url.path == "/api/history":
            ident = (q.get("id") or [""])[0]
            try:
                window = int((q.get("window") or ["0"])[0])
            except ValueError:
                window = 0
            window = max(0, window)
            payload = get_history(ident, window)
            self._send(json.dumps({"id": ident, **payload}).encode())
            return

        if url.path in ("/", "/index.html"):
            self._send(read_ui(), "text/html; charset=utf-8")
            return

        static = serve_static(url.path)
        if static:
            self._send(static[0], static[1])
            return

        self._send(b"not found", "text/plain; charset=utf-8", code=404)

    def do_POST(self):
        url = urlparse(self.path)

        if url.path == "/api/config":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("config must be a JSON object")
                cfg = _clean_config(_merge_cfg(DEFAULT_CONFIG, body))
                CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
                CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
                _cfg_cache["mtime"] = None
                _cfg_cache["data"] = None
                self._send(json.dumps(cfg).encode())
            except Exception as exc:
                self._send(json.dumps({"ok": False, "error": str(exc)}).encode(), code=400)
            return

        if url.path == "/api/scan/clear":
            with LOCK:
                SEEN.clear()
            save_seen(force=True)
            self._send(json.dumps({"ok": True}).encode())
            return

        if url.path == "/api/devices":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                addr = str(body.get("address") or "")
                with LOCK:
                    item = next((i for i in SCAN["items"]
                                 if i["address"].lower() == addr.lower()), None)
                if not item:
                    self._send(json.dumps({"ok": False, "error": "not in scan list"}).encode(), code=404)
                    return
                if MAIN_LOOP is None:
                    self._send(json.dumps({"ok": False, "error": "not ready"}).encode(), code=503)
                    return
                name = item["name"] or addr
                future = asyncio.run_coroutine_threadsafe(
                    register_device(item["address"], name), MAIN_LOOP)
                added = future.result(timeout=8)
                self._send(json.dumps({"ok": True, "added": added,
                                       "name": name}).encode())
            except Exception as exc:
                self._send(json.dumps({"ok": False, "error": str(exc)}).encode(), code=400)
            return

        if url.path == "/api/devices/remove":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                addr = str(body.get("address") or "")
                if MAIN_LOOP is None:
                    self._send(json.dumps({"ok": False, "error": "not ready"}).encode(), code=503)
                    return
                ok = asyncio.run_coroutine_threadsafe(
                    remove_device(addr), MAIN_LOOP).result(timeout=8)
                self._send(json.dumps({"ok": ok}).encode(), code=200 if ok else 404)
            except Exception as exc:
                self._send(json.dumps({"ok": False, "error": str(exc)}).encode(), code=400)
            return

        if url.path != "/api/groups":
            self._send(json.dumps({"ok": False, "error": "not found"}).encode(), code=404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            items = body.get("groups", [])
            if not isinstance(items, list):
                raise ValueError("groups must be a list")
            items = [g for g in items if isinstance(g, dict) and g.get("name")]
            saved = set_groups(items)
            with LOCK:
                for g in GROUPS:
                    g["data"] = aggregate(g)
            self._send(json.dumps({"ok": True, "groups": saved}).encode())
        except (ValueError, TypeError) as exc:
            self._send(json.dumps({"ok": False, "error": str(exc)}).encode(), code=400)

    def log_message(self, *_args):
        pass


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


def _about_at(port: int, host: str = "127.0.0.1"):
    """About payload of the server already listening on port, or None."""
    try:
        with urlopen(f"http://{host}:{port}/api/about", timeout=1.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _find_free_port(start: int, host: str = "127.0.0.1", tries: int = 20):
    for port in range(start, start + tries):
        if not _port_open(port, host):
            return port
    return None


def open_window(url: str) -> None:
    """Blocking embedded window (pywebview + WebView2 on Windows)."""
    try:
        import webview
    except Exception as exc:
        print(f"[-] embedded window unavailable ({exc}); open {url} manually")
        return
    webview.create_window("Power Peasant", url, width=1120, height=840, min_size=(860, 620))
    webview.start()


# ------------------------------------------------------------------- main ---

async def run(args):
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()

    count = 0
    try:
        initial = await discover(args.device)
    except Exception as exc:
        initial = []
        print(f"[-] initial scan failed: {type(exc).__name__}: {exc}")
    for d in initial:
        if await register_device(d["address"], d["name"], d["rssi"]):
            count += 1
    if not count:
        print("[!] no batteries yet, still scanning…")
    with LOCK:
        for g in GROUPS:
            g["data"] = aggregate(g)

    while True:
        await asyncio.sleep(20)
        try:
            known = {(e.get("name") or "").upper() for e in DEVICES.values()}
            for d in await discover(args.device):
                if d["address"] not in DEVICES and (d["name"] or "").upper() not in known:
                    await register_device(d["address"], d["name"], d["rssi"])
        except Exception as exc:
            print(f"[-] discovery: {type(exc).__name__}: {exc}")


def main():
    global POLL_INTERVAL, LOG_INTERVAL, LOAD_HOURS, SHOW_ALL
    ap = argparse.ArgumentParser()
    ap.add_argument("device", nargs="*", help="name fragments, e.g. B02556 B01607 (default: all in range)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="HTTP bind address (default 127.0.0.1; use 0.0.0.0 for LAN access)")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--interval", type=float, default=None, help="BLE poll interval, seconds (default from config)")
    ap.add_argument("--log-interval", type=float, default=None, help="CSV row interval, seconds, 0 = off (default from config)")
    ap.add_argument("--history-hours", type=float, default=None, help="CSV history to preload, hours (default from config)")
    ap.add_argument("--all", action="store_true", help="also connect to unnamed devices that expose FFE0")
    ap.add_argument("--list", action="store_true", help="scan and print batteries")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--window", action="store_true", help="open the dashboard in an embedded window")
    ap.add_argument("--no-window", action="store_true", help="never open the embedded window (server mode)")
    args = ap.parse_args()
    srv = get_config().get("server", {})
    POLL_INTERVAL = max(0.5, args.interval if args.interval is not None
                        else float(srv.get("pollIntervalSec", 2.0)))
    LOG_INTERVAL = (args.log_interval if args.log_interval is not None
                    else float(srv.get("logIntervalSec", 10.0)))
    LOAD_HOURS = max(0.1, args.history_hours if args.history_hours is not None
                     else float(srv.get("historyHours", 2.0)))
    SHOW_ALL = args.all

    if args.list:
        for d in asyncio.run(discover(args.device)):
            print(f"{d['rssi']:4d} dBm  {d['address']}  {d['name']}")
        return

    migrate_legacy_csvs()
    upgrade_monthly_csvs()
    load_groups()
    load_ignored()
    load_seen()

    local = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = f"http://{local}:{args.port}/"
    frozen = bool(getattr(sys, "frozen", False))
    use_window = (args.window or frozen) and not args.no_window and not args.list

    if use_window and _port_open(args.port):
        info = _about_at(args.port, local)
        same_app = bool(info and info.get("name") == "Power Peasant")
        same_kind = bool(info) and bool(info.get("frozen")) == frozen
        if same_app and same_kind:
            print(f"[i] already listening on port {args.port} - opening window")
            open_window(url)
            return
        new_port = _find_free_port(args.port + 1, local)
        if new_port:
            print(f"[!] port {args.port} is used by another instance; using {new_port}")
            args.port = new_port
            url = f"http://{local}:{new_port}/"
        elif same_app:
            print(f"[i] already listening on port {args.port} - opening window")
            open_window(url)
            return
        else:
            raise SystemExit(f"ports {args.port}-{args.port + 19} are busy")

    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        raise SystemExit(f"cannot listen on {args.host}:{args.port} ({exc}); try --port/--host <other>")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[i] dashboard: {url}   (Ctrl+C to stop)")
    if args.host in ("0.0.0.0", "::"):
        print(f"[i] bound to {args.host}:{args.port} - reachable as http://<this-host-ip>:{args.port}/")
        print("[!] the dashboard has no authentication: expose it only on a trusted network")
    print(f"[i] history:   {DATA_DIR}  (every {LOG_INTERVAL:.0f}s)")

    if use_window:
        loop = asyncio.new_event_loop()

        def _monitor_loop():
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(run(args))
            except Exception as exc:
                print(f"[-] monitor loop stopped: {exc}")

        threading.Thread(target=_monitor_loop, daemon=True).start()
        try:
            open_window(url)
        finally:
            server.shutdown()
        return

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n[+] stopped")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
