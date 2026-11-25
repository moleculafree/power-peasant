import csv
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pq_monitor as pq


def write_csv(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(pq.CSV_HEADER)
        for ts, pack_v, current, soc in rows:
            w.writerow([ts, "", pack_v, current, pack_v * current, soc])


class MigrateLegacyCsvTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_splits_months_and_merges_existing(self):
        oct_ts = datetime(2026, 10, 15, 12, 0).timestamp()
        nov5 = datetime(2026, 11, 5, 12, 0).timestamp()
        nov10 = datetime(2026, 11, 10, 12, 0).timestamp()
        nov20 = datetime(2026, 11, 20, 12, 0).timestamp()
        legacy = self.root / "battery.csv"
        write_csv(legacy, [
            (oct_ts, 13.0, 1.0, 50),
            (nov5, 13.1, 1.1, 51),
            (nov10, 13.2, 1.2, 52),
        ])
        # November already has new-format data; nov5 is a duplicate.
        write_csv(self.root / "battery-2026-11.csv", [
            (nov5, 13.1, 1.1, 51),
            (nov20, 13.3, 1.3, 53),
        ])

        imported = pq.migrate_legacy_csvs(self.root)

        self.assertEqual(imported, ["battery.csv"])
        self.assertFalse(legacy.exists())
        self.assertTrue((self.root / "battery.csv.imported").exists())
        self.assertTrue((self.root / "battery-2026-10.csv").exists())
        payload = pq.read_csv_history(
            sorted(self.root.glob("battery-*.csv")), 0)
        self.assertEqual([p[0] for p in payload["points"]],
                         [round(oct_ts, 1), round(nov5, 1),
                          round(nov10, 1), round(nov20, 1)])

    def test_second_run_is_a_noop(self):
        legacy = self.root / "battery.csv"
        write_csv(legacy, [(datetime(2026, 11, 1).timestamp(), 13.0, 1.0, 50)])
        self.assertEqual(pq.migrate_legacy_csvs(self.root), ["battery.csv"])
        self.assertEqual(pq.migrate_legacy_csvs(self.root), [])

    def test_skips_monthly_and_unknown_files(self):
        (self.root / "battery-2026-11.csv").write_text("header\n", encoding="utf-8")
        (self.root / "notes.txt").write_text("x", encoding="utf-8")
        self.assertEqual(pq.migrate_legacy_csvs(self.root), [])

    def test_empty_directory(self):
        self.assertEqual(pq.migrate_legacy_csvs(self.root), [])


if __name__ == "__main__":
    unittest.main()
