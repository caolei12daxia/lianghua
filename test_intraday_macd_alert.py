import tempfile
import unittest
from pathlib import Path

import pandas as pd

from intraday_macd_alert import (
    analyze,
    action_summary,
    calculate_boll_rsi_vol,
    display_symbol,
    format_report,
    fetch_name,
    filter_markets,
    load_symbols,
    market_symbol,
    mark_sent,
    pending_crossovers,
    WatchItem,
    yahoo_symbol,
)


class IntradayMacdTests(unittest.TestCase):
    def test_load_symbols(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "symbols.txt"
            path.write_text("# list\n688563 52.00\n001391 6.89\nHK:00386\nUS:CMCSA\n688563 53.00\n", encoding="utf-8")
            self.assertEqual(
                load_symbols(path),
                [
                    WatchItem("688563", 52.0),
                    WatchItem("001391", 6.89),
                    WatchItem("HK:00386"),
                    WatchItem("US:CMCSA"),
                ],
            )

    def test_market_symbol(self):
        self.assertEqual(market_symbol("688563"), "sh688563")
        self.assertEqual(market_symbol("001391"), "sz001391")
        self.assertEqual(market_symbol("HK:00386"), "rt_hk00386")
        self.assertEqual(market_symbol("US:CMCSA"), "gb_cmcsa")
        self.assertEqual(display_symbol("HK:00386"), "00386.HK")
        self.assertEqual(display_symbol("US:CMCSA"), "CMCSA")
        self.assertEqual(yahoo_symbol("HK:00386"), "0386.HK")
        self.assertEqual(yahoo_symbol("US:CMCSA"), "CMCSA")

    def test_filter_markets(self):
        items = [WatchItem("688563"), WatchItem("HK:00386"), WatchItem("US:CMCSA")]
        self.assertEqual(filter_markets(items, "CN,HK"), items[:2])
        self.assertEqual(filter_markets(items, "US"), [items[2]])

    def test_close_report_contains_day_summary(self):
        bars = pd.DataFrame(
            {
                "datetime": pd.date_range("2026-05-23 10:00", periods=22, freq="30min"),
                "close": list(range(10, 32)),
                "volume": list(range(100, 122)),
            }
        )
        bars.loc[19:, "datetime"] = pd.to_datetime(
            ["2026-05-26 10:00", "2026-05-26 10:30", "2026-05-26 11:00"]
        )
        result = analyze("688563", bars, "2026-05-26", "航材股份", 29.0)
        report = format_report([result], "close", "2026-05-26")
        self.assertEqual(result["bars_today"], 3)
        self.assertAlmostEqual(result["change_today"], (31 / 29 - 1) * 100)
        self.assertIn("30分钟 MACD 收盘分析", report)
        self.assertIn("BOLL", report)
        self.assertIn("RSI(14)", report)
        self.assertIn("VOL量比(5)", report)
        self.assertIn("结论：", report)
        self.assertIn("688563", report)
        self.assertIn("航材股份", report)
        self.assertIn("成本价", report)
        self.assertIn("+6.90%", report)

    def test_indicator_columns_are_calculated(self):
        bars = pd.DataFrame({"close": list(range(1, 22)), "volume": [100] * 20 + [200]})
        calculated = calculate_boll_rsi_vol(bars)
        self.assertAlmostEqual(calculated.iloc[-1]["rsi"], 100.0)
        self.assertAlmostEqual(calculated.iloc[-1]["vol_ratio"], 2.0)
        self.assertFalse(pd.isna(calculated.iloc[-1]["boll_mid"]))

    def test_hourly_only_sends_new_crossovers(self):
        results = [
            {"symbol": "688563", "time": "2026-05-26 14:00", "signal": "GOLDEN_CROSS"},
            {"symbol": "001391", "time": "2026-05-26 14:00", "signal": "BULLISH"},
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            pending = pending_crossovers(results, path)
            self.assertEqual([item["symbol"] for item in pending], ["688563"])
            mark_sent(pending, path)
            self.assertEqual(pending_crossovers(results, path), [])

    def test_action_summary_handles_buy_and_sell_observation(self):
        golden = {"signal": "GOLDEN_CROSS", "close": 11, "boll_mid": 10, "rsi": 55, "vol_ratio": 1.2}
        dead = {"signal": "DEAD_CROSS", "close": 9, "boll_mid": 10, "rsi": 45, "vol_ratio": 1.1}
        self.assertIn("买入机会", action_summary(golden))
        self.assertIn("减仓或止损", action_summary(dead))


if __name__ == "__main__":
    unittest.main()
