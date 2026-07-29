#!/usr/bin/env python3
"""
data_writer.py - Bridges live bot state to the dashboard.

Reads:
  - /a0/usr/projects/trading/bot_output.log  (live status: price, RSI, balance, targets)
  - /a0/usr/projects/trading/bot_state.json  (anchors: bought_at, sold_at)
  - Bybit via CCXT                            (candle history for the chart)

Writes every 5s:
  - /a0/webui/public/trading/data.json

Structure (matches /a0/webui/public/trading.html exactly):
  {
    "ts": <unix>, "utc": "<UTC string>",
    "status": {"price", "rsi", "next_buy", "next_sell", "test_mode"},
    "balance": {"USDT", "BTC"},
    "candles": [{"time", "open", "high", "low", "close", "rsi"}, ...],
    "log": ["<last 30 log lines>"]
  }
"""

import json
import time
import re
import os
import ccxt
import traceback
from datetime import datetime, timezone
from collections import deque

# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------
LOG_FILE   = '/a0/usr/projects/trading/bot_output.log'
STATE_FILE = '/a0/usr/projects/trading/bot_state.json'
DATA_FILE  = '/a0/usr/projects/trading/data.json'

# ----------------------------------------------------------------------------
# Regex patterns for parsing bot_output.log lines
# Example lines:
#   Price: 77126.80 USDT | RSI: 63.6
#   Balance — USDT: 1240.63 | BTC: 0.000002
#   Next buy≤76591.54 | Next sell≥77816.76 | USDT=1180.80 | BTC=0.000001 (new combined line)
#   DayP&L=0.00 USDT | Cycles=0
#   ⚗️ TEST MODE: Using max $500.0 USDT (Total available: $1240.63)
#   [DEBUG] next_sell calculation: bought_at=76006.3, SELL_MARGIN=1.0025, expected_next_sell=76196.32
# ----------------------------------------------------------------------------
RE_PRICE_RSI = re.compile(r'Price: (\d+\.\d+) USDT \| RSI: (\d+\.\d+)')
RE_BALANCE   = re.compile(r'Balance — USDT: (\d+\.\d+) \| BTC: (\d+\.\d+)')
# This regex captures next_buy, next_sell, USDT, BTC from the new combined line format
RE_FULL_STATUS_LINE = re.compile(r'Next buy[<=\s\u2264]*([\d.]+) \| Next sell[>=\s\u2265]*([\d.]+) \| USDT=([\d.]+)\s*\| BTC=([\d.]+)')
# Individual Next Buy/Sell for lines that might only contain one
RE_NEXT_BUY_ONLY = re.compile(r'Next buy[<=\s\u2264]*([\d.]+)')
RE_NEXT_SELL_ONLY = re.compile(r'Next sell[>=\s\u2265]*([\d.]+)')
RE_TEST_MODE       = re.compile(r'(?:TEST MODE|Test: \$)', re.IGNORECASE)
RE_DAY_PNL         = re.compile(r'DayP&L=([+-]?\d+\.?\d*)')

# ----------------------------------------------------------------------------
# CCXT exchange (public endpoint only — no API keys needed for OHLCV)
# ----------------------------------------------------------------------------
exchange = ccxt.bybit({'enableRateLimit': True})


def tail(path, n=80):
    """Return last n lines of a file (memory-safe)."""
    try:
        with open(path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block = 4096
            data = b''
            while size > 0 and data.count(b'\n') <= n:
                read = min(block, size)
                size -= read
                f.seek(size)
                data = f.read(read) + data
            lines = data.decode('utf-8', errors='ignore').splitlines()
            return lines[-n:]
    except Exception as e:
        print(f"[tail] {e}")
        return []


def parse_log_status(lines):
    """Walk the tail backwards and pick the most recent value for each field."""
    status = {
        'price':     None,
        'rsi':       None,
        'next_buy':  None,
        'next_sell': None,
        'test_mode': False,
        'day_pnl':     None,
        'max_day_pnl': None,
        'total_pnl':   None,
        'bought_at': None,
        'stop_loss': None,
        'cycles': None,
        'stop_losses': None,
        'consecutive_losses': None,
    }
    balance = {'USDT': None, 'BTC': None}

    # Walk newest -> oldest, fill in any missing fields
    for line in reversed(lines):
        # Prioritize parsing comprehensive status line if available
        if status['next_buy'] is None and status['next_sell'] is None and \
           balance['USDT'] is None and balance['BTC'] is None:
            m_full = RE_FULL_STATUS_LINE.search(line)
            if m_full:
                status['next_buy'] = float(m_full.group(1))
                status['next_sell'] = float(m_full.group(2))
                balance['USDT'] = float(m_full.group(3))
                balance['BTC'] = float(m_full.group(4))

        # General Price and RSI parsing
        if status['price'] is None or status['rsi'] is None:
            m = RE_PRICE_RSI.search(line)
            if m:
                status['price'] = float(m.group(1))
                status['rsi'] = float(m.group(2))

        # General Balance parsing
        if balance['USDT'] is None or balance['BTC'] is None:
            m = RE_BALANCE.search(line)
            if m:
                balance['USDT'] = float(m.group(1))
                balance['BTC'] = float(m.group(2))

        # Process individual next buy/sell if not found in full line
        if status['next_buy'] is None:
            m_buy = RE_NEXT_BUY_ONLY.search(line)
            if m_buy:
                status['next_buy'] = float(m_buy.group(1))

        if status['next_sell'] is None:
            m_sell = RE_NEXT_SELL_ONLY.search(line)
            if m_sell:
                status['next_sell'] = float(m_sell.group(1))


        if status['day_pnl'] is None:
            m = RE_DAY_PNL.search(line)
            if m:
                status['day_pnl'] = float(m.group(1))

        # Test mode: presence anywhere in recent log = true
        if not status['test_mode'] and RE_TEST_MODE.search(line):
            status['test_mode'] = True

        # Break condition: all critical fields must be found, or implied by fund holding
        if (status['price'] is not None and status['rsi'] is not None and \
            balance['USDT'] is not None and balance['BTC'] is not None):
            # We can break if both Next targets are found, or if one is found and the other is not expected (based on balance)
            if (status['next_buy'] is not None and status['next_sell'] is not None) or \
               (status['next_buy'] is None and balance['BTC'] > 0.000001 and status['next_sell'] is not None) or \
               (status['next_sell'] is None and balance['USDT'] > 0.01 and status['next_buy'] is not None): # Sufficient USDT implies looking for buy
                break


    # Read total_pnl from bot_state.json (outside loop)
    try:
        with open(STATE_FILE) as bf:
            bs = json.load(bf)
            status['total_pnl'] = bs.get('total_realized_pnl')
            status['cycles'] = bs.get('cycles_today')
            status['stop_losses'] = bs.get('stop_losses_today')
            status['consecutive_losses'] = bs.get('consecutive_losses')
            status['max_day_pnl'] = bs.get('max_day_realized_pnl')
    except:
        pass

    return status, balance


def compute_rsi(closes, period=14):
    """Wilder's RSI. Returns a list aligned with closes (Nones until enough data)."""
    rsis = [None] * len(closes)
    if len(closes) < period + 1:
        return rsis

    gains = []
    losses = []
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    rs = avg_gain / avg_loss if avg_loss > 0 else 0
    rsis[period] = 100 - (100 / (1 + rs)) if avg_loss > 0 else 100

    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        gain = max(change, 0)
        loss = max(-change, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 0
        rsis[i] = 100 - (100 / (1 + rs)) if avg_loss > 0 else 100

    return rsis


def fetch_candles(limit=120):
    """Fetch 1m BTC/USDT OHLCV via CCXT, return list of dicts with RSI."""
    try:
        ohlcv = exchange.fetch_ohlcv('BTC/USDT', timeframe='1m', limit=limit)
        closes = [c[4] for c in ohlcv]
        rsis = compute_rsi(closes, period=14)
        out = []
        for i, c in enumerate(ohlcv):
            rsi_val = round(rsis[i], 2) if rsis[i] is not None else None
            out.append({
                'time':   int(c[0] / 1000),   # seconds for Lightweight Charts
                'open':   float(c[1]),
                'high':   float(c[2]),
                'low':    float(c[3]),
                'close':  float(c[4]),
                'volume': float(c[5]),
                'rsi':    rsi_val,
            })
        return out
    except Exception as e:
        print(f"[fetch_candles] {e}")
        return []


def fetch_ticker():
    """Fetch live ticker price from Bybit. Returns (price, ts) or (None, None)."""
    try:
        ticker = exchange.fetch_ticker('BTC/USDT')
        return ticker['last'], ticker['timestamp']
    except Exception as e:
        print(f"[fetch_ticker] {e}")
        return None, None


def is_log_stale(log_lines, max_minutes=10):
    """Check if the bot log has any line with a recent UTC timestamp."""
    for line in reversed(log_lines):
        m = re.search(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\]', line)
        if m:
            try:
                from datetime import timezone
                log_dt = datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S')
                elapsed = (datetime.now(tz=timezone.utc) - log_dt.replace(tzinfo=timezone.utc)).total_seconds()
                return elapsed > max_minutes * 60
            except:
                return True
    return True  # No timestamp found = stale


def read_console_log(n=30):
    """Last n log lines for the right-hand console panel."""
    return tail(LOG_FILE, n)


def write_data(payload):
    try:
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        tmp = DATA_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(payload, f, indent=2)
        os.rename(tmp, DATA_FILE)
    except Exception as e:
        print(f"[write_data] {traceback.format_exc()}")


def main():
    last_log_size = 0
    last_write_time = 0
    while True:
        try:
            # Read only new log lines since last read
            current_log_size = os.path.getsize(LOG_FILE)
            if current_log_size < last_log_size: # Log file was rotated or truncated
                last_log_size = 0 # Reset to reread entire file

            with open(LOG_FILE, 'r') as f:
                f.seek(last_log_size)
                new_lines = f.readlines()
                last_log_size = f.tell()
            
            lines = deque(maxlen=80) # Store up to 80 recent lines
            lines.extend([line.strip() for line in new_lines]) # Add new lines

            # Load historical log lines for contexts. Crucially, parse_log_status needs recent messages.
            # Tail the full log for parsing, not just new_lines which might miss context.
            full_log_lines = tail(LOG_FILE, n=80) # Always get the latest context for parsing
            status, balance = parse_log_status(full_log_lines)
            log_console_lines = read_console_log()

            # Fetch candles every 5 seconds (API rate limit friendly)
            now = time.time()
            if now - last_write_time >= 5:
                candles = fetch_candles()
                # --- Live data fallback when bot is OFF ---
                full_log_lines = tail(LOG_FILE, n=80)
                stale = is_log_stale(full_log_lines)
                if stale:
                    live_price, _ = fetch_ticker()
                    if live_price is not None:
                        status['price'] = live_price
                        status['bot_off'] = True
                        if candles and len(candles) > 0:
                            last_candle = candles[-1]
                            if last_candle.get('rsi') is not None:
                                status['rsi'] = last_candle['rsi']
                        if status.get('next_buy') is None and status.get('next_sell') is None:
                            status['next_buy'] = round(live_price * 0.99, 2)
                            status['next_sell'] = round(live_price * 1.01, 2)
                else:
                    status['bot_off'] = False
                # -------------------------------------------
                
                
                # Read bot_state.json for bought_at and compute stop_loss
                try:
                    with open(STATE_FILE, 'r') as sf:
                        state = json.load(sf)
                        bought = state.get('bought_at')
                        if bought is not None:
                            status['bought_at'] = bought
                            status['stop_loss'] = round(bought * 0.99, 2)
                except:
                    pass

                payload = {
                    'ts':          int(now),
                    'utc':         datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
                    'status':      status,
                    'balance':     balance,
                    'candles':     candles,
                    'log':         log_console_lines,
                }
                write_data(payload)
                last_write_time = now

        except Exception as e:
            print(f"[main_loop] {traceback.format_exc()}")

        time.sleep(1) # Write data every 5s, so 1s sleep is okay


if __name__ == '__main__':
    main()
