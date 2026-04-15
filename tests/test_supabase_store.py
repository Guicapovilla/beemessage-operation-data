import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bee_operation_data import config, supabase_store


class ConfigSupabaseUrlTests(unittest.TestCase):
    def test_normalize_accepts_https_project_url(self):
        self.assertEqual(
            config.normalize_supabase_api_url("https://jzrxbedrfjkfuihwkqzj.supabase.co"),
            "https://jzrxbedrfjkfuihwkqzj.supabase.co",
        )

    def test_normalize_converts_db_host_to_api_host(self):
        self.assertEqual(
            config.normalize_supabase_api_url("db.jzrxbedrfjkfuihwkqzj.supabase.co"),
            "https://jzrxbedrfjkfuihwkqzj.supabase.co",
        )

    def test_normalize_rejects_postgres_dsn(self):
        with self.assertRaises(RuntimeError):
            config.normalize_supabase_api_url(
                "postgresql://postgres:secret@db.jzrxbedrfjkfuihwkqzj.supabase.co:5432/postgres"
            )


class SupabaseStoreTests(unittest.TestCase):
    @patch("bee_operation_data.supabase_store._client")
    def test_fetch_latest_payload_handles_none_response(self, mock_client):
        query = mock_client.return_value.table.return_value.select.return_value.order.return_value.limit.return_value
        query.execute.return_value = None

        payload = supabase_store.fetch_latest_payload()

        self.assertIsNone(payload)

    @patch("bee_operation_data.supabase_store._client")
    def test_fetch_latest_payload_reads_first_row_from_list(self, mock_client):
        query = mock_client.return_value.table.return_value.select.return_value.order.return_value.limit.return_value
        query.execute.return_value = SimpleNamespace(data=[{"payload": {"week_key": "2026-W16"}}])

        payload = supabase_store.fetch_latest_payload()

        self.assertEqual(payload, {"week_key": "2026-W16"})

    @patch("bee_operation_data.supabase_store._client")
    def test_fetch_week_payload_handles_empty_rows(self, mock_client):
        query = (
            mock_client.return_value.table.return_value.select.return_value.eq.return_value.limit.return_value
        )
        query.execute.return_value = SimpleNamespace(data=[])

        payload = supabase_store.fetch_week_payload("2026-W16")

        self.assertIsNone(payload)


if __name__ == "__main__":
    unittest.main()
