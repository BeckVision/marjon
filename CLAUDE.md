# CLAUDE.md — marjon

## Quick Start

```bash
cd ~/Desktop/projects/marjon && source venv/bin/activate
docker compose up -d                    # PostgreSQL on port 5433
python manage.py test                   # Run all tests (must pass before commit)
python manage.py test --parallel        # Faster
```

## Architecture

4 Django apps. Each has one job. Nothing crosses boundaries.

| App | Role | Owns models? |
|-----|------|-------------|
| `warehouse` | Storage — models, managers, constraints, migrations | Yes |
| `data_service` | Access — 3 read-only operations, PIT enforcement, alignment, derived features | No |
| `pipeline` | ETL — connectors, conformance, loaders, runner, orchestration | No |
| `strategy` | Analysis — backtest engine, signals, strategies, sweep, walk-forward | Yes |

## Rules

1. **Run tests before committing.** `python manage.py test` must pass. Pre-commit hook enforces this.

2. **Constants live on warehouse models.** `TEMPORAL_RESOLUTION`, `OBSERVATION_WINDOW_*`, `LAYER_ID`, `UNIVERSE_ID` — import from the model, never duplicate as local constants. `OHLCVCandle.TEMPORAL_RESOLUTION` is the SSOT, not `timedelta(minutes=5)`.

3. **Connectors are anti-corruption layers.** They talk to external APIs and return `(records, metadata)`. They may import warehouse models for constants only. They never write to DB, never transform semantically.

4. **Conformance functions are pure.** No DB access, no API calls, no side effects. Crash on malformed input — never silently skip. Feed fixture in, verify output. No mocking required.

5. **Two universe types.** Event-driven (anchor_event + relative timedelta offsets) and calendar-driven (absolute datetimes, anchor_event NULL). Code in abstract bases, managers, data_service, and pipeline runner must dispatch on `UNIVERSE_TYPE` — never assume event-driven.

6. **Paradigm-level code is dataset-agnostic.** If you see `coin`, `mint_address`, `pump.fun`, `FL-001`, `U-001`, or `MigratedCoin` in abstract bases, managers, data_service operations, or the pipeline runner — it's a paradigm leak. Fix it.

7. **New pipeline? Use PipelineSpec + run_for_coin().** No standalone fetch scripts.

8. **Idempotency.** Delete-write for feature layers, upsert for universe/dimension tables. Every step safe to re-run.

9. **Open/Closed.** Adding a new universe requires zero changes to existing universe code. If it does, the abstraction is wrong.

10. **Fail fast.** `raw['tokenAddress']` not `raw.get('tokenAddress', None)`. Exceptions for documented nullable fields, network retries, and per-coin batch errors.

11. **Read docs before refactoring architecture.** `docs/rules.md`, `docs/coding_principles.md`, `docs/data_specification_guide.md`, `docs/warehouse_implementation_guide.md`, `docs/pipeline_implementation_guide.md`.

12. **Explore → Document → Spec → Implement.** For new data sources: API exploration findings → pipeline implementation record → code → E2E test. Never skip exploration.

## Key Patterns

- **PIT enforcement:** `.as_of(simulation_time)` on QuerySets. End-of-interval for feature layers, event-time for reference tables.
- **Pipeline framework:** `PipelineSpec` (spec.py) + `run_for_coin()` (runner.py) — 14-step scaffolding handles mode detection, run tracking, error handling, watermarks, completeness.
- **Data service:** 3 operations — `get_panel_slice()`, `get_universe_members()`, `get_reference_data()`. All reads go through these. No direct model queries from consumers.
- **Derived features:** On-the-fly via `DerivedFeatureSpec` + `DERIVED_REGISTRY`. Never stored.

## Stack

- Python 3.12, Django 6.0, PostgreSQL (docker-compose, port 5433)
- httpx with HTTP/2, 6 AWS API Gateways for IP rotation
- No Celery yet — orchestration via management commands
