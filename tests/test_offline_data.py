import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pq_monitor as pq


def csv_row(ts=1000.0, pack_v=13.2, current=1.5, soc=80, cells=(3300, 3301, 3299, 3302),
            balance="1 3"):
    row = [ts, "", pack_v, current, pack_v * current, soc, 99, 50.0, 100.0,
           25, 30, 10, 345]
    volts = [round(mv / 1000, 3) for mv in cells]
    row += volts + [""] * (16 - len(volts)) + [balance]
    return row


class DataFromCsvLineTest(unittest.TestCase):
    def test_rebuilds_payload_from_a_logged_row(self):
        data = pq.data_from_csv_line(",".join(str(x) for x in csv_row()))

        self.assertEqual(data["pack_mv"], 13200)
        self.assertEqual(data["cells_mv"], [3300, 3301, 3299, 3302])
        self.assertEqual(data["delta_mv"], 3)
        self.assertEqual(data["soc"], 80)
        self.assertEqual(data["remaining_ah"], 50.0)
        self.assertEqual(data["fcc_ah"], 100.0)
        self.assertEqual(data["balance_cells"], [1, 3])
        self.assertEqual(data["balance_mask"], 0b101)
        self.assertEqual(data["state"], "charging")

    def test_rejects_garbage(self):
        self.assertIsNone(pq.data_from_csv_line("not,a,real,row"))


class CsvLastRecordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "P-12345-2026-11.csv"
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(pq.CSV_HEADER)
            w.writerow(csv_row(ts=1000.0, soc=70))
            w.writerow(csv_row(ts=1010.0, soc=75))
        self.logger = pq.CsvLog(self.dir / "P-12345.csv")

    def tearDown(self):
        self.tmp.cleanup()

    def test_uses_the_newest_complete_row(self):
        data, ts = pq.csv_last_record(self.logger)
        self.assertEqual(ts, 1010.0)
        self.assertEqual(data["soc"], 75)

    def test_ignores_a_partially_written_last_row(self):
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            f.write("1020.0,2026-11-27T18:00:00,13.2,1.0")
        data, ts = pq.csv_last_record(self.logger)
        self.assertEqual(ts, 1010.0)


def frame():
    fr = bytearray(pq.FRAME_LEN)
    fr[4], fr[5], fr[6] = 0x93, 0x55, 0xAA
    fr[12:16] = (13200).to_bytes(4, "little")
    fr[16:24] = (3300).to_bytes(2, "little") + (3301).to_bytes(2, "little") + \
        (3299).to_bytes(2, "little") + (3302).to_bytes(2, "little")
    fr[62:64] = (5000).to_bytes(2, "little")
    fr[64:66] = (10000).to_bytes(2, "little")
    fr[90] = 80
    fr[104] = sum(fr[:-1]) & 0xFF
    return bytes(fr)


class ProbeProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def test_unsupported_protocol_is_rejected(self):
        class FakeClient:
            def __init__(self, *a, **kw):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def start_notify(self, *a):
                pass

        with mock.patch.object(pq, "BleakClient", FakeClient), \
                mock.patch.object(pq.Reader, "inquire", mock.AsyncMock(return_value=None)):
            result = await pq.probe_protocol("AA:BB")
        self.assertFalse(result["ok"])
        self.assertIn("unsupported", result["error"])

    async def test_valid_frame_and_version_are_accepted(self):
        class FakeClient:
            def __init__(self, *a, **kw):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return False
            async def start_notify(self, *a):
                pass

        with mock.patch.object(pq, "BleakClient", FakeClient), \
                mock.patch.object(pq.Reader, "inquire", mock.AsyncMock(return_value=frame())), \
                mock.patch.object(pq.Reader, "version", mock.AsyncMock(
                    return_value={"version": "1.2.3", "hardware": "T24-10"})):
            result = await pq.probe_protocol("AA:BB")
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["pack_mv"], 13200)
        self.assertEqual(result["info"]["version"], "1.2.3")


class RestoredRecordsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self._data_dir = pq.DATA_DIR
        self._last = pq.LAST_VALUES
        self._saved = pq.SAVED_DEVICES
        self._seen = pq.SEEN
        self._ignored = pq.IGNORED
        pq.DATA_DIR = self.dir
        pq.LAST_VALUES = {}
        pq.SAVED_DEVICES = {}
        pq.SEEN = {}
        pq.IGNORED = set()

    def tearDown(self):
        pq.DATA_DIR = self._data_dir
        pq.LAST_VALUES = self._last
        pq.SAVED_DEVICES = self._saved
        pq.SEEN = self._seen
        pq.IGNORED = self._ignored
        self.tmp.cleanup()

    def test_snapshot_is_used_without_a_scan(self):
        pq.LAST_VALUES["AA:BB"] = {"name": "P-12345", "updated": 123.0,
                                   "data": {"pack_mv": 13000, "soc": 55}}
        records = pq.restored_records()
        self.assertEqual(records["AA:BB"]["updated"], 123.0)
        self.assertEqual(records["AA:BB"]["data"]["soc"], 55)

    def test_scan_history_plus_csv_rebuilds_values(self):
        # log files are named after the display name, without the P- prefix
        with open(self.dir / "12345-2026-11.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(pq.CSV_HEADER)
            w.writerow(csv_row(ts=2222.0, soc=61))
        pq.SEEN["AA:BB"] = {"address": "AA:BB", "name": "P-12345", "ffe0": True}
        records = pq.restored_records()
        self.assertEqual(records["AA:BB"]["data"]["soc"], 61)
        self.assertEqual(records["AA:BB"]["updated"], 2222.0)

    def test_saved_device_without_data_still_restores(self):
        pq.SAVED_DEVICES["AA:BB"] = {"name": "R-12280BNN200-B00143", "manual": True}
        records = pq.restored_records()
        self.assertEqual(records["AA:BB"]["name"], "R-12280BNN200-B00143")
        self.assertEqual(records["AA:BB"]["data"], {})
        self.assertIsNone(records["AA:BB"]["updated"])

    def test_removed_battery_is_not_restored(self):
        pq.SEEN["AA:BB"] = {"address": "AA:BB", "name": "P-12345", "ffe0": True}
        pq.IGNORED.add("P-12345")
        self.assertEqual(pq.restored_records(), {})

    def test_known_devices_round_trip(self):
        file = self.dir / "devices.json"
        devices = {"AA:BB": {"name": "R-12280BNN200-B00143", "manual": True,
                             "updated": None, "data": {}}}
        with mock.patch.object(pq, "DEVICES_FILE", file), \
                mock.patch.object(pq, "DEVICES", devices):
            pq.save_known_devices(force=True)
            pq.SAVED_DEVICES.clear()
            pq.load_known_devices()
        self.assertTrue(pq.SAVED_DEVICES["AA:BB"]["manual"])
        self.assertEqual(pq.SAVED_DEVICES["AA:BB"]["name"], "R-12280BNN200-B00143")

    def test_utf8_bom_is_accepted(self):
        import json
        file = self.dir / "devices.json"
        payload = [{"address": "AA:BB", "name": "P-12345", "manual": True}]
        file.write_bytes(b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8"))
        with mock.patch.object(pq, "DEVICES_FILE", file):
            pq.load_known_devices()
        self.assertIn("AA:BB", pq.SAVED_DEVICES)

    def test_last_values_round_trip(self):
        file = self.dir / "last_values.json"
        with mock.patch.object(pq, "LAST_VALUES_FILE", file):
            pq.LAST_VALUES["AA:BB"] = {"name": "P-12345", "updated": 123.0,
                                       "data": {"pack_mv": 13000, "soc": 55}}
            pq.save_last_values(force=True)
            pq.LAST_VALUES.clear()
            pq.load_last_values()
        self.assertEqual(pq.LAST_VALUES["AA:BB"]["data"]["soc"], 55)


if __name__ == "__main__":
    unittest.main()
