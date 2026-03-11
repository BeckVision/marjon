# Pipeline Implementation Guide

A dataset-agnostic reference for implementing data pipelines that feed a quantitative trading warehouse. This document describes established patterns from the quantitative trading paradigm, decision points with all available options, and tradeoffs. It does not prescribe choices — those belong in a separate **Pipeline Implementation Record** per dataset.

**Relationship to other documents:**

| Document | Role |
|---|---|
| **Data Specification** | Defines what the warehouse should contain (the contract) |
| **Warehouse Implementation Guide** | Defines how the contract is stored (models, QuerySets, constraints) |
| **Pipeline Implementation Guide** (this document) | Defines how to fulfill the contract (extract, transform, load) |
| **Pipeline Implementation Record** (per dataset) | Documents specific pipeline choices for a dataset |

The data specification defines the ideal contract. The pipeline maps between what external sources actually return and what the contract demands. Gaps discovered during pipeline work may require revising the data specification.

Each universe should have at least two Pipeline Implementation Records: one for the universe population pipeline, and one for each feature layer pipeline. Some feature layers may share a record if they share a source and configuration.

---

## Part 1: Foundational Concepts

Three core concepts from the quantitative trading paradigm inform every pipeline pattern in this guide.

### ETL — Extract, Transform, Load

The foundational pipeline pattern. Three phases, each with a distinct contract:

| Phase | Contract |
|---|---|
| **Extract** | Pull raw data from external sources. Emit the raw response with enough metadata to reproduce the request. Do not interpret the data. |
| **Transform** | Convert the raw, source-specific representation into the warehouse's canonical format. Normalize timestamps, map fields, cast types, apply semantic decisions. |
| **Load** | Write transformed data into the warehouse. Enforce uniqueness, apply quality constraints, make data available to the data service. |

The order of Transform and Load can vary (ETL vs ELT). Which pattern to choose depends on which transformations are semantic vs computational (see below). The choice is a decision point (PDP2).

### Two Types of Transformations

The quantitative trading paradigm distinguishes two categories of transformation. They are fundamentally different things.

**Semantic transformations** change what the data claims to represent. They define the information boundary of the dataset.

| Category | Example |
|---|---|
| Timestamp interpretation | Does the source's timestamp mean interval-start or interval-end? |
| Field denomination | Is the source's `volume` field in USD, tokens, or contracts? |
| Timezone normalization | Does the source send UTC, local time, or Unix epoch? |
| Identifier resolution | The source uses one identifier for the asset; the warehouse uses another. |
| Pair direction | The source returns token A / token B ratio; the warehouse wants token B / USD price. |

**If a semantic transformation is wrong, the system has a causal violation built into it.** The data claims to represent one thing when it represents another.

**Computational transformations** take an already-defined representation and compute derived quantities. They do not change what the underlying data means.

| Example | Why computational |
|---|---|
| 20-candle moving average | Doesn't change what `close_price` means |
| Return from close prices | Math on an established field |
| Volume aggregation over a window | Sums an already-defined quantity |

**The decision rule:** Does this transformation change the information boundary of the dataset? If yes → semantic (requires contracts, determinism, versioning). If no → computational (placement decision based on efficiency).

**Why this matters for pipeline design:** Semantic transformations must be centralized, versioned, and tested. They are part of the dataset definition, not incidental processing steps. Computational transformations can happen anywhere — in the pipeline, in the warehouse, in the data service, or on-the-fly. The pipeline's core responsibility is semantic transformations.

### Three Timelines

Every piece of data in a quantitative trading system has three distinct timestamps:

| Timeline | Also called | Meaning |
|---|---|---|
| **Observation time** | Event time | When the market produced the observable |
| **Processing time** | Ingestion time | When the pipeline received and stored the data |
| **Decision time** | Simulation time | When a strategy acts on the data |

**How they relate to the existing architecture:**

| Relationship | Handled by |
|---|---|
| Observation time ↔ Decision time | PIT enforcement in the data service (`.as_of()`) |
| Observation time ↔ Processing time | The pipeline — this guide |

The pipeline's job is to correctly assign observation timestamps to each data point so that the warehouse's PIT enforcement works correctly. When fetching historical data from an API after the fact, the processing time (when the API call runs) is completely different from the observation time (when the market events actually occurred). The pipeline must assign observation timestamps, not processing timestamps.

---

## Part 2: Architecture

### Two Categories of Pipelines

Every universe requires two categories of pipelines:

| Category | Purpose | Writes to | Example |
|---|---|---|---|
| **Universe population pipeline** | Discovers assets and creates universe table rows | Universe table (master data) | Polling an API for newly listed tokens, querying an index for current constituents |
| **Feature layer pipeline(s)** | Fetches time-series data for known assets | Feature layer tables (time series facts) | Fetching OHLCV candles, holder snapshots, funding rates |

**Why this distinction matters:**

Feature layer pipelines assume assets exist. They query watermarks per asset, fetch time ranges, write to append-only tables. They cannot run until the universe table has rows.

The universe population pipeline creates those rows. It is the first link in the chain. Without it, feature layer pipelines have nothing to fetch for.

The two categories have different characteristics. When the warehouse follows the append-only for time series, updates for master data convention (Warehouse Implementation Guide WDP5 Option B): universe pipelines use upsert (master data allows updates), feature layer pipelines use delete-write or skip-existing (time series is append-only). Universe pipelines track a single global watermark (the newest asset's anchor event). Feature layer pipelines track watermarks per asset (the latest data point for each asset).

**The DAG relationship:**

```
Universe population → Dimension table population (if needed) → Feature layer pipeline(s)
```

Discovery must complete before feature layers run. If the universe has dimension tables (e.g., source-specific identifier mappings), those must be populated between discovery and feature layer fetching.

This ordering applies regardless of orchestration level — whether you chain management commands manually, use Celery task chains, or define an Airflow DAG.

**Universe population patterns vary by universe type:**

| Universe type | Discovery pattern | Example |
|---|---|---|
| Event-driven | Poll a source for new events, create rows as events occur | New token listings, new market events, new protocol launches |
| Calendar-driven | Evaluate inclusion criteria on a schedule, add/update membership | Re-evaluate market cap rankings monthly, update index constituents quarterly |

Event-driven universes discover assets when they appear. Calendar-driven universes re-evaluate membership periodically — assets can enter and leave (see membership_end in the Warehouse Implementation Guide). Not all universes use membership_end — universes with permanent membership leave it null for every asset.

### Established Pipeline Layers

The industry describes a pipeline as a **layered architecture governed by explicit contracts.** Data flows in one direction — from source to warehouse — while information about success, failure, and progress flows back to inform the next run.

The layers below apply to both universe population pipelines and feature layer pipelines. The same architecture (source connector → conformance → load → reconciliation) applies to both categories. The differences are in which table is written to, which idempotency mechanism is used, and how the watermark works — not in the fundamental architecture.

#### Source Connector

The outermost layer — the only part of the pipeline that touches the external world. Its contract: **emit raw responses with source metadata, explicit time conventions, and enough information to reproduce the request.**

The source connector knows everything about the API — endpoint URLs, authentication, rate limits, pagination, response format, error codes. Nothing downstream knows any of that. The industry calls this the **anti-corruption layer** (from domain-driven design) — it prevents the external API's conventions from leaking into the internal domain.

The source connector does NOT transform data. It returns exactly what the API gave it. Transformation belongs in the canonicalization layer. This separation makes each layer independently testable.

#### Staging Area (Bronze Layer)

Where raw data lands before any semantic transformation. In the medallion architecture (bronze → silver → gold), this is the bronze layer. Its contract: **preserve raw information, enforce uniqueness, and preserve provenance.**

The core principle is **raw fidelity** — whatever the API returned is stored exactly as-is. No field renaming, no type casting, no timestamp conversion.

Two reasons for a staging area: **re-transformability** (fix a conformance bug and re-process raw data without re-fetching from the API) and **forensics** (distinguish between a vendor problem and a pipeline bug by examining raw responses).

Not every system needs a staging area. It doubles storage and adds complexity. Whether to include one depends on how costly re-fetching is and how mature the conformance logic is. This is part of the ETL vs ELT decision (PDP2).

#### Canonicalization Layer (Semantic Conformance)

The most important and most dangerous layer. Where **semantic transformations** happen.

The term **canonicalization** comes from the concept of a **canonical form** — a single standard representation that all variants are reduced to. Related to **Kimball's conformed dimensions** — the principle that every source must be mapped to shared definitions so downstream consumers never deal with source-specific quirks.

The canonicalization layer takes raw API responses and produces records matching the warehouse schema exactly. It handles four categories of semantic conformance:

| Category | What it does |
|---|---|
| **Time conformance** | Convert every source's timestamp to the warehouse's canonical format (timezone, interval convention) |
| **Type conformance** | Cast every field to the warehouse's type (float → Decimal for prices is non-negotiable in quant systems) |
| **Symbol conformance** | Map source identifiers to warehouse identifiers (Kimball's conformed dimensions) |
| **Value conformance** | Normalize values to the warehouse's denomination (e.g. convert native-token-denominated prices to USD) |

Critical property: the canonicalization layer should be a **pure function.** Raw data in, canonical records out. No side effects, no API calls, no database writes. This makes it independently testable — feed saved raw responses in, verify canonical output. The industry calls these **conformance tests** and considers them the single highest-value tests in a pipeline.

#### Orchestration

Manages execution: scheduling, dependency ordering, retries, parallelism. The industry standard concept is the **DAG (Directed Acyclic Graph)** — a graph of tasks where edges represent dependencies, with no circular dependencies.

A single pipeline run is a small DAG: extract depends on nothing, conformance depends on extract, load depends on conformance, reconciliation depends on load. At a higher level, multiple pipelines form a larger DAG. The cross-pipeline DAG ordering is: universe population → dimension tables (if needed) → feature layers. This ordering is a paradigm requirement, not an implementation detail. A feature layer pipeline that runs before the universe is populated will either find no assets to process or miss newly discovered assets.

The industry recognizes three levels of orchestration complexity:

| Level | Mechanism | When to use |
|---|---|---|
| **Level 1** | Django management commands, triggered manually or by cron | Solo projects, single pipeline, prototyping |
| **Level 2** | Celery task chains with periodic scheduling (Celery beat) | Multiple tasks with dependencies, need retries, moderate scale |
| **Level 3** | Airflow or equivalent, full DAG orchestration with UI and alerting | Multiple pipelines, complex dependencies, operational visibility |

Industry principle: **the orchestration level should match the system's complexity, not its ambition.** Starting at Level 1 and moving up when pain emerges is standard practice.

Regardless of orchestration level, the industry requires **idempotent task design** — every task must be safe to re-run. The orchestrator relies on this for retries and manual re-triggers.

#### Extract Strategy

Two fundamental patterns for deciding what data to request from the source on each run:

**Full load** — every run fetches the entire dataset from scratch. Simple, correct, wasteful. Doesn't scale with universe size.

**Incremental load** — each run fetches only what's new since the last run, tracked by a **high-water mark** (the latest successfully ingested timestamp per asset). Efficient, scales well, requires watermark tracking.

A common variant is **windowed incremental** — incremental with an overlap margin, where the pipeline re-fetches a small window of already-loaded data as a safety net. The idempotent write mechanism handles the resulting duplicates.

**Backfill** (loading a new asset's full history) is always a full load for that specific asset, regardless of the steady-state extract strategy. See "Code Path Unification" below.

#### Idempotent Write

The established load-stage pattern. Three mechanisms:

**Upsert** (INSERT ... ON CONFLICT DO UPDATE) — insert new rows, update existing ones. Most common. Overwrites previous values.

**Delete-write** — delete all rows for the target scope, insert fresh, in one transaction. Naturally compatible with append-only semantics.

**Skip-existing** (INSERT ... ON CONFLICT DO NOTHING) — only insert rows that don't already exist. Strictest append-only, but cannot self-correct.

The choice interacts with the warehouse's append-only convention. See PDP3.

#### Reconciliation (Completeness Check)

Post-load verification. Compares source expectations against warehouse actuals. The industry distinguishes reconciliation from quality constraints — quality gates catch **corrupt data**, reconciliation catches **missing data that isn't corrupt.**

Must account for **legitimate sparsity** — a short-lived asset with few observations is not a reconciliation failure. Results are logged as **informational reports**, not pass/fail gates.

#### Data Lineage (Provenance Metadata)

Tracking the full path from source to warehouse. Three levels from the industry:

**Run-level** — log each pipeline execution (timing, counts, status). The minimum.

**Row-level** — add an `ingested_at` field to warehouse rows recording when the pipeline wrote them. The most common industry practice.

**Full provenance** — archive raw API responses with traceability from warehouse rows back to the exact response. Maximum forensic capability.

Industry principle: **lineage is cheap to add at build time and expensive to add retroactively.**

### How Layers Connect

The forward data flow for one asset, one feature layer:

```
Source Connector → (raw response)
    → Staging Area (optional) → (raw response, preserved)
        → Canonicalization Layer → (canonical records)
            → Idempotent Write → (rows in warehouse)
                → Reconciliation → (report logged)
```

The feedback flow:

```
Warehouse state (watermark) → Extract Strategy → Source Connector
Reconciliation report → Orchestrator (retry decisions, alerting)
```

Two important connections:

**Canonicalization is decoupled from extraction.** The source connector doesn't know the warehouse schema. The canonicalization layer doesn't know which API the data came from. They communicate through the raw response. This is what makes it possible to swap or add sources without changing load logic.

**Orchestration wraps everything.** The orchestrator doesn't do data work — it manages execution of all other layers. It decides when to run, handles retries, tracks success/failure, and produces the final run report. The pattern is the same at every orchestration level — only the infrastructure changes.

The cross-pipeline DAG for a complete universe:

```
Universe population pipeline:
    Source → Conformance → Upsert into universe table → Reconciliation
        ↓ (new assets discovered)
    Dimension table pipeline (if needed):
        Source → Conformance → Upsert into dimension table → Reconciliation
            ↓ (mappings available)
    Feature layer pipeline(s):
        Source → Conformance → Delete-write into feature layer table → Reconciliation
```

### Source Connector and Conformance Relationship

The industry uses the **adapter pattern** for this relationship. Each source connector is an **extractor adapter** — knows how to talk to one API. Each conformance mapping is a **transform adapter** — knows how to convert one source's raw response into the canonical schema.

These adapters are **paired.** A Source A connector produces Source A-shaped data. Only the Source A conformance mapping interprets that shape. They can be structured as:

| Structure | Description | Tradeoff |
|---|---|---|
| **Separate modules** | Connector is one module, conformance is another, orchestrator calls them in sequence | More testable — conformance can be tested independently with saved raw responses |
| **Single source adapter** | One module per source handles both extraction and conformance, exposes a method returning canonical records | Simpler — fewer moving parts, one place to look per source |

In both structures, downstream stages (validate, load, reconcile) are **source-agnostic** — they receive canonical records and don't know which source produced them.

### Code Path Unification

The industry principle: **one pipeline implementation, not two.** The difference between a daily run and a backfill is the input parameters, not the code.

| Scenario | What happens | Parameters |
|---|---|---|
| **Bootstrap** | New asset, zero data in warehouse. Watermark returns nothing. Pipeline fetches the full observation window. | No explicit params — pipeline detects empty watermark and does full window |
| **Steady-state** | Asset was fetched before. Watermark returns the latest timestamp. Pipeline fetches from watermark forward. | No explicit params — pipeline derives range from watermark |
| **Re-fill** | Bad data needs replacing. Fixed a bug, want to re-run for a specific asset and time range. | Explicit asset and time range params override watermark |

All three scenarios use the same source connector, conformance, load, and reconciliation. The only difference is how the time range is determined.

**Open-ended observation windows:** When a universe has no defined end (observation window end = None), the pipeline runs indefinitely on schedule:
- **Bootstrap** fetches from observation window start to the current time, not to a fixed end.
- **Steady-state** fetches from the watermark to the current time — identical to bounded windows.
- **Reconciliation** uses count-based checks relative to the fetched range (watermark to current time), not relative to the full observation window. There is no expected total count for an unbounded window.

**Pre-event observation windows (negative t₁):** When a universe defines a negative observation window start (t₁ < 0), the pipeline must collect data from before the anchor event. Since the anchor event is what makes the asset discoverable, pre-event data is always backfilled retroactively — the asset enters the universe after the start of its own observation window. The bootstrap scenario handles this naturally: when a new asset is discovered (anchor event occurs), the pipeline fetches the full observation window including the pre-event range.

The industry warns against the **pipeline divergence** anti-pattern: building a separate backfill script that starts as a quick hack, then the main pipeline evolves and the backfill script doesn't keep up. Eventually they produce different data for the same inputs.

Universe population pipelines also follow code path unification. Bootstrap discovers the full universe (all existing assets). Steady-state discovers only new assets since the last run. The same connector → conformance → loader path is used for both — only the pagination termination condition differs.

### Pipeline and Warehouse Interaction

The industry rule: **the pipeline writes to warehouse models directly. It does not go through the data service.**

The data service is a read-only interface for consumers. The pipeline is a privileged writer on the same side as the warehouse.

Three interaction points:

| Interaction | Direction | Purpose |
|---|---|---|
| **Writing data** | Pipeline → warehouse models | Load canonical records. Database constraints (CHECK, unique_together) act as the final safety net. |
| **Reading watermarks** | Pipeline ← warehouse models | Derive high-water mark per asset. Direct model query, not through data service. |
| **Reading dimension data** | Pipeline ← dimension tables | Look up source mappings, universe membership. Direct model access. |

### Dimension Tables

The pipeline may require supporting tables that don't fit the three paradigm categories (universe, feature layer, reference table). These are **dimension tables** — lookup data that serves the pipeline, not the consumer.

| Common dimension table | Purpose |
|---|---|
| **Source mapping** | Maps assets to API-specific identifiers (e.g. warehouse asset ID → source-specific query ID per source) |
| **Pipeline run log** | Records each pipeline execution (timing, counts, status) for run-level provenance |

Dimension tables are not part of the data specification. They are pipeline infrastructure. Where they live in Django is a decision point (PDP11).

---

## Part 3: Pipeline Properties

Five properties that a well-built pipeline must have. These are correctness requirements, not optional features.

### Idempotency

**Running the same pipeline with the same inputs produces the same result, no matter how many times it runs.** If the pipeline crashes halfway and is re-run, no duplicate rows, no inflated volumes, no corrupted data.

The single most important pipeline property. It turns retries from a risk into a feature.

In quantitative trading specifically: duplicate price observations inflate volume calculations. Duplicate snapshots corrupt change-over-time computations. One duplicate row can silently distort every backtest that touches it.

### Backfill

**The ability to retroactively load historical data through the same code path that handles daily ingestion.** A well-designed pipeline accepts explicit time range parameters rather than hard-coding "today."

The rule: same code path for daily refresh and historical backfill, just different time windows. If the backfill code path differs from the daily code path, the two will eventually diverge — the pipeline divergence anti-pattern.

### Watermark (High-Water Mark)

**Tracking the latest data point successfully ingested for each asset.** Enables resuming after failure, incremental fetching, and the discovery of what work needs doing.

Can be stored in a dedicated table, derived from the warehouse data itself, or not tracked at all (full load every time). Each approach has tradeoffs (PDP4).

### Reconciliation

**Confirming that what was received from the source matches what was expected.** Not a quality gate (that's the warehouse's job) — a pipeline health check.

Catches problems quality gates cannot: partial API responses, silent field drops, source-target drift. Must account for legitimate sparsity — assets with few observations are not reconciliation failures.

### Provenance

**Tracking where each piece of data came from.** At minimum: which source, when the pipeline ran. Enables debugging — tracing unexpected backtest results back to specific pipeline runs.

Industry principle: cheap to add at build time, expensive to add retroactively.

---

## Part 4: Decision Points

Each decision point lists all options with tradeoffs. Choices are made per dataset and recorded in the Pipeline Implementation Record.

### PDP1: Extract Strategy

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: Full load** | Every run fetches the entire observation window for every asset | Simplest. No watermark state. Always correct. | Wastes API calls. Hits rate limits fast. Doesn't scale. |
| **B: Incremental load** | Each run fetches only data newer than the high-water mark per asset | Efficient. Scales to large universes. Respects rate limits. | Requires watermark tracking. Watermark bugs can cause missed data. |
| **C: Windowed incremental** | Incremental with an overlap window. Re-fetches a small amount of already-loaded data as safety margin. Idempotent write handles duplicates. | Safety margin against edge cases. Best of incremental with less risk. | Slightly more API calls than pure incremental. Requires idempotent writes. |

Backfill (loading a new asset's full history) is always a full load for that asset, regardless of steady-state strategy.

### PDP2: ETL vs ELT

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: ETL (transform before load)** | Canonicalization happens in Python before writing to warehouse. No staging tables. | Warehouse only contains clean data. Simpler schema. Quality gates validate final-form data. | Raw data is gone after the run. Can't re-transform without re-fetching. |
| **B: ELT (load raw, then transform)** | Raw data loaded into staging tables, then transformed into warehouse tables. | Raw data preserved. Can re-transform without re-fetching. Full forensic capability. | Two sets of tables. More complex. Semantic decisions pushed downstream. |
| **C: Hybrid (ETL + raw archival)** | Canonicalization in Python, conformed data to warehouse. Raw responses archived separately. | Warehouse stays clean. Raw data available for debugging. | Two write paths. Must manage archive retention. |

General principle: Option A or C for research-stage systems. ELT pays off at scale but adds complexity not justified during experimentation.

### PDP3: Idempotency Mechanism

**Important:** This decision should be made separately per table category, aligned with the warehouse's append-only convention (Warehouse Implementation Guide WDP5).

- **Universe tables** (if updates allowed) → choose based on update semantics
- **Feature layer tables** (if append-only) → choose based on append-only semantics
- **Reference tables** (if append-only) → same consideration as feature layers

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: Upsert** | INSERT ... ON CONFLICT DO UPDATE. If row exists, overwrite. Django: `update_or_create()`. | Simple. One operation for both insert and update. Most common pattern. | Overwrites previous values. Conflicts with strict append-only semantics for time series. |
| **B: Delete-write** | Delete all rows for the target scope, insert fresh, in one database transaction. | Clean slate. Self-correcting on re-run. Compatible with append-only (remove and replace, not modify in place). | Larger transactions. Scope must be defined correctly — too broad deletes other data, too narrow leaves stale data. |
| **C: Skip-existing** | INSERT ... ON CONFLICT DO NOTHING. Only insert genuinely new rows. Django: `bulk_create(ignore_conflicts=True)`. | Strictest append-only. Fastest for incremental runs. | Cannot self-correct. Bad data from a previous run stays forever unless manually deleted. |

**Tradeoff:** Upsert is simplest but least aligned with append-only. Skip-existing is most aligned but can't fix mistakes. Delete-write balances self-correction with append-only compatibility.

This alignment between idempotency mechanism and table category is not a coincidence. It reflects the fundamental difference between master data (identity, metadata that can evolve) and time series facts (immutable observations). Universe tables represent master data — upsert is the natural fit when updates are allowed. Feature layer and reference tables represent time series facts or event facts — delete-write or skip-existing preserve append-only semantics.

### PDP4: Watermark Strategy

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: Derive from warehouse** | Query `MAX(timestamp)` per asset from the feature layer table. No separate tracking state. | Always consistent with actual data. No drift. Zero maintenance. | Requires a query per asset (or one grouped query) before each run. |
| **B: Dedicated tracking table** | Separate table: `(asset, layer, last_timestamp, last_run_time, status)`. Updated after each load. | Fast lookup. Can store operational metadata. | Extra table. Can drift from warehouse if pipeline crashes after load but before tracking update. |
| **C: No watermark (full load)** | Every run fetches the full observation window. No incremental state. | Simplest. No state bugs. | Wastes API calls. Only viable for small datasets or generous rate limits. Incompatible with incremental extract strategy (PDP1 Options B/C). |

**Watermark scope:** Feature layer pipelines track watermarks per asset — each asset has its own latest timestamp. Universe population pipelines track a single global watermark — the newest asset's anchor event. The scope differs because feature layers fetch data for specific assets, while universe pipelines discover assets themselves.

### PDP5: Rate Limit Handling

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: Serial with sleep** | One request at a time, fixed delay between calls. | Simplest. Predictable. Easy to tune. Easy to debug. | Slow for large universes. |
| **B: Async with semaphore** | Concurrent requests limited by a semaphore. Python `asyncio` or threading. | Faster. Better utilization of rate limit budget. | More complex. Concurrent error handling harder. Harder to debug. |
| **C: Queue with rate limiter** | Task queue (Celery, etc.) with rate-limiting middleware or token bucket. | Scales to multiple workers. Built-in retry. Distributes work. | Infrastructure overhead (message broker). Over-engineered for small datasets. |

General principle: start with A, move to B or C when serial processing can't complete within the scheduling window.

### PDP6: Error Handling

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: Fail-fast** | Any error stops the entire pipeline run. | Simple. All-or-nothing. Errors surface immediately. | One bad asset blocks all. Wastes completed work. |
| **B: Skip-and-continue** | Log error, skip failed asset, continue with rest. | One bad asset doesn't block others. Maximizes work per run. | Can silently accumulate skipped assets if not monitored. |
| **C: Retry with backoff, then skip** | Retry with exponential backoff. After N retries, fall back to skip-and-continue. | Handles transient errors. Only skips persistent failures. Industry convention for mature systems with monitoring. | Must set retry limits. Adds latency. Skipped assets can accumulate silently. |
| **D: Retry with backoff, then fail** | Retry with exponential backoff. After N retries, fail the entire run. | Handles transient errors. Persistent failures surface immediately. Nothing silently skipped. | One persistently failing asset blocks entire run after retries exhausted. |

Industry convention: Option C for mature systems with monitoring. Option D for early-stage systems where silent skipping is more dangerous than blocked runs.

### PDP7: Reconciliation Strategy

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: Count-based** | Compare expected record count to actual records loaded. | Simple. Catches gross failures. | Can't detect subtle problems. |
| **B: Boundary-based** | Verify first and last timestamps match expected range. | Catches time range drift and off-by-one errors. | Doesn't catch missing records in the middle. |
| **C: Count + boundary** | Both checks together. | Catches more failure modes. Still simple. The pattern across many assets reveals systemic issues. | Still can't detect value-level corruption — that's the warehouse quality gates' job. |
| **D: None** | No reconciliation. | Zero overhead. | Silent failures accumulate. |

**Important:** Reconciliation must account for legitimate sparsity. Results are informational reports, not pass/fail gates.

### PDP8: Provenance Tracking

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: Run-level logging only** | Log each pipeline execution. No per-row metadata. | Low overhead. No schema changes. | Cannot trace a specific row to a specific run. |
| **B: Row-level ingest timestamp** | Add `ingested_at` field to warehouse models. Plus run-level logging. | Trace rows to runs. Distinguish backfill from daily. Cheap — one column. | Extra column on every row. |
| **C: Full raw response archival** | Store raw API JSON. Trace warehouse rows back to exact responses. Plus run-level logging. | Complete forensic capability. Can re-transform without re-fetching. | Storage grows fast. Must manage retention. May conflict with ETL (PDP2-A) decision. |

Industry principle: lineage is cheap to add at build time and expensive to add retroactively.

### PDP9: Multi-Source Handling

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: Single source** | One API designated as the only source. | Simplest. One connector, one conformance. No mixing risk. | If source goes down or is deprecated, pipeline stops. |
| **B: Primary with fallback** | One source primary. If it fails for an asset, try secondary. | Resilience against source problems. | Two connectors, two conformance mappings. Risk of mixing data from different sources for the same asset. |
| **C: Source per asset** | Different assets may use different sources. Pipeline tracks assignment. | Maximum coverage. Best source per asset. | Most complex. Must track per-asset assignment. Harder to verify consistency. |

General principle: start with A. Add sources when reliability becomes a measured problem. The architecture (source connector decoupled from canonicalization, decoupled from load) makes adding sources an additive change.

### PDP10: Scheduling

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: Manual** | Trigger pipeline runs by hand. Management commands or manual task submission. | Full control. No infrastructure. | No automation. Relies on discipline. |
| **B: Scheduled** | Fixed schedule — cron, Celery beat, or equivalent. | Automated. Predictable. | Fixed schedule may not match data availability. Runs when there's nothing to fetch. |
| **C: Event-driven** | Pipeline triggers on events (e.g. new asset detected). | Timely. No wasted runs. | Requires event detection infrastructure. Must still handle backfill separately. |

These are not mutually exclusive. Scheduled runs for steady-state + manual triggers for backfills is a common combination.

### PDP11: Dimension Table Location

Where pipeline infrastructure tables (source mappings, run logs) live in the Django project.

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: In the warehouse app** | Dimension tables as models in the warehouse app, clearly named as pipeline infrastructure. Not inheriting from paradigm bases. | Simple. One app, one database. Django ORM for everything. | Pipeline infrastructure mixed with paradigm models in the same app namespace. |
| **B: Separate pipeline app** | A dedicated Django app owns dimension tables, source connectors, conformance functions, orchestration logic. Warehouse app owns paradigm models only. | Clean separation. Pipeline doesn't pollute warehouse namespace. | More apps to maintain. Pipeline app must import warehouse models to write data. |

General principle: as the system grows, separate apps are cleaner. For a single dataset with few dimension tables, keeping them in the warehouse app is simpler.

---

## Part 5: Conformance Layer Design

The conformance layer is the pipeline's most important component. It is where semantic transformations are centralized and where the most dangerous bugs hide.

### Conformance Function Contract

Each source has a conformance function (or class) that takes a raw API response and returns records in the canonical warehouse format.

| Property | Description |
|---|---|
| **Input** | Raw API response (dict, list, or bytes) exactly as received |
| **Output** | List of dicts matching the warehouse model's field names and types |
| **Pure function** | No side effects. No API calls. No database writes (except reads from dimension tables like source mappings). Takes data in, returns data out. |
| **Deterministic** | Same input always produces the same output |
| **Documented** | Each semantic decision is explicitly documented (timestamp convention, field mapping, denomination) |

**Why pure and deterministic:** Conformance functions are the most testable part of the pipeline. Save a raw API response to a JSON file, feed it to the conformance function, verify the output. The industry calls these **conformance tests** — the single highest-value tests in a pipeline.

### Field Mapping Table

Each conformance function should have an explicit field mapping table documenting how source fields become warehouse fields. This is part of the dataset's semantic contract, not just code documentation.

| Warehouse field | Source field | Transformation |
|---|---|---|
| (example) | (example) | (example) |
| `timestamp` | `time_open` | Parse ISO 8601 string to UTC datetime |
| `open_price` | `open` | Cast float to Decimal |
| `volume` | `volume` | Cast int to Decimal |

Field mapping tables are dataset-specific and source-specific. They live in the Pipeline Implementation Record.

### Handling Source Differences

When multiple sources can populate the same feature layer, their conformance functions must produce output with identical semantics for the same market event:

1. **Same timestamp for the same candle.** If sources use different conventions, conformance functions normalize to the warehouse's convention.
2. **Same denomination.** If the warehouse stores USD, every conformance function converts to USD regardless of what the source returns.
3. **Same field semantics.** Every source's `volume` must mean the same thing after conformance.

**Values will still differ slightly** between sources for the same candle — different aggregation methods, different data feeds. This is expected. The requirement is that values **mean** the same thing, not that they are numerically identical.

---

## Part 6: Pipeline Failure Modes

The quantitative trading paradigm catalogs specific ways pipelines fail. These are amplified in trading because signals are marginal and backtests are sensitive to subtle errors.

### Temporal Integrity Risk

**The most frequent source of silent contamination.** Three sub-categories:

**Timestamp convention mismatch.** The source labels candles by interval-start, but the pipeline interprets them as interval-end (or vice versa). This shifts every data point by one interval, creating systematic look-ahead bias invisible in the data but destructive to PIT semantics.

**Timezone confusion.** The source sends timestamps in an ambiguous format — no timezone suffix, local time assumed to be UTC, Unix epoch with unclear reference. The conformance layer must resolve this explicitly.

**Format inconsistency.** Different endpoints from the same vendor use different timestamp formats. Each must be handled in the conformance layer.

### Heterogeneity Risk

**Same field name, different meaning across sources.** The most dangerous variant: volume denomination — "volume" in one API is USD, in another it's token units, both called "volume." Mixed without noticing → silent corruption.

This is why the conformance layer explicitly documents what each source field means and how it maps to the canonical format.

### Operational Risk

**Infrastructure failures that produce valid-looking but incomplete data.**

- API rate limits silently truncating responses (HTTP 200 with partial data)
- Network timeouts creating gaps
- Retries creating duplicates (if idempotency is broken)
- Successful API calls returning empty results because the asset was queried before data was available
- Vendor maintenance windows returning cached/stale data

Key insight: operational failures often look like valid empty results. Reconciliation helps distinguish these.

### Data Leakage Risk (Pipeline-Specific)

**Vendor-side data revision.** A source corrects a historical value after the pipeline already ingested it. If the idempotency mechanism updates the old value (upsert), historical data silently changes. If it skips (skip-existing), the correction is ignored. The warehouse's append-only convention is the primary defense. The idempotency mechanism must align with that convention.

---

## Glossary

*Terms specific to warehouse architecture are defined in the Warehouse Implementation Guide glossary.*

**Adapter pattern** — Software engineering pattern where a component converts one interface into another that a client expects. In pipelines, source connectors are extractor adapters (API → raw response) and conformance mappings are transform adapters (raw response → canonical records).

**Anti-corruption layer** — From domain-driven design. A boundary that prevents a foreign system's conventions from leaking into the internal domain. The source connector serves as the anti-corruption layer between external APIs and the pipeline's internal data flow.

**Backfill** — Retroactively loading historical data through the same pipeline that handles daily ingestion. Uses the same code path with different time windows. A core pipeline capability, not an exception path.

**Bootstrap** — The initial load for a new asset that has no data in the warehouse. A full-window fetch using the same code path as steady-state runs.

**Bronze layer** — The staging area in the medallion architecture (bronze → silver → gold). Stores raw, untransformed data.

**Canonical form** — A single standard representation that all variants are reduced to. The warehouse schema is the canonical form. Every source's conformance function produces records matching this form.

**Canonicalization layer** — See conformance layer.

**Code path unification** — The principle that daily runs and backfills should use the same pipeline implementation, differing only in input parameters. Prevents the pipeline divergence anti-pattern.

**Computational transformation** — A transformation that computes derived quantities from already-defined data without changing what the underlying fields mean. A compute-placement decision, not a semantic decision.

**Conformance function** — A pure, deterministic function that takes a raw API response and returns records in the canonical warehouse format. Each source has its own conformance function.

**Conformance layer** — The pipeline component that bridges source-specific formats and the canonical warehouse schema. Where semantic transformations are centralized. Also called the canonicalization layer.

**Conformance test** — A unit test that feeds saved raw API responses to a conformance function and verifies the output. The industry considers these the single highest-value tests in a pipeline.

**Conformed dimensions** — From Kimball's dimensional modeling. The principle that every source must be mapped to shared definitions so downstream consumers never deal with source-specific quirks.

**DAG (Directed Acyclic Graph)** — A graph of tasks where edges represent dependencies, with no circular dependencies. The industry standard concept for representing pipeline execution order.

**Data lineage** — The full path from source to warehouse for every data point. Provenance metadata enables traceability along this path.

**Decision time** — When a strategy commits to an action. The third of three timelines.

**Delete-write** — An idempotency mechanism that deletes all rows for a target scope, then inserts fresh data in one transaction. Compatible with append-only semantics.

**Dimension table** — A supporting lookup table that serves the pipeline, not the consumer. Examples: source mapping (warehouse asset ID → source query key), pipeline run logs.

**ETL (Extract, Transform, Load)** — The foundational pipeline pattern. Transform before load. Contrast with ELT where raw data is loaded first.

**Event time** — See observation time.

**Extract strategy** — The approach for deciding what data to request from the source on each run. Full load vs incremental vs windowed incremental.

**Field mapping table** — An explicit table documenting how each source field becomes a warehouse field, including the transformation applied. Part of the semantic contract.

**Full load** — An extract strategy where every run fetches the entire dataset from scratch.

**Gate check** — Validation from the warehouse's Four Shelves of Logic. Database constraints and model validation that reject corrupt data at write time. The pipeline's last line of defense.

**Heterogeneity risk** — Pipeline failure mode where the same field name has different meaning across sources.

**High-water mark** — See watermark.

**Idempotency** — The property that running the pipeline multiple times with the same inputs produces the same result. The single most important pipeline property.

**Idempotency scope** — The range of data one pipeline run claims ownership over (e.g. one asset + one time range). Delete-write deletes this scope. Skip-existing inserts within this scope. Getting it wrong is a common source of bugs.

**Incremental load** — An extract strategy where each run fetches only data newer than the high-water mark.

**Ingestion time** — See processing time.

**Medallion architecture** — A data architecture pattern with three layers: bronze (raw), silver (conformed), gold (aggregated/derived). The staging area corresponds to bronze, the warehouse to silver.

**Observation time (event time)** — When the market produced the observable. The first of three timelines. See also: "as-of time" in the Warehouse Implementation Guide, which describes the same concept from the warehouse perspective.

**Operational risk** — Pipeline failure mode from infrastructure issues: rate limit truncation, network gaps, partial responses.

**Pipeline category** — The quantitative trading paradigm recognizes two categories: universe population (discovers assets, writes to master data) and feature layer (fetches time-series data for known assets, writes to append-only tables). Both follow the same layered architecture but differ in idempotency mechanism, watermark scope, and ordering.

**Pipeline divergence** — Anti-pattern where a separate backfill script diverges from the main pipeline over time, producing different data for the same inputs.

**Processing time (ingestion time)** — When the pipeline received and stored the data. The second of three timelines.

**Provenance** — Metadata tracking where each piece of data came from. Run-level, row-level, or full raw response archival.

**Pure function** — A function with no side effects that always produces the same output for the same input. Conformance functions should be pure for testability.

**Raw fidelity** — The staging area principle of storing exactly what the API returned with no transformation.

**Reconciliation** — Post-load verification that received data matches expectations. Informational, not pass/fail. Must account for legitimate sparsity.

**Re-fill** — Re-running the pipeline for a specific asset and time range to replace bad data. Uses the same code path as daily runs with explicit parameters.

**Re-transformability** — The ability to re-process raw data through updated conformance logic without re-fetching from the API. Requires a staging area or raw archival.

**Semantic transformation** — A transformation that changes what the data claims to represent. Must be centralized, versioned, and tested. If wrong, introduces causal violations.

**Skip-existing** — An idempotency mechanism that only inserts rows not already present. Strictest append-only but cannot self-correct.

**Source connector** — The outermost pipeline layer that interacts with external APIs. The anti-corruption layer. Returns raw responses without transformation.

**Staging area** — See bronze layer.

**Steady-state** — Normal pipeline operation after the initial bootstrap, where incremental loads fetch only new data.

**Symbol conformance** — Mapping source identifiers to warehouse identifiers. A category of semantic conformance.

**Temporal integrity risk** — The most frequent pipeline failure mode. Timestamp convention mismatch, timezone confusion, format inconsistency.

**Time conformance** — Converting source timestamps to the warehouse's canonical format. A category of semantic conformance.

**Type conformance** — Casting source types to warehouse types (float → Decimal for prices). A category of semantic conformance.

**Universe population pipeline** — The pipeline responsible for discovering assets and creating rows in the universe table. Must run before feature layer pipelines. Uses upsert idempotency when master data allows updates (Warehouse Implementation Guide WDP5 Option B). Has a global watermark (newest asset discovered), not a per-asset watermark.

**Upsert** — An idempotency mechanism that inserts new rows or updates existing ones. Simple but may conflict with append-only semantics.

**Value conformance** — Normalizing values to the warehouse's denomination (e.g. converting to USD). A category of semantic conformance.

**Watermark (high-water mark)** — The latest successfully ingested data point per asset. Enables incremental loading and failure recovery.

**Windowed incremental** — An incremental extract strategy with an overlap margin that re-fetches a small window of already-loaded data as a safety net.
