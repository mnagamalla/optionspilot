# OptionsPilot 🛫

A full-stack personal options trading platform for wheel strategy traders. Scan for CSPs and covered calls, track your Robinhood journal, detect unusual flow, and ask AI questions about your positions — all in one Bloomberg-inspired dark-mode UI.

![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)

---

## Features

### 📊 CSP Scanner
Scan any ticker universe for cash-secured put candidates using live options chain data.
- Filter by collateral budget, DTE window, safety preference (Conservative / Balanced / Aggressive)
- Results grouped by ticker, sorted by annualized yield
- Table and Cards view
- Earnings calendar inline — color-coded warnings when earnings fall within your DTE window
- Position sizing calculator — enter strike + premium to see max contracts, capital used, break-even, ROI

### 📈 Covered Call Scanner
Scan stocks you own for OTM covered call opportunities.
- Same filters and views as the CSP scanner
- Earnings calendar and position sizer included

### 🌊 Unusual Flow Scanner
Detect institutional activity across your watchlist and the broader market.
- Volume spike detection (Vol/OI ratio: 1.5x / 3x / 5x thresholds)
- Block trade detection ($25k / $50k / $100k minimum notional)
- Bullish/bearish sentiment per ticker with signal badges
- Sortable columns — click any header
- Results grouped by ticker with per-group notional and sentiment summary

### 📒 Journal (Robinhood Integration)
Full trade history synced directly from your Robinhood account.

**Dashboard**
- P&L cards: All Time / YTD / This Month / This Week
- Stacked bar chart of monthly premium by option type
- Cumulative P&L line chart
- Per-ticker drill-down — click any card to see individual trades
- Click a ticker row to expand live mark-to-market pricing with unrealized P&L
- Win rate stats by trade type

**Trade Log**
- Full trade history with roll detection badges
- Inline roll labels on buy-to-close + sell-to-open pairs

**Rolls Tab**
- Auto-detected roll pairs grouped with net premium
- Credit vs debit roll summary

**Cost Basis Tracker**
- Per-lot adjusted basis after CC premiums collected
- Buffer % alerts (🔴 <2%, 🟡 <5%, 🟢 safe)
- Live mark-to-market prices

**Wheel Cycles**
- Auto-detected CSP → Assignment → CC progression
- Full cycle P&L tracking

**Account Value**
- Live market value of stock positions (excludes EBAY, PYPL)
- Open options mark-to-market (spread pairs netted)
- Persistent header tile — visible throughout the Journal

**Utilities**
- CSV export: trades, monthly P&L, cost basis lots
- Account renaming (inline edit)
- Assignment detector with one-click lot creation

### 🤖 Ask MyTradingBot
AI-powered Q&A about your portfolio, powered by Claude (Anthropic API).
- Your live journal data injected automatically into every question
- Suggested follow-up questions after each answer
- Ask anything: *"Which positions should I write a CC on this week?"* / *"What's my YTD premium collected on PLTR?"*

---

## Project Structure

```
mytradingbot/
├── backend/
│   ├── main.py                  ← FastAPI entry point
│   ├── db/
│   │   ├── database.py          ← SQLAlchemy setup
│   │   ├── models.py            ← ORM models
│   │   └── journal_repo.py      ← All DB query functions
│   ├── models/
│   │   ├── scan.py              ← Pydantic scan models
│   │   └── journal.py           ← Pydantic journal models
│   ├── routers/
│   │   ├── csp.py               ← POST /scan/csp
│   │   ├── covered_calls.py     ← POST /scan/cc
│   │   ├── journal.py           ← /journal/* (30+ endpoints)
│   │   ├── unusual_flow.py      ← GET /flow/scan
│   │   └── ask.py               ← POST /ask/
│   └── services/
│       ├── scanner.py           ← CSP/CC scan (parallel ThreadPoolExecutor)
│       ├── flow_scanner.py      ← Unusual flow detection
│       └── robinhood.py         ← robin_stocks wrapper
├── frontend/
│   └── index.html               ← Complete single-file frontend (~112KB)
├── tests/
│   ├── test_api.py
│   └── test_scanner.py
├── .env.example                 ← Copy to .env and fill in credentials
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## Setup

### Prerequisites
- Python 3.9+
- Robinhood account (for Journal sync)
- Anthropic API key (for Ask tab) — get one at https://console.anthropic.com

### 1. Clone and create virtual environment

```bash
git clone https://github.com/yourusername/optionspilot.git
cd optionspilot
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
RH_USERNAME=your_robinhood_email@example.com
RH_PASSWORD=your_robinhood_password
DB_PATH=trading_bot.db
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

### 4. Start the server

```bash
uvicorn backend.main:app --reload --port 8000
```

Open **http://localhost:8000** in your browser.

API docs available at: **http://localhost:8000/docs**

---

## First Use — Syncing Robinhood

1. Go to the **Journal** tab
2. Click **⟳ Sync Robinhood**
3. Enter your MFA code when prompted (first time only — device token saved after)
4. All your trades, positions, and P&L populate automatically

> **Note:** Positions transferred from other brokerages won't appear via API. Add those manually using SQL inserts into the `stock_lots` table.

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/scan/csp` | Cash-secured put scan |
| POST | `/scan/cc` | Covered call scan |
| GET | `/flow/scan` | Unusual options flow |
| GET | `/flow/tickers` | Default ticker universes |
| GET | `/journal/accounts` | List accounts |
| PATCH | `/journal/accounts/{id}` | Rename account |
| POST | `/journal/sync` | Sync from Robinhood |
| GET | `/journal/trades` | Trade log |
| GET | `/journal/pnl/summary` | P&L summary |
| GET | `/journal/pnl/monthly` | Monthly breakdown |
| GET | `/journal/pnl/drilldown` | Per-ticker drill-down |
| GET | `/journal/winrate` | Win rate stats |
| GET | `/journal/lots` | Cost basis lots |
| GET | `/journal/cycles` | Wheel cycles |
| GET | `/journal/rolls` | Roll pairs |
| GET | `/journal/earnings` | Upcoming earnings |
| GET | `/journal/account-value` | Live account value |
| GET | `/journal/positions` | Option MTM detail |
| GET | `/journal/assignments/pending` | Unprocessed assignments |
| POST | `/journal/assignments/confirm` | Create lots from assignments |
| GET | `/journal/export/trades` | Download trades CSV |
| GET | `/journal/export/monthly` | Download monthly P&L CSV |
| GET | `/journal/export/lots` | Download cost basis CSV |
| POST | `/ask/` | AI chat about positions |
| GET | `/health` | Health check |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI, Python 3.9+ |
| Database | SQLite via SQLAlchemy |
| Market Data | yfinance |
| Brokerage | robin_stocks |
| AI | Anthropic Claude API |
| Frontend | Vanilla JS + HTML/CSS |
| Charts | Chart.js |

---

## Roadmap

- [ ] Scheduled auto-sync (daily at market open)
- [ ] PostgreSQL support for cloud deployment
- [ ] Rate limiting on scan endpoints
- [ ] Mobile-responsive layout
- [ ] Manual lot entry UI (for RSU/transferred positions)
- [ ] Put wall visualization on Unusual Flow page
- [ ] 0 DTE filter toggle and signal confidence scoring

---

## License

Apache 2.0 License — see [LICENSE](LICENSE) for details.

---

## Disclaimer

This tool is for personal use and educational purposes only. It is not financial advice. Options trading involves significant risk of loss. Always conduct your own research and consult a licensed financial advisor before making investment decisions.