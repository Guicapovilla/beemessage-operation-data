from datetime import datetime, timezone
import unittest

from bee_operation_data.plane_ops import classify
from bee_operation_data.time_window import WeekWindow


class PlaneOpsTests(unittest.TestCase):
    def test_classify_basic(self):
        now = datetime(2026, 4, 14, 12, 0, 0, tzinfo=timezone.utc)
        week_start = datetime(2026, 4, 7, 0, 0, 0, tzinfo=timezone.utc)
        week_end = datetime(2026, 4, 13, 23, 59, 59, tzinfo=timezone.utc)
        prev_week_start = datetime(2026, 3, 31, 0, 0, 0, tzinfo=timezone.utc)
        timebox = WeekWindow(now=now, week_start=week_start, week_end=week_end, prev_week_start=prev_week_start)
        states = {"s1": {"group": "started"}}
        issues = [
            {
                "id": "1",
                "created_at": "2026-04-10T12:00:00Z",
                "state": {"id": "s1", "group": "started"},
                "assignees": [],
            }
        ]
        this_week, prev_week, backlog = classify(issues, states, timebox)
        self.assertEqual(len(this_week), 1)
        self.assertEqual(len(prev_week), 0)
        self.assertEqual(len(backlog), 1)


if __name__ == "__main__":
    unittest.main()

