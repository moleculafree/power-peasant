import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pq_monitor as pq


class MergeConfigTest(unittest.TestCase):
    def test_overrides_known_value(self):
        cfg = pq._merge_cfg(pq.DEFAULT_CONFIG, {"ui": {"language": "en"}})
        self.assertEqual(cfg["ui"]["language"], "en")

    def test_keeps_defaults_for_missing_keys(self):
        cfg = pq._merge_cfg(pq.DEFAULT_CONFIG, {"ui": {"language": "de"}})
        self.assertEqual(cfg["ui"]["cells"]["scaleMinV"], 3.00)
        self.assertEqual(cfg["server"]["pollIntervalSec"], 2.0)

    def test_drops_unknown_keys(self):
        cfg = pq._merge_cfg(pq.DEFAULT_CONFIG, {"ui": {"bogus": 1}, "junk": True})
        self.assertNotIn("bogus", cfg["ui"])
        self.assertNotIn("junk", cfg)

    def test_wrong_type_falls_back(self):
        cfg = pq._merge_cfg(pq.DEFAULT_CONFIG, {"ui": {"language": 5}})
        self.assertEqual(cfg["ui"]["language"], "en")
        cfg = pq._merge_cfg(pq.DEFAULT_CONFIG, {"server": {"pollIntervalSec": "fast"}})
        self.assertEqual(cfg["server"]["pollIntervalSec"], 2.0)

    def test_list_keeps_only_strings(self):
        cfg = pq._merge_cfg(pq.DEFAULT_CONFIG,
                            {"ui": {"colors": {"chartPalette": ["#fff", 7, "#000"]}}})
        self.assertEqual(cfg["ui"]["colors"]["chartPalette"], ["#fff", "#000"])


class CleanConfigTest(unittest.TestCase):
    def clean(self, raw):
        return pq._clean_config(pq._merge_cfg(pq.DEFAULT_CONFIG, raw))

    def test_invalid_colors_reset_to_defaults(self):
        cfg = self.clean({"ui": {"colors": {"cellMax": "red", "cellMin": "#12"}}})
        self.assertEqual(cfg["ui"]["colors"]["cellMax"], "#35d07f")
        self.assertEqual(cfg["ui"]["colors"]["cellMin"], "#f2a33c")

    def test_invalid_palette_reset(self):
        cfg = self.clean({"ui": {"colors": {"chartPalette": ["nope"]}}})
        self.assertEqual(cfg["ui"]["colors"]["chartPalette"],
                         pq.DEFAULT_CONFIG["ui"]["colors"]["chartPalette"])

    def test_bad_scale_pair_resets(self):
        cfg = self.clean({"ui": {"cells": {"scaleMinV": 3.4, "scaleMaxV": 3.0}}})
        self.assertEqual(cfg["ui"]["cells"]["scaleMinV"], 3.00)
        self.assertEqual(cfg["ui"]["cells"]["scaleMaxV"], 3.45)

    def test_numbers_are_clamped(self):
        cfg = self.clean({"server": {"pollIntervalSec": 0.1, "historyHours": 999}})
        self.assertEqual(cfg["server"]["pollIntervalSec"], 0.5)
        self.assertEqual(cfg["server"]["historyHours"], 48)

    def test_warn_threshold_not_below_ok(self):
        cfg = self.clean({"ui": {"thresholds": {"cellDeltaOkMv": 50, "cellDeltaWarnMv": 10}}})
        self.assertEqual(cfg["ui"]["thresholds"]["cellDeltaWarnMv"], 50)

    def test_default_window_keeps_seven_days(self):
        cfg = self.clean({"ui": {"chart": {"defaultWindowSec": 604800}}})
        self.assertEqual(cfg["ui"]["chart"]["defaultWindowSec"], 604800)

    def test_default_window_keeps_two_years(self):
        cfg = self.clean({"ui": {"chart": {"defaultWindowSec": 63072000}}})
        self.assertEqual(cfg["ui"]["chart"]["defaultWindowSec"], 63072000)

    def test_unknown_language_defaults(self):
        cfg = self.clean({"ui": {"language": "xx"}})
        self.assertEqual(cfg["ui"]["language"], "en")


if __name__ == "__main__":
    unittest.main()
