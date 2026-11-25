import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pq_monitor as pq

FUNDING_YML = """\
# sponsor links
custom:
  - "https://send.monobank.ua/2MXeqRNEPw"
  - https://www.paypal.com/donate/?hosted_button_id=J6SPY3YL5RLGE
"""


class ParseFundingTest(unittest.TestCase):
    def test_parses_custom_and_platforms(self):
        urls = pq._parse_funding(FUNDING_YML)
        self.assertEqual(urls, [
            "https://send.monobank.ua/2MXeqRNEPw",
            "https://www.paypal.com/donate/?hosted_button_id=J6SPY3YL5RLGE",
        ])

    def test_deduplicates(self):
        text = "custom:\n- https://example.com/x\n- https://example.com/x\n"
        self.assertEqual(pq._parse_funding(text), ["https://example.com/x"])

    def test_ignores_non_urls_and_empty(self):
        self.assertEqual(pq._parse_funding("custom:\n- not-a-url\n- ''\n"), [])

    def test_inline_list(self):
        urls = pq._parse_funding(
            'custom: ["https://example.com/a", "https://example.com/b"]\n')
        self.assertEqual(urls, ["https://example.com/a", "https://example.com/b"])

    def test_platform_keys_without_urls_are_ignored(self):
        self.assertEqual(pq._parse_funding("github: moleculafree\npatreon: peasant\n"), [])


class FundingLabelTest(unittest.TestCase):
    def test_known_hosts(self):
        self.assertEqual(pq._funding_label("https://send.monobank.ua/abc"), "Monobank")
        self.assertEqual(
            pq._funding_label("https://www.paypal.com/donate/?hosted_button_id=x"), "PayPal")

    def test_unknown_host_uses_domain(self):
        self.assertEqual(pq._funding_label("https://example.com/donate"), "example.com")


class FundingLinksTest(unittest.TestCase):
    def test_reads_funding_from_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets").mkdir()
            (root / "assets" / "FUNDING.yml").write_text(FUNDING_YML, encoding="utf-8")
            with mock.patch.object(pq, "ROOT", root):
                links = pq.funding_links()
            self.assertEqual([l["label"] for l in links],
                             ["Monobank", "PayPal"])
            self.assertEqual(links[0]["url"], "https://send.monobank.ua/2MXeqRNEPw")

    def test_falls_back_to_github_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".github").mkdir()
            (root / ".github" / "FUNDING.yml").write_text(FUNDING_YML, encoding="utf-8")
            with mock.patch.object(pq, "ROOT", root):
                links = pq.funding_links()
            self.assertEqual(len(links), 2)

    def test_extra_donate_link_comes_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets").mkdir()
            (root / "assets" / "FUNDING.yml").write_text(FUNDING_YML, encoding="utf-8")
            with mock.patch.object(pq, "ROOT", root):
                links = pq.funding_links("https://example.com/give")
            self.assertEqual(links[0], {"label": "example.com", "url": "https://example.com/give"})

    def test_empty_when_nothing_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(pq, "ROOT", Path(tmp)):
                self.assertEqual(pq.funding_links(), [])


class AboutInfoTest(unittest.TestCase):
    def test_reads_version_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "assets").mkdir()
            (root / "assets" / "version.json").write_text(
                '{"version":"1.4.2","build":"v1.4.2","date":"2026-11-26"}',
                encoding="utf-8")
            with mock.patch.object(pq, "ROOT", root):
                info = pq.about_info()
            self.assertEqual(info["name"], "Power Peasant")
            self.assertEqual(info["version"], "1.4.2")
            self.assertEqual(info["build"], "v1.4.2")
            self.assertEqual(info["date"], "2026-11-26")
            self.assertIn("funding", info)
            self.assertIsInstance(info["frozen"], bool)

    def test_missing_version_json_stays_dev(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(pq, "ROOT", Path(tmp)):
                info = pq.about_info()
            self.assertEqual(info["version"], "dev")
            self.assertEqual(info["build"], "dev")


if __name__ == "__main__":
    unittest.main()
