#!/usr/bin/env python3
"""
paper_server.py - Paper BTC/USDT trading chart with manual BUY/SELL buttons.

Self-contained stdlib HTTP server. PAPER ONLY: no API keys, no real orders.
Simulated fills at the last public Bybit price with a taker fee per side.

Endpoints
  GET  /          -> paper.html (the UI)
  GET  /data.json -> candles, price, buy_window, trade state, recent trades
  POST /buy       -> open a paper position at the current price
  POST /sell      -> close the paper position at the current price
  POST /reset     -> reset the session to the starting equity

Run: nohup /opt/venv/bin/python -u paper_server.py >> paper_server.log 2>&1 &
"""

import json
import time
import os
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import ccxt

BASE          = '/a0/usr/projects/trading'
HTML_FILE     = os.path.join(BASE, 'paper.html')
STATE_FILE    = os.path.join(BASE, 'paper_state.json')
TRADES_FILE   = os.path.join(BASE, 'paper_trades.json')
PORT          = 5080
PUB_DATA_FILE = '/a0/webui/public/trading/paper_data.json'  # served by the A0 WebUI

START_EQUITY  = 861.99          # paper starting USDT (mirrors real balance)
FEE_RATE      = 0.001           # Bybit spot taker fee, per side
SELL_MULT     = 1.007           # +0.7% gross -> +0.5% net after 2x 0.1% fees
STOP_MULT     = 0.99            # -1% suggested stop line

CANDLE_LIMIT  = 1440            # 24h of 1m candles for the lull baseline
LULL_WINDOW   = 30              # minutes used to judge the current lull
LULL_BASELINE = 1440            # rolling window for the average range
LULL_FACTOR   = 0.80            # current range must be < 80% of the average
DEEP_RSI      = 30.0            # RSI below this upgrades the window to 'deep'

LOCK = threading.Lock()
CANDLES = []
LAST_PRICE = None
LAST_ERROR = None

exchange = ccxt.bybit({'enableRateLimit': True})


def compute_rsi(closes, period=14):
    """Wilder's RSI, matching data_writer.py."""
    rsis = [None] * len(closes)
    if len(closes) < period + 1:
        return rsis
    gains, losses = [], []
    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0))
        losses.append(max(-ch, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    rsis[period] = 100 - (100 / (1 + (avg_gain / avg_loss))) if avg_loss > 0 else 100
    for i in range(period + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(ch, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-ch, 0)) / period
        rsis[i] = 100 - (100 / (1 + (avg_gain / avg_loss))) if avg_loss > 0 else 100
    return rsis


def compute_buy_window(candles):
    """Is the current 1m tape a lull worth buying?"""
    if len(candles) < LULL_WINDOW + 60:
        return {'active': False, 'deep': False, 'reason': 'warming up',
                'cur': None, 'avg': None, 'ratio': None, 'rsi': None}
    closed = candles[:-1]  # ignore the still-forming last candle
    ranges = [c['high'] - c['low'] for c in closed]
    cur = sum(ranges[-LULL_WINDOW:]) / LULL_WINDOW
    base = ranges[-LULL_BASELINE:]
    avg = sum(base) / len(base)
    ratio = (cur / avg) if avg > 0 else None
    rsi = closed[-1]['rsi']
    active = ratio is not None and ratio < LULL_FACTOR
    deep = active and rsi is not None and rsi < DEEP_RSI
    if active:
        reason = 'lull' + (' + RSI dip' if deep else '')
    else:
        reason = 'no lull'
    return {'active': active, 'deep': deep, 'reason': reason,
            'cur': round(cur, 1), 'avg': round(avg, 1),
            'ratio': round(ratio, 3) if ratio is not None else None,
            'rsi': rsi}


def fetch_loop():
    """Fetch 1m OHLCV from Bybit public endpoint every 5s."""
    global LAST_PRICE, LAST_ERROR
    while True:
        try:
            ohlcv = exchange.fetch_ohlcv('BTC/USDT', timeframe='1m', limit=CANDLE_LIMIT)
            closes = [c[4] for c in ohlcv]
            rsis = compute_rsi(closes)
            out = []
            for i, c in enumerate(ohlcv):
                out.append({
                    'time':   int(c[0] / 1000),
                    'open':   float(c[1]),
                    'high':   float(c[2]),
                    'low':    float(c[3]),
                    'close':  float(c[4]),
                    'volume': float(c[5]),
                    'rsi':    round(rsis[i], 2) if rsis[i] is not None else None,
                })
            with LOCK:
                CANDLES.clear()
                CANDLES.extend(out)
                LAST_PRICE = out[-1]['close'] if out else None
                LAST_ERROR = None
        except Exception as e:
            with LOCK:
                LAST_ERROR = f'{type(e).__name__}: {e}'
            print('[fetch]', traceback.format_exc(), flush=True)
        time.sleep(5)


def default_state():
    return {
        'start_equity': START_EQUITY,
        'usdt': START_EQUITY,
        'btc': 0.0,
        'position': None,
        'cycles': 0,
        'wins': 0,
        'losses': 0,
        'realized_pnl': 0.0,
        'fees_paid': 0.0,
        'started': time.time(),
        'last_event': None,
    }


def load_state():
    try:
        with open(STATE_FILE) as f:
            s = json.load(f)
        d = default_state()
        d.update(s)
        return d
    except Exception:
        return default_state()


def save_state(state):
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def log_trade(trade):
    recs = []
    try:
        with open(TRADES_FILE) as f:
            recs = json.load(f)
    except Exception:
        recs = []
    recs.append(trade)
    tmp = TRADES_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(recs[-200:], f, indent=2)
    os.replace(tmp, TRADES_FILE)


def equity_of(state):
    if state['position'] and LAST_PRICE:
        return state['position']['btc'] * LAST_PRICE
    return state['usdt']


def public_state(state):
    eq = equity_of(state)
    return {
        'usdt': round(state['usdt'], 2),
        'btc': round(state['btc'], 8),
        'equity': round(eq, 2),
        'start_equity': state['start_equity'],
        'net_vs_start': round(eq - state['start_equity'], 2),
        'cycles': state['cycles'],
        'wins': state['wins'],
        'losses': state['losses'],
        'realized_pnl': round(state['realized_pnl'], 2),
        'fees_paid': round(state['fees_paid'], 2),
        'position': state['position'],
        'last_event': state['last_event'],
    }


def do_buy():
    with LOCK:
        price = LAST_PRICE
        if price is None:
            return {'ok': False, 'error': 'no price yet'}
        state = load_state()
        if state['position']:
            return {'ok': False, 'error': 'already holding a position'}
        if state['usdt'] <= 0:
            return {'ok': False, 'error': 'no USDT left'}
        usdt = state['usdt']
        btc = (usdt / price) * (1 - FEE_RATE)
        fee = usdt * FEE_RATE
        state['position'] = {
            'bought_at': round(price, 2),
            'btc': btc,
            'target': round(price * SELL_MULT, 2),
            'stop': round(price * STOP_MULT, 2),
            'time': int(time.time()),
        }
        state['usdt'] = 0.0
        state['fees_paid'] += fee
        state['last_event'] = f"BUY {btc:.6f} BTC @ {price:.1f}"
        save_state(state)
        return {'ok': True, 'state': public_state(state)}


def do_sell():
    with LOCK:
        price = LAST_PRICE
        if price is None:
            return {'ok': False, 'error': 'no price yet'}
        state = load_state()
        pos = state['position']
        if not pos:
            return {'ok': False, 'error': 'no open position'}
        proceeds = pos['btc'] * price * (1 - FEE_RATE)
        fee = pos['btc'] * price * FEE_RATE
        pnl = proceeds - (pos['btc'] * pos['bought_at'])
        state['usdt'] = proceeds
        state['btc'] = 0.0
        state['position'] = None
        state['cycles'] += 1
        state['realized_pnl'] += pnl
        state['fees_paid'] += fee
        if pnl >= 0:
            state['wins'] += 1
        else:
            state['losses'] += 1
        state['last_event'] = f"SELL @ {price:.1f}  P&L {pnl:+.2f} USDT"
        save_state(state)
        log_trade({
            'bought_at': pos['bought_at'], 'sold_at': round(price, 2),
            'btc': pos['btc'], 'pnl': round(pnl, 4),
            'target': pos['target'], 'stop': pos['stop'],
            'open_time': pos['time'], 'close_time': int(time.time()),
            'win': pnl >= 0,
        })
        return {'ok': True, 'state': public_state(state), 'pnl': round(pnl, 2)}


def do_reset():
    with LOCK:
        state = default_state()
        state['last_event'] = 'session reset'
        save_state(state)
        return {'ok': True, 'state': public_state(state)}


def recent_trades(n=20):
    try:
        with open(TRADES_FILE) as f:
            recs = json.load(f)
        return recs[-n:][::-1]
    except Exception:
        return []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, path):
        try:
            with open(path, 'rb') as f:
                body = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self._send_json({'ok': False, 'error': str(e)}, 500)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ('/', '/index.html', '/paper.html'):
            self._send_html(HTML_FILE)
        elif path == '/data.json':
            self._send_json(build_payload())
        else:
            self._send_json({'ok': False, 'error': 'not found'}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == '/buy':
                self._send_json(do_buy())
            elif path == '/sell':
                self._send_json(do_sell())
            elif path == '/reset':
                self._send_json(do_reset())
            else:
                self._send_json({'ok': False, 'error': 'not found'}, 404)
        except Exception as e:
            self._send_json({'ok': False, 'error': str(e)}, 500)


def build_payload():
    """Assemble the full data payload (used by /data.json and the public file)."""
    with LOCK:
        candles = list(CANDLES)
        price = LAST_PRICE
        err = LAST_ERROR
    state = load_state()
    return {
        'ts': int(time.time()),
        'price': price,
        'error': err,
        'candles': candles,
        'buy_window': compute_buy_window(candles),
        'trade': public_state(state),
        'recent': recent_trades(),
        'params': {
            'sell_mult': SELL_MULT, 'stop_mult': STOP_MULT,
            'fee_rate': FEE_RATE, 'lull_factor': LULL_FACTOR,
            'lull_window': LULL_WINDOW,
        },
    }


def pub_writer():
    """Write the payload to PUB_DATA_FILE every 5s so the A0 WebUI can serve it
    as a static file (the WebUI port is the only one reachable from outside)."""
    while True:
        try:
            payload = build_payload()
            tmp = PUB_DATA_FILE + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(payload, f)
            os.replace(tmp, PUB_DATA_FILE)
        except Exception:
            print('[pub_writer]', traceback.format_exc(), flush=True)
        time.sleep(5)


def main():
    t = threading.Thread(target=fetch_loop, daemon=True)
    t.start()
    t2 = threading.Thread(target=pub_writer, daemon=True)
    t2.start()
    server = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    print(f'paper_server listening on 0.0.0.0:{PORT}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
