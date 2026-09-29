import re

with open("core/services/symbol_runner.py", "r") as f:
    content = f.read()

# Replace imports
content = re.sub(r'from core\.services\.strategies\.unified_entry_strategy import .*?\n',
                 'from core.services.strategies.unified_entry_strategy import confirmed\nfrom core.services.strategies.pure_trend_v2 import PureTrendStrategyV2\n', content)
content = re.sub(r'from core\.services\.exits\.dual_track_exit_service import .*?\n',
                 '', content)

# Remove old DUAL_TRACK logic inside `if position:`
content = re.sub(r'if position:.*?# ── 全倉平倉 ─────────────────',
'''if position:
        engine._take_over_manual_position(symbol,position)
        
        # 1. 盤中極端熔斷評估 (每一秒都驗證)
        btc_status = {"is_crashing": btc_1m_turn == "SHORT"} # simplified mapping
        exit_reason = PureTrendStrategyV2().check_intra_bar_emergency_exit(position, quote, frame.iloc[-1].to_dict(), btc_status)
        
        # 2. 收盤平倉評估 (只在收線確定時)
        if exit_reason is None and len(closed) >= 2:
            last_closed_bar = closed.iloc[-1].to_dict()
            prev_closed_bar = closed.iloc[-2].to_dict()
            # We must pass the closed bars to evaluate
            exit_reason = PureTrendStrategyV2().evaluate_bar_closed_exit(position, last_closed_bar, prev_closed_bar)
            
        if not exit_reason:
            return [], []
            
        old_side = position['side']
        # ── 全倉平倉 ─────────────────''', content, flags=re.DOTALL)

# Replace entry logic
content = re.sub(r'ok, reason, decision = evaluate_closed_entry\(.*?\).*?if ok:\s*candidates\.append\(decision\)',
'''if len(closed) >= 3:
            bar_curr = closed.iloc[-1].to_dict()
            bar_prev1 = closed.iloc[-2].to_dict()
            bar_prev2 = closed.iloc[-3].to_dict()
            decision = PureTrendStrategyV2().evaluate_entry(symbol, bar_curr, bar_prev1, bar_prev2)
            if decision and decision['side'] == side:
                decision['rule'] = decision['type']
                decision['confirmation_bar_id'] = bar_curr['timestamp']
                log_entry_gate(engine,symbol,side,'CLOSED_SIGNAL',decision['reason'],float(closed.iloc[-1].timestamp), snapshot=entry_frame_evidence(frame))
                candidates.append(decision)
            else:
                log_entry_gate(engine,symbol,side,'CLOSED_SIGNAL',"WAIT_PURE_TREND_V2",float(closed.iloc[-1].timestamp), snapshot=entry_frame_evidence(frame))
        else:
            log_entry_gate(engine,symbol,side,'CLOSED_SIGNAL',"NOT_ENOUGH_BARS",float(closed.iloc[-1].timestamp), snapshot=entry_frame_evidence(frame))
''', content, flags=re.DOTALL)

with open("core/services/symbol_runner.py", "w") as f:
    f.write(content)

