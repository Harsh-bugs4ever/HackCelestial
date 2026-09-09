<p align="center">
  <img src="https://img.shields.io/badge/HackCelestial_3.0-PS_4-blue?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Team-VOID-purple?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Next.js-15-black?style=for-the-badge&logo=next.js" />
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?style=for-the-badge&logo=fastapi" />
  <img src="https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white" />
</p>

# 🏨 Smart Resort 360

> **One data spine · Four AI engines · An action layer on top.**

Smart Resort 360 is an AI-powered resort operations platform that unifies demand forecasting, predictive maintenance, workforce optimization, and guest intelligence into a single decision layer. Every insight is surfaced as an **approvable action card** — AI recommends, the manager decides, the system executes, and the feedback loop makes the next batch sharper.

Built for **HackCelestial 3.0 — Problem Statement 4** by **Team VOID**.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        FRONTEND  (Next.js 15)                       │
│  Dashboard · Action Bus · Revenue · Maintenance · Workforce · Guest │
│  Simulator · Feedback Loop · 3D Resort Model (Three.js)             │
└────────────────────────────┬────────────────────────────────────────┘
                             │  REST + WebSocket
┌────────────────────────────▼────────────────────────────────────────┐
│                        BACKEND  (FastAPI)                           │
│                                                                     │
│  ┌──────────┐ ┌──────────────┐ ┌──────────┐ ┌───────────────────┐  │
│  │ Demand   │ │ Maintenance  │ │Workforce │ │Guest Intelligence │  │
│  │ Engine   │ │ Engine       │ │ Engine   │ │     Engine        │  │
│  │          │ │              │ │          │ │                   │  │
│  │ Prophet  │ │ Isolation    │ │ OR-Tools │ │ Sentence-BERT     │  │
│  │ XGBoost  │ │ Forest       │ │ CP-SAT   │ │ FAISS             │  │
│  │ LightGBM │ │ Survival     │ │ Prophet  │ │ Claude LLM        │  │
│  └────┬─────┘ └──────┬───────┘ └────┬─────┘ └────────┬──────────┘  │
│       │              │              │                │              │
│  ┌────▼──────────────▼──────────────▼────────────────▼──────────┐  │
│  │              ACTION BUS  (Layer 3)                            │  │
│  │   Cards ranked by confidence × impact × urgency              │  │
│  │   Manager: Approve / Snooze / Dismiss                        │  │
│  └──────────────────────────┬───────────────────────────────────┘  │
│                             │                                      │
│  ┌──────────────────────────▼───────────────────────────────────┐  │
│  │              FEEDBACK LOOP  (Layer 4)                         │  │
│  │   Decisions scored against outcomes → confidence adjusted     │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │              DATA SPINE  (Layer 1 — SQLAlchemy)               │  │
│  │   SQLite (dev) / PostgreSQL+TimescaleDB (prod)                │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## ✨ Features

| Module | What it does |
|---|---|
| **Live Dashboard** | Real-time occupancy, revenue, sentiment, equipment health, staffing gaps — all from one spine |
| **3D Resort Model** | Interactive Three.js scene — lit windows = rooms sold, floating markers = asset risk |
| **Action Bus** | AI-generated action cards ranked by impact. Approve / Snooze / Dismiss with one click |
| **Demand & Revenue** | Prophet + XGBoost forecast with rate recommendations inside floor/ceiling guardrails |
| **Predictive Maintenance** | Isolation Forest anomaly detection + survival analysis on IoT telemetry |
| **Workforce Optimizer** | OR-Tools CP-SAT solver assigns people to shifts under labour + demand constraints |
| **Guest Intelligence** | Sentence-BERT embeddings + FAISS for guest DNA, plus an AI concierge |
| **Revenue Simulator** | Drag price/staffing/promotion levers and see projected occupancy & profit before committing |
| **Feedback Loop** | Every decision is a training signal — acceptance rates and outcome accuracy adjust future confidence |
| **WebSocket Live Updates** | New action cards push to the dashboard in real time without polling |

---

## 🛠️ Tech Stack

### Frontend
| Technology | Purpose |
|---|---|
| **Next.js 15** | React framework with SSR |
| **React 19** | UI library |
| **Three.js / React Three Fiber** | 3D resort visualisation |
| **Recharts** | Charts and data visualisation |
| **Motion (Framer Motion)** | Animations and micro-interactions |
| **Tailwind CSS 3** | Utility-first styling |

### Backend
| Technology | Purpose |
|---|---|
| **FastAPI** | Async Python API framework |
| **SQLAlchemy 2** | ORM + data spine |
| **SQLite / PostgreSQL** | Database (zero-setup SQLite in dev) |
| **Prophet** | Time-series demand forecasting |
| **XGBoost / LightGBM** | Residual learning for demand |
| **scikit-learn** | Isolation Forest for anomaly detection |
| **OR-Tools** | CP-SAT constraint solver for workforce |
| **Sentence Transformers** | Guest DNA embeddings |
| **FAISS** | Vector similarity search |
| **uvicorn** | ASGI server with WebSocket support |

---

## 📁 Project Structure

```
HackCelestial/
├── frontend/                    # Next.js 15 app
│   ├── app/
│   │   ├── page.tsx             # Dashboard (home)
│   │   ├── actions/             # Action Bus page
│   │   ├── revenue/             # Demand & Revenue engine
│   │   ├── assets/              # Predictive Maintenance
│   │   ├── workforce/           # Workforce Optimizer
│   │   ├── guests/              # Guest Intelligence + Concierge
│   │   ├── simulator/           # Revenue-impact simulator
│   │   ├── learning/            # Feedback Loop
│   │   ├── layout.tsx           # Root layout + nav
│   │   └── globals.css          # Design system tokens + styles
│   ├── components/
│   │   ├── ResortHero.tsx       # Hero section with 3D model
│   │   ├── ResortScene.tsx      # Three.js resort scene
│   │   ├── ActionCardView.tsx   # Action card component
│   │   ├── charts.tsx           # Recharts wrappers
│   │   ├── motion.tsx           # Animation utilities
│   │   └── ui.tsx               # StatTile, Section, StatusPill, etc.
│   ├── lib/
│   │   ├── api.ts               # API client + TypeScript types
│   │   ├── format.ts            # Number formatting (INR, %, etc.)
│   │   └── useLiveData.ts       # SWR-like hook with stale-while-revalidate
│   └── package.json
│
├── backend/                     # FastAPI Python backend
│   ├── app/
│   │   ├── main.py              # FastAPI app + WebSocket hub
│   │   ├── models.py            # SQLAlchemy models (data spine)
│   │   ├── api/
│   │   │   └── routes.py        # All REST endpoints
│   │   ├── core/
│   │   │   ├── config.py        # Pydantic Settings
│   │   │   └── db.py            # Engine + session factory
│   │   ├── engines/
│   │   │   ├── demand.py        # Prophet + XGBoost forecasting
│   │   │   ├── maintenance.py   # Isolation Forest + survival
│   │   │   ├── workforce.py     # OR-Tools CP-SAT optimiser
│   │   │   ├── guest.py         # Sentence-BERT + FAISS + LLM
│   │   │   ├── orchestrator.py  # Runs all engines + cross-domain
│   │   │   └── bus.py           # Action card creation + ranking
│   │   └── seed/
│   │       └── generate.py      # 2-year realistic data generator
│   ├── scripts/
│   │   └── smoke.py             # End-to-end smoke test
│   ├── requirements.txt         # Light runtime set (Render free tier)
│   ├── requirements-ml.txt      # + heavy ML stack (local / paid instance)
│   ├── Procfile                 # Render start command
│   └── runtime.txt              # Python version for Render
│
├── netlify.toml                 # Netlify deployment config
├── render.yaml                  # Render blueprint (free tier)
├── .env.example                 # Environment variable template
└── .gitignore
```

---

## 🚀 Local Development

### Prerequisites

- **Node.js 18+** and **npm**
- **Python 3.11+**

### 1. Clone & setup environment

```bash
git clone https://github.com/YOUR_USERNAME/HackCelestial.git
cd HackCelestial
cp .env.example .env
```

### 2. Backend

```bash
cd backend

# Create virtual environment
python -m venv venv
# Windows
.\venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

# Install dependencies (full ML stack: Prophet, XGBoost, OR-Tools, FAISS, MiniLM)
pip install -r requirements-ml.txt

# ...or just the light runtime set, if you don't need the ML engines.
# The engines then log a warning and use their fallbacks - see Deployment.
# pip install -r requirements.txt

# Seed the database (generates 2 years of realistic resort data)
python -m app.seed.generate

# Start the API server
uvicorn app.main:app --reload --port 8000
```

The backend runs at `http://localhost:8000`. Health check: `http://localhost:8000/health`

### 3. Frontend

```bash
cd frontend

# Install dependencies
npm install

# Start dev server
npm run dev
```

The frontend runs at `http://localhost:3000`.

### 4. Verify

1. Open `http://localhost:3000` — the dashboard should load with live data
2. Click **"Run all engines now"** to generate AI action cards
3. Approve/Dismiss/Snooze cards to feed the feedback loop
4. Visit the **Feedback Loop** page and click **"Score outcomes now"**

---

## ☁️ Deployment

### Backend → Render (free tier)

The repo ships `render.yaml`, so the fastest path is a Blueprint:

1. Push the repo to GitHub
2. Go to [render.com](https://render.com) → **New** → **Blueprint**
3. Connect the repo — Render reads `render.yaml` and pre-fills everything
4. Set `ANTHROPIC_API_KEY` when prompted (optional — unset means the extractive fallback)
5. Deploy — note your service URL (e.g. `https://smart-resort-360-api.onrender.com`)

To wire it up by hand instead, use **New** → **Web Service** with:

- **Root Directory**: `backend`
- **Runtime**: Python 3
- **Build Command**: `pip install -r requirements.txt && python -m app.seed.generate`
- **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`
- **Health Check Path**: `/health`

#### Why `requirements.txt` is the light set

`requirements.txt` holds only what the API needs to boot (FastAPI, SQLAlchemy,
pandas, numpy, scikit-learn) and installs from wheels in ~2 minutes. The heavy ML
stack lives in **`requirements-ml.txt`** — `sentence-transformers` alone pulls
PyTorch (~2.5GB), which will not build or fit in the free tier's 512MB.

Every one of those imports is lazy and `try`/`except`-guarded, so the deployed API
is fully functional without them; the affected engines log a warning and degrade:

| Missing package | Engine | Falls back to |
|---|---|---|
| `prophet` | demand | seasonal-naive baseline |
| `xgboost` | demand | prophet/naive leg only |
| `lightgbm` | maintenance | trend + anomaly rules |
| `ortools` | workforce | greedy scheduler |
| `faiss-cpu` | guest | numpy cosine search |
| `sentence-transformers` | guest | hashed bag-of-words |

For the full stack locally (or on a paid instance with ≥2GB RAM):

```bash
pip install -r requirements-ml.txt
```

> **Note**: Render's free tier uses ephemeral disk. The build command seeds SQLite
> into the instance image, so the demo data is present on boot and resets on each
> deploy. Free instances also sleep after ~15 min idle — the first request back
> takes ~50s to wake. For persistent data, add Render's managed PostgreSQL and set
> `DATABASE_URL`.

### Frontend → Netlify

The root `netlify.toml` already sets the base, publish dir, and Next.js plugin.

1. Go to [netlify.com](https://netlify.com) → **Add new site** → **Import from Git**
2. Connect the repo — `netlify.toml` supplies the build settings
3. Add the environment variable **before** the first build:
   - `NEXT_PUBLIC_API_BASE` = your Render URL (e.g. `https://smart-resort-360-api.onrender.com`)
4. Deploy

> **Important**: `NEXT_PUBLIC_API_BASE` is inlined at build time, not read at
> runtime. If you change it, trigger a fresh deploy — a redeploy of the existing
> build will keep the old value. The same value drives the WebSocket URL
> (`https` → `wss`), and the backend's CORS rule already allows `*.netlify.app`.

---

## 🔐 Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | No | SQLite file | PostgreSQL connection string. Leave unset for SQLite (zero setup) |
| `TIMESCALE_ENABLED` | No | `false` | Set `true` when using TimescaleDB for hypertables |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Celery broker + cache. Unreachable → Celery runs eager |
| `ANTHROPIC_API_KEY` | No | _(unset)_ | For Claude-powered concierge + review summarisation. Unset → extractive fallback |
| `LLM_MODEL` | No | `claude-opus-5` | Which Claude model to use |
| `FORECAST_HORIZON_DAYS` | No | `30` | How many days ahead the demand engine forecasts |
| `NEXT_PUBLIC_API_BASE` | **Frontend only** | `http://127.0.0.1:8000` | Backend API URL — set this on Netlify |

---

## 🧪 Smoke Test

After seeding, run the end-to-end smoke test to verify all layers:

```bash
cd backend
python -m scripts.smoke
```

This exercises: data spine → all 4 engines → action bus → approve → execute → outcome scoring.

---

## 🎯 Problem Statement Mapping

| PS 4 Requirement | Our Implementation |
|---|---|
| Unified data spine | SQLAlchemy models across PMS, POS, IoT, HR, reviews (Layer 1) |
| AI/ML engines | Prophet, XGBoost, Isolation Forest, OR-Tools CP-SAT, Sentence-BERT (Layer 2) |
| Actionable recommendations | Action Bus with confidence-ranked cards (Layer 3) |
| Human-in-the-loop | Approve / Snooze / Dismiss workflow per card |
| Feedback & learning | Outcome scoring → confidence adjustment loop (Layer 4) |
| Cross-domain intelligence | Guest sentiment lifts workforce staffing; demand drives maintenance windows |
| Real-time operations | WebSocket live updates, 20s polling, stale-while-revalidate |
| Revenue optimization | Rate cards inside floor/ceiling guardrails + what-if simulator |

---

## 👥 Team VOID

Built with ❤️ for HackCelestial 3.0

---

## 📄 License

This project was built for the HackCelestial 3.0 hackathon. All rights reserved by Team VOID.
