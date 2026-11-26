import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pq_monitor as pq


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def release(tag="v1.0.5", url="https://github.com/moleculafree/power-peasant/releases/tag/v1.0.5"):
    return FakeResponse({
        "tag_name": tag,
        "html_url": url,
        "published_at": "2026-11-26T10:00:00Z",
    })


class VersionTupleTest(unittest.TestCase):
    def test_semver(self):
        self.assertEqual(pq._version_tuple("v1.2.3"), (1, 2, 3))
        self.assertEqual(pq._version_tuple("1.0.10"), (1, 0, 10))

    def test_invalid(self):
        self.assertIsNone(pq._version_tuple("dev"))
        self.assertIsNone(pq._version_tuple("v1.2"))


class CheckForUpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.update_file = Path(self.tmp.name) / "update_check.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_newer_release_is_reported(self):
        with mock.patch.object(pq, "UPDATE_FILE", self.update_file), \
                mock.patch.object(pq, "about_info", return_value={"version": "1.0.4"}), \
                mock.patch.object(pq, "urlopen", return_value=release()) as fetch:
            result = pq.check_for_update(force=True)

        self.assertTrue(result["ok"])
        self.assertTrue(result["update"])
        self.assertEqual(result["current"], "1.0.4")
        self.assertEqual(result["latest"], "1.0.5")
        self.assertEqual(result["url"], "https://github.com/moleculafree/power-peasant/releases/tag/v1.0.5")
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(self.update_file.exists())

    def test_same_version_is_not_an_update(self):
        with mock.patch.object(pq, "UPDATE_FILE", self.update_file), \
                mock.patch.object(pq, "about_info", return_value={"version": "1.0.5"}), \
                mock.patch.object(pq, "urlopen", return_value=release("v1.0.5")):
            result = pq.check_for_update(force=True)

        self.assertTrue(result["ok"])
        self.assertFalse(result["update"])

    def test_cache_avoids_second_request(self):
        with mock.patch.object(pq, "UPDATE_FILE", self.update_file), \
                mock.patch.object(pq, "about_info", return_value={"version": "1.0.4"}), \
                mock.patch.object(pq, "urlopen", return_value=release()) as fetch:
            first = pq.check_for_update(force=True)
            second = pq.check_for_update(force=False)

        self.assertEqual(fetch.call_count, 1)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(second["latest"], "1.0.5")

    def test_dev_build_is_skipped(self):
        with mock.patch.object(pq, "UPDATE_FILE", self.update_file), \
                mock.patch.object(pq, "about_info", return_value={"version": "dev"}), \
                mock.patch.object(pq, "urlopen") as fetch:
            result = pq.check_for_update(force=True)

        self.assertTrue(result["disabled"])
        self.assertFalse(result["update"])
        fetch.assert_not_called()

    def test_network_error_is_reported(self):
        with mock.patch.object(pq, "UPDATE_FILE", self.update_file), \
                mock.patch.object(pq, "about_info", return_value={"version": "1.0.4"}), \
                mock.patch.object(pq, "urlopen", side_effect=OSError("offline")):
            result = pq.check_for_update(force=True)

        self.assertFalse(result["ok"])
        self.assertIn("offline", result["error"])


if __name__ == "__main__":
    unittest.main()
