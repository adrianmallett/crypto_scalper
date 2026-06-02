# 🤖 Crypto Scalper: RSI-Adaptive Volatility Scalper with Intelligent Stop-Loss

A production-grade BTC/USDT scalping bot for Bybit that automatically adapts to market conditions using RSI, ADX, and EMA trend filters.

---

## 🎯 Core Strategy (v2.2.0)

| Gate | Function | Threshold |
|------|----------|-----------|
| **RSI Entry** | Oversold dip detection | RSI ≤ 30 |
| **RSI Exit** | Overbought take-profit | RSI ≥ 70 + 0.75% margin |
| **Stop-Loss** | Capital preservation | −1.00% hard stop |
| **ADX Filter** | Blocks trending markets | ADX < 25 (ranging only) |
| **EMA Filter** | Blocks bear trends | Price > 4H EMA × 0.995 |

---

## 🛡️ Bear Market Protection (NEW in v2.2.0)

The bot now includes **two trend-adaptive gates** that automatically pause trading when conditions are unfavorable:

| Gate | Condition | Effect |
|------|-----------|--------|
| **ADX Gate** | ADX > 25 | Pauses bot during volatile/trending moves |
| **EMA Gate** | Price < 4H EMA | Pauses bot during major downtrends |

These gates prevented further losses during the June 2026 bear move (price dropped 20% below the 4H EMA), while competing bots would have continued catching falling knives.

---

## 🧠 Key Features

- **Hybrid RSI-Buy + Fixed-Margin-Sell** — buys pure RSI dips, sells at fixed profit targets
- **`quoteOrderQty` Fee Fix** — bypasses Bybit fee reserve issues
- **Dynamic Rally Re-Anchor** — auto-adjusts buy target during bull rallies
- **Dust Filter** — ignores residual BTC < 0.0002
- **TEST_MODE** — cap capital per trade ($100 default for observation)
- **Day Rollover Tracking** — persists cycles and P&L across UTC days
- **Live Dashboard** — real-time candlestick chart with buy/sell target lines

---

## 🇬🇧 UK Regulatory Workaround

For UK users restricted by Bybit deposit rules, use this flow:

1. **GBP → Kraken** (deposit via bank)
2. **GBP → USDT** (Kraken spot)
3. **USDT → Bybit** (send via **TRC-20** network, ~$1 fee)

Bybit's VIP fee structure (maker 0.02%, taker 0.055%) makes this cost-effective.

---

## 📁 File Structure

```text
crypto_scalper/
├── plugin.yaml              # Manifest (v2.2.0)
├── README.md                # This file
├── LICENSE                  # MIT
├── tools/
│   ├── candles_bot.py       # Main trading logic
│   └── data_writer.py       # Dashboard data pipeline
└── webui/
    └── public/
        └── trading.html     # Live dashboard
```

---

## 🚀 Installation

Clone into your Agent Zero plugin directory:
```bash
cd /a0/usr/plugins/
git clone https://github.com/adrianmallett/crypto_scalper.git
```

Then enable via Agent Zero plugin manager.

---

## ⚙️ Configuration

Key settings in `tools/candles_bot.py`:

```python
TEST_MODE = True                 # False for live trading
TEST_CAPITAL = 100.0             # Max USDT per trade in test mode
RSI_OVERSOLD = 30                # Buy when RSI drops below this
RSI_OVERBOUGHT = 70              # Sell when RSI rises above this
SELL_MARGIN = 1.0075             # 0.75% profit margin target
STOP_LOSS_PCT = 0.01             # 1.0% hard stop-loss
USE_ADX = True                   # Block volatile trends (ADX > 25)
USE_EMA = True                   # Block bear trends (price < EMA)
```

---

## 🏆 Performance Log

| Phase | Result |
|-------|--------|
| Initial test (May 2026) | +35.9% simulated gain |
| Live cycles (May 29) | 7 profitable cycles in ranging market |
| Bear trend (June 1-2) | Gates paused trading, capital preserved |
| Post-gate restart | Zero further losses despite 20%+ market drop |

---

## ⚠️ Disclaimer

This is an educational tool. Crypto trading carries significant risk. Never trade with funds you cannot afford to lose. Past performance does not guarantee future results.

---

## License

MIT License
