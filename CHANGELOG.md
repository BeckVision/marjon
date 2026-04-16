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
- Added a landing page for explaining Marjon, U-001, and RD-001 in plain language.
- Added the first U-001 operations cockpit page at `/ops/u001/`, with freshness, layer coverage, error buckets, recent batch history, and a rule-based next-action panel.
- Added `/ops/u001/coverage/` and `/ops/u001/queues/`, so the cockpit now includes a recent-cohort funnel view and a read-only RD-001 queue planner.
- Added `/ops/u001/coin/<mint>/`, so one page now shows identity, pool mapping, layer statuses, run history, warehouse counts, and timeline for a single U-001 coin.
- Added `/ops/u001/trends/`, which shows current operational pressure plus recent run-history-derived batch and layer activity.
- Added `U001OpsSnapshot` and `snapshot_u001_ops`, so `/ops/u001/trends/` can read exact daily U-001 backlog and error counts instead of relying only on reconstructed run history.
- Added `U001AutomationState`, `automate_u001`, and `run_u001_automation.sh`, establishing the first policy-driven U-001 automation tick with stale-state repair, single-lane selection, and optional daily snapshotting.
- Added `run_u001_rd001_recent_cycle` plus `run_u001_rd001_recent_continuous.sh`, creating a separate recent-window RD-001 maintenance runner that repeatedly repairs state and ingests recent RD-001 for already-mapped pools.
- Added automation-controller visibility to `/ops/u001/`, so the cockpit now shows the last tick, action, status, snapshot date, and failure pressure without shell access.
- Aligned the overview recommendation panel with the live U-001 automation policy, so the cockpit now reflects controller pauses and real next-lane decisions instead of only static heuristics.
- Added `U001AutomationTick` and recent-tick rendering in `/ops/u001/`, so each non-dry-run automation tick is now persisted and visible from the cockpit.
- Added `/ops/u001/automation/`, giving U-001 automation its own filtered history page and JSON endpoint for controller tick inspection.
- Added manual lane-equivalent commands to `/ops/u001/automation/`, so controller decisions and past ticks now map directly to copyable operator commands.
- Added `audit_u001` plus a `make u001-audit` entrypoint, so unattended-safety checks now have an explicit command for scheduler freshness, controller health, snapshot freshness, and coverage-floor auditing.
- Added persisted `U001SourceAuditRun` storage plus overview rendering on `/ops/u001/`, so the latest sampled external-truth audit is now visible from the cockpit instead of only from shell output.
- Added `audit_u001_rd001_chain`, `U001RD001ChainAuditRun`, and a `make u001-rd001-chain-audit` entrypoint, creating the first persisted direct-RPC RD-001 chain-truth audit that compares sampled warehouse transactions against Solana RPC instead of treating Shyft or Helius as the source of truth.
- Expanded the direct-RPC RD-001 chain audit from single-row checks into sampled pool-window reconciliation, so the command now detects missing or extra trade signatures over a sampled time window and degrades public-RPC rate-limit failures into explicit warnings instead of hard run failures.
- Improved `audit_u001_sources` to prefer informative FL-001 / FL-002 / RD-001 samples, skip empty windows, and run a low-budget default sample across all three layers.
- Fixed `audit_u001_sources` false positives on unaligned FL-001 / FL-002 sample windows by aligning warehouse and source comparison ranges to the canonical 5-minute grid.
- Added a dedicated `pool_mapping_recent` automation lane and controller recommendation/manual-command support, so recent unmapped discovery no longer has to wait for the broader `refresh_core` lane.
- Changed U-001 automation priority so recent RD-001 work inside Shyft retention, including bootstrap candidates with no watermark yet, can run before FL-002 backlog catch-up.
- Added U-001 price-action derived features for close returns, candle structure, and breakout context.
- Added U-001 momentum, breakout, and strong-close signals plus the `u001_breakout_close_v1` strategy config.

### Changed

- Hardened the shared HTTP client for RD-001 by disabling HTTP/2 for Shyft by default and retrying `417 Expectation Failed` with a fresh session.
- Changed RD-001 recovery ordering so `error` and `partial` retries prioritize the oldest and smallest work first instead of the busiest work first.
- Short-circuited RD-001 signature discovery once the free-tier filtered-signature guard is exceeded, reducing wasted RPC pagination on oversized windows.
- Preserved RD-001 `partial` and `window_complete` status rows when a free-tier guard failure occurs, and excluded free-tier-guarded `partial` and `error` rows from normal retry queues unless explicitly requested.
- Surfaced free-tier-guarded RD-001 status counts in `u001_ingestion_health` so parked oversized rows remain visible after being excluded from normal retry queues.
- Tightened RD-001 Shyft conformance so multi-event transactions prefer the requested pool, ignore duplicate trade events, and only warn on genuinely ambiguous same-pool conflicts.
- Preserved the final shared HTTP retry failure detail and surfaced current RD-001 error buckets in `u001_ingestion_health`, so Shyft transport instability is measurable instead of opaque.
- Disabled HTTP keep-alive reuse for Shyft hosts in the shared client, targeting the RD-001 `Server disconnected` failure pattern without changing pooling for other providers.
- Tightened U-001 automation with lane cooldowns and circuit-breaker style pauses after repeated failures or known auth/transport failures on the same lane.
- Added RD-001 Shyft parse fallback that splits unstable `parse_selected` batches into smaller chunks after retry exhaustion, reducing full-coin failures from intermittent transport disconnects.
- Lowered the default RD-001 Shyft `parse_selected` batch size and exposed `MARJON_U001_RD001_PARSE_BATCH_SIZE` so the recent-coin lane can trade a few more API calls for better transport stability.
- Made the recent RD-001 batch selector more conservative by preferring steady-state coins first and skipping bootstrap candidates whose first-page signature counts are already near the free-tier guard.
- Added a separate low-signature threshold for steady-state RD-001 coins so the recent lane can keep making safe incremental progress even when every bootstrap candidate is too large.
- Applied the same bootstrap safety cap to recent RD-001 `error` and `partial` retry lanes, so targeted retries do not waste budget on likely free-tier guard failures.
- Changed the explicit guarded Helius recovery lane to work from the smallest known free-tier-guard overage upward, making one-off historical retries more deliberate.
- Added a holders anti-starvation rule to U-001 automation, so repeated `pool_mapping_recent` / `rd001_recent` streaks now force a periodic `holders_catchup` tick while FL-002 remains below its coverage floor.
- Persisted structured result summaries on `U001AutomationTick` for batch-driven lanes, so `/ops/u001/automation/` and the overview can show actual RD-001 tick yield instead of only complete/error status.
- Extended `audit_u001` with controller streak and no-progress detection, so unattended safety now warns when automation appears to be spinning on one lane or when consecutive `rd001_recent` ticks complete without loading rows.
- Extended lane-specific no-progress detection in `audit_u001` to cover `holders_catchup` and `pool_mapping_recent`, so unattended safety now warns when repeated orchestrate ticks only skip work or fail to map any recent coins.
- Added a spin-risk panel to `/ops/u001/automation/`, so the cockpit now surfaces the latest complete-lane streak and lane-specific no-progress risk without requiring a manual `audit_u001` run.
- Added the same automation spin-risk summary to `/ops/u001/`, so the top-level U-001 cockpit now shows when the controller is busy but potentially unproductive.
- Added reboot hardening for local unattended U-001 automation: the Postgres container now uses `restart: unless-stopped`, and the new `scripts/recover_after_reboot.sh` wrapper can restore DB availability, run migrations, and trigger one immediate automation tick after boot.
- Added persisted `U001BootRecoveryRun` rows plus `/ops/u001/` overview rendering for the latest reboot recovery result, so post-boot recovery success or failure is now visible in the cockpit instead of only in shell logs.
- Extended `audit_u001` to report the latest persisted reboot recovery status, so unattended safety now warns when the most recent post-boot recovery failed after database startup.
- Extended `audit_u001` and `/ops/u001/` with the latest persisted `U001RD001ChainAuditRun`, so sampled direct-RPC RD-001 chain-truth results are now visible in unattended safety and the overview cockpit instead of only through the standalone management command.
- Centralized Phase 0 chain-audit RPC selection in `marjon/settings.py`, so `audit_u001_rd001_chain` now prefers an explicit `U001_CHAIN_AUDIT_RPC_URL`, otherwise falls back through generic Solana RPC env vars, then a Helius-keyed RPC URL, and only uses the public Solana endpoint as a last resort.
- Added `audit_u001_fl001_derived`, `U001FL001DerivedAuditRun`, and a `make u001-fl001-derived-audit` entrypoint, creating the first self-derived U-001 candle audit from warehouse RD-001 trades plus local `SOLUSDT` candles instead of treating GeckoTerminal as the truth source.
- Extended `/ops/u001/` and `audit_u001` with the latest persisted `U001FL001DerivedAuditRun`, so self-derived FL-001 candle-truth status is now visible in the overview cockpit and unattended-safety audit alongside the existing live-source and RD-001 chain-audit surfaces.
- Extended `/ops/u001/trends/` with current truth-audit cards and daily history for `U001SourceAuditRun`, `U001RD001ChainAuditRun`, and `U001FL001DerivedAuditRun`, so Phase 0 chain-truth confidence is visible over time instead of only as latest-state.
- Added truth-audit coverage summaries to `/ops/u001/trends/`, so the trends page now highlights days with no Phase 0 audit coverage and days that produced truth-audit warnings or findings.
- Added a compact 7-day `Truth Audit Coverage` summary to `/ops/u001/`, so the overview now surfaces missing recent Phase 0 audit coverage without requiring a drill-down into trends.
- Extended `audit_u001` with recent truth-audit coverage summaries and a configurable minimum-coverage warning floor, so unattended safety now flags thin recent Phase 0 audit coverage instead of only reporting the latest individual truth-audit runs.
- Extended U-001 automation with dedicated `truth_source_audit`, `truth_rd001_chain_audit`, and `truth_fl001_derived_audit` lanes, so the controller can now close thin recent Phase 0 truth coverage on its own instead of only reporting it.
- Added `Truth Audit Lanes` to `/ops/u001/automation/`, so the automation page now shows both scheduler activity and latest persisted result status for the three Phase 0 audit lanes.
- Simplified the dedicated recent-window RD-001 loop to focus on `repair -> pool_mapping -> fetch_transactions_batch`, and made `run_u001_rd001_recent_continuous.sh` survive failed cycles with a separate error backoff instead of exiting on the first transient failure.
- Changed reboot recovery to launch the separate RD-001 recent continuous runner by default after the first post-boot automation tick, so local startup now restores both the main controller and the dedicated recent-window maintainer.
- Extended `audit_u001` to evaluate the dedicated recent-window RD-001 runner heartbeat, so unattended safety now reports when that separate loop is missing, stale, dead, or alive but backing off after a failed cycle.
- Bounded the dedicated recent-window RD-001 loop to a capped recent candidate set before Phase 1 discovery, so the continuous Shyft lane no longer fans out across the full recent universe on every cycle before selecting its per-cycle queue.
- Changed recent candidate selection inside that bounded RD-001 slice to prefer coins with existing raw history first, so the continuous runner now spends recent discovery budget on steady-state watermark maintenance before raw bootstrap names.
- Changed the main U-001 controller to defer `rd001_recent` whenever the dedicated recent-window runner is healthy and fresh, so recent Shyft maintenance now has one owner instead of two competing schedulers.
- Changed the main U-001 controller to prioritize historical RD-001 catch-up lanes while the dedicated recent-window runner is healthy, so freed automation capacity now goes into partial/error/guarded backlog reduction before lower-urgency holders or truth-audit work.
- Tightened that historical catch-up policy so ordinary RD-001 partial backlog can run on every 30-minute controller tick, scheduled error recovery can still preempt when due, and guarded rows no longer interrupt while regular partial/error backlog still exists.
- Changed `rd001_error_recovery` to use Helius explicitly, so the main controller now spends those ticks on the real historical RD-001 error backlog instead of selecting mostly empty recent-window error queues.
- Changed historical Helius `partial` and `error` retry ordering to prefer smaller existing raw histories first, so backlog slices now spend recovery budget on cheaper rows that are more likely to close cleanly instead of repeatedly hitting giant windows first.
- Changed the guarded Helius lane to use its own raised filtered-signature threshold and to skip guarded rows still above that raised limit, so automation no longer spends guarded budget on retries that are guaranteed to fail the normal free-tier guard again.
- Fixed a Shyft recent-runner regression where the shared RD-001 fetch path started passing `max_filtered_signatures` into the Shyft connector before that connector accepted it, which had been causing recent cycles to fail before parsing any transactions.
- Raised the RD-001 batch path and dedicated recent-runner defaults to an aggressive posture: no success sleep between cycles, `4` recent batch workers, `4` parse workers, Shyft parse batch size `100`, no first-page oversized/bootstrap signature caps by default, no connector-level filtered-signature guard by default, and no per-cycle pool-mapping step so the runner focuses only on already-mapped recent pools.
- Added a holders no-progress pause to the main controller, so repeated `holders_catchup` ticks that load `0` rows now back off temporarily instead of monopolizing automation time.
- Tightened automation tick result semantics so RD-001 batch lanes are now marked `error` when they queue work but every queued coin fails with `0` rows loaded, instead of being recorded as successful no-op completions during outages.
- Added `Connectivity Risk` to `/ops/u001/` and `audit_u001`, so likely internet or upstream reachability failures are surfaced explicitly instead of only appearing as generic automation errors.
- Added `Connectivity Risk` to `/ops/u001/automation/`, so the automation page now shows reachability/outage pressure beside the controller tick log and truth-audit lane rollups.
- Added `audit_u001_rd001_solscan`, a low-budget optional Solscan-backed RD-001 parser spot check for recent windows, requiring `SOLSCAN_API_KEY` and intended as a secondary heuristic audit rather than chain truth.
- Tightened U-001 automation with a connectivity-specific pause rule, so repeated transport/reachability failures now trigger an explicit `no_action` pause sooner instead of spending multiple extra ticks on doomed network-dependent lanes.

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
