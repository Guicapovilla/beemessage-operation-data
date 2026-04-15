from datetime import datetime, timezone
import unittest

from bee_operation_data.common import days_between, parse_dt


class CommonTests(unittest.TestCase):
    def test_parse_dt_iso(self):
        dt = parse_dt("2026-04-14T10:00:00Z")
        self.assertEqual(dt, datetime(2026, 4, 14, 10, 0, 0, tzinfo=timezone.utc))

    def test_days_between_none(self):
        self.assertIsNone(days_between(None, None))

    def test_days_between_positive(self):
        start = datetime(2026, 4, 1, tzinfo=timezone.utc)
        end = datetime(2026, 4, 3, tzinfo=timezone.utc)
        self.assertEqual(days_between(start, end), 2)


if __name__ == "__main__":
    unittest.main()

