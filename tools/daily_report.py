"""產生每日交易檢核報表（給每天早上 6 點排程用）。

輸出：reports/daily/YYYY-MM-DD.md（Markdown），並在 stdout 印出摘要。
只讀取狀態檔，不會改動任何交易資料。
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import time
from typing import Dict, List

ROOT = pathlib.Path(__file__).resolve().parents[1]
STATE = ROOT / "data" / "paper_account.json"
OUT_DIR = ROOT / "reports" / "daily"


def load_state(path: pathlib.Path) -> Dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def split_reason(reason: str) -> str:
    text = str(reason or "")
    for prefix in ("Channel Swing ",):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text.split(" ")[0] if text else "(無)"


def build_report(state: Dict) -> str:
    trades: List[Dict] = state.get("trades") or []
    closes = [t for t in trades if t.get("pnl") is not None and str(t.get("action", "")).startswith("CLOSE")]
    opens = [t for t in trades if str(t.get("action", "")).startswith("OPEN")]
    wins = [t["pnl"] for t in closes if t["pnl"] > 0]
    losses = [t["pnl"] for t in closes if t["pnl"] < 0]
    net = round(sum(t["pnl"] for t in closes), 2)
    fees = round(sum(float(t.get("fee") or 0.0) for t in trades), 2)
    win_rate = round(len(wins) / len(closes) * 100, 1) if closes else 0.0
    expectancy = round(net / len(closes), 2) if closes else 0.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses)) or 0.0
    profit_factor = round(gross_win / gross_loss, 3) if gross_loss else 0.0

    exit_counts = collections.Counter(split_reason(t.get("reason")) for t in closes)
    entry_counts = collections.Counter(split_reason(t.get("reason")) for t in opens)
    long_body = sum(1 for t in opens if "LIVE_BODY_BREAKOUT" in str(t.get("reason")))

    pairs = []
    pending: Dict[str, Dict] = {}
    for t in sorted(trades, key=lambda x: x.get("id") or 0):
        symbol = t.get("symbol")
        if str(t.get("action", "")).startswith("OPEN"):
            pending[symbol] = t
        elif t.get("pnl") is not None and symbol in pending:
            opened = pending.pop(symbol)
            pairs.append(((t.get("id") or 0) - (opened.get("id") or 0)) / 60000.0)
    hold_buckets = collections.Counter()
    for minutes in pairs:
        key = ("<1分" if minutes < 1 else "1-3分" if minutes < 3 else "3-10分" if minutes < 10
               else "10-60分" if minutes < 60 else ">60分")
        hold_buckets[key] += 1

    positions = state.get("positions") or {}
    lines = [
        f"# 每日交易檢核 {time.strftime('%Y-%m-%d %H:%M', time.localtime())}",
        "",
        "## 總覽",
        f"- 帳戶餘額：{state.get('balance')}｜已實現損益：{state.get('realized_pnl')}",
        f"- 已平倉：{len(closes)} 筆（勝 {len(wins)}／敗 {len(losses)}，勝率 {win_rate}%）",
        f"- 淨損益：{net:+.2f} U｜手續費合計：{fees:.2f} U",
        f"- **單筆期望值：{expectancy:+.2f} U**｜獲利因子：{profit_factor}",
        f"- 平均獲利：{round(gross_win / len(wins), 2) if wins else 0} U｜平均虧損：{round(sum(losses) / len(losses), 2) if losses else 0} U",
        f"- 長K（即時破軌）進場：{long_body} 筆",
        "",
        "## 進場原因分布",
    ]
    lines += [f"- {name}：{count} 筆" for name, count in entry_counts.most_common()] or ["- （無）"]
    lines += ["", "## 出場原因分布"]
    lines += [f"- {name}：{count} 筆" for name, count in exit_counts.most_common()] or ["- （無）"]
    lines += ["", "## 持倉時間分布"]
    lines += [f"- {name}：{count} 筆" for name, count in hold_buckets.most_common()] or ["- （無）"]
    lines += ["", "## 目前持倉"]
    if positions:
        for symbol, pos in positions.items():
            lines.append(f"- {symbol} {pos.get('side')} 進場 {pos.get('entry_price')} "
                         f"未實現 {pos.get('unrealized_pnl')} 原因 {split_reason(pos.get('reason'))}")
    else:
        lines.append("- （空手）")
    lines += ["", "## 判讀重點", "- 單筆期望值是否往 0 或正的方向移動（>0 才算有效）",
              "- ATR 括號（目標/停損）與長K入袋（1 ATR）各自的實際貢獻",
              "- 是否出現連續停損（冷卻是否生效）"]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default=str(STATE))
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    args = parser.parse_args()
    state = load_state(pathlib.Path(args.state))
    report = build_report(state)
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{time.strftime('%Y-%m-%d')}.md"
    target.write_text(report, encoding="utf-8")
    print(report)
    print(f"(已寫入 {target})")


if __name__ == "__main__":
    main()
