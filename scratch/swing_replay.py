class SwingReplayEngine:
    def __init__(self, entry_price, side, initial_stop, entry_atr):
        self.entry_price = entry_price
        self.side = side
        self.initial_stop = initial_stop
        self.entry_atr = entry_atr
        self.initial_risk_price = abs(initial_stop - entry_price)
        
        self.mfe = entry_price
        self.mae = entry_price
        
        self.swing_state = 'SWING_HOLD'
        self.consecutive_warning = 0
        
        self.floors = {
            'F0': {'arm_atr': None, 'floor_atr': None, 'active': True},
            'F1': {'arm_atr': 0.5, 'floor_atr': 0.0, 'active': True},
            'F2': {'arm_atr': 0.75, 'floor_atr': 0.0, 'active': True},
            'F3': {'arm_atr': 1.0, 'floor_atr': 0.0, 'active': True},
            'F4': {'arm_atr': 1.0, 'floor_atr': 0.25, 'active': True},
            'F5': {'arm_atr': 1.5, 'floor_atr': 0.5, 'active': True},
        }
        
        for k in self.floors:
            self.floors[k].update({
                'armed_at': None,
                'floor_price': None,
                'hit_at': None,
                'exit_price': None,
                'exit_reason': None,
                'resolution': None, # DETERMINISTIC, INTRABAR_UNKNOWN, TICK_RESOLVED
                'tick_count_used': 0
            })
            
    def is_worse_than_or_eq(self, p1, p2):
        if self.side == 'SHORT': return p1 >= p2
        else: return p1 <= p2
        
    def is_better_than_or_eq(self, p1, p2):
        if self.side == 'SHORT': return p1 <= p2
        else: return p1 >= p2
        
    def calc_floor_price(self, floor_atr):
        if self.side == 'SHORT': return self.entry_price - (floor_atr * self.entry_atr)
        else: return self.entry_price + (floor_atr * self.entry_atr)
            
    def calc_arm_price(self, arm_atr):
        if self.side == 'SHORT': return self.entry_price - (arm_atr * self.entry_atr)
        else: return self.entry_price + (arm_atr * self.entry_atr)

    def process_tick(self, tick, name, f, t_str):
        # We process a single tick sequentially to resolve intrabar unknowns.
        # This is ONLY used if we have tick data for a bar.
        price = tick['price']
        
        # Did we hit SL?
        if self.is_worse_than_or_eq(price, self.initial_stop):
            f['hit_at'] = f"{t_str} (TICK_RESOLVED)"
            f['exit_price'] = self.initial_stop
            f['exit_reason'] = 'INITIAL_ATR_STOP'
            f['resolution'] = 'TICK_RESOLVED'
            f['active'] = False
            return True
            
        # Did we arm?
        if f['floor_price'] is None and f['arm_atr'] is not None:
            dist_atr = (self.entry_price - price) / self.entry_atr if self.side == 'SHORT' else (price - self.entry_price) / self.entry_atr
            if dist_atr >= f['arm_atr']:
                f['floor_price'] = self.calc_floor_price(f['floor_atr'])
                f['armed_at'] = t_str
                
        # Did we hit floor?
        if f['floor_price'] is not None:
            if self.is_worse_than_or_eq(price, f['floor_price']):
                f['hit_at'] = f"{t_str} (TICK_RESOLVED)"
                f['exit_price'] = f['floor_price']
                f['exit_reason'] = 'PROFIT_FLOOR'
                f['resolution'] = 'TICK_RESOLVED'
                f['active'] = False
                return True
                
        return False

    def process_kline(self, kline, t_str, ticks=None):
        if self.swing_state == 'SWING_RELEASED':
            # Already exited fully
            return
            
        # If we have ticks, we can use process_tick for active floors
        if ticks is not None and len(ticks) > 0:
            for name, f in self.floors.items():
                if not f['active']: continue
                for tick in ticks:
                    f['tick_count_used'] += 1
                    if self.process_tick(tick, name, f, t_str):
                        break
        else:
            # Without ticks, we use OHLC logic
            hit_sl = self.is_worse_than_or_eq(kline['high'] if self.side == 'SHORT' else kline['low'], self.initial_stop)
            
            for name, f in self.floors.items():
                if not f['active']: continue
                
                # Check already armed floors
                if f['floor_price'] is not None:
                    hit_floor = self.is_worse_than_or_eq(kline['high'] if self.side == 'SHORT' else kline['low'], f['floor_price'])
                    
                    if hit_sl and hit_floor:
                        open_price = kline['open']
                        if self.is_better_than_or_eq(open_price, f['floor_price']):
                            f['hit_at'] = t_str
                            f['exit_price'] = f['floor_price']
                            f['exit_reason'] = 'PROFIT_FLOOR'
                            f['resolution'] = 'DETERMINISTIC'
                        else:
                            f['hit_at'] = f"{t_str} (MULTI_EXIT_INTRABAR_UNKNOWN)"
                            f['exit_price'] = None
                            f['exit_reason'] = 'MULTI_EXIT_INTRABAR_UNKNOWN'
                            f['resolution'] = 'INTRABAR_UNKNOWN'
                        f['active'] = False
                    elif hit_sl:
                        f['hit_at'] = t_str
                        f['exit_price'] = self.initial_stop
                        f['exit_reason'] = 'INITIAL_ATR_STOP'
                        f['resolution'] = 'DETERMINISTIC'
                        f['active'] = False
                    elif hit_floor:
                        f['hit_at'] = t_str
                        f['exit_price'] = f['floor_price']
                        f['exit_reason'] = 'PROFIT_FLOOR'
                        f['resolution'] = 'DETERMINISTIC'
                        f['active'] = False
                else:
                    if hit_sl:
                        arm_price = self.calc_arm_price(f['arm_atr']) if f['arm_atr'] is not None else None
                        hit_arm = (arm_price is not None and self.is_better_than_or_eq(kline['low'] if self.side == 'SHORT' else kline['high'], arm_price))
                        if hit_arm:
                            f['hit_at'] = f"{t_str} (MULTI_EXIT_INTRABAR_UNKNOWN)"
                            f['exit_price'] = None
                            f['exit_reason'] = 'MULTI_EXIT_INTRABAR_UNKNOWN'
                            f['resolution'] = 'INTRABAR_UNKNOWN'
                        else:
                            f['hit_at'] = t_str
                            f['exit_price'] = self.initial_stop
                            f['exit_reason'] = 'INITIAL_ATR_STOP'
                            f['resolution'] = 'DETERMINISTIC'
                        f['active'] = False

            # Process MFE/MAE
            best_price = kline['low'] if self.side == 'SHORT' else kline['high']
            worst_price = kline['high'] if self.side == 'SHORT' else kline['low']
            
            if self.side == 'SHORT':
                if best_price < self.mfe: self.mfe = best_price
                if worst_price > self.mae: self.mae = worst_price
            else:
                if best_price > self.mfe: self.mfe = best_price
                if worst_price < self.mae: self.mae = worst_price
                
            dist_atr = (self.entry_price - self.mfe) / self.entry_atr if self.side == 'SHORT' else (self.mfe - self.entry_price) / self.entry_atr
            
            # Check Arming inside bar
            for name, f in self.floors.items():
                if not f['active']: continue
                if f['floor_price'] is None and f['arm_atr'] is not None:
                    if dist_atr >= f['arm_atr']:
                        f['floor_price'] = self.calc_floor_price(f['floor_atr'])
                        f['armed_at'] = t_str
                        
                        hit_floor = self.is_worse_than_or_eq(worst_price, f['floor_price'])
                        if hit_floor:
                            if self.is_worse_than_or_eq(kline['close'], f['floor_price']):
                                f['hit_at'] = t_str
                                f['exit_price'] = f['floor_price']
                                f['exit_reason'] = 'PROFIT_FLOOR'
                                f['resolution'] = 'DETERMINISTIC'
                            else:
                                f['hit_at'] = f"{t_str} (ARM_AND_HIT_SAME_BAR_UNKNOWN)"
                                f['exit_price'] = None
                                f['exit_reason'] = 'ARM_AND_HIT_SAME_BAR_UNKNOWN'
                                f['resolution'] = 'INTRABAR_UNKNOWN'
                            f['active'] = False

        # State Machine Update (Swing Hold)
        if self.swing_state != 'SWING_RELEASED':
            close = kline['close']
            kc_mid = kline['kc_middle']
            ma5 = kline.get('ma5', None)
            ma15 = kline.get('ma15', None)
            ma5_slope = kline.get('ma5_slope', None)
            
            if self.side == 'SHORT':
                if close >= kc_mid:
                    self.swing_state = 'SWING_WARNING'
                    self.consecutive_warning += 1
                else:
                    if ma5 is not None and ma15 is not None and ma5 < ma15:
                        self.swing_state = 'SWING_HOLD'
                    self.consecutive_warning = 0
                    
                release_a = (self.consecutive_warning >= 2 and ma5_slope >= 0)
                release_b = (ma5 is not None and ma15 is not None and ma5 >= ma15)
                
                if release_a or release_b:
                    self.swing_state = 'SWING_RELEASED'
                    
            elif self.side == 'LONG':
                if close <= kc_mid:
                    self.swing_state = 'SWING_WARNING'
                    self.consecutive_warning += 1
                else:
                    if ma5 is not None and ma15 is not None and ma5 > ma15:
                        self.swing_state = 'SWING_HOLD'
                    self.consecutive_warning = 0
                    
                release_a = (self.consecutive_warning >= 2 and ma5_slope <= 0)
                release_b = (ma5 is not None and ma15 is not None and ma5 <= ma15)
                
                if release_a or release_b:
                    self.swing_state = 'SWING_RELEASED'

            # If released, terminate remaining active floors
            if self.swing_state == 'SWING_RELEASED':
                for name, f in self.floors.items():
                    if f['active']:
                        f['hit_at'] = t_str
                        f['exit_price'] = close
                        f['exit_reason'] = 'SWING_RELEASED'
                        f['resolution'] = 'DETERMINISTIC'
                        f['active'] = False


def aggregate_replay_results(results):
    stats = {
        'trade_count': 0,
        'wins': 0,
        'losses': 0,
        'win_rate': 0.0,
        'average_R': 0.0,
        'median_R': 0.0,
        'total_R': 0.0,
        'profit_factor': 0.0,
        'expectancy_R': 0.0,
        'max_drawdown_R': 0.0,
        'unknown_count': 0,
        'unknown_rate': 0.0,
        'unknown_best_case_R': 0.0,
        'unknown_worst_case_R': 0.0,
    }
    
    total_samples = len(results)
    if total_samples == 0: return stats
    
    valid_R = []
    unknown_best = []
    unknown_worst = []
    
    for r in results:
        if r.get('resolution') == 'INTRABAR_UNKNOWN':
            stats['unknown_count'] += 1
            if 'best_case_R' in r: unknown_best.append(r['best_case_R'])
            if 'worst_case_R' in r: unknown_worst.append(r['worst_case_R'])
        else:
            stats['trade_count'] += 1
            valid_R.append(r.get('realized_R', 0))
            if r.get('realized_R', 0) > 0:
                stats['wins'] += 1
            else:
                stats['losses'] += 1
                
    if stats['trade_count'] > 0:
        stats['win_rate'] = stats['wins'] / stats['trade_count']
        stats['total_R'] = sum(valid_R)
        stats['average_R'] = stats['total_R'] / stats['trade_count']
        stats['expectancy_R'] = stats['average_R']
        
        valid_R_sorted = sorted(valid_R)
        n = len(valid_R_sorted)
        if n % 2 == 0:
            stats['median_R'] = (valid_R_sorted[n//2 - 1] + valid_R_sorted[n//2]) / 2
        else:
            stats['median_R'] = valid_R_sorted[n//2]
            
        gross_profit = sum(x for x in valid_R if x > 0)
        gross_loss = abs(sum(x for x in valid_R if x <= 0))
        stats['profit_factor'] = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
        
        peak = 0
        current = 0
        max_dd = 0
        for x in valid_R:
            current += x
            if current > peak:
                peak = current
            dd = peak - current
            if dd > max_dd:
                max_dd = dd
        stats['max_drawdown_R'] = max_dd

    stats['unknown_rate'] = stats['unknown_count'] / total_samples
    if unknown_best:
        stats['unknown_best_case_R'] = sum(unknown_best) / len(unknown_best)
    if unknown_worst:
        stats['unknown_worst_case_R'] = sum(unknown_worst) / len(unknown_worst)
    
    return stats
