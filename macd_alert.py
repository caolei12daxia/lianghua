"""Analyze A-share MACD signals and send alerts to a WeCom group robot."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests


SIGNAL_LABELS = {
    "GOLDEN_CROSS": "金叉",
    "DEAD_CROSS": "死叉",
    "BULLISH": "多头延续",
    "BEARISH": "空头延续",
}


@dataclass(frozen=True)
class MacdResult:
    symbol: str
    trade_date: str
    close: float
    dif: float
    dea: float
    histogram: float
    signal: str


def calculate_macd(
    data: pd.DataFrame, fast: int = 12, slow: int = 26, signal_period: int = 9
) -> pd.DataFrame:
    """Return market data with DIF, DEA and MACD histogram columns."""
    if fast <= 0 or slow <= 0 or signal_period <= 0 or fast >= slow:
        raise ValueError("MACD periods must satisfy 0 < fast < slow and signal > 0")
    if "close" not in data.columns:
        raise ValueError("market data must contain a close column")
    if len(data) < 2:
        raise ValueError("at least two price rows are required")

    frame = data.copy()
    frame["close"] = pd.to_numeric(frame["close"], errors="raise")
    ema_fast = frame["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = frame["close"].ewm(span=slow, adjust=False).mean()
    frame["dif"] = ema_fast - ema_slow
    frame["dea"] = frame["dif"].ewm(span=signal_period, adjust=False).mean()
    frame["macd"] = 2 * (frame["dif"] - frame["dea"])
    return frame


def latest_result(symbol: str, frame: pd.DataFrame) -> MacdResult:
    """Classify the latest MACD relation and identify a crossover event."""
    if not {"date", "close", "dif", "dea", "macd"}.issubset(frame.columns):
        raise ValueError("calculated market data is missing required columns")
    if len(frame) < 2:
        raise ValueError("at least two calculated price rows are required")

    previous = frame.iloc[-2]
    current = frame.iloc[-1]
    if previous["dif"] <= previous["dea"] and current["dif"] > current["dea"]:
        signal = "GOLDEN_CROSS"
    elif previous["dif"] >= previous["dea"] and current["dif"] < current["dea"]:
        signal = "DEAD_CROSS"
    elif current["dif"] >= current["dea"]:
        signal = "BULLISH"
    else:
        signal = "BEARISH"

    trade_date = pd.to_datetime(current["date"]).strftime("%Y-%m-%d")
    return MacdResult(
        symbol=symbol,
        trade_date=trade_date,
        close=float(current["close"]),
        dif=float(current["dif"]),
        dea=float(current["dea"]),
        histogram=float(current["macd"]),
        signal=signal,
    )


def fetch_a_share_history(
    symbol: str, start_date: str, end_date: str, adjust: str
) -> pd.DataFrame:
    """Fetch daily A-share history from AkShare and normalize its columns."""
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("缺少 akshare，请先执行: pip install -r requirements.txt") from exc

    raw = ak.stock_zh_a_hist(
        symbol=symbol,
        period="daily",
        start_date=start_date,
        end_date=end_date,
        adjust=adjust,
    )
    return normalize_market_data(raw)


def load_csv(path: Path) -> pd.DataFrame:
    return normalize_market_data(pd.read_csv(path))


def normalize_market_data(raw: pd.DataFrame) -> pd.DataFrame:
    """Accept AkShare or conventional CSV columns and produce date/close."""
    renamed = raw.rename(columns={"日期": "date", "收盘": "close", "Date": "date", "Close": "close"})
    if "date" not in renamed.columns or "close" not in renamed.columns:
        raise ValueError("行情数据必须包含 date/close 或 日期/收盘 列")
    frame = renamed.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    frame = frame.sort_values("date").drop_duplicates("date", keep="last")
    return frame.reset_index(drop=True)


class StateStore:
    """Store already-sent crossover identifiers for scheduled executions."""

    def __init__(self, path: Path):
        self.path = path
        self.sent = self._load()

    def _load(self) -> set[str]:
        if not self.path.exists():
            return set()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return set(payload.get("sent", []))
        except (json.JSONDecodeError, OSError, AttributeError):
            return set()

    def contains(self, result: MacdResult) -> bool:
        return self._key(result) in self.sent

    def mark(self, results: Iterable[MacdResult]) -> None:
        self.sent.update(self._key(item) for item in results)
        self.path.write_text(
            json.dumps({"sent": sorted(self.sent)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _key(result: MacdResult) -> str:
        return f"{result.symbol}:{result.trade_date}:{result.signal}"


class WeComRobot:
    def __init__(self, webhook_url: str, timeout: int = 10):
        if not webhook_url.startswith("https://qyapi.weixin.qq.com/cgi-bin/webhook/send"):
            raise ValueError("WECOM_WEBHOOK_URL 不是企业微信群机器人的 webhook 地址")
        self.webhook_url = webhook_url
        self.timeout = timeout

    def send_markdown(self, content: str) -> None:
        response = requests.post(
            self.webhook_url,
            json={"msgtype": "markdown", "markdown": {"content": content}},
            timeout=self.timeout,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("errcode") != 0:
            raise RuntimeError(f"企业微信推送失败: {result}")


def format_markdown(results: list[MacdResult], notify_mode: str) -> str:
    title = "MACD 信号告警" if notify_mode == "signal" else "MACD 每日报告"
    lines = [f"## {title}", f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"]
    for item in results:
        color = "warning" if item.signal == "DEAD_CROSS" else "info"
        lines.extend(
            [
                "",
                f"**{item.symbol}** <font color=\"{color}\">{SIGNAL_LABELS[item.signal]}</font>",
                f"> 交易日: {item.trade_date} | 收盘: {item.close:.2f}",
                f"> DIF: {item.dif:.4f} | DEA: {item.dea:.4f} | MACD: {item.histogram:.4f}",
            ]
        )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A 股 MACD 分析并推送企业微信告警")
    parser.add_argument("symbols", nargs="+", help="A 股代码，例如 000001 600519")
    parser.add_argument("--days", type=int, default=180, help="向前拉取的自然日数，默认 180")
    parser.add_argument("--end-date", default=date.today().strftime("%Y%m%d"), help="结束日期 YYYYMMDD")
    parser.add_argument("--adjust", choices=["", "qfq", "hfq"], default="qfq", help="复权方式")
    parser.add_argument("--fast", type=int, default=12)
    parser.add_argument("--slow", type=int, default=26)
    parser.add_argument("--signal-period", type=int, default=9)
    parser.add_argument(
        "--notify",
        choices=["signal", "all", "off"],
        default="signal",
        help="signal=只推金叉/死叉，all=始终推送报告，off=只输出",
    )
    parser.add_argument("--csv", type=Path, help="以本地 CSV 代替 AkShare，仅支持单个股票代码")
    parser.add_argument("--state-file", type=Path, default=Path(".macd_alert_state.json"))
    parser.add_argument("--no-dedupe", action="store_true", help="不抑制同交易日重复告警")
    parser.add_argument("--dry-run", action="store_true", help="打印将发送的信息但不请求微信")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.days < 2:
        raise ValueError("--days 至少为 2")
    if args.csv and len(args.symbols) != 1:
        raise ValueError("--csv 只能配合单个股票代码使用")

    end = datetime.strptime(args.end_date, "%Y%m%d").date()
    start_date = (end - timedelta(days=args.days)).strftime("%Y%m%d")
    state = StateStore(args.state_file)
    analyzed: list[MacdResult] = []

    for symbol in args.symbols:
        market_data = load_csv(args.csv) if args.csv else fetch_a_share_history(
            symbol, start_date, args.end_date, args.adjust
        )
        calculated = calculate_macd(market_data, args.fast, args.slow, args.signal_period)
        result = latest_result(symbol, calculated)
        analyzed.append(result)
        print(
            f"{result.symbol} {result.trade_date} {SIGNAL_LABELS[result.signal]} "
            f"close={result.close:.2f} DIF={result.dif:.4f} DEA={result.dea:.4f} "
            f"MACD={result.histogram:.4f}"
        )

    candidates = (
        analyzed
        if args.notify == "all"
        else [item for item in analyzed if item.signal in {"GOLDEN_CROSS", "DEAD_CROSS"}]
    )
    if args.notify == "off" or not candidates:
        return 0
    pending = candidates if args.no_dedupe else [item for item in candidates if not state.contains(item)]
    if not pending:
        print("符合推送条件的信号已发送过，本次跳过。")
        return 0

    message = format_markdown(pending, args.notify)
    if args.dry_run:
        print("\n--- 企业微信 Markdown 预览 ---\n" + message)
        return 0

    webhook_url = os.getenv("WECOM_WEBHOOK_URL", "")
    if not webhook_url:
        raise RuntimeError("需要设置环境变量 WECOM_WEBHOOK_URL，或使用 --dry-run 预览")
    WeComRobot(webhook_url).send_markdown(message)
    if not args.no_dedupe:
        state.mark(pending)
    print(f"已向企业微信推送 {len(pending)} 条 MACD 信息。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, requests.RequestException) as error:
        print(f"错误: {error}", file=sys.stderr)
        raise SystemExit(1)
