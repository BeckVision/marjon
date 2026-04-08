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

Bootstrap the local environment with the repo script:

```bash
./scripts/bootstrap.sh
```

That script will:

- create `.env` from `.env.example` if needed
- create a virtualenv if neither `.venv` nor `venv` exists
- install `requirements.txt`
- start the PostgreSQL container
- run migrations

Daily commands then go through the shared wrapper:

```bash
./scripts/doctor.sh
./scripts/manage.sh runserver
./scripts/test.sh
```

There is also a `Makefile` wrapper if you prefer:

```bash
make bootstrap
make doctor
make test
make runserver
```

The scripts prefer `.venv` when both `.venv` and `venv` exist, which avoids the drift that can happen when different entrypoints activate different environments.
