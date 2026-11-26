import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_negative_chart_color_kept(self):
        cfg = self.clean({"ui": {"colors": {"chartNegative": "#0e7490"}}})
        self.assertEqual(cfg["ui"]["colors"]["chartNegative"], "#0e7490")

    def test_invalid_negative_chart_color_resets(self):
        cfg = self.clean({"ui": {"colors": {"chartNegative": "#12"}}})
        self.assertEqual(cfg["ui"]["colors"]["chartNegative"],
                         pq.DEFAULT_CONFIG["ui"]["colors"]["chartNegative"])

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


class ViewConfigTest(unittest.TestCase):
    def clean(self, raw):
        return pq._clean_config(pq._merge_cfg(pq.DEFAULT_CONFIG, raw))

    def test_default_view(self):
        cfg = self.clean({})
        self.assertEqual(cfg["ui"]["view"],
                         {"metric": "cells", "chartStyle": "line",
                          "windowSec": 600, "selected": ""})

    def test_invalid_view_values_fall_back(self):
        cfg = self.clean({"ui": {"view": {"metric": "bogus", "chartStyle": "sparkle",
                                          "selected": 5}}})
        self.assertEqual(cfg["ui"]["view"]["metric"], "cells")
        self.assertEqual(cfg["ui"]["view"]["chartStyle"], "line")
        self.assertEqual(cfg["ui"]["view"]["selected"], "")

    def test_view_window_snaps_to_selector(self):
        cfg = self.clean({"ui": {"view": {"windowSec": 1234}}})
        self.assertEqual(cfg["ui"]["view"]["windowSec"], 1800)

    def test_view_zero_means_all(self):
        cfg = self.clean({"ui": {"view": {"windowSec": 0}}})
        self.assertEqual(cfg["ui"]["view"]["windowSec"], 0)

    def test_view_selected_is_trimmed_and_limited(self):
        cfg = self.clean({"ui": {"view": {"selected": "  B01607  "}}})
        self.assertEqual(cfg["ui"]["view"]["selected"], "B01607")
        cfg = self.clean({"ui": {"view": {"selected": "x" * 500}}})
        self.assertEqual(len(cfg["ui"]["view"]["selected"]), 128)


class UiMetaTest(unittest.TestCase):
    def test_lists_match_view_constants(self):
        meta = pq.ui_meta()
        self.assertEqual(meta["windows"], list(pq.VIEW_WINDOWS))
        self.assertEqual(meta["styles"], list(pq.VIEW_STYLES))
        self.assertEqual(meta["metrics"], list(pq.VIEW_METRICS))
        self.assertEqual(meta["windows"][0], 0)
        self.assertIn(600, meta["windows"])


class SaveConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_file = pq.CONFIG_FILE
        self.old_cache = dict(pq._cfg_cache)
        pq.CONFIG_FILE = Path(self.tmp.name) / "config.json"
        pq._cfg_cache["mtime"] = None
        pq._cfg_cache["data"] = None

    def tearDown(self):
        pq.CONFIG_FILE = self.old_file
        pq._cfg_cache.update(self.old_cache)
        self.tmp.cleanup()

    def test_save_writes_file_and_reads_back(self):
        cfg = pq._clean_config(pq._merge_cfg(pq.DEFAULT_CONFIG, {}))
        cfg["ui"]["view"]["metric"] = "current"
        pq.save_config(cfg)
        on_disk = json.loads(pq.CONFIG_FILE.read_text(encoding="utf-8"))
        self.assertEqual(on_disk, cfg)
        self.assertEqual(pq.get_config()["ui"]["view"]["metric"], "current")

    def test_first_run_uses_default_window(self):
        pq.CONFIG_FILE.write_text(
            json.dumps({"ui": {"chart": {"defaultWindowSec": 1800}}}), encoding="utf-8")
        cfg = pq.get_config()
        self.assertEqual(cfg["ui"]["view"]["windowSec"], 1800)
        self.assertEqual(cfg["ui"]["chart"]["defaultWindowSec"], 1800)

    def test_set_view_persists_patch_only(self):
        view = pq.set_view({"metric": "soc", "chartStyle": "bars",
                            "windowSec": 1800, "selected": "B01607"})
        self.assertEqual(view, {"metric": "soc", "chartStyle": "bars",
                                "windowSec": 1800, "selected": "B01607"})
        stored = pq.get_config()["ui"]["view"]
        self.assertEqual(stored, view)
        # unrelated sections keep their values
        self.assertEqual(pq.get_config()["ui"]["colors"]["cellMax"], "#35d07f")


class EnsureConfigTest(unittest.TestCase):
    """data/config.json is created on first start, migrating the old assets copy."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.target = root / "data" / "config.json"
        self.target.parent.mkdir(parents=True)
        self.template = root / "assets" / "config.json"
        self.template.parent.mkdir(parents=True)

    def ensure(self):
        with mock.patch.object(pq, "CONFIG_FILE", self.target), \
                mock.patch.object(pq, "CONFIG_TEMPLATE", self.template):
            pq.ensure_config()
        return json.loads(self.target.read_text(encoding="utf-8"))

    def test_creates_defaults_without_template(self):
        self.assertEqual(self.ensure(), pq.DEFAULT_CONFIG)

    def test_migrates_legacy_assets_config(self):
        self.template.write_text(
            '{"ui": {"language": "de"}, "server": {"pollIntervalSec": 5}}',
            encoding="utf-8")
        data = self.ensure()
        self.assertEqual(data["ui"]["language"], "de")
        self.assertEqual(data["server"]["pollIntervalSec"], 5)

    def test_keeps_existing_data_config(self):
        self.target.write_text('{"ui": {"language": "uk"}}', encoding="utf-8")
        self.template.write_text('{"ui": {"language": "de"}}', encoding="utf-8")
        data = self.ensure()
        self.assertEqual(data["ui"]["language"], "uk")


if __name__ == "__main__":
    unittest.main()
