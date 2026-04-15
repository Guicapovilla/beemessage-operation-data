import unittest
from unittest.mock import patch

from bee_operation_data import persistence


class PersistenceTests(unittest.TestCase):
    @patch("bee_operation_data.persistence.supabase_store.fetch_week_index")
    def test_load_snapshots_uses_supabase_view(self, mock_fetch_week_index):
        mock_fetch_week_index.return_value = [{"key": "2026-W16", "label": "Semana 16 · April 2026"}]
        rows = persistence.load_snapshots()
        self.assertEqual(rows[0]["key"], "2026-W16")
        mock_fetch_week_index.assert_called_once_with()

    @patch("bee_operation_data.persistence.supabase_store.upsert_week_payload")
    def test_save_latest_requires_week_key(self, mock_upsert):
        with self.assertRaises(ValueError):
            persistence.save_latest({"current": {}})
        mock_upsert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
