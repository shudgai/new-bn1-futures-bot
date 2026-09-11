"""生效規則註冊表：同一時間只能有一組規則在跑，且必須說得出來是哪一組。"""
import pytest

from core import config
from core.services import rule_registry


def test_retired_gate_list_matches_source():
    """註冊表與程式碼必須一致，否則規則會再無聲地新舊並存。"""
    assert rule_registry.retired_gate_mismatches() == {"undocumented": [], "still_active": []}
    assert len(rule_registry.RETIRED_GATE_FUNCTIONS) >= 30


def test_banner_reports_live_thresholds():
    banner = "\n".join(rule_registry.rule_banner())
    assert f"< {config.CHANNEL_FLAT_MIDDLE_RATIO:g}" in banner
    assert f"≥ {config.CHANNEL_SWING_PROFIT_LADDER_ARM_NET_USDT:g}U" in banner
    assert f"峰值 − {config.CHANNEL_SWING_PROFIT_LADDER_LOCK_OFFSET_USDT:g}U" in banner
    assert f"{config.CHANNEL_WATERFALL_BODY_ATR:g} ATR" in banner
    assert f"{config.MAX_POSITION_MARGIN_LOSS_RATIO * 100:g}%" in banner


@pytest.mark.parametrize("flag,expected", [(True, "啟用"), (False, "停用")])
def test_banner_reports_peak_exit_switch(monkeypatch, flag, expected):
    monkeypatch.setattr(config, "CHANNEL_FADING_MA3_EXIT_ENABLED", flag)
    assert f"MA3 峰谷反向：{expected}" in "\n".join(rule_registry.rule_banner())


def test_banner_reports_disabled_daily_halt(monkeypatch):
    monkeypatch.setattr(config, "MAX_DAILY_LOSS_PCT", 0.0)
    assert "日虧損停機：未啟用（0）" in "\n".join(rule_registry.rule_banner())


def test_stale_environment_keys_flags_retired_profit_lock(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "CHANNEL_FLAT_MIDDLE_RATIO=0.05\n"
        "CHANNEL_SWING_PROFIT_LOCK_STEP_USDT=2.0\n"
        "# comment\n",
        encoding="utf-8",
    )
    assert rule_registry.stale_environment_keys(env) == ["CHANNEL_SWING_PROFIT_LOCK_STEP_USDT"]


def test_stale_environment_keys_is_empty_for_missing_file(tmp_path):
    assert rule_registry.stale_environment_keys(tmp_path / "missing.env") == []


def _long_position():
    return {"side": "LONG", "entry_price": 100.0, "qty": 10.0, "open_timestamp": 1.0}


def test_profit_ladder_uses_default_arm_and_offset():
    from core.services.exits import profit_protection_service as svc

    # 峰值淨利 10U：ARM=4、OFFSET=2 → floor(10/2)*2-2 = 8U
    result = svc.protection(_long_position(), 101.0, 0.0, 0.0)
    assert result is not None and result["locked_net"] == pytest.approx(8.0)


def test_profit_ladder_arm_comes_from_config(monkeypatch):
    from core.services.exits import profit_protection_service as svc

    monkeypatch.setattr(config, "CHANNEL_SWING_PROFIT_LADDER_ARM_NET_USDT", 10_000.0)
    monkeypatch.setattr(config, "CHANNEL_SWING_PROFIT_FLOOR_NET_USDT", 0.0)
    assert svc.protection(_long_position(), 100.8, 0.0, 0.0) is None
