import csv
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pq_monitor as pq


def write_csv(path: Path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(pq.CSV_HEADER)
        for ts, pack_v, current, soc in rows:
            w.writerow([ts, "", pack_v, current, pack_v * current, soc])


def write_csv_cells(path: Path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(pq.CSV_HEADER)
        for ts, pack_v, current, soc, cells in rows:
            row = [ts, "", pack_v, current, pack_v * current, soc] + [""] * 7
            row += list(cells) + [""] * (16 - len(cells)) + [""]
            w.writerow(row)


class CollectHistoryTest(unittest.TestCase):
    def test_keeps_raw_points_when_they_fit(self):
        lines = [f"{ts},x,13.1,{i},0,50" for i, ts in enumerate([100, 110, 120])]
        pts, step, first, last = pq._collect_history(lines, 0, 200, max_points=10)
        self.assertEqual(len(pts), 3)
        self.assertEqual(step, 0.0)
        self.assertEqual((first, last), (100, 120))
        self.assertEqual(pts[0][:4], [100, 13.1, 0.0, 50])

    def test_downsamples_and_reports_step(self):
        lines = [f"{ts},x,13.1,1.0,0,50" for ts in range(0, 1000, 10)]
        pts, step, first, last = pq._collect_history(lines, 0, 1000, max_points=10)
        self.assertLessEqual(len(pts), 10)
        self.assertGreater(step, 0)
        self.assertEqual((first, last), (0, 990))

    def test_skips_rows_outside_range(self):
        lines = ["10,x,13,1,0,50", "50,x,13,1,0,50", "90,x,13,1,0,50"]
        pts, _, first, last = pq._collect_history(lines, 40, 80, max_points=10)
        self.assertEqual(len(pts), 1)
        self.assertEqual((first, last), (50, 50))


class CsvHistoryTest(unittest.TestCase):
    def setUp(self):
        pq.HISTORY_CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "battery.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_returns_whole_period(self):
        now = time.time()
        write_csv(self.path, [
            (now - 7200, 13.0, 1.0, 40),
            (now - 3600, 13.1, 1.1, 45),
            (now - 60, 13.2, 1.2, 50),
        ])
        payload = pq.read_csv_history(self.path, 0)
        self.assertEqual(len(payload["points"]), 3)
        self.assertAlmostEqual(payload["from"], now - 7200, delta=1)
        self.assertAlmostEqual(payload["to"], now - 60, delta=1)
        self.assertEqual(payload["step"], 0.0)

    def test_window_returns_only_recent_points(self):
        now = time.time()
        write_csv(self.path, [
            (now - 7200, 13.0, 1.0, 40),
            (now - 1800, 13.1, 1.1, 45),
            (now - 60, 13.2, 1.2, 50),
        ])
        payload = pq.read_csv_history(self.path, 3600)
        self.assertEqual(len(payload["points"]), 2)
        self.assertAlmostEqual(payload["from"], now - 1800, delta=1)
        self.assertAlmostEqual(payload["to"], now - 60, delta=1)

    def test_tail_text_spans_multiple_chunks(self):
        now = time.time()
        rows = [(now - 600 + i * 10, 13.0, 1.0, 50) for i in range(60)]
        write_csv(self.path, rows)
        text = pq._tail_text(self.path, now - 250, chunk=256)
        pts, _, first, last = pq._collect_history(
            text.splitlines(), now - 250, now)
        self.assertGreaterEqual(len(pts), 20)
        self.assertLessEqual(first, now - 240)
        self.assertAlmostEqual(last, rows[-1][0], delta=1)

    def test_all_downsamples_long_period(self):
        now = time.time()
        rows = [(now - 8000 + i * 10, 13.0, 1.0, 50) for i in range(800)]
        write_csv(self.path, rows)
        payload = pq.read_csv_history(self.path, 0)
        self.assertLessEqual(len(payload["points"]), pq.MAX_POINTS)
        self.assertGreater(payload["step"], 0)
        self.assertAlmostEqual(payload["from"], rows[0][0], delta=1)
        self.assertAlmostEqual(payload["to"], rows[-1][0], delta=1)

    def test_empty_file(self):
        write_csv(self.path, [])
        payload = pq.read_csv_history(self.path, 0)
        self.assertEqual(payload, {"points": [], "from": None, "to": None, "step": 0.0})

    def test_monthly_files_are_merged(self):
        logger = pq.CsvLog(self.path)
        oct_ts = datetime(2026, 10, 15, 12, 0).timestamp()
        nov_ts = datetime(2026, 11, 15, 12, 0).timestamp()
        write_csv(logger.path_for(oct_ts), [(oct_ts, 13.0, 1.0, 50)])
        write_csv(logger.path_for(nov_ts), [(nov_ts, 13.1, 1.1, 51)])
        files = logger.files()
        self.assertEqual([p.name for p in files],
                         [logger.path_for(oct_ts).name, logger.path_for(nov_ts).name])
        payload = pq.read_csv_history(files, 0)
        self.assertEqual(len(payload["points"]), 2)
        self.assertAlmostEqual(payload["from"], oct_ts, delta=1)
        self.assertAlmostEqual(payload["to"], nov_ts, delta=1)

    def test_append_writes_monthly_file(self):
        logger = pq.CsvLog(self.path)
        ts = datetime(2026, 10, 15, 12, 0).timestamp()
        logger.append({"pack_mv": 13000, "current_a": 1.0, "power_w": 13.0,
                       "soc": 50, "cells_mv": [3300, 3301]}, ts)
        self.assertTrue(logger.path_for(ts).is_file())
        self.assertFalse(self.path.exists())

    def test_csv_points_keep_cells(self):
        now = time.time()
        write_csv_cells(self.path, [
            (now - 60, 13.2, 1.0, 50, [3.3, 3.31, 3.32, 3.33]),
        ])
        payload = pq.read_csv_history(self.path, 600)
        self.assertEqual(len(payload["points"]), 1)
        self.assertEqual(payload["points"][0][4:], [3.3, 3.31, 3.32, 3.33])

    def test_downsample_averages_cells(self):
        now = time.time()
        rows = [(now - 8000 + i * 10, 13.0, 1.0, 50,
                 [3.30 + i / 10000, 3.31, 3.32, 3.33]) for i in range(800)]
        write_csv_cells(self.path, rows)
        payload = pq.read_csv_history(self.path, 0)
        self.assertLessEqual(len(payload["points"]), pq.MAX_POINTS)
        self.assertEqual(len(payload["points"][0]), 8)
        for value in payload["points"][0][4:]:
            self.assertTrue(3.25 < value < 3.40)

    def test_window_is_anchored_to_last_row(self):
        last = time.time() - 6 * 3600
        write_csv_cells(self.path, [
            (last - 1800, 13.0, 1.0, 40, []),
            (last, 13.1, 1.1, 45, []),
        ])
        payload = pq.read_csv_history(self.path, 3600)
        self.assertEqual(len(payload["points"]), 2)
        self.assertAlmostEqual(payload["to"], last, delta=1)

    def test_explicit_range_returns_only_that_span(self):
        now = time.time()
        rows = [(now - 3600 + i * 60, 13.0, 1.0, 50) for i in range(60)]
        write_csv(self.path, rows)
        start, end = now - 1830, now - 930
        payload = pq.read_csv_history(self.path, 0, start, end)
        expected = [r for r in rows if start <= r[0] <= end]
        self.assertEqual(len(payload["points"]), len(expected))
        self.assertAlmostEqual(payload["from"], expected[0][0], delta=1)
        self.assertAlmostEqual(payload["to"], expected[-1][0], delta=1)
        self.assertEqual(payload["step"], 0.0)

    def test_range_is_clamped_to_stored_data(self):
        now = time.time()
        rows = [(now - 600 + i * 60, 13.0, 1.0, 50) for i in range(5)]
        write_csv(self.path, rows)
        payload = pq.read_csv_history(self.path, 0, now - 86400, now + 86400)
        self.assertEqual(len(payload["points"]), 5)
        self.assertAlmostEqual(payload["from"], rows[0][0], delta=1)
        self.assertAlmostEqual(payload["to"], rows[-1][0], delta=1)

    def test_empty_or_inverted_range_is_empty(self):
        now = time.time()
        write_csv(self.path, [(now - 600, 13.0, 1.0, 50)])
        empty = {"points": [], "from": None, "to": None, "step": 0.0}
        self.assertEqual(pq.read_csv_history(self.path, 0, now, now), empty)
        self.assertEqual(pq.read_csv_history(self.path, 0, now, now - 60), empty)
        self.assertEqual(pq.read_csv_history(self.path, 0, now + 60, now + 120), empty)

    def test_cache_separates_window_and_range(self):
        now = time.time()
        rows = [(now - 1200 + i * 60, 13.0, 1.0, 50) for i in range(20)]
        write_csv(self.path, rows)
        window = pq.history_from_csv([self.path], 600)
        ranged = pq.history_from_csv([self.path], 0, now - 1190, now - 910)
        self.assertNotEqual(len(window["points"]), len(ranged["points"]))
        self.assertEqual(len(ranged["points"]), len(
            [r for r in rows if now - 1190 <= r[0] <= now - 910]))


class GetHistoryTest(unittest.TestCase):
    def setUp(self):
        pq.HISTORY_CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "battery.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_short_window_uses_memory(self):
        now = time.time()
        mem = [[now - 120 + i, 13.0, 1.0, 50] for i in range(60)]
        entry = {"history": mem, "logger": pq.CsvLog(self.path)}
        with mock.patch.object(pq, "DEVICES", {"a": entry}):
            payload = pq.get_history("a", 300)
        self.assertEqual(len(payload["points"]), 60)
        self.assertIsNotNone(payload["from"])

    def test_long_window_reads_csv(self):
        now = time.time()
        rows = [(now - 7200 + i * 60, 13.0, 1.0, 50) for i in range(100)]
        logger = pq.CsvLog(self.path)
        write_csv(logger.path, rows)
        mem = [[now - 60 + i, 13.1, 1.0, 50] for i in range(10)]
        entry = {"history": mem, "logger": logger}
        with mock.patch.object(pq, "DEVICES", {"a": entry}):
            payload = pq.get_history("a", 21600)
        # CSV rows plus the newest memory tail that has not been logged yet
        self.assertGreaterEqual(len(payload["points"]), 100)
        self.assertAlmostEqual(payload["from"], rows[0][0], delta=1)
        # bounds cover the whole stored file plus the unlogged memory tail
        self.assertAlmostEqual(payload["bounds"]["from"], rows[0][0], delta=1)
        self.assertAlmostEqual(payload["bounds"]["to"], mem[-1][0], delta=1)

    def test_long_window_keeps_cells(self):
        now = time.time()
        cells = [3.3, 3.31, 3.32, 3.33]
        rows = [(now - 7200 + i * 60, 13.0, 1.0, 50, cells) for i in range(100)]
        logger = pq.CsvLog(self.path)
        write_csv_cells(logger.path, rows)
        entry = {"history": [], "logger": logger}
        with mock.patch.object(pq, "DEVICES", {"a": entry}):
            payload = pq.get_history("a", 21600)
        self.assertEqual(len(payload["points"][0]), 8)
        self.assertEqual(payload["points"][0][4:], cells)

    def test_old_data_is_still_reachable(self):
        last = time.time() - 10 * 3600
        logger = pq.CsvLog(self.path)
        write_csv_cells(logger.path, [
            (last - 900, 13.0, 1.0, 40, []),
            (last, 13.1, 1.1, 45, []),
        ])
        entry = {"history": [], "logger": logger}
        with mock.patch.object(pq, "DEVICES", {"a": entry}):
            payload = pq.get_history("a", 1800)
        self.assertEqual(len(payload["points"]), 2)
        self.assertAlmostEqual(payload["to"], last, delta=1)

    def test_all_reads_csv(self):
        now = time.time()
        rows = [(now - 3600 + i * 60, 13.0, 1.0, 50) for i in range(10)]
        logger = pq.CsvLog(self.path)
        write_csv(logger.path, rows)
        entry = {"history": [[now, 13.0, 1.0, 50]], "logger": logger}
        with mock.patch.object(pq, "DEVICES", {"a": entry}):
            payload = pq.get_history("a", 0)
        self.assertGreaterEqual(len(payload["points"]), 10)
        self.assertAlmostEqual(payload["from"], rows[0][0], delta=1)

    def test_unknown_id_is_empty(self):
        self.assertEqual(pq.get_history("nope", 0),
                         {"points": [], "from": None, "to": None, "step": 0.0,
                          "bounds": None})

    def test_past_range_does_not_add_newer_memory_points(self):
        now = time.time()
        rows = [(now - 7200 + i * 60, 13.0, 1.0, 50) for i in range(60)]
        logger = pq.CsvLog(self.path)
        write_csv(logger.path, rows)
        mem = [[now - 30 + i, 13.1, 1.0, 50] for i in range(10)]
        entry = {"history": mem, "logger": logger}
        with mock.patch.object(pq, "DEVICES", {"a": entry}):
            payload = pq.get_history("a", 0, now - 7000, now - 6010)
        self.assertTrue(payload["points"])
        self.assertTrue(all(now - 7000 <= p[0] <= now - 6010
                            for p in payload["points"]))

    def test_range_reads_memory_when_no_csv_exists(self):
        now = time.time()
        mem = [[now - 600 + i, 13.0, 1.0, 50] for i in range(100)]
        entry = {"history": mem, "logger": None}
        with mock.patch.object(pq, "DEVICES", {"a": entry}):
            payload = pq.get_history("a", 0, now - 550, now - 510)
        self.assertTrue(payload["points"])
        self.assertTrue(all(now - 550 <= p[0] <= now - 510
                            for p in payload["points"]))
        # no CSV: bounds come from memory
        self.assertAlmostEqual(payload["bounds"]["from"], mem[0][0], delta=1)
        self.assertAlmostEqual(payload["bounds"]["to"], mem[-1][0], delta=1)


if __name__ == "__main__":
    unittest.main()
