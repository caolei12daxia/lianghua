"""Send daily technical-analysis reports for a watchlist to WeCom."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from intraday_macd_alert import (
    CROSSOVER_SIGNALS,
    SIGNAL_TEXT,
    WatchItem,
    action_summary,
    boll_status,
    calculate_boll_rsi_vol,
    fetch_name,
    display_symbol,
    fetch_overseas_bars,
    filter_markets,
    load_symbols,
    mark_sent,
    market_symbol,
    pending_crossovers,
    rsi_status,
    vol_status,
)
from macd_alert import WeComRobot, calculate_macd

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SYMBOLS_FILE = SCRIPT_DIR / "symbols.txt"
DEFAULT_STATE_FILE = SCRIPT_DIR / ".daily_alert_state.json"


def fetch_daily_bars(symbol: str, count: int) -> pd.DataFrame:
    if symbol.startswith(("HK:", "US:")):
        return fetch_overseas_bars(symbol, "1d", "6mo")
    response = requests.get(
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService.getKLineData",
        params={
            "symbol": market_symbol(symbol),
            "scale": "240",
            "ma": "no",
            "datalen": str(min(max(count, 40), 1023)),
        },
        timeout=10,
    )
    response.raise_for_status()
    body = response.text
    start = body.find("([")
    end = body.rfind("]);")
    if start < 0 or end < 0:
        raise RuntimeError(f"日线行情响应格式异常: {symbol}")
    records = json.loads(body[start + 1 : end + 1])
    if not records:
        raise RuntimeError(f"未获取到日线行情: {symbol}")
    bars = pd.DataFrame(records).rename(columns={"day": "datetime"})
    bars["datetime"] = pd.to_datetime(bars["datetime"], errors="raise")
    for column in ("close", "high", "low", "volume"):
        bars[column] = pd.to_numeric(bars[column], errors="raise")
    return bars.sort_values("datetime").reset_index(drop=True)


def analyze_daily(item: WatchItem, bars: pd.DataFrame, name: str) -> dict:
    calculated = calculate_macd(calculate_boll_rsi_vol(bars).rename(columns={"datetime": "date"}))
    previous, current = calculated.iloc[-2], calculated.iloc[-1]
    if previous["dif"] <= previous["dea"] and current["dif"] > current["dea"]:
        signal = "GOLDEN_CROSS"
    elif previous["dif"] >= previous["dea"] and current["dif"] < current["dea"]:
        signal = "DEAD_CROSS"
    elif current["dif"] >= current["dea"]:
        signal = "BULLISH"
    else:
        signal = "BEARISH"
    cost = item.cost
    result = {
        "symbol": display_symbol(item.symbol),
        "name": name,
        "time": current["date"].strftime("%Y-%m-%d"),
        "close": float(current["close"]),
        "cost": cost,
        "profit_pct": (float(current["close"]) / cost - 1) * 100 if cost else None,
        "dif": float(current["dif"]),
        "dea": float(current["dea"]),
        "macd": float(current["macd"]),
        "signal": signal,
        "boll_upper": float(current["boll_upper"]),
        "boll_mid": float(current["boll_mid"]),
        "boll_lower": float(current["boll_lower"]),
        "boll_status": boll_status(current["close"], current["boll_upper"], current["boll_mid"], current["boll_lower"]),
        "rsi": float(current["rsi"]),
        "rsi_status": rsi_status(current["rsi"]),
        "vol_ratio": float(current["vol_ratio"]),
        "vol_status": vol_status(current["vol_ratio"]),
    }
    result["summary"] = action_summary(result)
    return result


def format_daily_report(results: list[dict], mode: str) -> str:
    title = "日线 MACD 金叉/死叉预警" if mode == "signal" else "日线技术分析收盘报告"
    lines = [f"## {title}", f"> 最新交易日: {results[0]['time']} | 分析股票: {len(results)} 只"]
    for result in results:
        color = "warning" if result["signal"] == "DEAD_CROSS" else "info"
        lines += [
            "",
            f"**{result['name']}（{result['symbol']}）** <font color=\"{color}\">{SIGNAL_TEXT[result['signal']]}</font>",
            f"> 收盘价: {result['close']:.2f}",
        ]
        if result["cost"] is not None:
            pnl_status = "盈利" if result["profit_pct"] >= 0 else "亏损"
            lines.append(f"> 成本价: {result['cost']:.2f} | 相对成本: {result['profit_pct']:+.2f}% | {pnl_status}")
        lines += [
            f"> MACD: DIF {result['dif']:.4f} | DEA {result['dea']:.4f} | 柱值 {result['macd']:.4f}",
            f"> BOLL: 上 {result['boll_upper']:.2f} | 中 {result['boll_mid']:.2f} | 下 {result['boll_lower']:.2f} | {result['boll_status']}",
            f"> RSI(14): {result['rsi']:.2f} | {result['rsi_status']} | VOL量比(5): {result['vol_ratio']:.2f} | {result['vol_status']}",
            f"> **{result['summary']}**",
        ]
    lines.append("\n> 提示：以上为技术指标观察结论，不构成投资建议。")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="基于股票池的日线技术分析与MACD预警")
    parser.add_argument("--symbols-file", type=Path, default=DEFAULT_SYMBOLS_FILE)
    parser.add_argument("--markets", default="ALL", help="市场筛选: CN,HK,US 或 ALL，例如 CN,HK。")
    parser.add_argument("--mode", choices=["report", "signal"], default="report")
    parser.add_argument("--bars", type=int, default=120)
    parser.add_argument("--webhook-url", default=os.getenv("WECOM_WEBHOOK_URL", ""))
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    items = filter_markets(load_symbols(args.symbols_file), args.markets)
    results = [
        analyze_daily(item, fetch_daily_bars(item.symbol, args.bars), fetch_name(item.symbol))
        for item in items
    ]
    send_results = pending_crossovers(results, args.state_file) if args.mode == "signal" else results
    if args.mode == "signal" and not send_results:
        print("本次日线分析未发现新增 MACD 金叉或死叉，不发送微信提醒。")
        return 0
    report = format_daily_report(send_results, args.mode)
    print(report)
    if args.dry_run:
        return 0
    if not args.webhook_url:
        raise RuntimeError("请设置 WECOM_WEBHOOK_URL 或传入 --webhook-url。")
    WeComRobot(args.webhook_url).send_markdown(report)
    if args.mode == "signal":
        mark_sent(send_results, args.state_file)
    print("企业微信日线报告已发送。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, requests.RequestException) as error:
        print(f"错误: {error}", file=sys.stderr)
        raise SystemExit(1)
