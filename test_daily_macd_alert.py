import unittest

import pandas as pd

from daily_macd_alert import analyze_daily, format_daily_report
from intraday_macd_alert import WatchItem


class DailyMacdTests(unittest.TestCase):
    def test_daily_report_contains_name_cost_and_indicators(self):
        bars = pd.DataFrame(
            {
                "datetime": pd.date_range("2026-04-01", periods=25, freq="D"),
                "close": list(range(30, 55)),
                "volume": [100] * 24 + [180],
            }
        )
        result = analyze_daily(WatchItem("688563", 50.0), bars, "航材股份")
        report = format_daily_report([result], "report")
        self.assertIn("日线技术分析收盘报告", report)
        self.assertIn("航材股份（688563）", report)
        self.assertIn("成本价: 50.00", report)
        self.assertIn("相对成本: +8.00%", report)
        self.assertIn("BOLL", report)
        self.assertIn("RSI(14)", report)
        self.assertIn("VOL量比(5)", report)
        self.assertIn("结论：", report)


if __name__ == "__main__":
    unittest.main()
