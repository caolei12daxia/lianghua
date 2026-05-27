"""Send 30-minute MACD reports for a symbol watchlist to WeCom."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from macd_alert import WeComRobot, calculate_macd

SIGNAL_TEXT = {
    "GOLDEN_CROSS": "金叉",
    "DEAD_CROSS": "死叉",
    "BULLISH": "多头运行（本根K线无新金叉）",
    "BEARISH": "空头运行（本根K线无新死叉）",
}
CROSSOVER_SIGNALS = {"GOLDEN_CROSS", "DEAD_CROSS"}


@dataclass(frozen=True)
class WatchItem:
    symbol: str
    cost: float | None = None


def item_market(item: WatchItem) -> str:
    if item.symbol.startswith("HK:"):
        return "HK"
    if item.symbol.startswith("US:"):
        return "US"
    return "CN"


def filter_markets(items: list[WatchItem], market_option: str) -> list[WatchItem]:
    selected = {market.strip().upper() for market in market_option.split(",") if market.strip()}
    invalid = selected - {"CN", "HK", "US"}
    if invalid:
        raise ValueError(f"Unsupported market values: {', '.join(sorted(invalid))}")
    filtered = [item for item in items if item_market(item) in selected]
    if not filtered:
        raise ValueError(f"No watchlist items selected for markets: {market_option}")
    return filtered


def load_symbols(path: Path) -> list[WatchItem]:
    items: list[WatchItem] = []
    seen: set[str] = set()
    if not path.exists():
        raise ValueError(f"Watchlist file does not exist: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        columns = line.split("#", 1)[0].replace(",", " ").split()
        if not columns:
            continue
        symbol = columns[0]
        if symbol in seen:
            continue
        cost = float(columns[1]) if len(columns) > 1 else None
        if cost is not None and cost <= 0:
            raise ValueError(f"Cost price must be positive for {symbol}.")
        items.append(WatchItem(symbol, cost))
        seen.add(symbol)
    if not items:
        raise ValueError(f"Watchlist is empty: {path}")
    return items


def market_symbol(symbol: str) -> str:
    if symbol.startswith("HK:"):
        return f"rt_hk{symbol[3:].zfill(5)}"
    if symbol.startswith("US:"):
        return f"gb_{symbol[3:].lower()}"
    if symbol.startswith(("5", "6", "9")):
        return f"sh{symbol}"
    if symbol.startswith(("0", "1", "2", "3")):
        return f"sz{symbol}"
    raise ValueError(f"Unsupported A-share symbol: {symbol}")


def display_symbol(symbol: str) -> str:
    if symbol.startswith("HK:"):
        return f"{symbol[3:].zfill(5)}.HK"
    if symbol.startswith("US:"):
        return symbol[3:].upper()
    return symbol


def get_with_retry(url: str, *, attempts: int = 3, **kwargs) -> requests.Response:
    last_error: requests.RequestException | None = None
    for attempt in range(attempts):
        try:
            return requests.get(url, **kwargs)
        except requests.RequestException as error:
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(1)
    assert last_error is not None
    raise last_error


def yahoo_symbol(symbol: str) -> str:
    if symbol.startswith("HK:"):
        return f"{symbol[3:].lstrip('0').zfill(4)}.HK"
    if symbol.startswith("US:"):
        return symbol[3:].upper()
    return symbol


def fetch_name(symbol: str) -> str:
    try:
        response = get_with_retry(
            f"http://hq.sinajs.cn/list={market_symbol(symbol)}",
            headers={"Referer": "https://finance.sina.com.cn"},
            timeout=10,
        )
        response.raise_for_status()
        text = response.content.decode("gbk", errors="replace")
        start = text.find('="')
        if start < 0:
            return symbol
        fields = text[start + 2 :].split(",")
        name = fields[1].strip() if symbol.startswith("HK:") and len(fields) > 1 else fields[0].strip()
        return name or symbol
    except requests.RequestException:
        return display_symbol(symbol)


def fetch_overseas_bars(symbol: str, interval: str, range_value: str) -> pd.DataFrame:
    ticker = yahoo_symbol(symbol)
    response = get_with_retry(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
        params={"interval": interval, "range": range_value},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    response.raise_for_status()
    result = (response.json().get("chart", {}).get("result") or [None])[0]
    if not result or not result.get("timestamp"):
        raise RuntimeError(f"No market bars returned for {symbol}.")
    quote = result["indicators"]["quote"][0]
    timezone = result.get("meta", {}).get("exchangeTimezoneName", "UTC")
    bars = pd.DataFrame(
        {
            "datetime": pd.to_datetime(result["timestamp"], unit="s", utc=True)
            .tz_convert(timezone)
            .tz_localize(None),
            "close": quote["close"],
            "high": quote["high"],
            "low": quote["low"],
            "volume": quote["volume"],
        }
    )
    return bars.dropna(subset=["close", "volume"]).sort_values("datetime").reset_index(drop=True)


def fetch_bars(symbol: str, lookback_days: int) -> pd.DataFrame:
    if symbol.startswith(("HK:", "US:")):
        return fetch_overseas_bars(symbol, "30m", "1mo")
    response = get_with_retry(
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService.getKLineData",
        params={
            "symbol": market_symbol(symbol),
            "scale": "30",
            "ma": "no",
            "datalen": str(min(max(lookback_days * 8, 40), 1023)),
        },
        timeout=10,
    )
    response.raise_for_status()
    body = response.text
    start = body.find("([")
    end = body.rfind("]);")
    if start < 0 or end < 0:
        raise RuntimeError(f"Unexpected market data response for {symbol}.")
    records = json.loads(body[start + 1 : end + 1])
    if not records:
        raise RuntimeError(f"No 30-minute bars returned for {symbol}.")
    bars = pd.DataFrame(records).rename(columns={"day": "datetime"})
    bars["datetime"] = pd.to_datetime(bars["datetime"], errors="raise")
    for column in ("close", "high", "low", "volume"):
        bars[column] = pd.to_numeric(bars[column], errors="raise")
    return bars.sort_values("datetime").reset_index(drop=True)


def calculate_boll_rsi_vol(bars: pd.DataFrame) -> pd.DataFrame:
    frame = bars.copy()
    close = frame["close"]
    middle = close.rolling(20).mean()
    deviation = close.rolling(20).std(ddof=0)
    frame["boll_mid"] = middle
    frame["boll_upper"] = middle + 2 * deviation
    frame["boll_lower"] = middle - 2 * deviation
    change = close.diff()
    gains = change.clip(lower=0).rolling(14).mean()
    losses = (-change.clip(upper=0)).rolling(14).mean()
    relative_strength = gains / losses.replace(0, float("nan"))
    frame["rsi"] = 100 - 100 / (1 + relative_strength)
    frame.loc[(losses == 0) & (gains > 0), "rsi"] = 100.0
    frame.loc[(losses == 0) & (gains == 0), "rsi"] = 50.0
    frame["vol_avg5"] = frame["volume"].shift(1).rolling(5).mean()
    frame["vol_ratio"] = frame["volume"] / frame["vol_avg5"]
    return frame


def boll_status(close: float, upper: float, middle: float, lower: float) -> str:
    if pd.isna(upper):
        return "数据不足"
    if close > upper:
        return "突破上轨"
    if close < lower:
        return "跌破下轨"
    if close >= middle:
        return "中轨上方"
    return "中轨下方"


def rsi_status(rsi: float) -> str:
    if pd.isna(rsi):
        return "数据不足"
    if rsi >= 70:
        return "超买"
    if rsi <= 30:
        return "超卖"
    return "中性"


def vol_status(ratio: float) -> str:
    if pd.isna(ratio):
        return "数据不足"
    if ratio >= 1.5:
        return "放量"
    if ratio <= 0.67:
        return "缩量"
    return "量能正常"


def action_summary(result: dict) -> str:
    signal = result["signal"]
    close = result["close"]
    boll_mid = result["boll_mid"]
    rsi = result["rsi"]
    vol_ratio = result["vol_ratio"]
    if signal == "GOLDEN_CROSS":
        if close >= boll_mid and rsi < 70 and vol_ratio >= 1:
            return "结论：金叉且量价配合，偏多增强，可关注买入机会并设置止损。"
        return "结论：出现金叉，但确认条件不足，先观察，不建议立即追买。"
    if signal == "DEAD_CROSS":
        return "结论：出现死叉，偏空风险上升，持仓应关注减仓或止损，暂不建议新买入。"
    if signal == "BULLISH":
        if rsi >= 70:
            return "结论：趋势偏多但 RSI 超买，持仓观察，暂不建议追高买入。"
        return "结论：趋势偏多但没有新金叉，持仓可观察，新增买入等待信号确认。"
    if rsi <= 30:
        return "结论：趋势偏空且已超卖，不建议仅凭超卖抄底，等待金叉确认。"
    return "结论：趋势偏空，暂不建议新买入，已有持仓关注风险控制。"


def analyze(
    symbol: str, bars: pd.DataFrame, trade_date: str | None, name: str | None = None, cost: float | None = None
) -> dict:
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
    session_date = trade_date or current["date"].strftime("%Y-%m-%d")
    today = bars[bars["datetime"].dt.strftime("%Y-%m-%d") == session_date]
    change = None
    if not today.empty:
        change = (float(today.iloc[-1]["close"]) / float(today.iloc[0]["close"]) - 1) * 100
    profit_pct = (float(current["close"]) / cost - 1) * 100 if cost is not None else None
    result = {
        "symbol": display_symbol(symbol),
        "name": name or symbol,
        "cost": cost,
        "profit_pct": profit_pct,
        "time": current["date"].strftime("%Y-%m-%d %H:%M"),
        "close": float(current["close"]),
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
        "bars_today": len(today),
        "change_today": change,
    }
    result["summary"] = action_summary(result)
    return result


def pending_crossovers(results: list[dict], state_file: Path) -> list[dict]:
    sent: set[str] = set()
    if state_file.exists():
        try:
            sent = set(json.loads(state_file.read_text(encoding="utf-8")).get("sent", []))
        except (OSError, json.JSONDecodeError, AttributeError):
            sent = set()
    return [
        result
        for result in results
        if result["signal"] in CROSSOVER_SIGNALS
        and f"{result['symbol']}:{result['time']}:{result['signal']}" not in sent
    ]


def mark_sent(results: list[dict], state_file: Path) -> None:
    sent: set[str] = set()
    if state_file.exists():
        try:
            sent = set(json.loads(state_file.read_text(encoding="utf-8")).get("sent", []))
        except (OSError, json.JSONDecodeError, AttributeError):
            sent = set()
    sent.update(f"{result['symbol']}:{result['time']}:{result['signal']}" for result in results)
    state_file.write_text(json.dumps({"sent": sorted(sent)}, ensure_ascii=False, indent=2), encoding="utf-8")


def format_report(results: list[dict], mode: str, trade_date: str) -> str:
    title = "30分钟 MACD 金叉/死叉预警" if mode == "hourly" else "30分钟 MACD 收盘分析"
    lines = [f"## {title}", f"> 交易日: {trade_date} | 分析股票: {len(results)} 只"]
    for result in results:
        color = "warning" if result["signal"] == "DEAD_CROSS" else "info"
        lines += [
            "",
            f"**{result['name']}（{result['symbol']}）** <font color=\"{color}\">{SIGNAL_TEXT[result['signal']]}</font>",
            f"> 最新30分钟K线: {result['time']} | 收盘价: {result['close']:.2f}",
            f"> DIF: {result['dif']:.4f} | DEA: {result['dea']:.4f} | MACD: {result['macd']:.4f}",
            f"> BOLL: 上 {result['boll_upper']:.2f} | 中 {result['boll_mid']:.2f} | 下 {result['boll_lower']:.2f} | {result['boll_status']}",
            f"> RSI(14): {result['rsi']:.2f} | {result['rsi_status']} | VOL量比(5): {result['vol_ratio']:.2f} | {result['vol_status']}",
            f"> **{result['summary']}**",
        ]
        if result["cost"] is not None:
            pnl_status = "盈利" if result["profit_pct"] >= 0 else "亏损"
            lines.insert(
                len(lines) - 4,
                f"> 成本价: {result['cost']:.2f} | 相对成本: {result['profit_pct']:+.2f}% | {pnl_status}",
            )
        if mode == "close" and result["change_today"] is not None:
            lines.append(
                f"> 当日30分钟K线: {result['bars_today']} 根 | 日内区间涨跌: {result['change_today']:.2f}%"
            )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="基于股票池的30分钟MACD预警")
    parser.add_argument("--symbols-file", type=Path, default=Path("symbols.txt"))
    parser.add_argument("--markets", default="CN,HK,US", help="Market filter: CN,HK,US; for example CN,HK.")
    parser.add_argument("--mode", choices=["hourly", "close"], default="hourly")
    parser.add_argument("--date", help="Optional trading date in YYYY-MM-DD; defaults to each market's latest session.")
    parser.add_argument("--days", type=int, default=15)
    parser.add_argument("--webhook-url", default=os.getenv("WECOM_WEBHOOK_URL", ""))
    parser.add_argument("--state-file", type=Path, default=Path(".intraday_alert_state.json"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    items = filter_markets(load_symbols(args.symbols_file), args.markets)
    results = [
        analyze(item.symbol, fetch_bars(item.symbol, args.days), args.date, fetch_name(item.symbol), item.cost)
        for item in items
    ]
    send_results = pending_crossovers(results, args.state_file) if args.mode == "hourly" else results
    if args.mode == "hourly" and not send_results:
        print("本次分析未发现新增 MACD 金叉或死叉，不发送微信提醒。")
        return 0
    report_date = args.date or "各市场最近交易日"
    report = format_report(send_results, args.mode, report_date)
    print(report)
    if args.dry_run:
        return 0
    if not args.webhook_url:
        raise RuntimeError("请设置 WECOM_WEBHOOK_URL 或传入 --webhook-url。")
    WeComRobot(args.webhook_url).send_markdown(report)
    if args.mode == "hourly":
        mark_sent(send_results, args.state_file)
    print("企业微信提醒已发送。")
    print("提示：以上为技术指标观察结论，不构成投资建议。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, requests.RequestException) as error:
        print(f"错误: {error}", file=sys.stderr)
        raise SystemExit(1)
