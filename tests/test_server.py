"""Unit tests for server.py helpers and cheap endpoints — no network needed.

Run: python3 -m unittest discover -s tests -v
"""
import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server


class Time24Tests(unittest.TestCase):
    def test_parses_local_datetime(self):
        self.assertEqual(server._time24({"showDateTimeLocal": "2026-07-06T19:30:00"}), "19:30")

    def test_zero_pads(self):
        self.assertEqual(server._time24({"showDateTimeLocal": "2026-07-06T09:05:00"}), "09:05")

    def test_unparseable_returns_none(self):
        self.assertIsNone(server._time24({"showDateTimeLocal": ""}))
        self.assertIsNone(server._time24({}))


class SeatStatusTests(unittest.TestCase):
    def test_sold_out_wins(self):
        self.assertEqual(server._seat_status({"isSoldOut": True, "isAlmostSoldOut": True}), "sold_out")

    def test_almost(self):
        self.assertEqual(server._seat_status({"isAlmostSoldOut": True}), "almost")

    def test_open_default(self):
        self.assertEqual(server._seat_status({}), "open")


class FillKeyTests(unittest.TestCase):
    def test_stable_and_distinct(self):
        d = datetime.date(2026, 7, 6)
        k1 = server._fill_key(2325, d, "Obsession", "Standard")
        k2 = server._fill_key(2325, d, "Obsession", "IMAX")
        self.assertEqual(k1, "2325|2026-07-06|Obsession|Standard")
        self.assertNotEqual(k1, k2)


class StatusEndpointTests(unittest.TestCase):
    def test_status_shape(self):
        client = server.app.test_client()
        data = client.get("/api/status").get_json()
        self.assertTrue(data["ok"])
        self.assertIn(data["stage"], {"idle", "amc", "letterboxd", "details", "done"})
        self.assertIsInstance(data["progress"], int)


class GcsSnapshotTests(unittest.TestCase):
    def test_disabled_without_bucket_env(self):
        # GCS_BUCKET unset in tests: load/save must be no-ops, not errors.
        self.assertEqual(server.GCS_BUCKET, "")
        server._load_gcs_snapshot()
        server._save_gcs_snapshot()


if __name__ == "__main__":
    unittest.main()
