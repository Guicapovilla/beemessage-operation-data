import unittest
from unittest.mock import patch

from bee_operation_data.config import OkrConfig
from bee_operation_data.okr import TrendStore, _fetch_module_issues, calcular_arpu
from bee_operation_data.persistence import build_trend_weeks


class OkrTests(unittest.TestCase):
    def test_calcular_arpu(self):
        subs = [
            {
                "customer": "cus_1",
                "items": {"data": [{"price": {"unit_amount": 10000, "recurring": {"interval": "month"}}}]},
            },
            {
                "customer": "cus_2",
                "items": {"data": [{"price": {"unit_amount": 20000, "recurring": {"interval": "month"}}}]},
            },
        ]
        arpu, n, missing = calcular_arpu(subs)
        self.assertEqual(arpu, 150.0)
        self.assertEqual(n, 2)
        self.assertEqual(missing, 0)

    def test_build_trend_weeks(self):
        rows = build_trend_weeks([{"label": "Semana 1"}, {"week_label": "Semana 2"}], "Semana 3")
        self.assertEqual(rows[-1], {"week": "Semana 3"})
        self.assertEqual(len(rows), 3)

    def test_trend_store_handles_missing_previous_payload(self):
        store = TrendStore(None)
        trend = store.load_trend("kr1_trend", 10.2)
        self.assertEqual(trend, [10.2])

    @patch("bee_operation_data.okr.plane_get_in_workspace")
    def test_fetch_module_issues_stops_on_repeated_batch(self, mock_plane_get):
        # Primeira página com 100 itens e segunda repetida: deve encerrar sem loop infinito.
        page1 = [{"issue_id": str(i), "name": f"Issue {i}"} for i in range(100)]
        page2_same = [{"issue_id": str(i), "name": f"Issue {i}"} for i in range(100)]
        mock_plane_get.side_effect = [page1, page2_same]
        okr_cfg = OkrConfig(
            trimestre_inicio=None,
            trimestre_fim=None,
            ticket_base=0.0,
            produto_pro="",
            produtos_enterprise=set(),
            plane_workspace="ws",
            plane_project_id="proj",
            plane_module_id="mod",
            kr3_concluidas_fallback=0,
            kr3_em_andamento_fallback=0,
        )
        items = _fetch_module_issues(okr_cfg, max_pages=50)
        self.assertEqual(len(items), 100)
        self.assertEqual(mock_plane_get.call_count, 2)


if __name__ == "__main__":
    unittest.main()

