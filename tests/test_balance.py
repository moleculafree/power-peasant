import csv
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pq_monitor as pq


class BalanceColumnTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.logger = pq.CsvLog(self.root / "battery.csv")
        self.ts = time.time()

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_stores_balancing_cells(self):
        self.logger.append({
            "pack_mv": 13200,
            "current_a": 1.5,
            "power_w": 19.8,
            "soc": 50,
            "soh": 100,
            "remaining_ah": 50.0,
            "fcc_ah": 100.0,
            "cell_temp_c": 24,
            "mos_temp_c": 25,
            "cycles": 10,
            "discharged_ah": 100,
            "cells_mv": [3300, 3301, 3302],
            "balance_cells": [1, 3],
        }, self.ts)
        with open(self.logger.path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0][-1], "balance")
        self.assertEqual(rows[1][-1], "1 3")
        self.assertEqual(len(rows[0]), len(pq.CSV_HEADER))

    def test_append_derives_balance_from_mask(self):
        self.logger.append({
            "pack_mv": 13200, "current_a": 0.0, "power_w": 0.0, "soc": 50,
            "cells_mv": [3300, 3301, 3302], "balance_mask": 0b101,
        }, self.ts)
        with open(self.logger.path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[1][-1], "1 3")

    def test_no_balancing_stores_empty_field(self):
        self.logger.append({
            "pack_mv": 13200, "current_a": 0.0, "power_w": 0.0, "soc": 50,
            "cells_mv": [3300, 3301], "balance_cells": [],
        }, self.ts)
        with open(self.logger.path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[1][-1], "")

    def test_load_points_ignores_balance_column(self):
        self.logger.append({
            "pack_mv": 13200, "current_a": 0.0, "power_w": 0.0, "soc": 50,
            "cells_mv": [3300, 3301], "balance_cells": [2],
        }, self.ts)
        pts = pq.load_points(self.logger.path, 24)
        self.assertEqual(len(pts), 1)
        self.assertEqual(pts[0][4:], [3.3, 3.301])


class UpgradeCsvTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "battery-2026-11.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_adds_balance_column_to_old_file(self):
        ts = time.time()
        row = [""] * (len(pq.CSV_HEADER) - 1)
        row[0], row[2], row[3], row[5] = ts, 13.0, 1.0, 50
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(pq.CSV_HEADER[:-1])
            w.writerow(row)

        self.assertEqual(pq.upgrade_monthly_csvs(self.root), [self.path.name])
        with open(self.path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0][-1], "balance")
        self.assertEqual(len(rows[1]), len(pq.CSV_HEADER))
        self.assertEqual(rows[1][-1], "")
        self.assertEqual(pq.upgrade_monthly_csvs(self.root), [])

    def test_leaves_new_file_untouched(self):
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(pq.CSV_HEADER)
        self.assertEqual(pq.upgrade_monthly_csvs(self.root), [])


if __name__ == "__main__":
    unittest.main()
