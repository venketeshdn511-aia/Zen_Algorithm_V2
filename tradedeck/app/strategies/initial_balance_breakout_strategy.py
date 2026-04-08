"""
Initial Balance Breakout Strategy (Bidirectional)
=================================================

CONCEPT:
 The "Initial Balance" (IB) is the price range established during the first
 60 minutes of trading (09:15 – 10:15 IST for NSE).  After the IB is formed,
 institutions often use the IB High / IB Low as reference levels.  When price
 breaks AND CLOSES outside the IB range on strong momentum, it tends to follow
 through in the breakout direction for a measured move.

ENTRY LOGIC (LONG – Bullish Breakout):
 1. IB formed between 09:15 and 10:15 → IB_High, IB_Low defined.
 2. After 10:15 IST: current 5-min Close > IB_High (breakout above).
 3. Volume at breakout bar > 1.2× 20-period Volume MA (volume confirmation).
 4. Price > Daily VWAP (momentum alignment).
 5. ATR(14) > minimum_atr_points (ensures there is enough volatility to trade).
 6. One trade per IB side per day (prevents re-entry on same breakout).

ENTRY LOGIC (SHORT – Bearish Breakdown):
 1. IB formed between 09:15 and 10:15 → IB_High, IB_Low defined.
 2. After 10:15 IST: current 5-min Close < IB_Low (breakdown below).
 3. Volume at breakdown bar > 1.2× 20-period Volume MA.
 4. Price < Daily VWAP.
 5. ATR(14) > minimum_atr_points.
 6. One trade per IB side per day.

STOP LOSS:
 - LONG:  SL = IB_High − (0.5 × ATR).   i.e. just inside the IB.
 - SHORT: SL = IB_Low  + (0.5 × ATR).

TAKE PROFIT (1 : 2.5 Risk-Reward):
 - LONG:  TP = Entry + 2.5 × Risk
 - SHORT: TP = Entry − 2.5 × Risk

TRAILING STOP (after 1R profit):
 - Move SL to Break-Even once trade reaches 1× Risk in profit.
 - Then trail by ATR at each new bar.

EXIT / RISK MANAGEMENT:
 - Hard Stop Loss (above).
 - Take Profit (above).
 - End-of-Day square-off at 15:15 IST.
 - No new entries after 14:00 IST.

ANALYSIS TIMEFRAME : 5-minute bars
ENTRY TIMEFRAME    : 1-minute precision (for live execution)
"""

import pandas as pd  # pyre-ignore[21]
import numpy as np  # pyre-ignore[21]
from datetime import time, datetime
from typing import Optional, Tuple, Dict
from src.interfaces.strategy_interface import StrategyInterface  # pyre-ignore[21]


class InitialBalanceBreakoutStrategy(StrategyInterface):
    """
    Bidirectional Initial Balance Breakout Strategy.

    Parameters
    ----------
    ib_start       : IB window start (default 09:15 IST).
    ib_end         : IB window end   (default 10:15 IST).
    no_entry_after : Latest time to enter a new trade (default 14:00 IST).
    eod_exit       : End-of-day square-off time (default 15:15 IST).
    atr_period     : ATR period (default 14).
    volume_ma_period: Volume Moving Average period (default 20).
    volume_mult    : Volume multiplier threshold (default 1.2×).
    atr_sl_mult    : ATR multiplier for stop-loss distance from IB edge (default 0.5).
    rr_ratio       : Risk-Reward ratio for Take Profit (default 2.5).
    min_atr_points : Minimum ATR in index points to allow a trade (default 20).
    """

    def __init__(
        self,
        ib_start: time = time(9, 15),
        ib_end: time = time(10, 15),
        no_entry_after: time = time(14, 0),
        eod_exit: time = time(15, 15),
        atr_period: int = 14,
        volume_ma_period: int = 20,
        volume_mult: float = 1.2,
        atr_sl_mult: float = 0.5,
        rr_ratio: float = 2.5,
        min_atr_points: float = 20.0,
    ) -> None:
        super().__init__("Initial Balance Breakout")  # pyre-ignore[28]
        self.ib_start = ib_start
        self.ib_end = ib_end
        self.no_entry_after = no_entry_after
        self.eod_exit = eod_exit
        self.atr_period = atr_period
        self.volume_ma_period = volume_ma_period
        self.volume_mult = volume_mult
        self.atr_sl_mult = atr_sl_mult
        self.rr_ratio = rr_ratio
        self.min_atr_points = min_atr_points

        # State tracking (reset daily)
        self.current_status: str = "Initializing..."
        self.last_signal_data: Dict = {}

        # Per-day tracking to prevent multiple entries on same IB side
        self._today: Optional[object] = None
        self._ib_high: Optional[float] = None
        self._ib_low: Optional[float] = None
        self._long_taken_today: bool = False
        self._short_taken_today: bool = False

    # ------------------------------------------------------------------ #
    #  Interface Methods                                                   #
    # ------------------------------------------------------------------ #

    def get_status(self) -> str:
        return self.current_status

    # ------------------------------------------------------------------ #
    #  Indicator Helpers                                                   #
    # ------------------------------------------------------------------ #

    def _calculate_atr(self, df: pd.DataFrame) -> pd.Series:
        """Average True Range using Wilder's smoothing."""
        high = df["High"]
        low = df["Low"]
        prev_close = df["Close"].shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
        ).max(axis=1)
        return tr.rolling(window=self.atr_period).mean()

    def _calculate_vwap(self, df: pd.DataFrame) -> pd.Series:
        """
        Intra-day VWAP (resets each calendar day).
        Expects a DatetimeIndex.
        """
        df = df.copy()
        if "date" not in df.columns:
            df["date"] = df.index.date
        df["tp"] = (df["High"] + df["Low"] + df["Close"]) / 3
        df["tp_vol"] = df["tp"] * df["Volume"]
        df["cum_tp_vol"] = df.groupby("date")["tp_vol"].cumsum()
        df["cum_vol"] = df.groupby("date")["Volume"].cumsum()
        return df["cum_tp_vol"] / df["cum_vol"]

    def _volume_ma(self, df: pd.DataFrame) -> pd.Series:
        """Rolling mean volume."""
        return df["Volume"].rolling(window=self.volume_ma_period).mean()

    # ------------------------------------------------------------------ #
    #  IB Calculation                                                       #
    # ------------------------------------------------------------------ #

    def _get_ib_levels(
        self, day_df: pd.DataFrame
    ) -> Tuple[Optional[float], Optional[float]]:
        """
        Compute IB High and IB Low from the first-hour consolidation.

        Parameters
        ----------
        day_df : DataFrame for a single trading day (DatetimeIndex).

        Returns
        -------
        (ib_high, ib_low) or (None, None) if IB window data is missing.
        """
        ib_data = day_df[
            (day_df.index.time >= self.ib_start)
            & (day_df.index.time < self.ib_end)
        ]
        if len(ib_data) < 3:  # need at least 3 bars to define a range
            return None, None
        return float(ib_data["High"].max()), float(ib_data["Low"].min())

    # ------------------------------------------------------------------ #
    #  Daily Reset                                                          #
    # ------------------------------------------------------------------ #

    def _reset_day(self, today: object) -> None:
        """Reset all per-day state variables when date changes."""
        self._today = today
        self._ib_high = None
        self._ib_low = None
        self._long_taken_today = False
        self._short_taken_today = False

    # ------------------------------------------------------------------ #
    #  Core Signal Generator                                               #
    # ------------------------------------------------------------------ #

    def calculate_signal(self, df: pd.DataFrame) -> Optional[str]:
        """
        Evaluate current bar for IB Breakout signal.

        Parameters
        ----------
        df : OHLCV DataFrame with DatetimeIndex (IST, no tz).
             Columns: Open, High, Low, Close, Volume.

        Returns
        -------
        'buy'  – long breakout above IB High.
        'sell' – short breakdown below IB Low.
        None   – no actionable signal.
        """
        # ── Warmup guard ──────────────────────────────────────────────
        min_bars = max(self.atr_period, self.volume_ma_period) + 10
        if len(df) < min_bars:
            self.current_status = f"Warming up ({len(df)}/{min_bars} bars)"
            return None

        df = df.copy()

        # ── Indicators ────────────────────────────────────────────────
        df["atr"] = self._calculate_atr(df)
        df["vwap"] = self._calculate_vwap(df)
        df["vol_ma"] = self._volume_ma(df)

        curr = df.iloc[-1]
        curr_time: time = df.index[-1].time()
        curr_date = df.index[-1].date()

        # ── Daily reset ────────────────────────────────────────────────
        if curr_date != self._today:
            self._reset_day(curr_date)

        # ── Build / cache IB levels for today ─────────────────────────
        if self._ib_high is None or self._ib_low is None:
            today_df = df[df.index.date == curr_date]
            self._ib_high, self._ib_low = self._get_ib_levels(today_df)

        # ── Time guards ────────────────────────────────────────────────
        if curr_time < self.ib_end:
            self.current_status = (
                f"Building IB ({self.ib_start.strftime('%H:%M')}–"
                f"{self.ib_end.strftime('%H:%M')})…"
            )
            return None

        if curr_time >= self.no_entry_after:
            self.current_status = "After 14:00 – no new entries."
            return None

        if curr_time >= self.eod_exit:
            self.current_status = "EOD square-off zone."
            return None

        # ── IB validity ────────────────────────────────────────────────
        if self._ib_high is None or self._ib_low is None:
            self.current_status = "IB not yet defined."
            return None

        # At this point self._ib_high / _ib_low are guaranteed non-None (guarded above).
        ib_high: float = float(self._ib_high)  # pyre-ignore[6]
        ib_low: float = float(self._ib_low)   # pyre-ignore[6]
        ib_range: float = ib_high - ib_low

        close_p: float = float(curr["Close"])
        atr: float = float(curr["atr"])
        vwap: float = float(curr["vwap"])
        volume: float = float(curr["Volume"])
        vol_ma: float = float(curr["vol_ma"])

        # ── Minimum ATR filter ─────────────────────────────────────────
        if np.isnan(atr) or atr < self.min_atr_points:
            self.current_status = (
                f"ATR {atr:.1f} below minimum {self.min_atr_points}. Waiting…"
            )
            return None

        # ── Volume filter ──────────────────────────────────────────────
        volume_ok: bool = (not np.isnan(vol_ma)) and (volume >= vol_ma * self.volume_mult)

        # ──────────────────────────────────────────────────────────────
        #  LONG BREAKOUT (Above IB High)
        # ──────────────────────────────────────────────────────────────
        if (
            not self._long_taken_today
            and close_p > ib_high                      # price closed above IB High
            and close_p > vwap                         # price above VWAP (bullish bias)
            and volume_ok                              # strong volume at breakout
        ):
            entry_price = close_p
            stop_loss = ib_high - (self.atr_sl_mult * atr)
            risk = entry_price - stop_loss

            if risk <= 0:
                # Fallback: half ATR risk
                risk = atr * 0.5
                stop_loss = entry_price - risk

            take_profit = entry_price + (self.rr_ratio * risk)

            self.last_signal_data = {
                "side": "buy",
                "entry": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "risk": risk,
                "ib_high": ib_high,
                "ib_low": ib_low,
                "ib_range": ib_range,
                "atr": atr,
                "vwap": vwap,
                "pattern": "IB Breakout Long (Above IB High)",
                "rr_ratio": self.rr_ratio,
            }
            self._long_taken_today = True
            self.current_status = (
                f"🟢 LONG IB Breakout @ {entry_price:.1f} | "
                f"IB_H:{ib_high:.1f} SL:{stop_loss:.1f} TP:{take_profit:.1f}"
            )
            return "buy"

        # ──────────────────────────────────────────────────────────────
        #  SHORT BREAKDOWN (Below IB Low)
        # ──────────────────────────────────────────────────────────────
        if (
            not self._short_taken_today
            and close_p < ib_low                       # price closed below IB Low
            and close_p < vwap                         # price below VWAP (bearish bias)
            and volume_ok                              # strong volume at breakdown
        ):
            entry_price = close_p
            stop_loss = ib_low + (self.atr_sl_mult * atr)
            risk = stop_loss - entry_price

            if risk <= 0:
                risk = atr * 0.5
                stop_loss = entry_price + risk

            take_profit = entry_price - (self.rr_ratio * risk)

            self.last_signal_data = {
                "side": "sell",
                "entry": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "risk": risk,
                "ib_high": ib_high,
                "ib_low": ib_low,
                "ib_range": ib_range,
                "atr": atr,
                "vwap": vwap,
                "pattern": "IB Breakdown Short (Below IB Low)",
                "rr_ratio": self.rr_ratio,
            }
            self._short_taken_today = True
            self.current_status = (
                f"🔴 SHORT IB Breakdown @ {entry_price:.1f} | "
                f"IB_L:{ib_low:.1f} SL:{stop_loss:.1f} TP:{take_profit:.1f}"
            )
            return "sell"

        # ── No signal ──────────────────────────────────────────────────
        side_status = []
        if self._long_taken_today:
            side_status.append("Long taken")
        if self._short_taken_today:
            side_status.append("Short taken")

        zone = "Above IB_H" if close_p > ib_high else ("Below IB_L" if close_p < ib_low else "Inside IB")
        vol_str = f"Vol/MA={volume/vol_ma:.2f}×" if not np.isnan(vol_ma) and vol_ma > 0 else ""

        self.current_status = (
            f"Scanning… {zone} | IB[{ib_low:.0f}–{ib_high:.0f}] "
            f"VWAP:{vwap:.0f} ATR:{atr:.1f} {vol_str}"
            + (f" [{', '.join(side_status)}]" if side_status else "")
        )
        return None
