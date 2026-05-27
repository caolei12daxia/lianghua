import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from macd_alert import MacdResult, StateStore, WeComRobot, calculate_macd, latest_result, normalize_market_data


class MacdAnalysisTests(unittest.TestCase):
    def test_calculate_macd_adds_standard_columns(self):
        data = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=5), "close": [10, 11, 12, 11, 13]})

        calculated = calculate_macd(data, fast=2, slow=3, signal_period=2)

        self.assertTrue({"dif", "dea", "macd"}.issubset(calculated.columns))
        self.assertAlmostEqual(calculated.iloc[0]["macd"], 0.0)

    def test_latest_result_identifies_golden_cross(self):
        frame = pd.DataFrame(
            {
                "date": ["2026-05-22", "2026-05-25"],
                "close": [10.0, 10.5],
                "dif": [-0.1, 0.2],
                "dea": [0.0, 0.1],
                "macd": [-0.2, 0.2],
            }
        )

        result = latest_result("000001", frame)

        self.assertEqual(result.signal, "GOLDEN_CROSS")
        self.assertEqual(result.trade_date, "2026-05-25")

    def test_normalize_akshare_column_names(self):
        raw = pd.DataFrame({"日期": ["2026-05-25"], "收盘": [12.3]})

        normalized = normalize_market_data(raw)

        self.assertEqual(normalized.loc[0, "close"], 12.3)


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.result = MacdResult("000001", "2026-05-25", 10.0, 0.1, 0.05, 0.1, "GOLDEN_CROSS")

    def test_state_store_persists_sent_signal(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            state = StateStore(path)
            state.mark([self.result])

            restored = StateStore(path)

            self.assertTrue(restored.contains(self.result))
            self.assertIn("000001:2026-05-25:GOLDEN_CROSS", json.loads(path.read_text(encoding="utf-8"))["sent"])

    @patch("macd_alert.requests.post")
    def test_wecom_robot_sends_markdown_payload(self, post: Mock):
        response = Mock()
        response.json.return_value = {"errcode": 0}
        post.return_value = response

        WeComRobot("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test").send_markdown("content")

        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["json"]["msgtype"], "markdown")
        response.raise_for_status.assert_called_once()


if __name__ == "__main__":
    unittest.main()
