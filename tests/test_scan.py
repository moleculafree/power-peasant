import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pq_monitor as pq


def _discover_result():
    dev = SimpleNamespace(name="P-12345")
    adv = SimpleNamespace(rssi=-60, service_uuids=["0000ffe0-0000-1000-8000-00805f9b34fb"])
    return {"AA:BB:CC:DD:EE:FF": (dev, adv)}


class ScanTargetTest(unittest.TestCase):
    """lookup used by POST /api/devices: live scan first, then saved history."""

    def setUp(self):
        with pq.LOCK:
            self._scan = list(pq.SCAN["items"])
            self._seen = dict(pq.SEEN)
            pq.SCAN["items"] = []
            pq.SEEN.clear()

    def tearDown(self):
        with pq.LOCK:
            pq.SCAN["items"] = self._scan
            pq.SEEN.clear()
            pq.SEEN.update(self._seen)

    def test_live_item_is_found_case_insensitively(self):
        pq.SCAN["items"] = [{"address": "AA:BB:CC:DD:EE:FF", "name": "P-12345", "rssi": -55}]
        got = pq.scan_target("aa:bb:cc:dd:ee:ff")
        self.assertEqual(got, {"address": "AA:BB:CC:DD:EE:FF", "name": "P-12345", "rssi": -55})

    def test_live_scan_wins_over_history(self):
        pq.SCAN["items"] = [{"address": "AA:BB:CC:DD:EE:FF", "name": "P-12345", "rssi": -55}]
        pq.SEEN["AA:BB:CC:DD:EE:FF"] = {"address": "AA:BB:CC:DD:EE:FF",
                                        "name": "old", "last_rssi": -90}
        self.assertEqual(pq.scan_target("AA:BB:CC:DD:EE:FF")["rssi"], -55)

    def test_history_is_found_when_not_in_air(self):
        pq.SEEN["AA:BB:CC:DD:EE:FF"] = {"address": "AA:BB:CC:DD:EE:FF",
                                        "name": "P-12345", "last_rssi": -90}
        got = pq.scan_target("AA:BB:CC:DD:EE:FF")
        self.assertEqual(got, {"address": "AA:BB:CC:DD:EE:FF", "name": "P-12345", "rssi": -90})

    def test_history_lookup_ignores_case(self):
        pq.SEEN["AA:BB:CC:DD:EE:FF"] = {"address": "AA:BB:CC:DD:EE:FF",
                                        "name": "P-12345", "last_rssi": -90}
        self.assertEqual(pq.scan_target("aa:bb:cc:dd:ee:ff")["address"], "AA:BB:CC:DD:EE:FF")

    def test_unknown_address_returns_none(self):
        self.assertIsNone(pq.scan_target("00:00:00:00:00:00"))
        self.assertIsNone(pq.scan_target(""))


class DiscoverFilterTest(unittest.IsolatedAsyncioTestCase):
    def _items(self):
        return [
            {"address": "A1", "name": "P-12100BNNA70-B00143", "rssi": -60, "ffe0": True},
            {"address": "A2", "name": "LT-12100BNN200", "rssi": -61, "ffe0": True},
            {"address": "A3", "name": "JK-BD4A20S4P", "rssi": -62, "ffe0": True},
            {"address": "A4", "name": "P-99999", "rssi": -63, "ffe0": False},
        ]

    async def test_known_names_pass_and_foreign_ffe0_does_not(self):
        async def fake_scan():
            return self._items()
        with mock.patch.object(pq, "scan_all", fake_scan), \
                mock.patch.object(pq, "SHOW_ALL", False):
            out = await pq.discover([])
        self.assertEqual([d["address"] for d in out], ["A1", "A2"])

    async def test_show_all_accepts_named_ffe0_devices(self):
        async def fake_scan():
            return self._items()
        with mock.patch.object(pq, "scan_all", fake_scan), \
                mock.patch.object(pq, "SHOW_ALL", True):
            out = await pq.discover([])
        self.assertEqual({d["address"] for d in out}, {"A1", "A2", "A3"})


class ScanLockTest(unittest.IsolatedAsyncioTestCase):
    """scan_all() serialises radio scans and reuses a fresh result."""

    def setUp(self):
        self._lock = pq.SCAN_LOCK
        pq.SCAN_LOCK = asyncio.Lock()
        with pq.LOCK:
            self._scan = dict(pq.SCAN)
            self._seen = dict(pq.SEEN)
            pq.SCAN.update(ts=0.0, items=[])
            pq.SEEN.clear()

    def tearDown(self):
        pq.SCAN_LOCK = self._lock
        with pq.LOCK:
            pq.SCAN.update(self._scan)
            pq.SEEN.clear()
            pq.SEEN.update(self._seen)

    def _patch_radio(self, calls):
        async def fake(timeout, return_adv):
            calls.append(1)
            return _discover_result()
        return mock.patch.object(pq.BleakScanner, "discover", fake), \
            mock.patch.object(pq, "save_seen")

    async def test_concurrent_calls_share_one_radio_scan(self):
        calls = []
        radio, save = self._patch_radio(calls)
        with radio, save:
            first, second = await asyncio.gather(pq.scan_all(), pq.scan_all())
        self.assertEqual(len(calls), 1)
        self.assertEqual(first, second)
        self.assertEqual(first[0]["name"], "P-12345")
        self.assertTrue(first[0]["ffe0"])

    async def test_stale_result_triggers_a_new_scan(self):
        calls = []
        radio, save = self._patch_radio(calls)
        with radio, save:
            await pq.scan_all()
            pq.SCAN["ts"] = 0.0
            await pq.scan_all()
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
