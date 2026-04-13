# BB Squeeze Breakout Strategy - Rules and Logic

## 1. Strategy Overview
The **BB Squeeze Breakout** strategy is designed to capture high-momentum moves following a period of low volatility. It uses Bollinger Bands to identify 'squeezes' where price is consolidating, followed by a breakout trigger.

---

## 2. Core Logic & Pattern Detection

### Bollinger Band Squeeze (Bollinger Band Contraction)
- **Calculation**: Standard Bollinger Bands (20 period, 2.0 std).
- **BB Width**: (Upper_BB - Lower_BB) / SMA.
- **Squeeze Detection**: The current BB Width is in the **bottom 50%** of its values over the last 100 bars (Quantile 0.50).
- **Recency**: The breakout can occur during the squeeze or within **12 bars** after the squeeze ends.

---

## 3. Entry Rules (Short Entry Only)

1. **Timeframe**: 5-Minute (5m).
2. **Setup**: The Bollinger Bands are currently squeezed, or were squeezed recently (last 12 bars).
3. **Trigger**: Price closes **below the Lower Bollinger Band** (Close <= Lower BB).
4. **Action**: Market Sell (Nifty Options) at the close of the breakout candle.

---

## 4. Exit Rules & Risk Management

### Stop Loss (SL)
- **Placement**: **1.5x ATR** (Average True Range) above the entry price.
- **ATR Period**: 14.

### Take Profit (TP)
- **Target**: **4.3x Risk (RR 4.3)**.
- **Logic**: Aims to catch extended trends that often follow volatility breakouts.

### VWAP Reversal Exit
- **Logic**: If price crosses back **above the VWAP** (Close > VWAP) while in a short position, the trade is closed immediately to protect gains or minimize losses.
- *Reason*: VWAP acts as a critical institutional value line; crossing back over it suggests the bearish momentum has failed.

---

## 5. Risk Management
- **Portfolio Risk**: Default risk is **2% of capital** per trade.
- **Quantity**: Standardized for option trades (1 lot = 65 shares).
