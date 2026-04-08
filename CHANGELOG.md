# Changelog

This project uses Semantic Versioning.

The `Unreleased` section is the staging area for the next GitHub release.
When you publish a release, move the relevant bullets into a versioned section,
tag the matching commit as `vX.Y.Z`, and use that section as the GitHub release notes.
The historical sections below were bootstrapped from notable repo milestones
because the project did not yet have git tags.

## [Unreleased]

### Added

- Added a tracked [CHECKLIST.md](/home/beck/Desktop/projects/marjon/CHECKLIST.md) so the active U-001 ingestion and research-readiness backlog lives in the repo.
- Added [run_holders.sh](/home/beck/Desktop/projects/marjon/scripts/run_holders.sh) as the dedicated capped FL-002 catch-up wrapper for mature coins.
- Added [run_batch_errors.sh](/home/beck/Desktop/projects/marjon/scripts/run_batch_errors.sh) and RD-001 status filtering so error-row retries can be targeted directly.
- Added [run_batch_partials.sh](/home/beck/Desktop/projects/marjon/scripts/run_batch_partials.sh) so RD-001 `partial` rows can be recovered deliberately.
- Added [run_batch_partials_historical.sh](/home/beck/Desktop/projects/marjon/scripts/run_batch_partials_historical.sh) for capped Helius recovery of old RD-001 partial rows.
- Added [run_batch_partials_guarded.sh](/home/beck/Desktop/projects/marjon/scripts/run_batch_partials_guarded.sh) as the explicit opt-in lane for historical RD-001 partial rows parked by the free-tier guard.

### Changed

- Hardened the shared HTTP client for RD-001 by disabling HTTP/2 for Shyft by default and retrying `417 Expectation Failed` with a fresh session.
- Changed RD-001 recovery ordering so `error` and `partial` retries prioritize the oldest and smallest work first instead of the busiest work first.
- Short-circuited RD-001 signature discovery once the free-tier filtered-signature guard is exceeded, reducing wasted RPC pagination on oversized windows.
- Preserved RD-001 `partial` and `window_complete` status rows when a free-tier guard failure occurs, and excluded free-tier-guarded `partial` and `error` rows from normal retry queues unless explicitly requested.
- Surfaced free-tier-guarded RD-001 status counts in `u001_ingestion_health` so parked oversized rows remain visible after being excluded from normal retry queues.

## [0.4.0] - 2026-04-08

### Added

- Added `u001_ingestion_health` to report live U-001 freshness, per-layer coverage, stale pipeline state, and common upstream failures.
- Added `repair_u001_ingestion` to convert stale U-001 `started` and `in_progress` rows into explicit error state so the pipeline can resume cleanly.
- Added a tracked `VERSION` file and a release-oriented changelog workflow for GitHub releases.

### Changed

- Hardened U-001 orchestration so step-level config is forwarded correctly into handlers.
- Moved U-001 daily and RD-001 batch wrappers to conservative free-tier defaults, including capped slices, mature-only OHLCV selection, and guarded RD-001 throughput.
- Added free-tier guards for oversized RD-001 coins based on newly discovered and filtered signature counts.
- Made Moralis daily CU budget configurable via environment.
- Improved HTTP transport recovery by dropping broken pooled sessions on transport errors.

### Operational Result

- Advanced U-001 discovery to April 8, 2026.
- Advanced FL-001 to April 8, 2026 with additional mature-coin backfill.
- Advanced RD-001 with a validated low-budget batch path that completed successfully on current provider settings.

## [0.3.0] - 2026-04-08

### Added

- Added standardized local entrypoints for bootstrap, management commands, tests, health checks, and tracked git hooks.
- Added CI that runs the same doctor and test entrypoints used locally.

### Changed

- Standardized the repository on `.venv` and removed the old split-environment behavior.
- Moved git hooks into tracked repo state instead of relying on local-only `.git/hooks`.

## [0.2.0] - 2026-03-23

### Added

- Added U-002 as a second universe with Binance data collection, daily update flow, order book polling, and visualization support.
- Added liquidity metrics, CVD-related work, and chart-facing query improvements for the visualization path.

### Changed

- Improved CSV ingestion, incremental backfill behavior, and universe-agnostic orchestration.

## [0.1.0] - 2026-03-23

### Added

- Established the universe-agnostic warehouse and pipeline abstractions.
- Added guardrail coverage to enforce core modeling and pipeline invariants in code.
