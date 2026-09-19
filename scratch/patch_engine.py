import re

with open("core/engine.py", "r") as f:
    content = f.read()

# Add to TradingEngine.__init__
init_patch = """        self.adx_1h_declining_cache = {}
        self.last_1h_cache_time = 0.0
        self.api_weight_1m = 0  # <--- Added API Weight Tracker"""

content = content.replace("        self.adx_1h_declining_cache = {}\n        self.last_1h_cache_time = 0.0", init_patch)

# Add to update_market_prices
update_patch = """        except Exception as e:
            self.account.log(f"⚠️ update_market_prices 獲取報價失敗: {e}", "WARNING")
            raise e
        finally:
            # Capture API Weight
            if hasattr(self.account, 'exchange') and self.account.exchange and hasattr(self.account.exchange, 'last_response_headers'):
                headers = self.account.exchange.last_response_headers or {}
                for k, v in headers.items():
                    if 'x-mbx-used-weight-1m' in k.lower():
                        try:
                            self.api_weight_1m = int(v)
                        except:
                            pass"""

# Find the end of update_market_prices function
# It ends with:
#         except Exception as e:
#             self.account.log(f"⚠️ update_market_prices 獲取報價失敗: {e}", "WARNING")
#             raise e

content = content.replace("""        except Exception as e:
            self.account.log(f"⚠️ update_market_prices 獲取報價失敗: {e}", "WARNING")
            raise e""", update_patch)

with open("core/engine.py", "w") as f:
    f.write(content)
print("Done engine")
