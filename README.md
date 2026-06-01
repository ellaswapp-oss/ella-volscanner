# Vol Dashboard

A full-stack volatility trading dashboard for evaluating options trades using IV, RV, VRP, IV Rank, term structure, skew, macro stress, and regime-based trade signals.

**Tickers:** SPY · QQQ · IWM · AAPL · NVDA · TSLA

---

## Stack

| Layer | Tech |
|-------|------|
| Frontend | Next.js 14, React, TypeScript, Tailwind, Recharts |
| Backend | Python 3.11+, FastAPI, pandas, numpy |
| Data (prototype) | yfinance + synthetic IV |
| Data (production) | Polygon, ORATS, CBOE, FRED, SpotGamma |
| Database | Postgres (schema TBD — add SQLAlchemy models) |

---

## Quick Start

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

API docs: http://localhost:8000/docs

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Dashboard: http://localhost:3000

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/api/vol/dashboard` | Full dashboard payload (all tickers) |
| GET | `/api/vol/tickers` | List supported tickers |
| GET | `/api/vol/ticker/{ticker}` | Single ticker snapshot |
| GET | `/api/vol/macro` | Macro stress indicators |

---

## Calculations

| Metric | Formula |
|--------|---------|
| Realized Vol (N-day) | `std(log(P_t / P_{t-1}), N days) × √252 × 100` |
| IV/RV Ratio | `IV30 / RV30` |
| Vol Risk Premium | `(IV30 - RV30) / RV30` |
| IV Rank | `(current_IV - 1yr_low) / (1yr_high - 1yr_low) × 100` |
| IV/RV Z-Score | `(spread - mean_spread) / std_spread` trailing 252 days |
| Term Structure | `IV60 - IV30` (back slope), `IV30 - IV7` (front slope) |
| Regime Score | Weighted composite (IVR 35%, VRP 25%, ratio 25%, z-score 15%) |

### Trade Signals

| Regime Score | Signal |
|-------------|--------|
| 0–30 | Buy Premium |
| 30–50 | Neutral |
| 50–70 | Sell Premium Selectively |
| 70–100 | Aggressive Premium Selling |

---

## Tests

```bash
cd backend
pytest tests/ -v
```

---

## Dashboard Panels

1. **Market Regime** — composite score gauge for each ticker + market-wide signal
2. **IV vs RV** — sparkline chart + multi-window RV metrics + VRP
3. **Term Structure** — IV7/IV30/IV60 bar charts with contango/inversion flag
4. **Skew** — 25Δ put-call skew per ticker
5. **Macro Stress** — VIX regime, credit spread, yield curve, financial conditions
6. **Trade Recommendations** — ranked ticker table + strategy playbook

---

## Wiring Production Data

Replace functions in `backend/app/data/mock_provider.py`:

| Function | Production replacement |
|----------|----------------------|
| `get_price_history()` | Polygon `/v2/aggs/ticker` |
| `get_iv_data()` | ORATS options chain API |
| `get_macro_data()` | FRED series API (VIX: `VIXCLS`, HY OAS: `BAMLH0A0HYM2`) |

Add Postgres models via SQLAlchemy in `backend/app/models/` and cache fetched data to avoid repeated API calls.
