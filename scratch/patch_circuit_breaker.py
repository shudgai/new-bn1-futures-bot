import re

with open("core/testnet_account.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add state fields
init_hook = r"(self\.staged_state_directory = .*)"
content = re.sub(init_hook, r"\1\n        self.live_session_start_equity = None\n        self.circuit_breaker_latched = False\n        self.trigger_timestamp = None\n        self.trigger_equity = None\n        self.trigger_drawdown = None\n        self.live_session_equity_available = False", content, count=1)

# 2. Add load/save
load_hook = r"(self\.shadow_parameter_last = loaded_shadow_last)"
content = re.sub(load_hook, r"\1\n            self.live_session_start_equity = data.get('live_session_start_equity')\n            self.circuit_breaker_latched = data.get('circuit_breaker_latched', False)\n            self.trigger_timestamp = data.get('trigger_timestamp')\n            self.trigger_equity = data.get('trigger_equity')\n            self.trigger_drawdown = data.get('trigger_drawdown')", content, count=1)

save_hook = r"(\"shadow_parameter_last\": self\.shadow_parameter_last,)"
content = re.sub(save_hook, r"\1\n            'live_session_start_equity': self.live_session_start_equity,\n            'circuit_breaker_latched': self.circuit_breaker_latched,\n            'trigger_timestamp': self.trigger_timestamp,\n            'trigger_equity': self.trigger_equity,\n            'trigger_drawdown': self.trigger_drawdown,", content, count=1)

# 3. Modify _master_breaker_create_order
master_hook = r"(is_reduce = \(params\.get\('reduceOnly'\) in \(True, 'true', 'TRUE'\)\s*or params\.get\('closePosition'\) in \(True, 'true', 'TRUE'\)\))"

new_breaker = """
            from core.config import PAPER_TRADING, USE_TESTNET
            is_live_mainnet = not PAPER_TRADING and not USE_TESTNET
            
            is_reducing = self._is_verified_reducing_order(symbol, side, amount, params)
            if is_live_mainnet and not is_reducing:
                if getattr(self, "circuit_breaker_latched", False):
                    raise ValueError("[FORBIDDEN_ENTRY] Circuit Breaker Latched")
                if getattr(self, "live_session_start_equity", None) is None:
                    raise ValueError("[FORBIDDEN_ENTRY] Baseline Missing")
                if not getattr(self, "live_session_equity_available", False):
                    raise ValueError("[FORBIDDEN_ENTRY] Equity Unavailable")
            
            is_reduce = is_reducing or (not is_live_mainnet and (params.get('reduceOnly') in (True, 'true', 'TRUE') or params.get('closePosition') in (True, 'true', 'TRUE')))
"""
content = re.sub(master_hook, new_breaker.strip(), content, count=1)

# 4. Inject _is_verified_reducing_order and _trigger_circuit_breaker
new_methods = """
    def _is_verified_reducing_order(self, symbol, side, qty, params):
        if params.get("reduceOnly") not in (True, 'true', 'TRUE') and params.get("closePosition") not in (True, 'true', 'TRUE'):
             return False
        pos = self.positions.get(symbol)
        if not pos:
             return False
        if pos["side"] == "LONG" and side.lower() == "sell" and float(qty) <= float(pos["qty"]):
             return True
        if pos["side"] == "SHORT" and side.lower() == "buy" and float(qty) <= float(pos["qty"]):
             return True
        return False

    async def _trigger_circuit_breaker(self, current_equity, baseline):
        import time
        from decimal import Decimal
        if not self.circuit_breaker_latched:
            self.circuit_breaker_latched = True
            self.trigger_timestamp = time.time()
            self.trigger_equity = float(current_equity)
            self.trigger_drawdown = float((current_equity - baseline) / baseline) if baseline > 0 else 0.0
            self.save_state()
            self.log("🛑 ACCOUNT CIRCUIT BREAKER LATCHED! -15% EQUITY HIT.", "WARNING")

        # C & D: positions and pending orders
        raw_positions = await self.exchange.fapiPrivateV2GetPositionRisk()
        
        open_orders = []
        for sym in list(self.pending_limit_orders.keys()):
             try:
                 open_orders.extend(await self.exchange.fetch_open_orders(sym))
             except Exception:
                 pass
                 
        # E: Cancel ONLY pending ENTRY orders
        for order in open_orders:
             is_reduce = order.get("info", {}).get("reduceOnly") in (True, 'true', 'TRUE')
             if order.get("type") == "limit" and not is_reduce:
                  try:
                      await self.exchange.cancel_order(order["id"], order["symbol"])
                  except Exception:
                      pass
                      
        # G: Submit verified reduce-only market close
        for pos in raw_positions:
            qty = float(pos.get("positionAmt", 0))
            sym = pos.get("symbol")
            if abs(qty) > 0:
                side = "sell" if qty > 0 else "buy"
                try:
                    await self._raw_create_order(sym, "market", side, abs(qty), None, {"reduceOnly": True})
                except Exception as e:
                    self.log(f"🛑 Emergency Flatten Failed for {sym}: {e}", "WARNING")
                    
        # H: Re-fetch and I: Clean orphan protection
        try:
            new_positions = await self.exchange.fapiPrivateV2GetPositionRisk()
            all_flat = all(abs(float(p.get("positionAmt", 0))) == 0 for p in new_positions)
            
            if all_flat:
                 for sym in [p.get("symbol") for p in raw_positions if p.get("symbol")]:
                      try:
                          algo_orders = await self.exchange.request("algoOpenOrders", "fapiPrivate", "GET", {"symbol": self._raw_symbol(sym)})
                          for algo in algo_orders:
                              algo_id = algo.get("algoId") or algo.get("id")
                              await self.exchange.request("algoOrder", "fapiPrivate", "DELETE", {"symbol": sym, "algoId": algo_id})
                      except Exception:
                          pass
            else:
                 self.log("🛑 POSITION_FLATTENED = FALSE (Close Failure)", "WARNING")
        except Exception:
            pass

"""
content = content.replace("    def _load_state(self) -> None:", new_methods + "\n    def _load_state(self) -> None:")


# 5. Inject refresh logic
refresh_hook = r"(balance_rows = await self\.exchange\.fapiPrivateV2GetBalance\(\))"
refresh_injection = """
        from core.config import PAPER_TRADING, USE_TESTNET
        from decimal import Decimal
        is_live_mainnet = not PAPER_TRADING and not USE_TESTNET
        if is_live_mainnet:
            try:
                account_data = await self.exchange.fapiPrivateV2GetAccount()
                equity_str = account_data.get("totalMarginBalance")
                if equity_str is None:
                    raise ValueError("Missing totalMarginBalance")
                current_equity = Decimal(str(equity_str))
                self.live_session_equity_available = True
                
                if self.live_session_start_equity is not None:
                    baseline = Decimal(str(self.live_session_start_equity))
                    if current_equity <= baseline * Decimal("0.85"):
                        await self._trigger_circuit_breaker(current_equity, baseline)
            except Exception as e:
                self.log(f"⚠️ Circuit Breaker equity fetch failed: {e}", "WARNING")
                self.live_session_equity_available = False

        balance_rows = await self.exchange.fapiPrivateV2GetBalance()
"""
content = re.sub(refresh_hook, refresh_injection.strip(), content, count=1)


with open("core/testnet_account.py", "w", encoding="utf-8") as f:
    f.write(content)

