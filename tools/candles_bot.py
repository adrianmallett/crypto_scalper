#!/usr/bin/env python3
import ccxt
import numpy as np
import pandas as pd
import pandas_ta as ta
import time
import math
import traceback
from datetime import datetime, timezone
import json
import os

# ============================================================
# DYNAMIC MARGIN (ATR-based)
# ============================================================
USE_DYN_MARGIN = False
DYN_MARGIN_MIN = 0.0025  # minimum 0.25%
DYN_MARGIN_MAX = 0.01    # maximum 1.0%
ATR_PERIOD = 14          # same as RSI period

# ============================================================
# SETTINGS — tweak these to tune the bot
# ============================================================

# --- Exchange & Pair ---
SYMBOL        = 'BTC/USDT'
TIMEFRAME     = '1m'

# --- Trade Margins ---
BUY_MARGIN    = 0.0025  # Buy when price is 0.25% below last sell
SELL_MARGIN   = 1.0075  # DEPRECATED 2026-08-13: superseded by FEE_BUFFER (the real sell gate) — kept for reference only
FEE_BUFFER = 1.010  # Fee-aware sell floor: 1.0% gross = ~0.8% net profit after 0.2% round-trip taker fees (raised 2026-08-13: backtest showed 0.6% net-negative at 65% win rate; breakeven now 60%)
RALLY_REANCHOR_PCT = 0.0050  # Re-anchor sold_at to current price if price rallies 0.50% above last sell

# --- Bollinger Bands ---
USE_BB        = False    # DISABLED - using fixed 0.4% margins

# --- RSI Cycle Trigger ---
USE_RSI       = True     # Use RSI to detect real dips/peaks
RSI_PERIOD    = 14       # Standard RSI period
RSI_OVERSOLD  = 30       # Buy when RSI < 30 (genuine oversold dip)
RSI_OVERBOUGHT = 70      # Sell when RSI > 70 (genuine overbought peak)
PURE_RSI_BUY  = True    # Require RSI + price target (no blind RSI buys)

# --- Lull Buy-Window Gate (Adrian's discipline 2026-08-24: only buy when the paper chip is green/blue) ---
USE_LULL_GATE   = True    # Buys only while the paper page BUY WINDOW chip is ON (lull active; blue=deep adds RSI<30 which the RSI cross already covers)
PAPER_DATA_FILE = '/a0/webui/public/trading/paper_data.json'  # single source of truth — the exact chip Adrian watches
LULL_MAX_AGE_SECS = 30    # fail safe (no buy) if the paper feed is stale

# --- Test Capital Limit ---
TEST_MODE     = True      # LIVE but capital-capped (orders are real either way — this only limits size per buy, see line ~279)
TEST_CAPITAL  = 100    # Safe repo default — live runs a higher cap on Adrian's word

# --- Cycle Control ---
MAX_CYCLES    = 4       # Max 4 buy/sell cycles per day (reinstated 2026-08-24 at Adrian's request; enforced in the buy gate below — buys only, sells always allowed)       # Unlimited cycles — circuit breaker handles safety
MIN_CYCLES    = 1       # Start conservatively
DAILY_PROFIT_TARGET = 1.0  # Stop for the day after 1% daily profit

# --- Risk Controls ---
MAX_CONSECUTIVE_LOSSES = 3     # Pause bot after this many consecutive stop-losses
CONSECUTIVE_LOSS_PAUSE_MINS = 1440  # Pause duration in minutes (1440 = 24h)
ROLLING_STOP_WINDOW_SECS = 86400  # Rolling breaker window (24h)

# --- ADX Gate ---
USE_ADX       = False   # Enable ADX ranging filter (blocks trending markets)
ADX_TIMEFRAME = '15m'   # Timeframe for ADX calculation (faster trend detection)
ADX_PERIOD    = 14      # ADX period
ADX_THRESHOLD = 20      # Pause bot when ADX > this (stricter)

# --- EMA Trend Filter ---
USE_EMA       = False    # Enable EMA downtrend filter (blocks bear markets)
EMA_TIMEFRAME = '4h'    # Timeframe for EMA calculation
EMA_PERIOD    = 200     # EMA period
EMA_BUFFER    = 0.005   # Allow price to be 0.5% below EMA before blocking

# --- Session Windows (UTC) ---
USE_SESSIONS  = False   # ADX gate handles trending markets — no need to restrict hours
SESSIONS      = [
    (7, 9),             # London open: 07:00 - 09:00 UTC
    (13, 15),           # NY open:     13:00 - 15:00 UTC
]

# --- Indicator Refresh ---
INDICATOR_REFRESH_MINS = 15  # How often to recalculate ADX & EMA (minutes)

# --- Stop Loss ---
STOP_LOSS_PCT = 0.01   # Sell immediately if price drops this % below bought_at (0 = disabled)
STOP_LOSS_COOLDOWN_SECS = 300  # Cooldown after stop-loss before allowing new buy (300 = 5 minutes)

# --- State Persistence ---
STATE_FILE = '/a0/usr/projects/trading/bot_state.json'

# ============================================================
# LOAD CREDENTIALS
# ============================================================
def load_secrets(path='/a0/usr/projects/trading/.a0proj/secrets.env'):
    secrets = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                secrets[k.strip()] = v.strip()
    return secrets

secrets    = load_secrets()
api_key    = secrets['BYBIT_API_KEY']
api_secret = secrets['BYBIT_API_SECRET']


# ============================================================
# STATE PERSISTENCE
# ============================================================
def save_state(bought, sold, total_pnl_val):
    """Persist bought_at and sold_at so restarts don't lose position tracking."""
    try:
        global cycles_today, day_realized_pnl, current_day, stop_losses_today, max_day_realized_pnl, consecutive_losses, last_stop_loss_time, stop_loss_times, breaker_trip_time
        with open(STATE_FILE, 'w') as f:
            json.dump({
                'bought_at': bought,
                'sold_at': sold,
                'total_realized_pnl': total_pnl_val,
                'cycles_today': cycles_today if 'cycles_today' in globals() else 0,
                'stop_losses_today': stop_losses_today if 'stop_losses_today' in globals() else 0,
                'max_day_realized_pnl': max_day_realized_pnl if 'max_day_realized_pnl' in globals() else 0.0,
                'consecutive_losses': consecutive_losses if 'consecutive_losses' in globals() else 0,
                'last_stop_loss_time': last_stop_loss_time if 'last_stop_loss_time' in globals() else 0,
                'stop_loss_times': stop_loss_times if 'stop_loss_times' in globals() else [],
                'breaker_trip_time': breaker_trip_time if 'breaker_trip_time' in globals() else 0,
                'day_realized_pnl': day_realized_pnl if 'day_realized_pnl' in globals() else 0.0,
                'current_day': str(current_day) if 'current_day' in globals() else str(datetime.now(timezone.utc).date())
            }, f)
    except Exception as e:
        log(f"⚠️  Could not save state: {e}")

def load_state():
    """Load persisted state on startup. Returns 12 fields."""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                s = json.load(f)
            bought_at_val = f"{s['bought_at']:.2f}" if s['bought_at'] is not None else 'None'
            sold_at_val   = f"{s['sold_at']:.2f}" if s['sold_at'] is not None else 'None'
            log(f"📂 Restored state: bought_at={bought_at_val} sold_at={sold_at_val}")
            return (
                s['bought_at'],
                s['sold_at'],
                s.get('total_realized_pnl', 0.0),
                s.get('cycles_today', 0),
                s.get('day_realized_pnl', 0.0),
                s.get('current_day', None),
                s.get('stop_losses_today', 0),
                s.get('max_day_realized_pnl', 0.0),
                s.get('consecutive_losses', 0),
                s.get('last_stop_loss_time', 0),
                s.get('stop_loss_times', []),
                s.get('breaker_trip_time', 0)
            )
    except Exception as e:
        log(f"⚠️  Could not load state: {e}")
    return None, None, 0.0, 0, 0.0, None, 0, 0.0, 0, 0, [], 0

# Global variables for P&L tracking
total_realized_pnl = 0.0
day_realized_pnl = 0.0
max_day_realized_pnl = 0.0  # All-time best day P&L (never resets at rollover)
day_start_equity = 0.0
consecutive_losses = 0
stop_losses_today = 0  # Daily stop-loss counter (dashboard stat)
last_stop_loss_time = 0  # Unix timestamp of last stop-loss (for cooldown)
stop_loss_times = []  # Rolling 24h stop-loss timestamps (persisted)
breaker_trip_time = 0.0  # Breaker pause start (persisted)
prev_rsi = 0  # Previous RSI value for recovery confirmation

# ============================================================
# EXCHANGE
# ============================================================
def make_exchange():
    return ccxt.bybit({
        'apiKey': api_key,
        'secret': api_secret,
        'enableRateLimit': True,
        'options': {
            'recvWindow': 20000,
        },
    })

exchange = make_exchange()

# ============================================================
# LOGGING
# ============================================================
def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}")

def lull_status():
    """Read the paper page's BUY WINDOW chip (single source of truth Adrian watches).
    Returns (active, deep, reason). Fail-safe: any error or stale feed -> (False, False, 'feed unavailable').
    Gate semantics: we require active (lull present = chip green or blue). The blue chip's extra RSI<30
    condition is already covered by the bot's own RSI recovery-cross trigger (prev RSI <= 30)."""
    try:
        with open(PAPER_DATA_FILE) as f:
            d = json.load(f)
        if time.time() - d.get('ts', 0) > LULL_MAX_AGE_SECS:
            return False, False, 'paper feed stale'
        bw = d.get('buy_window') or {}
        return bool(bw.get('active')), bool(bw.get('deep')), str(bw.get('reason', ''))
    except Exception:
        return False, False, 'feed unavailable'

# ============================================================
# MARKET DATA
# ============================================================
def get_ticker():
    info = exchange.fetch_ticker(SYMBOL)
    return float(info['last'])

def get_candle():
    """Fetch the most recently CLOSED 1-minute candle."""
    server_time = exchange.fetch_time()
    tf_ms = exchange.parse_timeframe(TIMEFRAME) * 1000
    since = server_time - (server_time % tf_ms) - tf_ms
    ohlcv = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, since=since, limit=1)
    if not ohlcv:
        return None, None
    return float(ohlcv[0][1]), float(ohlcv[0][4])  # open, close

def get_balance():
    wallet = exchange.fetch_balance({'accountType': 'UNIFIED'})
    usdt   = float(wallet.get('USDT', {}).get('free', 0) or 0)
    btc    = float(wallet.get('BTC',  {}).get('free', 0) or 0)
    return usdt, btc

def time_to_next_candle():
    server_time = exchange.fetch_time()
    tf_ms = exchange.parse_timeframe(TIMEFRAME) * 1000
    return (tf_ms - (server_time % tf_ms)) / 1000

# ============================================================
# INDICATORS
# ============================================================
def fetch_ohlcv_df(timeframe, limit=250):
    """Fetch OHLCV and return as DataFrame."""
    ohlcv = exchange.fetch_ohlcv(SYMBOL, timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

def calculate_adx():
    df = fetch_ohlcv_df(ADX_TIMEFRAME, limit=ADX_PERIOD + 10)
    if len(df) < ADX_PERIOD:
        return None, None, None
    adx_obj = ta.adx(df['high'], df['low'], df['close'], length=ADX_PERIOD)
    adx = adx_obj[f'ADX_{ADX_PERIOD}'].iloc[-1]
    plus_di = adx_obj[f'DMP_{ADX_PERIOD}'].iloc[-1]
    minus_di = adx_obj[f'DMN_{ADX_PERIOD}'].iloc[-1]
    return adx, plus_di, minus_di

def calculate_ema():
    df = fetch_ohlcv_df(EMA_TIMEFRAME, limit=EMA_PERIOD + 10)
    if len(df) < EMA_PERIOD:
        return None
    ema = ta.ema(df['close'], length=EMA_PERIOD).iloc[-1]
    return ema

def calculate_rsi(df, period=RSI_PERIOD):
    if len(df) < period:
        return None
    rsi = ta.rsi(df['close'], length=period).iloc[-1]
    return rsi

# ============================================================
# TRADING LOGIC
# ============================================================
MIN_ORDER_USDT = 10.0 # Min order size for Bybit spot
MIN_BTC_DUST = 0.000001 # Minimum BTC amount to consider as a real holding

def get_current_dyn_margin(df_1m):
    if not USE_DYN_MARGIN or len(df_1m) < ATR_PERIOD + 1:
        return DYN_MARGIN_MIN
    atr = ta.atr(df_1m['high'], df_1m['low'], df_1m['close'], length=ATR_PERIOD).iloc[-1]
    current_price = df_1m['close'].iloc[-1]
    atr_pct = atr / current_price
    scaled_margin = DYN_MARGIN_MIN + (atr_pct - DYN_MARGIN_MIN) * \
                    (DYN_MARGIN_MAX - DYN_MARGIN_MIN) / (DYN_MARGIN_MAX - DYN_MARGIN_MIN)
    return max(DYN_MARGIN_MIN, min(DYN_MARGIN_MAX, scaled_margin))


def execute_trade(trade_type, price, usdt_balance, btc_balance):
    global total_realized_pnl, day_realized_pnl, bought_at, sold_at, cycles_today, max_day_realized_pnl

    filled_amount = 0
    filled_price = 0
    cost = 0
    order = None

    try:
        if trade_type == 'buy':
            amount_usdt = (min(usdt_balance, TEST_CAPITAL) if TEST_MODE else usdt_balance) * 0.99  # Reserve 1% buffer for fees and slippage

            if amount_usdt < MIN_ORDER_USDT:
                log(f"⚠️  Buy skipped: Capital too low. USDT: {usdt_balance:.2f}")
                return None, None, None

            # Use marketUnit=quoteCoin: tell Bybit exactly how much USDT to spend
            # This bypasses all fee/precision calculation issues
            log(f"🔄 Placing market buy order for {amount_usdt:.2f} USDT (quoteCoin mode)")
            order = exchange.create_market_buy_order(
                SYMBOL,
                amount_usdt,
                params={'marketUnit': 'quoteCoin'}
            )
            # FIX: Use 'average' for actual fill price (market orders on Bybit return 0 in 'price')
            filled_price = float(order.get('average') or order.get('price') or price)
            filled_amount = float(order.get('filled') or order.get('amount') or 0.0)
            order_cost = float(order.get('cost') or 0.0)
            # For quoteCoin mode, Bybit may return filled=0 but cost=USDT spent
            if filled_amount == 0 and order_cost > 0 and filled_price > 0:
                filled_amount = order_cost / filled_price
            cost = filled_amount * filled_price if filled_amount > 0 else order_cost

            bought_at = filled_price
            sold_at = None

            log(f"🛒 BUY {filled_amount:.7f} BTC @ {filled_price:.2f} USDT. Cost: {cost:.2f} USDT")
            cycles_today += 1

        elif trade_type == 'sell':
            amount_btc = btc_balance
            if amount_btc < MIN_BTC_DUST:
                log(f"⚠️  Sell skipped: BTC dust balance {btc_balance:.7f} is too low.")
                return None, None, None

            order = exchange.create_market_sell_order(SYMBOL, amount_btc)
            # FIX: Use 'average' for actual fill price (market orders on Bybit return 0 in 'price')
            filled_price = float(order.get('average') or order.get('price') or price)
            filled_amount = float(order.get('filled') or order.get('amount') or amount_btc)
            cost = filled_amount * filled_price

            sold_at = filled_price

            if bought_at:
                gross_pnl = (filled_price - bought_at) * filled_amount
                est_fees = 0.001 * filled_amount * (bought_at + filled_price)  # 0.1% taker on buy + 0.1% on sell
                pnl_usdt = gross_pnl - est_fees  # Net P&L — matches real balance change
                total_realized_pnl += pnl_usdt
                day_realized_pnl += pnl_usdt
                if day_realized_pnl > max_day_realized_pnl:
                    max_day_realized_pnl = day_realized_pnl
                log(f"📈 SELL {filled_amount:.7f} BTC @ {filled_price:.2f} USDT. Received: {cost:.2f} USDT. Net P&L: {pnl_usdt:.2f} USDT (gross {gross_pnl:.2f} - fees {est_fees:.2f})")
            else:
                log(f"🛑 SELL {filled_amount:.7f} BTC @ {filled_price:.2f} USDT. (No prior 'bought_at' for P&L tracking)")

            bought_at = None

        return filled_amount, filled_price, cost

    except ccxt.InsufficientFunds as e:
        log(f"⚠️  Trade error (Insufficient Funds): {e}")
    except ccxt.InvalidOrder as e:
        log(f"⚠️  Trade error (Invalid Order): {e}")
    except Exception as e:
        log(f"⚠️  Trade error: {trade_type} failed - {e}")
        log(traceback.format_exc())

    return None, None, None


# ============================================================
# MAIN LOOP
# ============================================================
def main():
    global bought_at, sold_at, total_realized_pnl, cycles_today, day_realized_pnl, consecutive_losses, stop_losses_today, max_day_realized_pnl
    global last_stop_loss_time, prev_rsi, stop_loss_times, breaker_trip_time
    global current_day, day_start_equity, last_indicator_refresh

    current_rsi = None  # Initialize before loop for prev_rsi tracking

    while True:
        try:
            # --- Day Rollover Check (handles bot running past midnight) ---
            today = datetime.now(timezone.utc).date()
            if str(today) != str(current_day):
                log(f"🗓️ Day rollover: {current_day} -> {today}. Resetting daily P&L and cycles.")
                current_day = today
                day_realized_pnl = 0.0
                cycles_today = 0
                stop_losses_today = 0
                day_start_equity = 0.0  # Will be reset below
                save_state(bought_at, sold_at, total_realized_pnl)

            # Ensure day_start_equity is set only once per day
            if day_start_equity == 0.0:
                usdt_bal, btc_bal = get_balance()
                total_current_equity = usdt_bal + (btc_bal * get_ticker() if btc_bal > 0 else 0)
                day_start_equity = total_current_equity
                log(f"[INIT] Day starting equity set to: {day_start_equity:.2f} USDT")

            price = get_ticker()
            if price is None:
                log("⚠️  Could not fetch price.")
                time.sleep(1)
                continue

            df_1m = fetch_ohlcv_df(TIMEFRAME, limit=RSI_PERIOD + 10)
            if df_1m.empty or len(df_1m) < RSI_PERIOD + 1:
                log("⚠️  Not enough 1m candle data for RSI calculation yet.")
                time.sleep(1)
                continue
            prev_rsi = current_rsi  # Save previous RSI for recovery confirmation
            current_rsi = calculate_rsi(df_1m)

            # Refresh indicators for ADX, EMA (less frequently)
            now_minute = datetime.now(timezone.utc).minute
            adx_value = None
            ema_value = None
            ranging_market = True
            no_downtrend = True
            
            if (now_minute % INDICATOR_REFRESH_MINS == 0) and (now_minute != last_indicator_refresh):
                # Calculate ADX if enabled
                if USE_ADX:
                    adx_value = calculate_adx()
                    if adx_value is not None:
                        ranging_market = (adx_value[0] < ADX_THRESHOLD) if adx_value else True
                        if not ranging_market:
                            log(f"⏸️  ADX gate: {adx_value[0]:.2f} > {ADX_THRESHOLD} (trending market, pausing)")
                    else:
                        log(f"⚠️  ADX calculation failed, assuming ranging")
                
                # Calculate EMA if enabled
                if USE_EMA:
                    ema_value = calculate_ema()
                    if ema_value is not None:
                        no_downtrend = (price > ema_value * (1 - EMA_BUFFER)) if ema_value else True
                        if not no_downtrend:
                            log(f"⏸️  EMA gate: {price:.2f} < {ema_value:.2f} (downtrend, pausing)")
                    else:
                        log(f"⚠️  EMA calculation failed, assuming no downtrend")
                
                adx_str = f'{adx_value:.2f}' if adx_value is not None else 'off'
                ema_str = f'{ema_value:.2f}' if ema_value is not None else 'off'
                if USE_ADX or USE_EMA:
                    log(f"Indicators refreshed. ADX:{adx_str} EMA:{ema_str}")
                else:
                    log(f"Indicators refreshed. ADX:{adx_str} EMA:{ema_str} (gates disabled — pure RSI mode)")
                
                last_indicator_refresh = now_minute
            
            # Gate check: pause all trading if conditions not met
            # Uptrend (price > EMA): ALLOW — profit from rising markets
            # Downtrend (price < EMA): BLOCK — protect capital from falling knives
            # ADX is advisory only — high ADX in uptrends is fine
            bot_allowed = no_downtrend

            # Get current balance for decisions
            balance_usdt, balance_btc = get_balance()

            next_buy_target = None
            next_sell_target = None

            now_dt = time.time()

            # --- DETERMINE PRIMARY TRADE PHASE (based on actual BTC holdings) ---
            next_trade = 'buy'
            if balance_btc >= MIN_BTC_DUST:
                next_trade = 'sell'

            # --- Buy Logic ---
            # NOTE: all buy-blocking guards MUST live in this condition (or nested inside it) — never in a later elif (dead-code lesson 2026-07-28)
            lull_active, lull_deep, lull_reason = lull_status() if USE_LULL_GATE else (True, False, 'gate off')
            if next_trade == 'buy' and bot_allowed and not (breaker_trip_time > 0 and (now_dt - breaker_trip_time) < CONSECUTIVE_LOSS_PAUSE_MINS * 60) and not (MAX_CYCLES > 0 and cycles_today >= MAX_CYCLES) and lull_active:
                anchor_price = sold_at if sold_at is not None else price
                next_buy_target = anchor_price * (1 - BUY_MARGIN)

                buy_condition_rsi = USE_RSI and prev_rsi is not None and prev_rsi <= RSI_OVERSOLD and current_rsi is not None and current_rsi > RSI_OVERSOLD
                buy_condition_price = price <= next_buy_target if next_buy_target is not None else True

                # Stop-loss cooldown: prevent instant re-buy after stop-loss
                if last_stop_loss_time > 0 and (time.time() - last_stop_loss_time) < STOP_LOSS_COOLDOWN_SECS:
                    cooldown_remaining = int(STOP_LOSS_COOLDOWN_SECS - (time.time() - last_stop_loss_time))
                    log(f"⏳ BUY BLOCKED: Stop-loss cooldown ({cooldown_remaining}s remaining)")
                elif PURE_RSI_BUY:
                    if buy_condition_rsi and balance_usdt >= MIN_ORDER_USDT:
                        log(f"🟢 BUY SIGNAL (RSI Recovery): RSI crossed {RSI_OVERSOLD} (prev: {prev_rsi:.1f} → now: {current_rsi:.1f}) | Price {price:.2f}")
                        execute_trade('buy', price, balance_usdt, balance_btc)
                    else:
                        prev_rsi_str = f"{prev_rsi:.1f}" if prev_rsi is not None else "N/A"
                        log(f"🔴 NO BUY: RSI {current_rsi:.1f} (prev: {prev_rsi_str}) — waiting for recovery above {RSI_OVERSOLD} | Price {price:.2f} | USDT:{balance_usdt:.2f}")
                else:
                    if buy_condition_price and buy_condition_rsi and balance_usdt >= MIN_ORDER_USDT:
                        log(f"🟢 BUY SIGNAL: Price {price:.2f} <= {next_buy_target:.2f} & RSI {current_rsi:.1f} <= {RSI_OVERSOLD}")
                        execute_trade('buy', price, balance_usdt, balance_btc)
                    elif buy_condition_price and not USE_RSI and balance_usdt >= MIN_ORDER_USDT:
                        log(f"🟢 BUY SIGNAL (No RSI): Price {price:.2f} <= {next_buy_target:.2f}")
                        execute_trade('buy', price, balance_usdt, balance_btc)
                    else:
                        log(f"🔴 NO BUY: Price {price:.2f} ({next_buy_target:.2f}) & RSI {current_rsi:.1f} ({RSI_OVERSOLD}) | USDT:{balance_usdt:.2f}")

            elif next_trade == 'buy' and MAX_CYCLES > 0 and cycles_today >= MAX_CYCLES:
                log(f"⏸️ BUY BLOCKED: daily cycle cap reached ({cycles_today}/{MAX_CYCLES}). Resets at UTC midnight.")

            elif next_trade == 'buy' and USE_LULL_GATE and not lull_active:
                log(f"⏸️ BUY BLOCKED: no buy window — {lull_reason} (chip not green/blue). Discipline: wait for the lull.")

            elif next_trade == 'buy' and breaker_trip_time > 0 and (now_dt - breaker_trip_time) < CONSECUTIVE_LOSS_PAUSE_MINS * 60:
                remaining = int(CONSECUTIVE_LOSS_PAUSE_MINS * 60 - (now_dt - breaker_trip_time))
                stops_in_window = len([t for t in stop_loss_times if now_dt - t <= ROLLING_STOP_WINDOW_SECS])
                log(f"⏸️ BUY BLOCKED: circuit breaker active — {stops_in_window} stops in 24h window. Pause ends in {remaining // 3600}h {(remaining % 3600) // 60}m.")

            elif next_trade == 'buy' and not bot_allowed:
                if adx_value and adx_value >= ADX_THRESHOLD:
                    log(f"⏸️ BUY BLOCKED: ADX gate active | ADX:{adx_value:.1f} > {ADX_THRESHOLD}")
                elif ema_value and not no_downtrend:
                    log(f"⏸️ BUY BLOCKED: EMA gate active | Price {price:.2f} < EMA {ema_value:.2f}")
                else:
                    log(f"⏸️ BUY BLOCKED: Gates inactive (bot_allowed=False)")

            # --- Sell Logic ---
            elif next_trade == 'sell':
                next_sell_target = bought_at * FEE_BUFFER if bought_at is not None else None  # REAL fee-aware gate — drives dashboard display and no-RSI fallback

                # Re-anchor sold_at if price rallies significantly above last profitable sell
                if RALLY_REANCHOR_PCT > 0 and sold_at is not None and price > sold_at * (1 + RALLY_REANCHOR_PCT):
                    log(f"📈 RALLY RE-ANCHOR: Price {price:.2f} is {RALLY_REANCHOR_PCT*100:.2f}% above last sell {sold_at:.2f}. Updating sold_at.")
                    sold_at = price
                    save_state(bought_at, sold_at, total_realized_pnl)

                sell_condition_rsi = USE_RSI and current_rsi is not None and current_rsi >= RSI_OVERBOUGHT
                sell_condition_price = next_sell_target is not None and price >= next_sell_target

                log(f"[DEBUG] next_sell calculation: bought_at={bought_at}, FEE_BUFFER={FEE_BUFFER}, expected_next_sell={next_sell_target}")

                # Check for Stop Loss first
                if STOP_LOSS_PCT > 0 and bought_at is not None and price < bought_at * (1 - STOP_LOSS_PCT):
                    log(f"🛑 STOP LOSS: price {price:.2f} <= {bought_at * (1 - STOP_LOSS_PCT):.2f} ({STOP_LOSS_PCT*100:.1f}% below buy {bought_at:.2f})")
                    stop_loss_times.append(time.time())
                    stop_loss_times[:] = [t for t in stop_loss_times if time.time() - t <= ROLLING_STOP_WINDOW_SECS]
                    consecutive_losses = len(stop_loss_times)  # rolling 24h count (wins do NOT reset)
                    # Re-arm: if a previous breaker's pause has fully expired, clear it so a new 3-stop window trips again
                    if breaker_trip_time > 0 and (time.time() - breaker_trip_time) >= CONSECUTIVE_LOSS_PAUSE_MINS * 60:
                        breaker_trip_time = 0
                    if consecutive_losses >= MAX_CONSECUTIVE_LOSSES and breaker_trip_time == 0:
                        breaker_trip_time = time.time()
                        log(f"🚨 CIRCUIT BREAKER TRIPPED: {consecutive_losses} stop-losses in 24h — buys paused for {CONSECUTIVE_LOSS_PAUSE_MINS}min")
                    stop_losses_today += 1
                    log(f"🔄 Stop-losses in 24h: {consecutive_losses}/{MAX_CONSECUTIVE_LOSSES} | Stop-losses today: {stop_losses_today}")
                    last_stop_loss_time = time.time()
                    log(f"⏳ Stop-loss cooldown active for {STOP_LOSS_COOLDOWN_SECS}s")
                    execute_trade('sell', price, balance_usdt, balance_btc)
                elif sell_condition_rsi and bought_at is not None and price >= bought_at * FEE_BUFFER and balance_btc >= MIN_BTC_DUST:
                    # Fee-aware sell floor: only sell at 1.0% gross (~0.8% net) so wins outweigh 1% stop-losses
                    # Rolling 24h breaker: wins do NOT reset the stop-loss window
                    log(f"🔴 SELL SIGNAL (Pure RSI + Fee-aware): RSI {current_rsi:.1f} >= {RSI_OVERBOUGHT} | Price {price:.2f} >= Buy+fees {bought_at * FEE_BUFFER:.2f}")
                    execute_trade('sell', price, balance_usdt, balance_btc)
                elif sell_condition_rsi and bought_at is not None and price < bought_at * FEE_BUFFER and balance_btc >= MIN_BTC_DUST:
                    # RSI says sell but price hasn't covered fees yet — hold and wait
                    log(f"⏳ HOLDING: RSI {current_rsi:.1f} >= {RSI_OVERBOUGHT} but Price {price:.2f} < Buy+fees {bought_at * FEE_BUFFER:.2f} — waiting for fee coverage")
                elif sell_condition_price and not USE_RSI and balance_btc >= MIN_BTC_DUST:
                    log(f"🔴 SELL SIGNAL (No RSI): Price {price:.2f} >= {next_sell_target:.2f}")
                    execute_trade('sell', price, balance_usdt, balance_btc)
                else:
                    log(f"🟢 NO SELL: RSI {current_rsi:.1f} ({RSI_OVERBOUGHT}) | Price {price:.2f} | BTC:{balance_btc:.7f}")

            save_state(bought_at, sold_at, total_realized_pnl)

            # Log comprehensive status for data_writer.py
            next_buy_target_str  = f"≤{next_buy_target:.2f}"  if next_buy_target  is not None else 'None'
            next_sell_target_str = f"≥{next_sell_target:.2f}" if next_sell_target is not None else 'None'
            log(f"Price: {price:.2f} USDT | RSI: {current_rsi:.1f} | Balance — USDT: {balance_usdt:.2f} | BTC: {balance_btc:.7f} | Next buy{next_buy_target_str} | Next sell{next_sell_target_str} | DayP&L={day_realized_pnl:.2f} USDT | Cycles={cycles_today}" + (f" | ⚗️ TEST MODE: Using max ${TEST_CAPITAL:.2f} USDT" if TEST_MODE else ""))

            time.sleep(1)

        except Exception as e:
            log(f"❌ Main loop error: {e}")
            log(traceback.format_exc())
            time.sleep(5)


if __name__ == '__main__':
    # --- Initialise globals before entering main() ---
    current_day = datetime.now(timezone.utc).date()
    bought_at, sold_at, total_realized_pnl, cycles_today, day_realized_pnl, loaded_day, stop_losses_today, max_day_realized_pnl, consecutive_losses, last_stop_loss_time, stop_loss_times, breaker_trip_time = load_state()

    # Day rollover on startup
    if loaded_day is not None and loaded_day != str(current_day):
        log(f"🗓️ Day rollover detected on startup: {loaded_day} -> {current_day}. Resetting daily P&L.")
        day_realized_pnl = 0.0
        cycles_today = 0
        stop_losses_today = 0
        day_start_equity = 0.0

    # If holding BTC with no buy record, anchor to current price
    if bought_at is None and sold_at is None:
        usdt_bal, btc_bal = get_balance()
        if btc_bal >= MIN_BTC_DUST:
            current_price = get_ticker()
            bought_at = current_price
            log(f"[INIT] Detected {btc_bal:.7f} BTC on startup. Anchoring bought_at to {bought_at:.2f} USDT.")

    last_indicator_refresh = datetime.now(timezone.utc).minute
    main()
