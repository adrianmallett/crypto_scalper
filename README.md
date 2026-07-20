# 🤖 Crypto Scalper: Pure RSI Recovery Confirmation for Bybit BTC/USDT

A production-grade BTC/USDT scalping bot that buys **confirmed bounces**, not falling knives — and never sells below fee-aware break-even. Built and battle-tested through months of live iterations.

---

## 🎯 The Strategy (v3.0.0)

### Buy: RSI Recovery Confirmation
Old RSI bots buy the moment RSI drops below 30 — often catching a **falling knife** mid-downtrend. This bot waits for proof the dip has bottomed:

| Step | RSI | Action |
|------|-----|--------|
| 1 | Drops below 30 | ⏳ Wait — could be a falling knife |
| 2 | **Crosses back above 30** | 🟢 **BUY** — bounce confirmed |

One line of logic, but it filters out most downtrend traps. You buy slightly above the exact bottom — in exchange for far fewer stop-losses.

### Sell: Fee-Aware Break-Even
Every sell must clear the **fee-aware floor** (buy × 1.006 = 0.6% gross, ~0.4% net after the 0.2% round-trip taker fee). When RSI hits 70+:

| Condition | Action |
|-----------|--------|
| Price ≥ buy × 1.006 | 🔴 **SELL** — ~0.4% net profit after fees |
| Price < buy × 1.006 | ⏳ **HOLD** — wait for the fee-aware floor |

No more "profitable" trades that quietly lose money to fees.

### Protection Layers

| Layer | Behavior |
|-------|----------|
| **Stop-loss (1%)** | Emergency exit if price falls 1% below buy |
| **Stop-loss cooldown (5 min)** | Blocks instant re-buys into a still-falling market |
| **Circuit breaker** | 3 consecutive stop-losses → 24h pause |
| **Buy buffer (1%)** | Reserves 1% of USDT for fees/slippage |
| **Dust filter** | Ignores sub-0.0002 BTC balances |

### Sell Target
Fixed margin: sell at buy × **1.0075** (0.75% gross, ~0.55% net after fees).

---

## 🧪 Start in Test Mode (Default)

The published version ships **safe by default**:

```python
TEST_MODE     = True   # Simulated capital — no real risk
TEST_CAPITAL  = 100    # Start small; raise only after consistent profits
```

Scaling ladder (proven in live use): $100 → $250 → $500 → 50% of account → full capital. Move up only after **2+ consecutive positive days**.

---

## 📁 Files

| File | Purpose |
|------|---------|
| `tools/candles_bot.py` | Core bot — exchange connectivity, RSI logic, trade execution, state persistence |
| `tools/data_writer.py` | Feeder — parses bot log, fetches candles, writes `data.json` every 5s (atomic rename) |
| `tools/dash_v5.html` | Live dashboard — candlesticks, volume, RSI, buy/sell/stop-loss price lines, console |

---

## 🚀 Quick Start

```bash
# 1. Install deps
pip install ccxt pandas

# 2. Set Bybit API keys (read + trade; never commit these)
export BYBIT_API_KEY="your_key"
export BYBIT_API_SECRET="your_secret"

# 3. Run (test mode by default)
nohup python -u candles_bot.py >> bot_output.log 2>&1 &
nohup python -u data_writer.py >> data_writer.log 2>&1 &

# 4. Serve the dashboard
python -m http.server 5080
# Open http://localhost:5080/dash_v5.html
```

---

## 📜 Version History

| Version | Change |
|---------|--------|
| **3.0.1** | Fee-aware sell floor raised to buy × 1.006 (~0.4% net per win) — fixes inverted risk/reward where 5 wins were needed per 1% stop-loss |
| 3.0.0 | Pure RSI recovery confirmation, fee-aware break-even, stop-loss cooldown, circuit breaker, buy parsing fix |
| 2.3.0 | Direction-aware ADX/EMA gates (superseded — pure RSI proved more reliable) |
| 2.2.0 | ADX + EMA trend gates, bear market protection |
| 2.1.0 | Fee fixes, quoteCoin order mode |
| 2.0.0 | Pure RSI 30/70 strategy |

---

## ⚠️ Disclaimer

Educational software. Crypto trading involves risk of loss. Start in test mode, understand the code, never trade funds you cannot afford to lose. No warranty. MIT License.
