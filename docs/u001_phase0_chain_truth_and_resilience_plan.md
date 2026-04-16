# U-001 Phase 0 Plan

## Purpose

Complete the pre-research foundation for U-001 before any serious signal work starts.

This phase is not about finding trades.
It is about making sure the system is:

- resilient after interruption or reboot
- auditable without guesswork
- provably close to chain truth for raw trades
- able to derive its own candles from canonical trades

Phase 0 ends when U-001 can be trusted as an operationally stable and technically defensible research substrate.

## Core Principle

There are two different requirements here, and they must both be satisfied:

1. The system must keep running or recover cleanly after interruption.
2. The data must be validated against chain-native truth, not just against the APIs we currently use.

One without the other is not enough.

- A resilient pipeline that ingests provider drift is still wrong.
- A chain-accurate pipeline that frequently stalls or corrupts state is still unusable.

## Important Clarification

`RD-001` can be compared to chain-native truth.

`FL-001` cannot be compared to an on-chain candle object, because OHLCV is not stored on-chain as a first-class entity.

So for Phase 0:

- `RD-001 truth` means canonical swap history reconstructed from chain data
- `FL-001 truth` means candles derived by us from canonical RD-001 trades

That means the target is not:

`match GeckoTerminal`

It is:

`build our own canonical trade tape, then build our own candles from that tape`

Provider parity can remain a useful secondary check, but it is not the main truth standard.

## What Phase 0 Means

For this repo, Phase 0 should mean all of the following:

1. Local automation resumes safely after reboot without manual babysitting.
2. Interrupted runs do not leave stale state that distorts the scheduler.
3. RD-001 can be sampled against direct chain-derived truth.
4. FL-001 can be recomputed from canonical RD-001 and compared to stored candles.
5. Mismatches are stored and classified, not observed informally in logs.
6. Acceptance gates are explicit enough that Phase 1 cannot start early by vibe.

## Current State

The repo already has meaningful groundwork:

- automated U-001 lane selection
- cron-based local scheduling
- stale-state repair
- daily ops snapshots
- sampled provider-source audits
- cockpit visibility for automation, coverage, and spin risk
- improved reboot recovery for the local machine

This is good operational progress, but it is still not Phase 0 complete.

What is still missing:

- direct chain-truth RD-001 reconciliation
- self-derived FL-001 candles from canonical trades
- persistent reconciliation artifacts
- explicit Phase 0 go/no-go gates

## Phase 0 Deliverables

### Deliverable 1: Reboot-Resilient Local Automation

The local machine must recover cleanly after power loss or restart.

Minimum outcome:

- Postgres comes back automatically
- the reboot wrapper waits for dependencies
- migrations run safely
- one immediate automation tick runs after boot
- ongoing cron-based automation continues
- recovery writes a clear log artifact

Already partially implemented:

- `run_u001_automation.sh`
- stale-state repair
- reboot wrapper
- `@reboot` cron entry
- DB restart policy

Still needed:

- explicit persisted reboot-recovery result
- cockpit visibility for last reboot recovery
- audit rule for failed post-boot recovery

### Deliverable 2: Canonical RD-001 Chain Audit

Add a direct audit path that does not depend on Shyft or Helius as truth.

This requires:

- direct Solana RPC signature fetch for sampled pools/windows
- direct transaction fetch for those signatures
- our own parser/decoder that identifies relevant swaps for the target pool
- canonical RD-001 record generation from chain data
- warehouse comparison against stored `RawTransaction`

Comparison output must detect at least:

- missing transaction in warehouse
- extra transaction in warehouse
- wrong side
- wrong pool
- wrong timestamp bucket
- wrong token amount
- wrong SOL amount

This does not require replacing the ingestion provider yet.
It requires building an independent truth path for validation.

### Deliverable 3: Canonical FL-001 from Canonical RD-001

Build a candle generator from canonical raw trades.

This requires:

- deterministic interval bucketing
- open/high/low/close from canonical trade price
- volume derived from canonical amounts
- optional trade count per interval

Then compare:

- stored `OHLCVCandle`
- provider candle
- self-derived candle from canonical RD-001

The self-derived candle should become the preferred truth reference.

### Deliverable 4: Persisted Reconciliation Artifacts

Do not leave reconciliation as transient command output.

Add persisted models for:

- RD-001 chain audit runs
- FL-001 derivation audit runs
- reboot recovery runs

Each run should store:

- started / completed timestamps
- sample scope
- status
- mismatch counts
- summarized findings
- a small number of representative mismatch examples

This allows:

- cockpit history
- trend tracking
- acceptance checks

### Deliverable 5: Phase 0 Acceptance Gates

Define explicit stop/go thresholds before Phase 1 starts.

Example gates:

1. reboot recovery succeeds consistently on local restart
2. no critical stale-state accumulation after reboot or interrupted runs
3. sampled RD-001 chain parity above a configured threshold
4. zero unexplained side/pool mismatches in sampled RD-001
5. sampled self-derived FL-001 parity above a configured threshold
6. all remaining mismatch classes are known and documented

Without this gate, “move to research” becomes subjective.

## Proposed Architecture

## 1. Reboot Recovery State

Add a small persisted model such as:

- `U001BootRecoveryRun`

Suggested fields:

- `started_at`
- `completed_at`
- `status`
- `db_reachable`
- `migrations_ok`
- `automation_tick_started`
- `automation_tick_status`
- `notes`

Reason:

Phase 0 should not rely on reading shell logs from `logs/`.

## 2. RD-001 Chain Audit Module

Add a dedicated module such as:

- `pipeline/audits/rd001_chain_truth.py`

Responsibilities:

- fetch canonical signatures from direct RPC
- fetch canonical transactions
- decode relevant swap instructions / balance movements
- normalize to the same shape as `RawTransaction`
- compare warehouse vs canonical truth

This module should not update warehouse rows.
It should only produce an audit result.

## 3. FL-001 Derivation Module

Add a dedicated module such as:

- `pipeline/audits/fl001_chain_derived.py`

Responsibilities:

- load canonical RD-001 trades for a window
- bucket them into candles
- produce deterministic OHLCV rows
- compare to stored `OHLCVCandle`

Again, this is an audit/derivation path first, not a replacement writer yet.

## 4. Management Commands

Add commands like:

- `audit_u001_rd001_chain`
- `audit_u001_fl001_derived`
- `recover_u001_after_reboot`

Recommended behavior:

- small default sample size
- explicit JSON/persisted summary
- human-readable summary to stdout
- non-zero exit on critical mismatches when requested

## 5. Cockpit Surfaces

Extend `/ops/u001/` with:

- latest reboot recovery result
- latest RD-001 chain-truth audit result
- latest FL-001 chain-derived audit result

Add dedicated pages if needed later, but Phase 0 does not require a large UI expansion first.

## Source of Truth Strategy

### RD-001

Primary truth:

- direct Solana RPC + our own parser

Secondary checks:

- Shyft parity
- Helius parity

Reason:

providers can be used for transport and convenience, but they should not define truth for this phase.

### FL-001

Primary truth:

- our own candles derived from canonical RD-001

Secondary checks:

- GeckoTerminal parity

Reason:

OHLCV is derived data, so the most defensible source is our own deterministic derivation from canonical raw trades.

## Mismatch Taxonomy

Do not collapse all drift into one number.

Use explicit classes.

### RD-001 mismatch classes

- `missing_tx`
- `extra_tx`
- `wrong_pool`
- `wrong_side`
- `amount_token_delta`
- `amount_sol_delta`
- `timestamp_delta`
- `duplicate_tx`
- `decode_failed`

### FL-001 mismatch classes

- `missing_interval`
- `extra_interval`
- `open_mismatch`
- `high_mismatch`
- `low_mismatch`
- `close_mismatch`
- `volume_mismatch`
- `trade_count_mismatch`
- `bucket_alignment_mismatch`

This matters because Phase 0 success depends on the type of drift, not just the count.

## Recommended Implementation Order

### Step 1: Finish local resilience visibility

Build:

- persisted reboot recovery run model
- management command wrapper for boot recovery
- cockpit surface for last reboot recovery
- audit check for failed recovery

Why first:

You already have most of the local resilience pieces.
This closes the loop and makes the behavior inspectable.

### Step 2: Build canonical RD-001 truth path

Build:

- direct RPC sample fetch
- canonical trade decoder
- comparison engine
- persisted audit run

Why second:

RD-001 is the rawest and most important truth layer.
FL-001 should depend on this.

### Step 3: Build chain-derived FL-001 audit

Build:

- candle derivation from canonical RD-001
- comparison to warehouse FL-001
- persisted audit run

Why third:

This is the correct way to define candle truth.

### Step 4: Add Phase 0 dashboards and alerts

Build:

- overview panels
- daily scheduled low-budget audits
- thresholds in `audit_u001`

Why fourth:

By this point there is meaningful data to visualize.

### Step 5: Freeze acceptance gates

Only after the first full audit loop exists should thresholds be frozen.

## Suggested Acceptance Criteria

These numbers are examples and should be tuned after first live measurements.

### Operational

- reboot recovery succeeds on repeated manual restart tests
- no stale `started` / `in_progress` buildup survives more than one automation tick
- first post-boot automation tick completes successfully after recovery

### RD-001 Truth

- sampled chain-parity rate >= `99.5%` by transaction presence
- zero unexplained `wrong_side` mismatches
- zero unexplained `wrong_pool` mismatches
- token/SOL amount mismatches are either below tolerance or fully classified

### FL-001 Truth

- sampled interval parity >= `99%`
- no unexplained `close` mismatches above tolerance
- no systematic bucket-alignment mismatch

### Reporting

- latest reboot recovery visible in cockpit
- latest RD-001 chain audit visible in cockpit
- latest FL-001 derivation audit visible in cockpit
- `audit_u001` fails when Phase 0 blockers are active

## What Phase 0 Does Not Require

To keep scope controlled, Phase 0 does not require:

- a new execution engine
- model training
- strategy backtesting
- replacing all provider-backed ingestion immediately
- full historical chain replay for every coin

It requires sampled, defensible truth verification first.

## Risks

### 1. Solana transaction decoding complexity

Direct chain parsing is harder than provider-normalized parsing.
This is real complexity, not optional polish.

### 2. RPC cost and rate limits

Chain-truth audits may be expensive if sampling is too broad.
Keep default audits narrow.

### 3. Trade classification ambiguity

Some transactions may still be messy or multi-event.
Those should become explicit mismatch classes, not silent exclusions.

### 4. Local machine limitations

Even with reboot recovery, a local PC is not an always-on host.
Phase 0 can still succeed, but missed wall-clock time remains a real constraint.

## Recommendation

Treat Phase 0 as complete only when:

- the machine can recover itself after reboot
- RD-001 is audited against direct chain truth
- FL-001 is audited against self-derived candles from canonical trades
- drift is classified and visible
- acceptance gates are written down and passing

Until then, Phase 1 signal research should be considered premature.

## Recommended First Build

If implementation starts immediately, the first ticket should be:

`Persist reboot recovery runs and surface them in /ops/u001/`

The second should be:

`Build sampled direct-RPC RD-001 chain audit`

That sequence keeps the work grounded:

- first prove the system survives interruption
- then prove the data is true
