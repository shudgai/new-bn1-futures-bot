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
    ("core/services/swing_service.py", "channel_entry_min_profit_ok"),  # -> True
    ("core/services/swing_service.py", "channel_peak_exit_entry_gate"),  # -> True
    ("core/services/swing_service.py", "channel_upper_red_short_reversal_allowed"),  # -> True
    ("core/services/swing_service.py", "channel_is_upper_red_peak_short"),  # -> False
    ("core/services/swing_service.py", "channel_exit_requests_rotation"),  # -> False
    ("core/services/swing_service.py", "channel_slope_entry_gate"),  # -> True
    ("core/services/swing_service.py", "channel_macro_continuation_entry_gate"),  # -> True
    ("core/services/swing_service.py", "channel_closed_body_volume_gate"),  # -> True
    ("core/services/swing_service.py", "channel_near_chop_entry_gate"),  # -> True
    ("core/services/swing_service.py", "channel_chop_gate"),  # -> True
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
    ("core/services/swing_service.py", "pivot_pullback_ready"),  # -> True
    ("core/services/swing_service.py", "detect_strict_pivot_prealert"),  # -> None
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
        "  1. 趨勢入口：最近兩根已收線 CK 中軌嚴格上升／下降；持平或無效不開",
        f"     走平禁開：中軌位移 ÷ 軌寬 < {config.CHANNEL_FLAT_MIDDLE_RATIO:g} 即不開"
        "（V 型快通道與即時破軌入口同樣適用）",
        f"  2. 即時長K破軌入口：{_flag(config.CHANNEL_LIVE_BODY_BREAKOUT_ENABLED)}",
        "  共用過濾："
        + ("末端禁開已停用（漲勢延續可再進場）、" if config.CHANNEL_TAIL_MAX_TREND_BARS <= 0
           else f"末端禁開（連續同向 {config.CHANNEL_TAIL_MAX_TREND_BARS} 根）、")
        + f"當根實體過熱 > {config.CHANNEL_ENTRY_MAX_BODY_ATR:g} ATR、"
        f"前一根大K > {config.CHANNEL_ENTRY_MAX_PREV_BODY_ATR:g} ATR 不追、"
        f"淨利空間 ≥ {config.NET_PROFIT_GUARANTEE_BUFFER * 100:g}%、反向異常攔截、每根限次、"
        + (f"停損後冷卻 {config.CHANNEL_STOP_LOSS_COOLDOWN_SEC / 60:g} 分鐘"
           if config.CHANNEL_STOP_LOSS_COOLDOWN_SEC > 0 else "停損後冷卻：未啟用"),
        f"  獲利重開票據有效期 {config.PROFIT_REENTRY_TICKET_TTL_SEC} 秒",
    ]


def active_exit_rule_lines() -> List[str]:
    """Describe the exits that can actually close a position right now."""
    return [
        ("  1. 階梯鎖利：停用（門檻設定為 999U，由峰谷出口負責）"
         if config.CHANNEL_SWING_PROFIT_LADDER_ARM_NET_USDT >= 900
         else f"  1. 階梯鎖利：淨利峰值 ≥ {config.CHANNEL_SWING_PROFIT_LADDER_ARM_NET_USDT:g}U 啟動，"
              f"鎖住峰值 − {config.CHANNEL_SWING_PROFIT_LADDER_LOCK_OFFSET_USDT:g}U"),
        (f"     保底停利：淨利峰值 ≥ {config.CHANNEL_SWING_PROFIT_FLOOR_ARM_NET_USDT:g}U 後，"
         f"出場不得低於 +{config.CHANNEL_SWING_PROFIT_FLOOR_NET_USDT:g}U"
         if config.CHANNEL_SWING_PROFIT_FLOOR_NET_USDT > 0 else "     保底停利：停用"),
        f"  2. CK 狹窄衰退＋MA3 峰谷反向：{_flag(config.CHANNEL_FADING_MA3_EXIT_ENABLED)}",
        f"  3. 單根瀑布反向實體 ≥ {config.CHANNEL_WATERFALL_BODY_ATR:g} ATR",
        f"  4. 雙已收線反向異常 K：各 ≥ {config.CHANNEL_ADVERSE_TWO_CANDLE_BODY_ATR:g} ATR",
        f"  5. 帳戶硬止損：保證金虧損 {config.MAX_POSITION_MARGIN_LOSS_RATIO * 100:g}%"
        f"（{config.LEVERAGE:g}x 槓桿 ≈ 價格逆向 {config.MAX_POSITION_MARGIN_LOSS_RATIO / config.LEVERAGE * 100:g}%）",
        f"  6. 量能衰退平倉：{_flag(config.CHANNEL_VOLUME_DECAY_EXIT_ENABLED)}（獲利中且量縮背離即平倉）",
        f"  7. MA3 在持倉側外軌外時，單根反向異常K ≥ "
        f"{config.CHANNEL_SINGLE_ADVERSE_EXIT_BODY_ATR:g} ATR 即平倉："
        f"{_flag(config.CHANNEL_SINGLE_ADVERSE_EXIT_ENABLED)}",
        f"  8. 日虧損停機："
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
                 f"槓桿 {config.LEVERAGE:g}x｜幣種輪替 {_flag(config.ENABLE_SYMBOL_ROTATION)}")
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
