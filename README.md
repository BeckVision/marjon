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
./scripts/manage.sh u001_ingestion_health
./scripts/manage.sh repair_u001_ingestion --dry-run
```

There is also a `Makefile` wrapper if you prefer:

```bash
make bootstrap
make doctor
make u001-health
make u001-holders
make u001-rd001-errors
make u001-rd001-partials
make u001-rd001-partials-historical
make u001-repair
make install-hooks
make test
make runserver
```

The scripts prefer `.venv` when both `.venv` and `venv` exist, which avoids the drift that can happen when different entrypoints activate different environments.

To install the tracked git hooks for this checkout:

```bash
./scripts/install-hooks.sh
```

That configures `core.hooksPath` to use `.githooks/`, so the pre-commit hook is versioned with the repo instead of living only in `.git/hooks/`.

For live U-001 pipeline state, use:

```bash
make u001-health
```

That reports freshness, per-layer coverage, stale `in_progress` rows, common upstream errors, and recent U-001 batch activity using the actual database contents.

The tracked operational backlog for U-001 lives in [CHECKLIST.md](/home/beck/Desktop/projects/marjon/CHECKLIST.md), so current ingestion follow-up work stays in the repo instead of only in chat.

If U-001 ingestion is interrupted and leaves stale `started` or `in_progress` rows behind, use:

```bash
./scripts/manage.sh repair_u001_ingestion --dry-run
make u001-repair
```

RD-001 defaults are intentionally conservative for free-tier usage. You can tune them with:

```bash
MARJON_U001_DAILY_STEPS
MARJON_U001_DAILY_COINS
MARJON_U001_DAILY_DAYS
MARJON_U001_DAILY_MATURE_ONLY
MARJON_U001_ENABLE_HOLDERS
MARJON_U001_HOLDERS_COINS
MARJON_U001_HOLDERS_DAYS
MARJON_U001_HOLDERS_MATURE_ONLY
MARJON_U001_RD001_MAX_COINS
MARJON_U001_RD001_ERROR_MAX_COINS
MARJON_U001_RD001_PARTIAL_MAX_COINS
MARJON_U001_RD001_PARTIAL_HIST_MAX_COINS
MARJON_U001_RD001_MAX_NEW_SIGS
MARJON_U001_RD001_MAX_FILTERED_SIGNATURES
MARJON_U001_RD001_BATCH_WORKERS
MARJON_U001_RD001_PARSE_WORKERS
MARJON_U001_RD001_RPC_BATCH_SIZE
MARJON_U001_RD001_MIN_SIGS
MARJON_U001_RD001_SLEEP
MORALIS_DAILY_CU_LIMIT
```

The tracked wrappers default to a free-tier-safe posture:

- [run_daily.sh](/home/beck/Desktop/projects/marjon/scripts/run_daily.sh) processes a capped recent slice and skips holders unless `MARJON_U001_ENABLE_HOLDERS=1`.
- [run_daily.sh](/home/beck/Desktop/projects/marjon/scripts/run_daily.sh) also prefers mature coins by default so OHLCV backfill does not waste its capped slice on immature names.
- [run_holders.sh](/home/beck/Desktop/projects/marjon/scripts/run_holders.sh) is the dedicated low-budget FL-002 catch-up path for mature coins.
- [run_batch.sh](/home/beck/Desktop/projects/marjon/scripts/run_batch.sh) caps RD-001 work per run and keeps concurrency conservative.
- [run_batch_errors.sh](/home/beck/Desktop/projects/marjon/scripts/run_batch_errors.sh) spends RD-001 budget specifically on coins already in `error` state.
- [run_batch_partials.sh](/home/beck/Desktop/projects/marjon/scripts/run_batch_partials.sh) spends RD-001 budget specifically on coins stuck in `partial` state.
- [run_batch_partials_historical.sh](/home/beck/Desktop/projects/marjon/scripts/run_batch_partials_historical.sh) spends a small Helius budget on old RD-001 `partial` rows that are outside Shyft retention.

The shared HTTP client also disables HTTP/2 for Shyft by default because RD-001 showed repeated transport instability there in live runs. Override the host list with `MARJON_HTTP2_DISABLED_HOSTS` if you need different behavior.

## Releases

The repo now tracks release notes in [CHANGELOG.md](/home/beck/Desktop/projects/marjon/CHANGELOG.md) and the current release number in [VERSION](/home/beck/Desktop/projects/marjon/VERSION).

Recommended release flow:

1. Add new notes under `Unreleased` in [CHANGELOG.md](/home/beck/Desktop/projects/marjon/CHANGELOG.md).
2. When you are ready to publish, move those notes into a new `X.Y.Z` section and update [VERSION](/home/beck/Desktop/projects/marjon/VERSION).
3. Commit the release prep.
4. Tag the release commit with `git tag -a vX.Y.Z -m "Release vX.Y.Z"`.
5. Push the branch and tag, then paste the matching changelog section into the GitHub release body.

Current release line: `v0.4.0`.
