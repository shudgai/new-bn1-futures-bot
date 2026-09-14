"""Single source of truth for the rules the running engine actually applies.

The repository keeps 75 specification blocks and dozens of helpers whose bodies
were replaced by fixed returns, so neither AGENTS.md nor the call sites tell an
operator which rule version is live. This module derives the effective rule set
from the loaded configuration, emits one startup banner, and reports state and
environment entries left behind by retired rules.

Everything here is read-only with respect to trading: it never opens, closes or
modifies an order, and it never rewrites account state.
"""
from __future__ import annotations

import ast
import pathlib
from typing import Iterable, List, Tuple

from core import config

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Helpers kept for import compatibility whose body is now a single fixed return.
# tests/test_rule_registry.py compares this list against the source, so a gate
# can no longer be retired (or re-armed) without the banner telling the operator.
RETIRED_GATE_FUNCTIONS: Tuple[Tuple[str, str], ...] = (
    ("core/engine.py", "_entry_direction_allowed"),  # -> True
    ("core/engine.py", "_ma3_ma15_entry_allowed"),  # -> True
    ("core/engine.py", "_ma5_stop_cooldown_remaining"),  # -> 0.0
    ("core/engine.py", "_ma2_confirmation_allowed"),  # -> True
    ("core/engine.py", "_structured_stop_cooldown_blocks"),  # -> False
    ("core/engine.py", "_live_pivot_ready"),  # -> False
    ("core/services/pulse_service.py", "record_btc_lead_shadow_candidate"),  # -> None
    ("core/services/strategies/direct_reverse_strategy.py", "authorized"),  # -> False
    ("core/services/swing_service.py", "channel_entry_reuses_exit_bar"),  # -> False
    ("core/services/swing_service.py", "channel_peak_exit_reentry_blocked"),  # -> False
    ("core/services/swing_service.py", "channel_peak_exit_entry_gate"),  # -> True
    ("core/services/swing_service.py", "channel_upper_red_short_reversal_allowed"),  # -> True
    ("core/services/swing_service.py", "channel_is_upper_red_peak_short"),  # -> False
    ("core/services/swing_service.py", "channel_exit_requests_rotation"),  # -> False
    ("core/services/swing_service.py", "channel_slope_entry_gate"),  # -> True
    ("core/services/swing_service.py", "channel_macro_continuation_entry_gate"),  # -> True
    ("core/services/swing_service.py", "channel_closed_body_volume_gate"),  # -> True
    ("core/services/swing_service.py", "channel_outer_half_space_hold"),  # -> True
    ("core/services/swing_service.py", "check_parabolic_reversal_exit"),  # -> None
    ("core/services/swing_service.py", "channel_impulse_turn_allowed"),  # -> True
    ("core/services/swing_service.py", "channel_ma15_convergence_is_gradual"),  # -> True
    ("core/services/swing_service.py", "channel_outer_gap_expanding"),  # -> True
    ("core/services/swing_service.py", "channel_trend_exit_reason"),  # -> None
    ("core/services/swing_service.py", "channel_impulse_first_turn"),  # -> False
    ("core/services/swing_service.py", "channel_all_same_color_inside"),  # -> False
    ("core/services/swing_service.py", "channel_closed_waves_falling"),  # -> False
    ("core/services/swing_service.py", "two_bar_structure_failure_exit"),  # -> False
    ("core/services/swing_service.py", "adverse_kc_outer_breached"),  # -> False
    ("core/services/swing_service.py", "confirmed_outer_reversal"),  # -> False
)

_FIXED_RETURNS = (False, None, True, 0, 0.0, "")


def _is_fixed_return(node: ast.AST) -> bool:
    return isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) and node.value.value in _FIXED_RETURNS


def iter_fixed_return_functions(root: pathlib.Path = REPO_ROOT / "core") -> List[Tuple[str, str]]:
    """Return (relative path, function name) for every fixed-return helper."""
    found: List[Tuple[str, str]] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = [item for item in node.body
                        if not (isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant)
                                and isinstance(item.value.value, str))]
                if len(body) == 1 and _is_fixed_return(body[0]):
                    found.append((str(path.relative_to(REPO_ROOT)), node.name))
    return found


def retired_gate_mismatches() -> dict:
    """Report gates that are retired in code but undocumented, or the reverse."""
    actual = set(iter_fixed_return_functions())
    documented = set(RETIRED_GATE_FUNCTIONS)
    return {
        "undocumented": sorted(actual - documented),
        "still_active": sorted(documented - actual),
    }


def _flag(enabled: bool) -> str:
    return "啟用" if enabled else "停用"


def active_entry_rule_lines() -> List[str]:
    """Describe the gates that can actually allow a new position right now."""
    return [
        "  1. 一般破軌：實體破軌根＋同向實體確認根，兩根均已收線且實體占全長至少20%",
        "     兩根收盤與送單現價均須在同側外軌外，已收線MA3與KC中軌均同向",
        "     完成確認後，同色收線持續軌外就保留資格，不因多走一根重等；送單失敗可重驗，成交才計次",
        "  2. 峰谷入口：已收線 MA3 三點谷底轉上開多／峰頂轉下開空，右側一根同向收線K即可",
        "     峰谷不等待1分鐘CK或1H轉向、不要求破軌；盤整不顯示訊號也不開倉",
        "  3. 盤整即時破軌：當根由軌內穿上軌立即開多、穿下軌立即開空，不等收線或MA3/KC轉向",
        "     縮回軌內取消；盤整突破不受舊末端標記攔截，平倉當根與帳戶風控仍限制進場",
        "  峰頂未開空：MA3自峰頂持續下降，下軌破軌一根實體K收線即可開空（實體占全長至少20%）",
        "  峰頂回落後轉向做多：上軌破軌根＋同色實體確認根均須收線，盤整與峰谷入口不得跳過",
        "  正常平倉後原方向MA3與KC趨勢持續：可同根立即重開，不等獲利冷卻或全新破軌；轉向後須重新確認",
        "  共用風控：帳戶餘額與槽位、有效報價、反向異常與送單前重驗；每根限次僅豁免已確認平倉的同向趨勢重開",
        f"  停損冷卻 {config.CHANNEL_STOP_LOSS_COOLDOWN_SEC:g} 秒，既有強趨勢豁免保留",
    ]


def active_exit_rule_lines() -> List[str]:
    """Describe the exits that can actually close a position right now."""
    return [
        "  峰谷平倉：已收線價格與MA3三點轉折＋右側同向K，KC不得反對平倉方向，現價不得跌破谷底／突破峰頂",
        (f"  1. ATR 括號出口：停損 {config.CHANNEL_ATR_STOP_MULT:g} ATR／目標 "
         f"{config.CHANNEL_ATR_TARGET_MULT:g} ATR（取代階梯鎖利）"
         if config.CHANNEL_ATR_EXIT_ENABLED else
         ("  1. 階梯鎖利：停用（門檻設定為 999U，由峰谷出口負責）"
          if config.CHANNEL_SWING_PROFIT_LADDER_ARM_NET_USDT >= 900
          else f"  1. 階梯鎖利：淨利峰值 ≥ {config.CHANNEL_SWING_PROFIT_LADDER_ARM_NET_USDT:g}U 啟動，"
               f"鎖住峰值 − {config.CHANNEL_SWING_PROFIT_LADDER_LOCK_OFFSET_USDT:g}U")),
        (f"     保底停利：淨利峰值 ≥ {config.CHANNEL_SWING_PROFIT_FLOOR_ARM_NET_USDT:g}U 後，"
         f"出場不得低於 +{config.CHANNEL_SWING_PROFIT_FLOOR_NET_USDT:g}U"
         if config.CHANNEL_SWING_PROFIT_FLOOR_NET_USDT > 0 else "     保底停利：停用"),
        f"  2. CK 狹窄衰退＋MA3 峰谷反向：{_flag(config.CHANNEL_FADING_MA3_EXIT_ENABLED)}",
        f"  3. 單根瀑布反向實體 ≥ {config.CHANNEL_WATERFALL_BODY_ATR:g} ATR",
        f"  4. 雙已收線反向異常 K：各 ≥ {config.CHANNEL_ADVERSE_TWO_CANDLE_BODY_ATR:g} ATR",
        f"  5. 帳戶硬止損：保證金虧損 {config.MAX_POSITION_MARGIN_LOSS_RATIO * 100:g}%"
        f"（{config.LEVERAGE:g}x 槓桿 ≈ 價格逆向 {config.MAX_POSITION_MARGIN_LOSS_RATIO / config.LEVERAGE * 100:g}%）",
        f"  6. 量能衰退平倉：{_flag(config.CHANNEL_VOLUME_DECAY_EXIT_ENABLED)}"
        + ("（需在獲利中）" if config.CHANNEL_VOLUME_DECAY_REQUIRE_PROFIT else "（不要求獲利）")
        + "＋MA3 一轉彎即平倉（真量能衰退，未收線K與單根爆量不列入判定）",
        f"  7. MA3 由持倉側外軌轉進軌內後，單根反向異常K ≥ "
        f"{config.CHANNEL_SINGLE_ADVERSE_EXIT_BODY_ATR:g} ATR 即平倉："
        f"{_flag(config.CHANNEL_SINGLE_ADVERSE_EXIT_ENABLED)}",
        f"  8. MA3 穿越 KC 中軌（趨勢反轉）平倉：{_flag(config.CHANNEL_MA3_MIDDLE_CROSS_EXIT_ENABLED)}",
        f"  9. 日虧損停機："
        + (f"{config.MAX_DAILY_LOSS_PCT:g}%" if config.MAX_DAILY_LOSS_PCT > 0 else "未啟用（0）"),
    ]


def rule_banner() -> List[str]:
    """Startup banner listing the rule set that is actually live."""
    retired = len(RETIRED_GATE_FUNCTIONS)
    lines = ["🧾 [生效規則] 開倉入口"] + active_entry_rule_lines()
    lines.append("🧾 [生效規則] 平倉出口")
    lines.extend(active_exit_rule_lines())
    lines.append(f"🧾 [生效規則] 已停用但仍在程式內的舊規則函式：{retired} 個（固定回傳、不參與判斷）")
    lines.append(f"🧾 [生效規則] 交易幣種：{', '.join(config.DEFAULT_SYMBOLS)}｜"
                 f"槓桿 {config.LEVERAGE:g}x｜幣種輪替 {_flag(config.ENABLE_SYMBOL_ROTATION)}｜"
                 f"全市場監控 {_flag(config.FULL_MARKET_SURVEILLANCE_ENABLED)}")
    return lines


def stale_environment_keys(env_path: pathlib.Path | None = None) -> List[str]:
    """Config keys present in .env that no Python module under core/services/tools reads."""
    path = pathlib.Path(env_path) if env_path else REPO_ROOT / ".env"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    sources = ""
    for folder in ("core", "services", "tools", "web"):
        for item in sorted((REPO_ROOT / folder).rglob("*.py")):
            try:
                sources += item.read_text(encoding="utf-8")
            except OSError:
                continue
    stale = []
    for line in raw.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        key = entry.split("=", 1)[0].strip()
        if key and key not in sources:
            stale.append(key)
    return stale
