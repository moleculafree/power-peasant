import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pq_monitor as pq


def make_frame(cells=(3300, 3301, 3299, 3302), pack_mv=13200, current_ma=1500,
               soc=87, soh=99, remaining_ah=15.0, fcc_ah=20.0,
               cell_temp_c=25, mos_temp_c=30, cycles=12, discharged_ah=345,
               balance_mask=0b101):
    """Build a valid 105-byte BLE frame with the given field values."""
    fr = bytearray(pq.FRAME_LEN)
    fr[4], fr[5], fr[6] = 0x93, 0x55, 0xAA
    fr[12:16] = int(pack_mv).to_bytes(4, "little")
    for i, mv in enumerate(cells):
        fr[16 + 2 * i:18 + 2 * i] = int(mv).to_bytes(2, "little")
    fr[48:52] = int(current_ma).to_bytes(4, "little", signed=True)
    fr[52:54] = int(cell_temp_c).to_bytes(2, "little", signed=True)
    fr[54:56] = int(mos_temp_c).to_bytes(2, "little", signed=True)
    fr[62:64] = int(round(remaining_ah * 100)).to_bytes(2, "little")
    fr[64:66] = int(round(fcc_ah * 100)).to_bytes(2, "little")
    fr[76:80] = bytes.fromhex("00000000")
    fr[80:83] = bytes.fromhex("000000")
    fr[84:88] = int(balance_mask).to_bytes(4, "little")
    fr[90] = soc
    fr[92] = soh
    fr[96:100] = int(cycles).to_bytes(4, "little")
    fr[100:104] = int(discharged_ah).to_bytes(4, "little")
    fr[104] = sum(fr[:-1]) & 0xFF
    return bytes(fr)


class RequestFrameTest(unittest.TestCase):
    def test_scan_command(self):
        self.assertEqual(pq.request_frame(0x13), bytes.fromhex("000004011355AA17"))

    def test_checksum_matches_body(self):
        for cmd in (0x00, 0x01, 0x13, 0xFF):
            frame = pq.request_frame(cmd)
            self.assertEqual(sum(frame[:-1]) & 0xFF, frame[-1])
            self.assertEqual(frame[4], cmd)


class IntegerHelpersTest(unittest.TestCase):
    def test_u16(self):
        self.assertEqual(pq.u16(bytes([0x34, 0x12]), 0), 0x1234)

    def test_s16(self):
        self.assertEqual(pq.s16(bytes([0xFF, 0xFF]), 0), -1)
        self.assertEqual(pq.s16(bytes([0x10, 0x27]), 0), 10000)

    def test_u32(self):
        self.assertEqual(pq.u32(bytes([1, 0, 0, 0]), 0), 1)

    def test_s32(self):
        self.assertEqual(pq.s32(bytes([0xFF, 0xFF, 0xFF, 0xFF]), 0), -1)
        self.assertEqual(pq.s32(bytes([0x00, 0x00, 0x00, 0x80]), 0), -(1 << 31))


class ValidFrameTest(unittest.TestCase):
    def test_accepts_valid_frame(self):
        self.assertTrue(pq.valid(make_frame()))

    def test_rejects_short_frame(self):
        self.assertFalse(pq.valid(make_frame()[:104]))

    def test_rejects_wrong_header(self):
        fr = bytearray(make_frame())
        fr[4] = 0x00
        fr[104] = sum(fr[:-1]) & 0xFF
        self.assertFalse(pq.valid(bytes(fr)))

    def test_rejects_bad_checksum(self):
        fr = bytearray(make_frame())
        fr[104] ^= 0xFF
        self.assertFalse(pq.valid(bytes(fr)))


class BatteryNameTest(unittest.TestCase):
    def test_known_brand_prefixes(self):
        for name in ("P-12100BNNA70-B00143", "R-12280BNN200-B00143",
                     "LT-12100BNN200", "L-12V100-100", "S-12320", "SS-12320"):
            self.assertTrue(pq.is_battery_name(name), name)

    def test_other_devices_are_rejected(self):
        for name in ("JK-BD4A20S4P", "TV-OLED", "65\" OLED", "", "P-123",
                     "LT-", "L-V100"):
            self.assertFalse(pq.is_battery_name(name), name)


class VersionFrameTest(unittest.TestCase):
    def test_parses_firmware_date_and_hardware(self):
        fr = bytearray(24)
        fr[5:7] = b"\x55\xaa"
        fr[8:10] = (1).to_bytes(2, "little")
        fr[10:12] = (2).to_bytes(2, "little")
        fr[12:14] = (3).to_bytes(2, "little")
        fr[14:16] = (2024).to_bytes(2, "little")
        fr[16] = 8
        fr[17] = 27
        fr[18:24] = b"T24-10"
        info = pq.parse_version_response(bytes(fr))
        self.assertEqual(info["version"], "1.2.3")
        self.assertEqual(info["date"], "2024-8-27")
        self.assertEqual(info["hardware"], "T24-10")

    def test_rejects_garbage(self):
        self.assertEqual(pq.parse_version_response(b"\x00" * 24), {})


class DecodeTest(unittest.TestCase):
    def test_decodes_all_fields(self):
        d = pq.decode(make_frame())
        self.assertEqual(d["pack_mv"], 13200)
        self.assertEqual(d["cells_mv"], [3300, 3301, 3299, 3302])
        self.assertEqual(d["delta_mv"], 3)
        self.assertAlmostEqual(d["current_a"], 1.5)
        self.assertAlmostEqual(d["power_w"], 19.8)
        self.assertEqual(d["soc"], 87)
        self.assertEqual(d["soh"], 99)
        self.assertAlmostEqual(d["remaining_ah"], 15.0)
        self.assertAlmostEqual(d["fcc_ah"], 20.0)
        self.assertEqual(d["cell_temp_c"], 25)
        self.assertEqual(d["mos_temp_c"], 30)
        self.assertEqual(d["cycles"], 12)
        self.assertEqual(d["discharged_ah"], 345)
        self.assertEqual(d["balance_cells"], [1, 3])
        self.assertEqual(d["state"], "charging")

    def test_zero_cell_terminates_list(self):
        d = pq.decode(make_frame(cells=(3300, 0, 3290, 3305)))
        self.assertEqual(d["cells_mv"], [3300])

    def test_discharging_state(self):
        d = pq.decode(make_frame(current_ma=-500))
        self.assertEqual(d["state"], "discharging")
        self.assertLess(d["power_w"], 0)

    def test_idle_state(self):
        d = pq.decode(make_frame(current_ma=0))
        self.assertEqual(d["state"], "idle")
        self.assertEqual(d["power_w"], 0.0)

    def test_no_cells_gives_no_delta(self):
        d = pq.decode(make_frame(cells=()))
        self.assertEqual(d["cells_mv"], [])
        self.assertIsNone(d["delta_mv"])


if __name__ == "__main__":
    unittest.main()
