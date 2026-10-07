"""Size new strategy positions against a fixed pre-sizing loss budget."""
import math
POLICY='structure_fixed_budget_v1'
FULL_SLOT_POLICY = 'half_wallet_margin_budget_v2'


def structure_risk_plan(entry,side,atr,stop,margin,leverage,ratio,fee,slippage,*,preserve_margin=False):
    values=tuple(map(float,(entry,atr,stop,margin,leverage,ratio,fee,slippage)))
    if side not in ('LONG','SHORT') or not all(math.isfinite(v) for v in values):raise ValueError('Invalid structural risk inputs')
    entry,atr,stop,margin,leverage,ratio,fee,slippage=values
    if min(entry,atr,stop,margin,leverage,ratio)<=0 or min(fee,slippage)<0:raise ValueError('Invalid structural risk limits')
    sign=1 if side=='LONG' else -1
    stop=(min if sign==1 else max)(stop,entry-sign*1.5*atr)
    if stop<=0 or sign*(entry-stop)<=0:raise ValueError('Invalid structural stop')
    budget=margin*ratio
    unit_risk=abs(entry-stop)+(entry+stop)*fee+stop*slippage
    qty=margin*leverage/entry if preserve_margin else min(margin*leverage/entry,budget/unit_risk)
    return dict(amount=margin if preserve_margin else qty*entry/leverage,stop=stop,
                structure_risk_policy=FULL_SLOT_POLICY if preserve_margin else POLICY,
                structure_risk_budget_usdt=budget,structure_original_margin=margin)
