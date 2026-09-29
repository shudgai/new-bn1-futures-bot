"""Read-only replay: historical OHLC, no account or live-order imports."""
import json,datetime
from pathlib import Path
import pandas as pd
from core.strategy import SuperTrendKeltnerStrategy
from core.services.strategies.unified_entry_strategy import evaluate_closed_entry
from core.services.entry_firewall import validate_entry_frame
root=Path(__file__).parent
records=[]
for name in ('pepe','lobster'):
 data=json.loads((root/(name+'_klines.json')).read_text())
 raw=pd.DataFrame(data['data'])
 raw['timestamp']=raw['time']*1000
 # The chart endpoint omits volume. Entry rules under test use OHLC only.
 raw['volume']=0.
 for i in range(199,len(raw)):
  stamp=int(raw.iloc[i].timestamp)
  if not 1790633100000 <= stamp <= 1790634960000:continue
  frame=SuperTrendKeltnerStrategy().compute_indicators(raw.iloc[i-198:i+1][['timestamp','open','high','low','close','volume']].reset_index(drop=True))
  frame['is_closed']=True;frame.attrs['timeframe_ms']=60000
  c=frame.iloc[-1]
  r={'symbol':data['symbol'],'bar_utc':datetime.datetime.fromtimestamp(stamp/1000,datetime.timezone.utc).isoformat(),
     **{k:float(c[k]) for k in ['timestamp','open','high','low','close','ma3','ma15','atr','kc_upper','kc_middle','kc_lower']},
     'previous5_low':float(frame.low.iloc[-6:-1].min()),'previous5_high':float(frame.high.iloc[-6:-1].max())}
  r['short_breakout_predicate']=bool(c.ma3<c.ma15 and c.close<r['previous5_low'] and c.close<c.kc_lower)
  r['long_breakout_predicate']=bool(c.ma3>c.ma15 and c.close>r['previous5_high'] and c.close>c.kc_upper)
  for side in ['LONG','SHORT']:
   ok,reason,decision=evaluate_closed_entry(frame,side)
   r[side]={'ok':ok,'reason':reason,'decision':decision}
   if ok:
    try:validate_entry_frame(frame,side,reason);r[side]['firewall']='PASS'
    except Exception as e:r[side]['firewall']=str(e)
  records.append(r)
(root/'replay.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
for r in records:
 if r['bar_utc'][11:16] in ('22:19','22:20','22:33','22:35'):
  print(json.dumps(r,ensure_ascii=False))
