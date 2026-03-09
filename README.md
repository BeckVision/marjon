# marjon

Crypto quantitative research platform. Collects market and on-chain data, stores it in a structured warehouse with point-in-time semantics, and provides a data service for analysis and strategy development. Universe-agnostic by design.

## Stack

- **Backend:** Django + PostgreSQL
- **Task queue:** Celery + Redis (Phase 2+)

## Roadmap

| Phase | What it delivers |
|-------|-----------------|
| **1** | Django models + empty warehouse tables |
| **2** | FL-001 pipeline — OHLCV data flowing in |
| **3** | FL-002 pipeline — holder snapshot data flowing in |
| **4** | Data service with point-in-time enforcement |
| **5** | Derived features + first analysis |
| **6** | Strategy specification + backtesting |
| **7** | Execution layer |

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
docker compose up -d          # PostgreSQL
python manage.py migrate
python manage.py runserver
```

Requires a `.env` file — see `.env.example` (not committed; needs `DJANGO_SECRET_KEY`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`).
