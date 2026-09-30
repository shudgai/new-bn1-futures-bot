# Strict swing holding

Historical early closes were caused by the 0.8-ATR spike rule. After reviewing logs, the user explicitly removed that rule. Strategy exits now use strict KC midpoint breach, large-profit 25% drawdown, or confirmed one-minute swing breaks. Retired pending exits and fixed profit tiers are invalidated; account/initial hard stops remain. See reports/strict_hold_20260930/README.md.
