# U-001 Full Automation Plan

## Purpose

Move U-001 from a manually operated set of wrappers into a self-running, observable, recoverable ingestion system.

This plan is not about adding more UI. It is about removing the need to manually decide:

- when to run discovery
- when to run holders catch-up
- when to run recent RD-001
- when to run historical RD-001 partial recovery
- when to run guarded Helius recovery
- when to repair stale state
- when to snapshot operational metrics

The target outcome is:

U-001 should continue making safe forward progress without human intervention, while still exposing enough diagnostics to override or debug it when needed.

## What “Fully Automated” Means

For this repo, “fully automated U-001” should mean all of the following:

1. Discovery, pool mapping, FL-001, FL-002, and RD-001 run on schedule without manual command selection.
2. Recent-vs-historical RD-001 source choice remains automatic.
3. Recovery lanes are selected by policy, not by the operator choosing shell commands.
4. Stale `started` / `in_progress` state is repaired automatically.
5. Daily operational snapshots are recorded automatically.
6. Failures are visible quickly in the cockpit and logs.
7. The system avoids overlapping runs and respects provider budget caps.
8. Manual commands still exist, but become overrides rather than the normal control surface.

## Current State

The repo already has useful building blocks:

- `./scripts/run_daily.sh` for `discovery,pool_mapping,ohlcv`
- `./scripts/run_holders.sh` for FL-002 catch-up
- `./scripts/run_batch.sh` for recent RD-001
- `./scripts/run_batch_errors.sh` for RD-001 error rows
- `./scripts/run_batch_partials.sh` for RD-001 partial rows
- `./scripts/run_batch_partials_historical.sh` for old RD-001 partial rows
- `./scripts/run_batch_partials_guarded.sh` for explicitly guarded historical rows
- `repair_u001_ingestion`
- `u001_ingestion_health`
- `snapshot_u001_ops`
- the `/ops/u001/` cockpit

What is still missing is not raw capability. It is an automation controller.

Right now the repo has multiple safe levers, but no single policy engine that decides which lever should fire next.

## Recommendation

Build full automation in two layers:

1. A policy layer that decides what U-001 should run next.
2. A scheduler layer that repeatedly invokes that policy.

Do not jump straight to Celery first.

The pragmatic first version should be:

- one management command that executes one automation tick
- one shell wrapper with locking and logging
- cron or systemd timer calling that wrapper on a fixed cadence

After that is stable, we can decide whether Celery beat is worth the complexity.

## Architecture

### 1. Automation Policy Module

Add a dedicated module, for example:

- `pipeline/u001_automation.py`

Responsibilities:

- inspect current state
- choose which lane should run next
- enforce simple cooldowns
- enforce budget caps
- explain the decision in logs

This module should not execute shell commands. It should return a structured decision.

Example decision shape:

```python
{
    "action": "rd001_partial_historical",
    "reason": "RD-001 partial backlog remains high and recent discovery is not stale",
    "command_args": ["fetch_transactions_batch", "--source", "helius", "--status-filter", "partial", "--max-coins", "3"],
    "cooldown_ok": True,
    "budget_ok": True,
}
```

### 2. One-Tick Controller Command

Add a command such as:

- `python manage.py automate_u001`

Responsibilities:

- run `repair_u001_ingestion` first
- read current health / status / snapshot context
- choose one action via the policy module
- execute that one action
- optionally record a post-run snapshot
- emit a clear structured log summary

Important:

One invocation should do one controlled step, not an unbounded loop.

That makes it:

- easy to schedule
- easy to debug
- easy to retry
- easy to inspect in logs

### 3. Wrapper Script

Add:

- `scripts/run_u001_automation.sh`

Responsibilities:

- `flock`-based overlap protection
- log file creation
- invoke `repair_u001_ingestion`
- invoke `automate_u001`
- optionally invoke `snapshot_u001_ops`

This becomes the single scheduling target for U-001.

## Policy Design

The core design problem is lane selection.

The controller should not “run everything every time.” It should choose the best next action based on current state.

### Priority Order

Recommended first-pass priority:

1. Repair stale state
2. Refresh stale discovery / mapping / OHLCV
3. Continue FL-002 catch-up if coverage is still below target
4. Run recent safe RD-001
5. Run historical RD-001 partial recovery
6. Run RD-001 error recovery
7. Run guarded RD-001 recovery
8. Record daily snapshot if due

### Decision Rules

#### Rule 1: Repair first

Always run:

- `repair_u001_ingestion`

before choosing any normal work.

Reason:

stale `started` and `in_progress` rows should never be left for humans to clean up.

#### Rule 2: Discovery freshness gates downstream work

If discovery is stale beyond threshold:

- prioritize `orchestrate --steps discovery,pool_mapping,ohlcv`

Reason:

if discovery is stale, all downstream coverage decisions are working on an outdated universe.

#### Rule 3: Pool mapping bottleneck gets promoted

If recent discovery exists but mapping coverage for recent cohorts is too low:

- prioritize daily orchestrator with `pool_mapping`

Reason:

RD-001 and FL-001 cannot progress for unmapped coins.

#### Rule 4: FL-002 should be treated as a standing catch-up lane

If FL-002 mature coverage is below a configured floor:

- run holders catch-up in capped mature-only slices

Reason:

FL-002 is currently backlog-driven and should not rely on human memory.

#### Rule 5: Recent RD-001 gets first pass on fresh work

If there are safe recent RD-001 candidates:

- run recent safe steady-state RD-001

Reason:

recent windows are time-sensitive and benefit from Shyft retention while available.

#### Rule 6: Historical RD-001 partials get steady background attention

If recent safe queue is thin or empty:

- run historical partial Helius slice

Reason:

this has already proven to be the cheapest reliable path for converting `partial` to `window_complete`.

#### Rule 7: Error lanes are controlled, not dominant

Error retries should be periodic but not allowed to starve productive work.

Recommended:

- run error recovery only every N ticks
- run guarded recovery only every M ticks or once per day

Reason:

error lanes are important, but they are often lower-yield than healthy partial recovery.

#### Rule 8: Guarded Helius should stay tightly capped

The guarded lane should remain:

- tiny
- deliberate
- budget-aware

Recommended policy:

- at most one guarded attempt per day
- only if known overage is below a configured threshold

Reason:

this is the easiest lane to make expensive and noisy.

## Required State and Inputs

The automation controller needs a small internal state model.

### Inputs from the warehouse

- `MigratedCoin`
- `PoolMapping`
- `U001PipelineStatus`
- `U001PipelineRun`
- `PipelineBatchRun`
- `U001OpsSnapshot`

### Runtime policy config

Add env-backed settings such as:

- `MARJON_U001_AUTOMATION_ENABLED`
- `MARJON_U001_AUTOMATION_MODE`
- `MARJON_U001_AUTOMATION_TICK_MINUTES`
- `MARJON_U001_AUTOMATION_FL002_MIN_COVERAGE_PCT`
- `MARJON_U001_AUTOMATION_RD001_MIN_COMPLETE_PCT`
- `MARJON_U001_AUTOMATION_MAX_GUARDED_PER_DAY`
- `MARJON_U001_AUTOMATION_ERROR_EVERY_N_TICKS`
- `MARJON_U001_AUTOMATION_SNAPSHOT_HOUR_UTC`
- `MARJON_U001_AUTOMATION_DISCOVERY_STALE_HOURS`

### Controller-owned state

Recommended new small model:

- `U001AutomationState`

Fields:

- `singleton_key`
- `last_tick_at`
- `last_action`
- `last_action_started_at`
- `last_action_completed_at`
- `last_snapshot_date`
- `guarded_attempts_today`
- `error_lane_tick_counter`
- `notes`

Why:

some automation behavior depends on memory across ticks, not just current warehouse state.

Example:

- “don’t run guarded lane more than once today”
- “run error lane every 4 ticks”
- “snapshot only once per day”

This state is operational, not analytical.

## Scheduler Design

### Phase 1 Scheduler

Use cron or systemd timer against:

- `scripts/run_u001_automation.sh`

Recommended cadence:

- every 30 minutes

Reason:

- fast enough to keep recent work moving
- slow enough to stay conservative on free-tier providers
- simple enough for single-machine operation

### Suggested Cron

```cron
*/30 * * * * /home/beck/Desktop/projects/marjon/scripts/run_u001_automation.sh
```

### Daily Snapshot

The controller may invoke `snapshot_u001_ops` when due, but the simplest reliable version is a separate daily schedule:

```cron
15 0 * * * /home/beck/Desktop/projects/marjon/scripts/manage.sh snapshot_u001_ops
```

If the controller owns snapshotting, keep it idempotent and date-aware anyway.

## Operational Lanes To Automate

The full automation plan should cover these exact lanes:

### A. Core freshness lane

Runs:

- `orchestrate --universe u001 --steps discovery,pool_mapping,ohlcv`

Goal:

- keep universe membership fresh
- keep mapping current
- keep FL-001 moving

### B. Holders catch-up lane

Runs:

- `orchestrate --universe u001 --steps holders`

Goal:

- raise FL-002 mature coverage steadily

### C. Recent RD-001 lane

Runs:

- `fetch_transactions_batch` with default recent-safe policy

Goal:

- consume recent Shyft-safe opportunities while retention exists

### D. Historical partial lane

Runs:

- `fetch_transactions_batch --source helius --status-filter partial`

Goal:

- reduce old RD-001 partial backlog

### E. Error lane

Runs:

- `fetch_transactions_batch --status-filter error`

Goal:

- retry recoverable failures without dominating the whole schedule

### F. Guarded lane

Runs:

- explicit guarded Helius recovery

Goal:

- chip away at smallest-overage parked rows under strict budget control

## Safety Requirements

Full automation should not mean reckless automation.

### Overlap protection

Keep `flock` at the top wrapper layer.

No second tick should start while the prior one is still running.

### Bounded work per tick

Each tick should run at most one main action plus optional repair/snapshot.

Do not allow one invocation to cascade into multiple heavy jobs.

### Budget caps

Preserve current conservative env defaults for:

- RD-001 recent work
- holders slices
- guarded Helius attempts

The automation layer should select actions, not silently raise budgets.

### Circuit breakers

Add simple policy stops such as:

- if FL-002 auth failures are active, reduce holders retry frequency
- if Shyft transport errors spike beyond threshold, reduce recent RD-001 frequency
- if guarded lane fails repeatedly, pause guarded lane for the rest of the day

### Idempotency

Every automated action must remain safe to rerun.

This is already mostly true for the existing pipeline commands and should remain a hard requirement.

## Observability

Automation without observability is just silent failure.

### Required outputs per tick

Each automation tick should log:

- tick start / end
- selected action
- reason for selection
- skipped lanes and why
- elapsed time
- success / failure

### Cockpit additions

The cockpit should eventually show:

- last automation tick
- last selected action
- last action result
- consecutive failure count
- snapshot freshness
- automation enabled/disabled

This is useful, but not required for phase 1.

## Failure Handling

### Automatic stale repair

Every tick should begin with:

- `repair_u001_ingestion`

### Action failure behavior

If the chosen action fails:

- record failure in controller state
- do not immediately cascade to a second heavy action in the same tick
- let the next scheduled tick re-evaluate

### Escalation threshold

If the same lane fails K ticks in a row:

- reduce its priority temporarily
- surface it prominently in logs and cockpit

This prevents one broken upstream from monopolizing the system.

## Implementation Plan

## Phase 1: Controller Skeleton

Deliver:

- `U001AutomationState` model
- `pipeline/u001_automation.py`
- `automate_u001` management command
- `scripts/run_u001_automation.sh`

Acceptance:

- one scheduled wrapper can run one automation tick safely
- action selection is rule-based and logged

## Phase 2: Lane Selection Policy

Deliver:

- freshness gating
- FL-002 catch-up policy
- RD-001 recent/historical/error/guarded selection
- cooldown counters

Acceptance:

- no manual lane picking needed in normal operation
- controller chooses sane actions from current system state

## Phase 3: Snapshot and Visibility Integration

Deliver:

- controller-aware snapshot scheduling
- cockpit automation status panel
- logs/notes for last action and last failure

Acceptance:

- operator can answer “what did automation do last?” without shell digging

## Phase 4: Hardening

Deliver:

- circuit breakers
- configurable backoff
- stronger alerts
- schedule tuning based on real provider behavior

Acceptance:

- automation keeps making progress even when one provider becomes unstable

## Recommended First Version

If we optimize for speed and correctness, the first implementation should be:

1. Add `U001AutomationState`
2. Add `automate_u001`
3. Make it choose among:
   - freshness lane
   - holders lane
   - recent RD-001 lane
   - historical partial lane
   - error lane
   - guarded lane
4. Add `scripts/run_u001_automation.sh`
5. Put it on a 30-minute cron

That is enough to make U-001 feel operationally automated without prematurely introducing Celery, Redis coordination, or a more complex scheduler.

## Explicit Recommendation

Build full automation in this order:

1. `U001AutomationState`
2. `automate_u001`
3. `run_u001_automation.sh`
4. cron/systemd scheduling
5. cockpit automation status
6. only then consider Celery beat

Reason:

the repo already has reliable command-level primitives. The missing piece is policy and scheduling, not a distributed task system.

## Success Criteria

We can say U-001 is “fully automated” when:

1. No daily manual command selection is required.
2. Discovery freshness no longer depends on someone remembering to run it.
3. FL-002 catch-up continues automatically until the configured coverage target is reached.
4. RD-001 recent and historical lanes both continue automatically according to policy.
5. Guarded RD-001 work is automated but remains tightly budgeted.
6. Stale state is auto-repaired.
7. Daily snapshots are auto-recorded.
8. The cockpit can explain what automation did and why.

## Proposed Next Task

Implement Phase 1.

Concrete scope:

- add `U001AutomationState`
- add `automate_u001`
- add `scripts/run_u001_automation.sh`
- make the controller choose exactly one action per tick
- log the action and reason

That is the smallest real step that turns U-001 from a set of tools into an actual automated system.
