#!/usr/bin/env python3
"""Real-money broker bridge for the paper chart's BUY/SELL buttons.

Called via subprocess by the paper_trading plugin (the A0 framework runtime
has no ccxt; this script runs under /opt/venv/bin/python which does).

Usage:
    real_broker.py balance   -> {"ok":true,"usdt":...,"btc":...,"price":...}
    real_broker.py buy       -> market buy BTC with ALL free USDT (0.5% buffer)
    real_broker.py sell      -> market sell ALL free BTC above dust

Always prints exactly one JSON object on stdout. Errors -> {"ok":false,"error":...}
REAL ORDERS ONLY. Keys read from the project's .a0proj/secrets.env.
"""
import json
import sys

try:
    import ccxt
except ImportError:
    print(json.dumps({'ok': False, 'error': 'ccxt not available in this runtime'}))
    sys.exit(1)

SYMBOL = 'BTC/USDT'
MIN_ORDER_USDT = 6.0      # Bybit spot min notional is ~5 USDT; keep a margin
DUST_BTC = 0.00001        # below this is untradeable dust
USDT_BUFFER = 0.995       # spend 99.5% of free USDT (price moves between calc and fill)
SECRETS = '/a0/usr/projects/trading/.a0proj/secrets.env'


def fail(msg, code=1):
    print(json.dumps({'ok': False, 'error': str(msg)}))
    sys.exit(code)


def load_secrets(path=SECRETS):
    s = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                s[k.strip()] = v.strip()
    return s


def make_exchange():
    sec = load_secrets()
    return ccxt.bybit({
        'apiKey': sec['BYBIT_API_KEY'],
        'secret': sec['BYBIT_API_SECRET'],
        'enableRateLimit': True,
        'options': {'recvWindow': 20000},
    })


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ('balance', 'buy', 'sell'):
        fail('usage: real_broker.py balance|buy|sell', 2)
    action = sys.argv[1]

    try:
        ex = make_exchange()
        bal = ex.fetch_balance()
        usdt = float(bal['free'].get('USDT') or 0)
        btc = float(bal['free'].get('BTC') or 0)
        price = float(ex.fetch_ticker(SYMBOL)['last'])
    except Exception as e:
        fail(f'exchange read failed: {e}')

    if action == 'balance':
        print(json.dumps({'ok': True, 'usdt': usdt, 'btc': btc, 'price': price}))
        return

    if action == 'buy':
        if usdt < MIN_ORDER_USDT:
            fail(f'only {usdt:.2f} USDT free — below minimum order {MIN_ORDER_USDT:.0f} USDT')
        spend = usdt * USDT_BUFFER
        amount = spend / price
        try:
            amount = float(ex.amount_to_precision(SYMBOL, amount))
        except Exception:
            amount = round(amount, 6)
        if amount * price < MIN_ORDER_USDT:
            fail(f'computed order too small after precision ({amount} BTC)')
        try:
            order = ex.create_market_buy_order(SYMBOL, amount)
        except Exception as e:
            fail(f'buy order rejected: {e}')
        filled = float(order.get('filled') or amount)
        avg = float(order.get('average') or order.get('price') or price)
        cost = float(order.get('cost') or filled * avg)
        print(json.dumps({'ok': True, 'side': 'buy', 'bought_at': avg,
                          'btc': filled, 'cost': cost, 'order_id': order.get('id')}))
        return

    # sell
    if btc < DUST_BTC:
        fail('no BTC to sell (only dust)')
    try:
        amount = float(ex.amount_to_precision(SYMBOL, btc))
    except Exception:
        amount = round(btc, 6)
    if amount < DUST_BTC or amount * price < MIN_ORDER_USDT:
        fail(f'free BTC {btc:.8f} too small to sell')
    try:
        order = ex.create_market_sell_order(SYMBOL, amount)
    except Exception as e:
        fail(f'sell order rejected: {e}')
    filled = float(order.get('filled') or amount)
    avg = float(order.get('average') or order.get('price') or price)
    proceeds = float(order.get('cost') or filled * avg)
    print(json.dumps({'ok': True, 'side': 'sell', 'sold_at': avg,
                      'btc': filled, 'proceeds': proceeds, 'order_id': order.get('id')}))


if __name__ == '__main__':
    main()
