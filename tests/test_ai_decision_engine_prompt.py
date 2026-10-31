import asyncio
from core.ai_advisor import LocalAIAdvisor


def test_ai_advisor_fallback_rules():
    async def _run():
        advisor = LocalAIAdvisor("http://127.0.0.1:9999/v1/chat/completions", enabled=False)

        # 1. LONG in upper channel -> HOLD
        res_long_hold = await advisor.evaluate_trading_decision(
            symbol="1000PEPE/USDT",
            macro_trend="BULLISH",
            position_status="LONG",
            entry_price=0.0050,
            current_price=0.0052,
            highest_profit_margin=0.05,
            current_ma3=0.0052,
            current_ma15=0.0051,
            ma3_slope=0.00001,
        )
        assert res_long_hold["action"] == "HOLD"
        assert "強勢續抱" in res_long_hold["reason"]

        # 2. LONG broken below MA15 with negative slope -> CLOSE_LONG
        res_long_exit = await advisor.evaluate_trading_decision(
            symbol="1000PEPE/USDT",
            macro_trend="BULLISH",
            position_status="LONG",
            entry_price=0.0050,
            current_price=0.0049,
            highest_profit_margin=0.05,
            current_ma3=0.0048,
            current_ma15=0.0051,
            ma3_slope=-0.00001,
        )
        assert res_long_exit["action"] == "CLOSE_LONG"

        # 3. SHORT in lower channel -> HOLD
        res_short_hold = await advisor.evaluate_trading_decision(
            symbol="1000PEPE/USDT",
            macro_trend="BEARISH",
            position_status="SHORT",
            entry_price=0.0050,
            current_price=0.0048,
            highest_profit_margin=0.05,
            current_ma3=0.0048,
            current_ma15=0.0049,
            ma3_slope=-0.00001,
        )
        assert res_short_hold["action"] == "HOLD"
        assert "強勢續抱" in res_short_hold["reason"]

        # 4. SHORT broken above MA15 with positive slope -> CLOSE_SHORT
        res_short_exit = await advisor.evaluate_trading_decision(
            symbol="1000PEPE/USDT",
            macro_trend="BEARISH",
            position_status="SHORT",
            entry_price=0.0050,
            current_price=0.0052,
            highest_profit_margin=0.05,
            current_ma3=0.0053,
            current_ma15=0.0051,
            ma3_slope=0.00001,
        )
        assert res_short_exit["action"] == "CLOSE_SHORT"

        # 5. Bullish Flat position but MA3 < MA15 (Triple Veto) -> HOLD
        res_long_veto = await advisor.evaluate_trading_decision(
            symbol="1000PEPE/USDT",
            macro_trend="BULLISH",
            position_status="NONE",
            entry_price=0.0,
            current_price=0.0052,
            highest_profit_margin=0.0,
            current_ma3=0.0049,
            current_ma15=0.0050,
            ma3_slope=0.00001,
        )
        assert res_long_veto["action"] == "HOLD"
        assert "三重否決" in res_long_veto["reason"]

        # 6. Bullish Flat position and MA3 > MA15 and slope > 0 -> OPEN_LONG
        res_long_open = await advisor.evaluate_trading_decision(
            symbol="1000PEPE/USDT",
            macro_trend="BULLISH",
            position_status="NONE",
            entry_price=0.0,
            current_price=0.0052,
            highest_profit_margin=0.0,
            current_ma3=0.0053,
            current_ma15=0.0051,
            ma3_slope=0.00001,
        )
        assert res_long_open["action"] == "OPEN_LONG"

    asyncio.run(_run())


def test_ai_advisor_system_prompt_payload():
    captured_payload = {}

    def fake_request(payload: dict) -> dict:
        nonlocal captured_payload
        captured_payload = payload
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"macro_trend": "BULLISH", "status_label": "大週期牛市 (順勢做多)", "position_state": "LONG", "ma3_slope": 0.00003, "action": "HOLD", "reason": "強勢通道續抱"}'
                    }
                }
            ]
        }

    async def _run():
        advisor = LocalAIAdvisor("http://127.0.0.1:9999/v1/chat/completions", enabled=True, request_fn=fake_request)
        res = await advisor.evaluate_trading_decision(
            symbol="1000PEPE/USDT",
            macro_trend="BULLISH",
            position_status="LONG",
            entry_price=0.0050,
            current_price=0.0052,
            highest_profit_margin=0.05,
            current_ma3=0.0052,
            current_ma15=0.0051,
            ma3_slope=0.00003,
        )

        assert res["action"] == "HOLD"
        system_msg = captured_payload["messages"][0]["content"]
        assert "MSB 結構破位極速切換標籤" in system_msg
        assert "強勢通道續抱（廢除中軌與峰頂/谷底平倉）" in system_msg
        assert "雙向動態斜率/偏離過大否決" in system_msg
        assert "CLOSE_SHORT_AND_OPEN_LONG" in system_msg
        assert "CLOSE_LONG_AND_OPEN_SHORT" in system_msg

    asyncio.run(_run())
