# 🤖 Crypto Scalper: RSI-Adaptive Volatility Scalper with Intelligent Stop-Loss

A production-grade BTC/USDT scalping bot for Bybit that automatically adapts to market conditions using RSI, ADX, and EMA trend filters. Built with **direction-aware gates** — profits during uptrends, protects during downtrends.

---

## 🎯 How It Works (Layman's Terms)

Think of your bot as a **smart fisherman** watching Bitcoin 24/7:

### The Brain (`candles_bot.py`)
Every 60 seconds it connects to Bybit, checks the price and momentum, and decides: **Buy, Sell, or Do Nothing.**

### The Thermometer (RSI)
Measures market temperature from 0 (freezing panic) to 100 (boiling greed):
- **RSI < 30** → People panicking, BTC is cheap → 🟢 **BUY**
- **RSI > 70** → People being greedy, BTC is expensive → 🔴 **SELL**

### The Bouncers (Gates)

**ADX Gate** — measures how strongly the market is moving:
| Value | Meaning | Bot Action |
|-------|---------|------------|
| < 20 | Calm, ranging market | ✅ Let the bot trade |
| > 20 | Strong trend happening | ⚠️ Warn only (see EMA for direction) |

**EMA Gate** — checks which way the wind is blowing (4-hour trend):
| Condition | Meaning | Bot Action |
|-----------|---------|------------|
| Price **>** EMA | Market going up (healthy trend) | ✅ **ALLOW trades** — profit from rising prices |
| Price **<** EMA | Market going down (bear trend) | ❌ **BLOCK trades** — protect capital until recovery |

### The Key Fix: Direction-Aware Gates (v2.3.0)

**Old logic (broken):** ADX high = block ALL trading. This meant the bot sat out during strong uptrends too, missing gains.

**New logic (fixed):**
- **Strong uptrend** (ADX high, Price > EMA) → ✅ **ALLOWED** — take advantage of rising markets
- **Strong downtrend** (ADX high, Price < EMA) → ❌ **BLOCKED** — protect capital
- **Ranging market** (ADX low) → ✅ **ALLOWED** — normal RSI strategy

This matches your intuition: *"if the trend is upward we should take advantage of it. when the trend is down the bot should stop trading until things improve."*

### The Pipeline (How It All Connects)

```
Bybit Exchange (real BTC price)
       ↓
candles_bot.py (brain — decides buy/sell every 60s)
       ↓ writes to
bot_output.log (diary) + bot_state.json (memory)
       ↓ read every 5s by
data_writer.py (messenger — extracts numbers)
       ↓ writes to
data.json (clean data file)
       ↓ served by
HTTP server (port 80, external via Docker 5080)
       ↓ fetched every 5s by
trading.html (your browser dashboard)
```

---

## 🛡️ Protection Features

| Feature | What It Does | Status |
|---------|-------------|--------|
| **RSI 30/70** | Buy panic, sell greed | ✅ Active |
| **0.75% Target Margin** | Lock in profit on each trade | ✅ Active |
| **1% Hard Stop-Loss** | Never lose more than 1% per trade | ✅ Active |
| **EMA Direction Gate** | Block trading in downtrends | ✅ Active |
| **ADX Awareness Gate** | Warns of strong trends (uptrends allowed) | ✅ Active |
| **Circuit Breaker** | 3 losses = auto-pause 24 hours | ✅ Active |
| **Max 4 Cycles/Day** | Prevents overtrading | ✅ Active |
| **+1% Daily Profit Target** | Lock in gains for the day | ✅ Active |
| **Test Mode ($100 Cap)** | Safe observation mode | ✅ Active |
| **Dust Filter** | Ignore residual BTC < 0.0002 | ✅ Active |

---

## 📁 File Structure

```text
crypto_scalper/
├── plugin.yaml              # Manifest (v2.3.0)
├── README.md                # This file
├── LICENSE                  # MIT
├── tools/
│   ├── candles_bot.py       # Main trading logic
│   └── data_writer.py       # Dashboard data pipeline
└── webui/
    └── public/
        └── trading.html     # Live dashboard with market readiness badge
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
USE_ADX = True                   # ADX awareness (warns of trends, doesn't block uptrends)
USE_EMA = True                   # Block bear trends (price < EMA)
MAX_CYCLES = 4                   # Max trades per day
DAILY_PROFIT_TARGET = 1.0        # Stop after +1% daily profit
```

---

## 📊 Dashboard

The live dashboard at `/public/trading.html` shows:
- **Market readiness badge** — 🟢 READY / 🟡 SOON / 🔴 WAIT
- **Status bar** — Price, USDT, BTC, RSI, Day P&L
- **Market status** — ✓ READY / ⚠ ALMOST / ✗ NOT READY
- **Bot status** — ON or OFF
- **Price chart** — Candlesticks with BUY/SELL target lines
- **RSI chart** — RSI oscillator with oversold/overbought lines

---

## 🏆 Performance Log

| Phase | Result |
|-------|--------|
| Initial test (May 2026) | +35.9% simulated gain in ranging market |
| Live cycles (May 29) | 7 profitable cycles |
| Bear trend (June 1-2) | −48.25 USDT realized (pre-gate, pure RSI mode) |
| Post-gate restart (v2.2.0) | Gates prevented further losses during 20%+ drop |
| Direction-aware fix (v2.3.0) | ADX no longer blocks uptrends — profit in rising markets |

---

## 🇬🇧 UK Regulatory Workaround

For UK users restricted by Bybit deposit rules, use this flow:

1. **GBP → Kraken** (deposit via bank)
2. **GBP → USDT** (Kraken spot)
3. **USDT → Bybit** (send via **TRC-20** network, ~$1 fee)

Bybit's VIP fee structure (maker 0.02%, taker 0.055%) makes this cost-effective.

---

## ⚠️ Disclaimer

This is an educational tool. Crypto trading carries significant risk. Never trade with funds you cannot afford to lose. Past performance does not guarantee future results.

---

## License

MIT License
