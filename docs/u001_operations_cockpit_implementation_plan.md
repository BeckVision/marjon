# U-001 Operations Cockpit Implementation Plan

## Purpose

Build a diagnostics UI that makes U-001 ingestion, coverage, and failure state visible at a glance.

The real problem is not missing data alone. The real problem is that operational truth is currently fragmented across:

- `u001_ingestion_health`
- batch command output
- ad hoc shell queries
- `U001PipelineStatus`
- `U001PipelineRun`
- personal memory of which lane is safe to run next

This document turns that problem into a concrete implementation plan.

## What This UI Is For

The cockpit should answer four questions quickly:

1. What is stale right now?
2. Where are coins dropping out of the pipeline?
3. Which recovery lane is worth running next?
4. Did the last action actually improve coverage?

This is an operations surface first, not a marketing dashboard.

## Recommendation

Build this as a server-rendered Django diagnostics surface inside the existing `visualization` app, with lightweight JSON APIs for charts and tables.

Why:

- the project already routes all public UI through `visualization/`
- the current codebase is server-rendered Django, not a separate frontend app
- the fastest path is to reuse the existing app, URL routing, and template structure
- this keeps the implementation aligned with repo conventions and avoids a parallel frontend stack

## Existing Integration Points

- top-level routes: [marjon/urls.py](/home/beck/Desktop/projects/marjon/marjon/urls.py:1)
- current UI app: [visualization/urls.py](/home/beck/Desktop/projects/marjon/visualization/urls.py:1)
- current views: [visualization/views.py](/home/beck/Desktop/projects/marjon/visualization/views.py:1)
- health summary command: [warehouse/management/commands/u001_ingestion_health.py](/home/beck/Desktop/projects/marjon/warehouse/management/commands/u001_ingestion_health.py:1)

## Product Direction

This should be one coherent "operations cockpit" with five panels or sub-pages, not five disconnected mini-products.

### Concepts To Include

1. Operations dashboard
2. Coverage funnel view
3. Queue planner
4. Coin detail page
5. Trend dashboard

## Information Architecture

### Routes

Recommended routes:

- `/ops/u001/`
- `/ops/u001/coverage/`
- `/ops/u001/queues/`
- `/ops/u001/coin/<mint>/`
- `/ops/u001/trends/`
- `/ops/u001/api/summary/`
- `/ops/u001/api/coverage/`
- `/ops/u001/api/queues/`
- `/ops/u001/api/coin/<mint>/`
- `/ops/u001/api/trends/`

### Navigation

Top nav for the cockpit:

- `Overview`
- `Coverage`
- `Queues`
- `Coin`
- `Trends`

## Build Order

Build in this order:

1. Operations dashboard
2. Coverage funnel
3. Queue planner
4. Coin detail page
5. Trends

Reason:

- `Overview` gives immediate value and can replace `u001_ingestion_health` as the main operator view
- `Coverage` explains where the system is failing structurally
- `Queues` makes action-taking explicit
- `Coin detail` helps debug exceptions
- `Trends` is useful only after the summary metrics are already exposed

## Scope By Page

## 1. Operations Dashboard

### Goal

Give a single-screen answer to:

- discovery freshness
- coverage by layer
- dominant failures
- current blockers
- recommended next action

### Widgets

#### A. Freshness strip

Show:

- latest `MigratedCoin.anchor_event`
- latest `MigratedCoin.ingested_at`
- latest `OHLCVCandle.ingested_at`
- latest `HolderSnapshot.ingested_at`
- latest `RawTransaction.ingested_at`

Display:

- absolute UTC timestamp
- age in hours/days
- color state: healthy / stale / critical

#### B. Layer scorecards

One card each for:

- Discovery
- Pool Mapping
- FL-001
- FL-002
- RD-001

Per card:

- total eligible coins
- coins with data
- `window_complete`
- `partial`
- `error`
- percent complete

#### C. Error buckets

For RD-001 and FL-002 especially, show:

- `transport`
- `free_tier_guard`
- `auth`
- `expectation_failed`
- `other`

This should come from the same classification logic now present in `u001_ingestion_health`.

#### D. Recent batch results

Table:

- started_at
- mode
- succeeded
- failed
- elapsed
- notes

#### E. Recommended action panel

Single explicit recommendation, for example:

- `Run safe recent RD-001 steady-state slice`
- `Run historical RD-001 partial Helius slice`
- `Discovery is stale; refresh discovery first`
- `Pool mapping is the current bottleneck`

This should be rule-based at first, not ML.

### Backend Data Needed

- `MigratedCoin`
- `PoolMapping`
- `U001PipelineStatus`
- `PipelineBatchRun`
- `OHLCVCandle`
- `HolderSnapshot`
- `RawTransaction`

### First Acceptance Criteria

- one page load shows system freshness and status counts without shell commands
- RD-001 current error buckets are visible
- operator can tell in under 10 seconds whether to run discovery, recent RD-001, or historical RD-001

## 2. Coverage Funnel

### Goal

Explain where recent coins fall out of the pipeline.

### Default Slices

Support these presets:

- last `100`
- last `500`
- last `1000`
- last `14 days`
- last `30 days`

### Funnel Stages

Recommended funnel:

1. discovered
2. pool mapped
3. FL-001 has status
4. FL-001 window_complete
5. FL-002 has status
6. FL-002 window_complete
7. RD-001 has status
8. RD-001 window_complete

### Required Breakdowns

For each stage, support:

- absolute count
- percent of discovered slice
- delta from prior stage

### Helpful Visuals

- funnel bars
- side-by-side table
- “top bottleneck” callout

### Why This Matters

This page makes it obvious whether the actual constraint is:

- stale discovery
- missing pool mapping
- FL-002 auth problems
- RD-001 partial backlog

### Acceptance Criteria

- can answer “why do only 72 of the last 1000 coins have pool mappings?”
- can answer “is RD-001 coverage low because of discovery, mapping, or RD-001 itself?”

## 3. Queue Planner

### Goal

Turn the operational lanes into a visible menu of next actions.

### Queues To Display

#### Recent safe Shyft steady-state lane

Show:

- mint
- latest anchor
- new signature count
- has watermark
- last RD-001 status

#### Recent risky bootstrap lane

Show:

- mint
- first-page signature count
- why skipped

#### Historical Helius partial lane

Show:

- mint
- last status
- last run at
- mature age

#### Historical guarded Helius lane

Show:

- mint
- last known guard count
- current status
- last error

#### Error lane

Show:

- mint
- status
- last error bucket
- last run at

### Queue Actions

Start read-only. Do not add “run batch” buttons in v1.

V1 actions:

- copyable command suggestions
- exact command text to run next

Examples:

- `./scripts/run_batch.sh --max-coins 3 --max-new-sigs 1000`
- `./scripts/run_batch_partials_historical.sh --max-coins 3`
- `MARJON_U001_RD001_MAX_FILTERED_SIGNATURES=1400 ./scripts/run_batch_partials_guarded.sh`

### Acceptance Criteria

- operator can see why a queue is empty
- operator can see why a coin is skipped
- operator can choose the safest next lane without shell exploration

## 4. Coin Detail Page

### Goal

Provide full debug context for one coin.

### Sections

#### A. Identity

- mint
- anchor event
- age
- mature or not
- pool mapping

#### B. Layer statuses

For each of:

- discovery
- FL-001
- FL-002
- RD-001

Show:

- status
- watermark
- last_run_at
- last_error

#### C. Run history

Recent `U001PipelineRun` rows:

- layer
- mode
- started_at
- completed_at
- status
- records_loaded
- api_calls
- error_message

#### D. Warehouse counts

- OHLCV row count
- holder row count
- raw transaction row count
- skipped transaction row count if present

#### E. Timeline

Show important moments:

- discovered
- mapped
- first FL-001 load
- first FL-002 load
- first RD-001 load
- most recent run

### Acceptance Criteria

- one page can replace ad hoc shell debugging for a single coin
- last error and current watermark are visible without SQL

## 5. Trends Dashboard

### Goal

Show whether operations are improving over time.

### Metrics To Track Over Time

- `window_complete` count by layer
- `partial` count by layer
- `error` count by layer
- RD-001 `transport` error count
- RD-001 `free_tier_guard` count
- FL-002 `auth` count
- latest ingested timestamps

### Data Strategy

Two options:

#### Option A: derive from `PipelineRun` and current status rows

Fastest to ship, but weak for historical trend accuracy.

#### Option B: add daily snapshot table

Recommended for real trends.

Suggested model:

- `U001OpsSnapshot`
- `snapshot_date`
- `discovered_count`
- `mapped_count`
- `fl001_complete_count`
- `fl002_complete_count`
- `rd001_complete_count`
- `rd001_partial_count`
- `rd001_error_count`
- `rd001_transport_error_count`
- `rd001_guard_error_count`

Populate via a daily management command.

### Acceptance Criteria

- can answer whether RD-001 transport instability is improving week over week
- can answer whether historical Helius slices are shrinking the partial backlog

## Visual Design Direction

This should not look like a generic admin page.

### Style

- bright, high-contrast operational UI
- dense, readable, non-decorative
- strong status colors
- numbers first, prose second

### Suggested palette

- background: warm off-white or pale sand
- panel surface: white
- ink: near-black
- healthy: deep green
- warning: amber
- blocked: rust red
- informational accent: blue-teal

### Typography

Use a more distinctive stack than default system-only.

Suggested:

- headings: `Space Grotesk`, `Sora`, or similar geometric sans
- body: `IBM Plex Sans`, `Public Sans`, or similar
- monospace: `IBM Plex Mono`

### Motion

Only small useful motion:

- counters fade in
- trend cards stagger
- no ornamental animation loops

## Technical Design

## Backend

### App

Use `visualization/` for v1.

### Views

Add views in [visualization/views.py](/home/beck/Desktop/projects/marjon/visualization/views.py:1):

- `u001_ops_overview_view`
- `u001_ops_coverage_view`
- `u001_ops_queues_view`
- `u001_ops_coin_view`
- `u001_ops_trends_view`

### APIs

Add JSON endpoints for:

- summary metrics
- coverage slice
- queue candidates
- coin detail payload
- trend series

Prefer a thin view layer plus dedicated query helpers in a new module:

- `visualization/u001_ops.py`

That module should own the read aggregation logic.

## Query Helpers To Add

Suggested functions:

- `get_u001_ops_summary()`
- `get_u001_coverage_slice(limit=None, days=None)`
- `get_u001_queue_state()`
- `get_u001_coin_detail(mint)`
- `get_u001_ops_trends()`

## Data Snapshot Model

Recommended but not required for v1:

- add `visualization/models.py` or `warehouse/models.py` snapshot model for daily ops aggregates

If added, prefer `warehouse/` because it stores operational truth.

## Frontend Structure

### Templates

Add:

- `visualization/templates/visualization/u001_ops_overview.html`
- `visualization/templates/visualization/u001_ops_coverage.html`
- `visualization/templates/visualization/u001_ops_queues.html`
- `visualization/templates/visualization/u001_ops_coin.html`
- `visualization/templates/visualization/u001_ops_trends.html`

### CSS

Add a dedicated stylesheet, not inline styles copied across templates.

Suggested:

- `visualization/static/visualization/u001-ops.css`

### JS

Keep JS small.

Suggested:

- initial render server-side
- charts and live table filters hydrate from JSON APIs

Suggested static files:

- `visualization/static/visualization/u001-ops.js`
- `visualization/static/visualization/u001-ops-trends.js`

## Metrics Definitions

These definitions must be explicit in code and UI text.

### Discovery coverage

Count of `MigratedCoin` rows in the selected slice.

### Mapping coverage

Count of distinct `coin_id` values in `PoolMapping` intersecting the selected slice.

### FL-001 complete

`U001PipelineStatus(layer_id='FL-001', status='window_complete')`

### FL-002 complete

`U001PipelineStatus(layer_id='FL-002', status='window_complete')`

### RD-001 complete

`U001PipelineStatus(layer_id='RD-001', status='window_complete')`

### RD-001 guarded

`last_error` contains `exceeds free-tier guard`

### RD-001 transport

Current error bucket equals `transport`

## Recommended v1 Deliverable

Ship this first:

1. `/ops/u001/` overview page
2. `/ops/u001/coverage/` funnel page
3. `/ops/u001/queues/` planner page

That is enough to replace most shell-based operational decision-making.

Hold these for v2:

- coin detail page
- trends page
- snapshot model
- action buttons

## Phased Implementation

## Phase 1: Read Model

Deliver:

- query helpers
- backend summary data
- no UI yet beyond a crude template

Acceptance:

- all summary metrics available in one Python call

## Phase 2: Overview UI

Deliver:

- overview page
- scorecards
- error buckets
- recent batches
- recommendation panel

Acceptance:

- operator can replace `u001_ingestion_health` for daily use

## Phase 3: Coverage Funnel

Deliver:

- recent-slice funnel
- last 1000 / last 14 days presets

Acceptance:

- operator can identify the main bottleneck for recent coins

## Phase 4: Queue Planner

Deliver:

- safe recent Shyft lane
- risky bootstrap lane
- historical partial lane
- guarded lane
- command suggestions

Acceptance:

- operator can choose next command without shell exploration

## Phase 5: Coin Detail

Deliver:

- one-coin diagnostics page

## Phase 6: Trends

Deliver:

- daily snapshot pipeline
- trend charts

## Risks

### Risk 1: Slow aggregate queries

Mitigation:

- pre-aggregate where possible
- use `values_list`, `annotate`, and indexed fields
- add daily snapshot model for trend pages

### Risk 2: Ambiguous definitions

Mitigation:

- explicitly define every metric in code and help text
- do not mix “has any rows” with “window_complete”

### Risk 3: UI becomes a second source of truth

Mitigation:

- UI must read from the same query logic as command summaries
- reuse classification logic from `u001_ingestion_health`

### Risk 4: Too many pages too early

Mitigation:

- ship Overview first
- prove utility before adding Trend infrastructure

## Acceptance Checklist

- [ ] Overview page loads in under 2 seconds locally
- [ ] Coverage page supports `last 1000` and `last 14 days`
- [ ] Queue planner shows safe recent, risky bootstrap, historical partial, guarded lanes
- [ ] RD-001 transport and free-tier-guard buckets are visible in UI
- [ ] Operator can identify next recommended command without using shell
- [ ] Coin detail page replaces ad hoc SQL for one-coin debugging
- [ ] Trend page shows whether RD-001 backlog is shrinking over time

## Suggested First Implementation Prompt

If this plan is used in a later coding session, start with:

> Build Phase 1 and Phase 2 of `docs/u001_operations_cockpit_implementation_plan.md`: add backend read helpers for U-001 ops summary and ship `/ops/u001/` in the existing `visualization` app with freshness cards, layer scorecards, RD-001 error buckets, recent batch table, and a simple rule-based recommended action panel.

