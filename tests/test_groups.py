import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pq_monitor as pq


def device(name, connected=True, pack_mv=13200, current_a=1.0, soc=80,
           delta_mv=5, cell_temp_c=25, mos_temp_c=30, remaining_ah=50.0,
           fcc_ah=100.0, soh=99, cycles=10, discharged_ah=100):
    return {
        "id": name.lower(),
        "name": name,
        "connected": connected,
        "data": {
            "pack_mv": pack_mv,
            "current_a": current_a,
            "soc": soc,
            "delta_mv": delta_mv,
            "cell_temp_c": cell_temp_c,
            "mos_temp_c": mos_temp_c,
            "remaining_ah": remaining_ah,
            "fcc_ah": fcc_ah,
            "soh": soh,
            "cycles": cycles,
            "discharged_ah": discharged_ah,
        },
    }


class AggregateTest(unittest.TestCase):
    def test_parallel_sums_current_and_capacity(self):
        devices = {
            "a": device("A", current_a=1.0),
            "b": device("B", pack_mv=13000, current_a=2.0, remaining_ah=30.0),
        }
        g = {"members": ["A", "B"], "mode": "parallel"}
        with mock.patch.object(pq, "DEVICES", devices):
            agg = pq.aggregate(g)

        self.assertEqual(agg["kind"], "group")
        self.assertEqual(agg["members_total"], 2)
        self.assertEqual(agg["online"], 2)
        self.assertFalse(agg["partial"])
        self.assertEqual(agg["pack_mv"], 13100)
        self.assertEqual(agg["current_a"], 3.0)
        self.assertEqual(agg["cap_total_ah"], 200.0)
        self.assertEqual(agg["cap_online_ah"], 200.0)
        self.assertEqual(agg["remaining_ah"], 80.0)
        self.assertEqual(agg["soc"], 40)
        self.assertAlmostEqual(agg["power_w"], 39.3)

    def test_series_averages_current_and_uses_min_capacity(self):
        devices = {"a": device("A", current_a=1.0), "b": device("B", current_a=2.0)}
        g = {"members": ["A", "B"], "mode": "series"}
        with mock.patch.object(pq, "DEVICES", devices):
            agg = pq.aggregate(g)

        self.assertEqual(agg["pack_mv"], 26400)
        self.assertEqual(agg["current_a"], 1.5)
        self.assertEqual(agg["remaining_ah"], 50.0)
        self.assertEqual(agg["cap_total_ah"], 100.0)

    def test_offline_member_keeps_known_capacity(self):
        devices = {
            "a": device("A", current_a=1.0),
            "b": device("B", connected=False, remaining_ah=0.0),
        }
        g = {"members": ["A", "B"], "mode": "parallel"}
        with mock.patch.object(pq, "DEVICES", devices):
            agg = pq.aggregate(g)

        self.assertEqual(agg["online"], 1)
        self.assertTrue(agg["partial"])
        self.assertEqual(agg["cap_total_ah"], 200.0)
        self.assertEqual(agg["cap_online_ah"], 100.0)

    def test_unknown_member_is_missing(self):
        devices = {"a": device("A")}
        g = {"members": ["A", "Z"], "mode": "parallel"}
        with mock.patch.object(pq, "DEVICES", devices):
            agg = pq.aggregate(g)

        self.assertEqual(agg["online"], 1)
        self.assertTrue(agg["members"][1]["missing"])
        self.assertIsNone(agg["members"][1]["id"])

    def test_all_offline_keeps_last_known_values_as_stale(self):
        devices = {"a": device("A", connected=False)}
        g = {"members": ["A"], "mode": "parallel"}
        with mock.patch.object(pq, "DEVICES", devices):
            agg = pq.aggregate(g)

        self.assertEqual(agg["online"], 0)
        self.assertTrue(agg["stale"])
        self.assertEqual(agg["pack_mv"], 13200)
        self.assertEqual(agg["members"][0]["stale"], True)

    def test_no_known_values_returns_base_only(self):
        devices = {"a": {**device("A", connected=False), "data": {}}}
        g = {"members": ["A"], "mode": "parallel"}
        with mock.patch.object(pq, "DEVICES", devices):
            agg = pq.aggregate(g)

        self.assertEqual(agg["online"], 0)
        self.assertFalse(agg["stale"])
        self.assertNotIn("pack_mv", agg)


class GroupTimeTest(unittest.TestCase):
    def test_parallel_discharge_uses_total_capacity_and_current(self):
        devices = {
            "a": device("A", current_a=-1.0, remaining_ah=50.0),
            "b": device("B", current_a=-2.0, remaining_ah=30.0),
        }
        g = {"members": ["A", "B"], "mode": "parallel"}
        with mock.patch.object(pq, "DEVICES", devices):
            agg = pq.aggregate(g)

        self.assertAlmostEqual(agg["time_empty_sec"], 80.0 / 3.0 * 3600.0, places=0)
        self.assertIsNone(agg["time_full_sec"])

    def test_series_discharge_uses_weakest_battery(self):
        devices = {
            "a": device("A", current_a=-1.0, remaining_ah=50.0),
            "b": device("B", current_a=-1.0, remaining_ah=30.0),
        }
        g = {"members": ["A", "B"], "mode": "series"}
        with mock.patch.object(pq, "DEVICES", devices):
            agg = pq.aggregate(g)

        self.assertEqual(agg["time_empty_sec"], 30.0 * 3600.0)
        self.assertIsNone(agg["time_full_sec"])

    def test_series_charge_uses_max_need(self):
        devices = {
            "a": device("A", current_a=1.0, remaining_ah=90.0, fcc_ah=95.0),
            "b": device("B", current_a=1.0, remaining_ah=80.0, fcc_ah=100.0),
        }
        g = {"members": ["A", "B"], "mode": "series"}
        with mock.patch.object(pq, "DEVICES", devices):
            agg = pq.aggregate(g)

        # A needs 5 Ah, B needs 20 Ah -> the full charge takes 20 h at 1 A.
        self.assertIsNone(agg["time_empty_sec"])
        self.assertEqual(agg["time_full_sec"], 20.0 * 3600.0)

    def test_parallel_charge_sums_need_and_current(self):
        devices = {
            "a": device("A", current_a=1.0, remaining_ah=90.0),
            "b": device("B", current_a=2.0, remaining_ah=80.0),
        }
        g = {"members": ["A", "B"], "mode": "parallel"}
        with mock.patch.object(pq, "DEVICES", devices):
            agg = pq.aggregate(g)

        self.assertIsNone(agg["time_empty_sec"])
        self.assertAlmostEqual(agg["time_full_sec"], 30.0 / 3.0 * 3600.0, places=0)

    def test_idle_group_has_no_time(self):
        devices = {"a": device("A", current_a=0.0), "b": device("B", current_a=0.0)}
        g = {"members": ["A", "B"], "mode": "parallel"}
        with mock.patch.object(pq, "DEVICES", devices):
            agg = pq.aggregate(g)

        self.assertIsNone(agg["time_empty_sec"])
        self.assertIsNone(agg["time_full_sec"])


class SanitizeIdTest(unittest.TestCase):
    def test_strips_unsafe_characters(self):
        self.assertEqual(pq.sanitize_id("a b/c!d"), "abcd")

    def test_empty_generates_uuid(self):
        value = pq.sanitize_id("")
        self.assertEqual(len(value), 32)
        self.assertRegex(value, r"^[0-9a-f]{32}$")


if __name__ == "__main__":
    unittest.main()
