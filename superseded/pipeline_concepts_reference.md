# Pipeline Concepts Reference

An early-stage reference introducing the established quantitative trading paradigm concepts that govern data pipelines. These are industry patterns, not project-specific decisions. Project-specific choices go in the Pipeline Implementation Record per dataset.

**Note:** This document was written as a quick reference during the initial learning phase. The **Pipeline Implementation Guide** (`pipeline_implementation_guide.md`) is the complete paradigm-level reference, covering the full layered architecture (source connector, staging area, canonicalization, orchestration, extract strategy, idempotent write, reconciliation, data lineage), 11 decision points, conformance layer design, and failure modes.

---

## Where the Pipeline Fits

The quantitative trading documentation structure has four layers:

| Layer | Question it answers | Status |
|---|---|---|
| 1. Data Specification | What should the warehouse contain? | Done |
| 2. Pipeline Specification | How does data get collected and maintained? | Done — see Pipeline Implementation Guide |
| 3. Strategy Specification | How are trading decisions made? | Future |
| 4. Execution Specification | How are trades placed and managed? | Future |

The data specification defines the ideal contract. The pipeline specification maps between what APIs actually return and what the spec demands.

---

## Concept 1: ETL — Extract, Transform, Load

The foundational pipeline pattern. Three phases, each with a distinct job:

| Phase | Job | Contract |
|---|---|---|
| **Extract** | Pull raw data from external sources (APIs, files, feeds) | Emit the raw response with enough metadata to reproduce the request later. Don't interpret the data — capture it faithfully. |
| **Transform** | Convert the raw source-specific representation into the internal canonical format | Normalize timestamps to UTC, map field names, cast types, make semantic decisions (e.g. "this vendor's timestamp means interval-end"). |
| **Load** | Write transformed data into the warehouse | Enforce uniqueness, apply quality constraints, make data available to the data service. |

### ETL vs ELT

The order of Transform and Load can vary:

| Pattern | Description | Tradeoff |
|---|---|---|
| **ETL** | Transform before loading. Warehouse only contains clean, conformed data. | Simpler warehouse. Semantic decisions happen before storage. |
| **ELT** | Load raw data first, transform inside the warehouse. | Preserves maximum raw information. Pushes semantic decisions downstream. |

The real question is not which pattern to pick globally — it's which transformations are **semantic** (must happen before or during load) versus **computational** (can happen anywhere). See Concept 2.

---

## Concept 2: Two Types of Transformations

Not all transformations are the same kind of thing. The quantitative trading paradigm distinguishes:

### Semantic Transformations

Change what the data claims to represent. Define the information boundary of the dataset. **Must be centralized, versioned, and tested as part of the dataset definition.**

| Category | Example |
|---|---|
| Timestamp interpretation | Vendor labels a candle at 1:05. Does that mean interval-start or interval-end? |
| Field meaning | Vendor field `volume` — is it denominated in tokens, USD, or contracts? |
| Timezone normalization | Source sends local time vs UTC vs Unix epoch |
| Identifier resolution | Source uses one identifier for the asset; warehouse uses another |

**If you get a semantic transformation wrong, you've built a causal violation into your data.** Your system believes it's observing one thing when it's observing another.

### Computational Transformations

Take an already-defined representation and compute derived quantities. **Don't change what the underlying data means.** These are a compute-placement decision — do them wherever is most efficient.

| Example | Why it's computational, not semantic |
|---|---|
| 20-candle moving average | Doesn't change what `close_price` means |
| Return from close prices | Just math on an established field |
| Volume aggregation over a window | Sums an already-defined quantity |

### The Decision Rule

> Does this transformation change the information boundary of the dataset? If yes → semantic (requires contracts, determinism, versioning). If no → computational (placement decision based on efficiency).

---

## Concept 3: Three Timelines

The quantitative trading paradigm recognizes three distinct timestamps:

| Timeline | Also called | What it measures | Example |
|---|---|---|---|
| **Observation time** | Event time | When the market produced the observable | When trades in a 1:00–1:05 candle actually happened |
| **Processing time** | Ingestion time | When the pipeline received and stored the data | When the API call ran and the row was written |
| **Decision time** | Simulation time | When a strategy commits to an action | The backtest's simulated clock or the live system's clock |

### How They Relate to Existing Architecture

| Relationship | Handled by |
|---|---|
| Observation time ↔ Decision time | PIT enforcement (`.as_of()` in the data service) — already designed |
| Observation time ↔ Processing time | Pipeline specification — **this is what the pipeline must handle** |

**Why this matters:** When fetching historical candles from APIs after the fact, the processing time (when the API was called) is completely different from the observation time (when those 5-minute candles occurred). The pipeline must assign observation timestamps, not processing timestamps, to each row.

---

## Concept 4: Conformance Layer

The industry term for the component that bridges the gap between "what the source gives you" and "what the warehouse expects." This is where semantic transformations happen.

### Four Categories of Mapping

| Category | What it does | Example |
|---|---|---|
| **Time normalization** | Convert source timestamp format to canonical format | Unix epoch → UTC DateTimeField, interval-start convention |
| **Type normalization** | Cast source types to warehouse types | String prices → DecimalField(38, 18). Zero → null. |
| **Field mapping** | Map source field names to warehouse column names | Source `c` → warehouse `close_price` |
| **Identifier mapping** | Resolve source asset identifiers to warehouse identifiers | Source query key → warehouse asset identifier |

### Key Property

Each API source needs its own conformance mapping, but they all output the same canonical format that the warehouse expects. **If you switch data sources, only the conformance layer changes — the warehouse and data service don't know or care.**

---

## Concept 5: Pipeline Properties

Correctness requirements for a well-built pipeline. These are not features — they're things that must be true.

### Idempotency

Running the same pipeline with the same inputs produces the same result, no matter how many times you run it. If the pipeline crashes halfway and is re-run, no duplicate rows, inflated volumes, or corrupted data.

**Implementation:** The industry recognizes three mechanisms — upsert, delete-write, and skip-existing — each with different tradeoffs. The choice depends on whether the target table follows append-only semantics. See the Pipeline Implementation Guide PDP3 for all options.

**Why it matters:** Retries happen. Jobs fail. Networks drop. Idempotency turns retries from a risk into a feature.

### Backfill

The ability to retroactively load historical data through the same pipeline that handles daily ingestion. A well-designed pipeline accepts explicit start and end parameters rather than hard-coding "today."

**The rule:** Same code path for daily refresh and historical backfill, just different time windows.

### Watermark (High-Water Mark)

Tracking "what is the latest data point I have successfully ingested for this asset?" Enables:

- Resuming after failure or interruption
- Incremental runs that fetch only new data

**Example:** "The most recent observation stored for asset X has timestamp T. Next fetch starts from T."

### Reconciliation

Confirming that what was received matches what was expected. Not a quality gate (that's the warehouse's job) — a pipeline health check.

**Example:** "Expected ~200 observations for this time range at the layer's resolution. Got 12. Not an error (asset may have stopped trading), but worth logging."

### Provenance

Tracking where each piece of data came from: which API endpoint, which source, when it was ingested. The warehouse's `DATA_SOURCE` per-definition constant captures the source at the layer level. An `ingest_timestamp` per row tracks when the pipeline wrote each record.

---

## Concept 6: Pipeline Failure Modes

The quantitative trading paradigm catalogs specific ways pipelines fail. Trading signals are marginal, so backtests are sensitive to subtle errors.

### Temporal Integrity Risk

**The most frequent source of silent contamination.**

- Timestamp convention ambiguity: does the vendor label candles by interval-start or interval-end?
- Timezone confusion: did they send UTC or local time?
- Format inconsistencies: Unix epoch vs ISO string vs vendor-specific format

### Heterogeneity Risk

Same field name, different meaning across sources.

- "Volume" in one API = denominated in tokens. In another API = denominated in USD.
- Both fields are called `volume`. If mixed without noticing, data is silently corrupted.

### Operational Risk

- API rate limits truncating responses
- Network errors creating gaps
- Retries creating duplicates (if not idempotent)
- Partial data from a successful HTTP 200 that only returned half the expected candles
- "Cron jobs can succeed while returning partial data"

### Data Leakage Risk (Pipeline-Specific)

Vendor revisions — a source corrects a historical candle after ingestion. If the pipeline overwrites without tracking the change, historical data is silently altered. The append-only convention for time series (WDP5) protects against this.

---

## Concept 7: How Pipeline Connects to Existing Architecture

| Document | Role | Example question it answers |
|---|---|---|
| **Data Specification** | Defines the contract — what the warehouse should contain | "I want 5-minute candles with these fields, this temporal resolution, this gap handling" |
| **Warehouse Implementation Guide** | Defines how the contract is stored | "Django models, QuerySets, abstract bases, quality constraints" |
| **Pipeline Implementation Guide** | Defines how to fulfill the contract | "Which API endpoint gives me candles? What does the response look like? How do I map their fields to my feature layer?" |
| **Pipeline Record** (per dataset) | Documents specific choices | "I use Source A. Their timestamp is interval-start. Their volume is in USD. Rate limit is 10,000 req/day." |

### The Pipeline Reveals Gaps

Important: the data spec defines the ideal. The pipeline maps between what APIs actually return and what the spec demands. **Gaps discovered during pipeline work may require revising the data spec.** Documentation order and implementation order are not the same.

---

## Glossary

**Backfill** — Retroactively loading historical data through the same pipeline that handles daily ingestion. Uses the same code path with different time windows. A core pipeline capability, not an exception path.

**Canonical format** — The internal standardized representation that all source data is converted into before entering the warehouse. Defined by the data specification. All conformance layers output this format regardless of source.

**Causal admissibility** — The property that any feature used at decision time T is computed only from data that was observable at time T. The pipeline's responsibility is to correctly assign observation timestamps so that the warehouse's PIT enforcement works correctly.

**Computational transformation** — A transformation that computes derived quantities from already-defined data without changing what the underlying fields mean. Examples: moving averages, returns, aggregations. A compute-placement decision, not a semantic decision.

**Conformance layer** — The pipeline component that bridges source-specific formats and the canonical warehouse schema. Handles time normalization, type normalization, field mapping, and identifier mapping. Each source has its own conformance mapping; all output the same canonical format.

**Decision time** — When a strategy commits to an action. The backtest's simulated clock or the live system's actual clock. The third of three timelines.

**ETL (Extract, Transform, Load)** — The foundational pipeline pattern. Extract pulls raw data from sources. Transform converts to canonical format. Load writes to the warehouse. Contrast with ELT where raw data is loaded first and transformed inside the warehouse.

**Field mapping** — A conformance layer operation that maps source field names to warehouse column names. Source `c` becomes warehouse `close_price`.

**Heterogeneity risk** — Pipeline failure mode where the same field name has different meaning across sources. "Volume" denominated in tokens vs USD, both called `volume`.

**Idempotency** — The property that running the pipeline multiple times with the same inputs produces the same result. No duplicates, no inflation, no corruption on retry. Implemented via upsert, delete-write, or skip-existing — each with different tradeoffs (see Pipeline Implementation Guide PDP3).

**Identifier mapping** — A conformance layer operation that resolves source asset identifiers to warehouse identifiers. Source query key becomes warehouse asset identifier.

**Observation time (event time)** — When the market produced the observable. The first of three timelines. For a candle, when the trades actually happened.

**Operational risk** — Pipeline failure mode from infrastructure issues: rate limit truncation, network gaps, partial responses, retry-induced duplicates.

**Processing time (ingestion time)** — When the pipeline received and stored the data. The second of three timelines. Has nothing to do with when the market event occurred.

**Provenance** — Tracking where each piece of data came from: which endpoint, which source, when ingested.

**Reconciliation** — Pipeline health check confirming that received data matches expectations. Not a quality gate — a sanity check. "Expected 200 observations, got 12 — asset may have stopped trading, logging it."

**Semantic transformation** — A transformation that changes what the data claims to represent. Defines the information boundary. Must be centralized, versioned, and tested. If wrong, introduces causal violations.

**Temporal integrity risk** — The most frequent pipeline failure mode. Timestamp convention ambiguity, timezone confusion, format inconsistencies causing silent data contamination.

**Time normalization** — A conformance layer operation converting source timestamp formats to the canonical format. Unix epoch to UTC DateTimeField.

**Type normalization** — A conformance layer operation casting source data types to warehouse types. String prices to DecimalField. Zero to null.

**Watermark (high-water mark)** — Tracking the latest successfully ingested data point per asset. Enables resumption after failure and incremental fetching.
