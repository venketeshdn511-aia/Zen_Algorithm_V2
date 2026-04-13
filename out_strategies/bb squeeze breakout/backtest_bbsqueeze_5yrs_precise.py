import pandas as pd
import numpy as np
from datetime import time, datetime, timedelta
import os
import sys

# Data Paths
PATH_5M = r"C:\Users\Vinay\Downloads\Market tf\nifty_5m_5yrs_chunked.csv"
PATH_1M = r"C:\Users\Vinay\Downloads\Market tf\nifty_1m_5yrs_chunked.csv"

def run_backtest(slippage_pct=0.02):
    print(f"\n🚀 Running BB Squeeze Backtest (Slippage: {slippage_pct}%)", flush=True)
    
    # Load 5m Data
    print("Loading 5m data...", end="", flush=True)
    df5 = pd.read_csv(PATH_5M, parse_dates=['datetime'])
    df5.set_index('datetime', inplace=True)
    df5.columns = [c.lower() for c in df5.columns]
    print(f" Done ({len(df5)} bars)")

    # Load 1m Data (for precision)
    print("Loading 1m data (might take a minute)...", end="", flush=True)
    df1 = pd.read_csv(PATH_1M, parse_dates=['datetime'])
    df1.set_index('datetime', inplace=True)
    df1.columns = [c.lower() for c in df1.columns]
    print(f" Done ({len(df1)} bars)")

    # ── Strategy Indicators (5m) ───────────────────────────
    print("Calculating indicators...", end="", flush=True)
    sma = df5['close'].rolling(window=20).mean()
    std = df5['close'].rolling(window=20).std()
    df5['bb_lower'] = sma - (2.0 * std)
    df5['bb_width'] = (4.0 * std) / sma
    df5['bb_width_q50'] = df5['bb_width'].rolling(window=100).quantile(0.50)
    df5['is_squeezed'] = df5['bb_width'] <= df5['bb_width_q50']
    df5['was_squeezed_recently'] = df5['is_squeezed'].shift(1).rolling(window=12).max() > 0
    
    # ATR (14)
    tr1 = df5['high'] - df5['low']
    tr2 = (df5['high'] - df5['close'].shift(1)).abs()
    tr3 = (df5['low'] - df5['close'].shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df5['atr'] = tr.rolling(window=14).mean()
    
    # VWAP (Intraday)
    df5['date'] = df5.index.date
    df5['pv'] = ((df5['high'] + df5['low'] + df5['close']) / 3) * df5['volume']
    groups = df5.groupby('date')
    df5['vwap'] = groups['pv'].cumsum() / groups['volume'].cumsum()
    print(" Done")

    # ── Simulation ───────────────────────────────────────
    trades = []
    days = df5.index.normalize().unique()
    no_entry_after = time(14, 0)
    eod_exit = time(15, 15)
    
    print(f"Simulating trades across {len(days)} days...", flush=True)
    
    for current_day in days:
        day_str = current_day.strftime('%Y-%m-%d')
        try:
            d5 = df5.loc[day_str]
            d1 = df1.loc[day_str]
        except KeyError:
            continue
        
        if d5.empty or d1.empty: continue
        
        trade_taken_today = False
        
        for t5, row in d5.iterrows():
            if trade_taken_today: break
            if t5.time() < time(10, 0): continue # Warmup/Opening
            if t5.time() > no_entry_after: break
            
            # Squeeze Breakout logic (SHORT ONLY as per the strategy class)
            is_breakout = row['close'] <= row['bb_lower']
            is_squeezed = row['is_squeezed'] or row['was_squeezed_recently']
            
            if is_breakout and is_squeezed:
                # ENTRY! Use the 1m bars following this 5m bar for exit simulation
                entry_time = t5
                entry_price = row['close']
                atr = row['atr']
                
                # SL: 1.5x ATR (Matching Code #1)
                risk = 1.5 * atr
                if risk < 10: risk = 10.0 # Min risk floor
                stop_loss = entry_price + risk
                target = entry_price - (risk * 4.3) # RR 1:4.3
                
                # Simulate using 1m data
                exit_res = simulate_exit(entry_time, entry_price, stop_loss, target, d1, d5, eod_exit)
                if exit_res:
                    # Apply slippage
                    slip = entry_price * (slippage_pct / 100.0)
                    exit_res['pnl_pts'] -= slip
                    trades.append(exit_res)
                    trade_taken_today = True

    return pd.DataFrame(trades)

def simulate_exit(entry_time, entry_price, sl_price, tp_price, d1, d5, eod_exit_t):
    # Precise 1m exit management
    management_1m = d1[d1.index >= entry_time]
    
    for t1, bar in management_1m.iterrows():
        # 1. Check EOD
        if t1.time() >= eod_exit_t:
            return finalize_trade(entry_time, entry_price, t1, bar['close'], "EOD")
        
        # 2. Check Hard Stop Loss (Short trade, so sl is above)
        if bar['high'] >= sl_price:
            return finalize_trade(entry_time, entry_price, t1, sl_price, "SL")
            
        # 3. Check Take Profit
        if bar['low'] <= tp_price:
            return finalize_trade(entry_time, entry_price, t1, tp_price, "TP")
            
        # 4. Check VWAP Reversal (Close > VWAP Cross)
        # Find 5m VWAP at this time
        try:
            # Approximate the 5m bar index for this 1m tick to get current VWAP
            t5_idx = entry_time.replace(minute=(t1.minute // 5) * 5)
            # Or just use the nearest earlier 5m bar's VWAP
            current_vwap = d5.asof(t1)['vwap']
            if bar['close'] > current_vwap:
                return finalize_trade(entry_time, entry_price, t1, bar['close'], "VWAP_EXIT")
        except:
            pass
            
    return None

def finalize_trade(entry_t, entry_p, exit_t, exit_p, reason):
    pnl = entry_p - exit_p # Short trade
    return {
        'entry_time': entry_t,
        'entry_price': entry_p,
        'exit_time': exit_t,
        'exit_price': exit_p,
        'exit_reason': reason,
        'pnl_pts': pnl
    }

def print_report(df, label):
    if df.empty:
        print(f"\n--- {label}: NO TRADES FOUND ---")
        return

    df['datetime'] = pd.to_datetime(df['entry_time'])
    df['year_month'] = df['datetime'].dt.to_period('M')
    
    monthly = []
    all_months = pd.period_range(start=df['year_month'].min(), end=df['year_month'].max(), freq='M')
    
    for pm in all_months:
        m_trades = df[df['year_month'] == pm]
        if m_trades.empty:
            monthly.append({'Month': str(pm), 'PnL': 0.0, 'Trades': 0, 'WR': '0%', 'PF': 0.0, 'MaxDD': 0.0})
            continue
            
        m_pnl = m_trades['pnl_pts'].sum()
        m_wins = m_trades[m_trades['pnl_pts'] > 0]
        m_losses = m_trades[m_trades['pnl_pts'] <= 0]
        m_wr = len(m_wins) / len(m_trades)
        m_pf = m_wins['pnl_pts'].sum() / abs(m_losses['pnl_pts'].sum()) if abs(m_losses['pnl_pts'].sum()) > 0 else 99.0
        
        m_cum = m_trades['pnl_pts'].cumsum()
        m_peak = m_cum.expanding().max()
        m_dd = (m_peak - m_cum).max()
        
        monthly.append({
            'Month': str(pm),
            'Trades': len(m_trades),
            'PnL': round(m_pnl, 1),
            'WR': f"{m_wr*100:.0f}%",
            'PF': round(m_pf, 2),
            'MaxDD': round(m_dd, 1)
        })
    
    monthly_df = pd.DataFrame(monthly)
    
    print(f"\n--- BB SQUEEZE PERFORMANCE REPORT: {label} ---")
    print(f"Total Period: {df['datetime'].min().date()} to {df['datetime'].max().date()}")
    print(f"Total Trades: {len(df)}")
    print(f"Total PnL Points: {df['pnl_pts'].sum():.1f}")
    win_rate = (df['pnl_pts'] > 0).mean()
    print(f"Overall Win Rate: {win_rate*100:.1f}%")
    
    # Calculate global drawdown
    df['cum_pnl'] = df['pnl_pts'].cumsum()
    df['peak'] = df['cum_pnl'].expanding().max()
    df['drawdown'] = df['peak'] - df['cum_pnl']
    print(f"Max Drawdown: {df['drawdown'].max():.1f} pts")
    
    print("\nMONTHLY BREAKDOWN (First/Last 12 months shown if long):")
    if len(monthly_df) > 24:
        print(monthly_df.head(12).to_string(index=False))
        print("...")
        print(monthly_df.tail(12).to_string(index=False))
    else:
        print(monthly_df.to_string(index=False))
    
    # Save to CSV
    filename = f"bbsqueeze_report_{label.lower().replace(' ', '_')}.csv"
    monthly_df.to_csv(filename, index=False)
    print(f"\n✅ Detailed monthly report saved to {filename}")

if __name__ == "__main__":
    res_no_slip = run_backtest(slippage_pct=0.0)
    print_report(res_no_slip, "NO SLIPPAGE")
    
    res_with_slip = run_backtest(slippage_pct=0.02)
    print_report(res_with_slip, "0.02 PERCENT SLIPPAGE")
