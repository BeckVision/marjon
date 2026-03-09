# Warehouse Implementation Decisions

This document records all architecture and implementation decisions for the quant data warehouse layer. Each decision is grounded in established quantitative trading paradigm conventions.

---

## Established Quant Concepts Applied

Three core concepts from quantitative trading infrastructure inform every decision below:

### 1. Panel Data

The standard structure for quant data: **asset × time × feature** in wide format. Feature layers are stored in separate tables (optimized for correctness and efficiency) but presented as one merged panel through alignment (optimized for the researcher's mental model). This is the foundation of the **separation between storage and access**.

### 2. Point-in-Time (PIT) Enforcement

Every data point has a **knowledge time** — when the data became known. Queries can only see data with knowledge time ≤ simulation time. For interval-based market data (candles, snapshots), knowledge time equals as-of time (end of interval). This prevents **look-ahead bias** — the most common and most dangerous error in backtesting.

### 3. Narrow Interface

The data layer exposes exactly **three read-only operations**:

| Operation | Purpose |
|---|---|
| **Get panel slice** | Returns features for asset(s) at time(s), with alignment and PIT already enforced |
| **Get universe members** | Returns all assets belonging to a universe at a given time |
| **Get reference data** | Returns raw granular records (e.g. transactions) for an asset in a time range |

All consumers go through these three doors. No direct table access for reads. This is the enforcement mechanism that makes panel structure and PIT rules actually work in practice.

**Write access is completely separate.** Consumers read data through the narrow interface. Pipelines write data directly to models. These are distinct paths.

---

## Architecture Decisions

### A1: Two Django Apps

| App | Role | Contains |
|---|---|---|
| **`warehouse`** | Storage layer | Models, custom QuerySets, database constraints, model validation |
| **`data_service`** | Access layer | Three read-only operations, no models |

This mirrors the quant separation between storage and access. The warehouse stores data correctly. The data service serves data safely.

### A2: Three Table Categories in the Warehouse

The warehouse contains three categories of tables, each following an established quant warehouse classification:

| Category | Quant term | Description | Example |
|---|---|---|---|
| **Universe tables** | Master data | One row per asset, identity and anchor event | Graduated tokens |
| **Feature layer tables** | Time series facts | One observation per asset per time interval | OHLCV candles, holder snapshots |
| **Reference tables** | Event facts | One discrete event per row, no fixed interval | Raw transactions |

### A3: Spec Concepts to Implementation Mapping

Of the 7 dataset specification concepts, 3 become tables and 4 become behavior:

| Concept | Becomes | Lives in |
|---|---|---|
| Universe Definition | Table (model) | `warehouse` app |
| Feature Layer | Table (model, one per layer) | `warehouse` app |
| Reference Dataset | Table (model) | `warehouse` app |
| Join Key | Alignment logic | `data_service` app |
| Point-in-Time | Time filtering QuerySet method | `warehouse` app (on abstract base QuerySet) |
| Data Quality Constraint | Database constraints + model validation + audit commands | `warehouse` app |
| Derived Feature | Computation logic | `data_service` app |

---

## Model-Level Decisions

### M1: Primary Key Strategy

**Decision:** Surrogate auto-increment primary key + `unique_together` constraint on asset + timestamp.

**Rationale:** Django's most battle-tested pattern. Every Django library, ORM feature, and third-party tool assumes a single auto-increment PK. The `unique_together` constraint enforces the natural key (asset + timestamp) at the database level, matching DQ-001's specified validation method.

### M2: Foreign Key Target

**Decision:** Foreign key to the universe model using Django's `to_field`, pointing to the mint address `CharField` rather than the surrogate `id`.

**Rationale:** Queries think in terms of mint addresses, not meaningless numbers. `to_field="mint_address"` keeps the ORM aligned with the domain.

### M3: Nullable Fields

**Decision:** All feature columns are nullable. Only the compound key fields (asset + timestamp) are required.

**Rationale:** The dataset specification (JK-001) explicitly states that row-level existence is determined by the row being present in the table, regardless of whether individual fields are null. Null fields within a row are the strategy's responsibility.

### M4: Abstract Base Models

**Decision:** Two abstract base models — one for feature layer tables, one for reference tables.

**Rationale:** Feature layers and reference tables share some structure (FK to universe, timestamp) but differ in:

| Aspect | Feature layer base | Reference table base |
|---|---|---|
| **Compound key** | Asset + timestamp | Asset + timestamp + event identifier |
| **PIT logic** | End-of-interval (knowable when interval closes) | Event-time (knowable at exact moment of occurrence) |
| **Temporal resolution** | Fixed interval | No fixed interval |

Each abstract base provides:
- The shared skeleton (FK, timestamp, `unique_together`)
- A custom QuerySet with `.as_of(timestamp)` implementing the appropriate PIT logic
- Inherited by all concrete models in its category

### M5: Append-Only Convention

| Table category | Append-only? | Rationale |
|---|---|---|
| **Feature layer tables** | Yes — never update or delete | Observations are historical facts. Modifying them would violate PIT semantics and break reproducibility. |
| **Reference tables** | Yes — never update or delete | Events are historical facts. |
| **Universe table** | No — updates allowed | Master data (status changes, metadata enrichment) is a property of the asset, not a point-in-time observation. |

### M6: Quality Constraints — Split by Severity

**Decision:** Hard rejects that can be expressed as single-row rules go into the database as CHECK constraints. Hard rejects requiring cross-table logic go into Django model validation. Warnings stay in application-level post-insert audits.

| Enforcement level | What it catches | Django mechanism |
|---|---|---|
| **Database CHECK constraints** | Simple row-level corruption (high < low, volume < 0) | Model `Meta.constraints` |
| **Model validation** | Cross-table corruption (timestamp outside observation window) | Model `clean()` method |
| **Standalone audit commands** | Suspicious but valid patterns (sparse data, unusual distributions) | Management commands |

### M7: Indexing Strategy

**Decision:** Two indexes per feature layer table. Add more only when proven query patterns demand it.

| Index | Covers query pattern |
|---|---|
| **`unique_together` on asset + timestamp** | "All rows for one asset in a time range" — the primary query pattern |
| **Separate index on timestamp alone** | "All assets at a specific time" — the cross-sectional query pattern |

Additional column-specific indexes (e.g. on close price) are added only when a concrete, slow query proves the need. PostgreSQL allows adding indexes to existing tables at any time without restructuring.

---

## Data Type Decisions

Established quant warehouse conventions for data types:

| Field category | Django field type | Rationale |
|---|---|---|
| **Prices** (open, high, low, close) | `DecimalField` (high precision) | Fixed-point decimal avoids floating-point rounding errors. Non-negotiable in quantitative trading. |
| **Volume** | `DecimalField` | Solana SPL tokens support 0–9 decimal places, so volumes can be fractional. |
| **Market cap** | `DecimalField` | Derived from price × supply, both decimal. |
| **Timestamps** | `DateTimeField` (UTC) | Timezone-aware, stored in UTC. No exceptions. Prevents silent PIT enforcement failures from timezone mixing. |
| **Asset identifiers** (mint address) | `CharField` | Identifiers are strings, never integers. You never do math on them. Solana addresses are base58, typically 32–44 characters. |
| **Holder counts** | `BigIntegerField` | Whole numbers — you cannot have fractional holders. |

These data types are universal across crypto assets and do not need to change when adding new universe definitions (e.g. Binance-listed tokens).

---

## QuerySet Layer

The queryset layer handles **row-level operations** — things that apply to a single table independently, without needing to know about any other table.

### Four Shelves of Logic

Each type of logic lives at a specific level in Django, following quant warehouse conventions:

| Shelf | Purpose | Django mechanism | When it runs |
|---|---|---|---|
| **1. Database constraints** | Simple hard rejects | `Meta.constraints` (CHECK constraints) | On every write, enforced by the database |
| **2. Model validation** | Cross-table hard rejects | `clean()` method | Before saving, enforced by Python |
| **3. Custom QuerySet** | PIT time filtering | `.as_of(timestamp)` method on abstract base QuerySet | On every read through the data service |
| **4. Standalone commands** | Post-insert audits (warnings) | Management commands | Periodically, on schedule |

### Time Filtering (`.as_of()`)

The `.as_of(timestamp)` method is the mechanical implementation of PIT enforcement. Key properties:

- **Stateless** — every call is independent. No cursor, no tracking of what was previously seen. "Given simulation time T, what is visible?" Statelessness is essential because backtests often jump around in time.
- **Lives on the abstract base QuerySet** — written once, inherited by every feature layer or reference table.
- **Two implementations** — feature layer base uses end-of-interval logic (interval end ≤ simulation time), reference table base uses event-time logic (event timestamp ≤ simulation time).

---

## Dataset Specification Update Required

### PIT-001: Add Knowledge Time Assumption

Add one attribute row to PIT-001:

| Attribute | Value |
|---|---|
| **Knowledge time assumption** | Knowledge time equals as-of time — data is not revised or delayed after the interval closes |

Add a note: "This assumption holds for real-time market data feeds. If a future feature layer uses a source that publishes with delay or revises data retroactively, it will need its own PIT rule with an explicit knowledge time offset."

---

## Data Service Layer

The `data_service` app is the access layer — the narrow interface. It contains a standalone Python module with three functions (one per operation), no models. Everything outside this module talks to the data service. Nothing outside this module imports warehouse models directly for reading.

---

### Query Pipelines

Each operation runs a well-defined sequence of steps called a **query pipeline**. Raw table data passes through these steps before reaching the consumer.

#### Operation 1: Get Panel Slice

The workhorse operation. Returns a wide-format panel with alignment and PIT already enforced.

| Step | Name | What it does |
|---|---|---|
| 1 | **Scope** | Validates that requested assets exist in the universe and time range falls within observation window |
| 2 | **Fetch** | Pulls raw rows from each requested feature layer table independently |
| 3 | **Time filter** | Applies `.as_of(simulation_time)` to each table's results (PIT enforcement) |
| 4 | **Align** | Joins results from multiple feature layers into a single panel — matching on asset + timestamp, forward-filling for resolution mismatches, attaching staleness fields, enforcing inner join |
| 5 | **Return** | Consumer receives one row per asset per timestamp, with columns from all requested feature layers |

**Critical ordering:** Time filtering happens **before** alignment. If alignment happened first, forward-fill could carry a value from a future row before PIT removes it.

#### Operation 2: Get Universe Members

Returns all assets that were known to exist at a given simulation time.

| Step | Name | What it does |
|---|---|---|
| 1 | **Time filter** | Returns only assets whose anchor event occurred at or before simulation time T |
| 2 | **Return** | Consumer receives a list of assets with their master data |

**Why this exists separately from get panel slice:** An asset might exist in the universe (it graduated) but have no candles yet (first candle hasn't closed). Strategies use this operation to decide which assets to evaluate, then call get panel slice for actual data.

#### Operation 3: Get Reference Data

Returns raw granular records for an asset within a time range. No alignment, no joining.

| Step | Name | What it does |
|---|---|---|
| 1 | **Scope** | Validates asset and time range |
| 2 | **Time filter** | Applies event-time PIT — only returns events whose timestamp ≤ simulation time |
| 3 | **Return** | Consumer receives raw event records in chronological order |

**Why reference data stays separate from the panel:** Reference data is granular — hundreds or thousands of events per 5-minute interval. Joining into the panel would explode row count and destroy the "one row per asset per timestamp" structure.

#### Pipeline Summary

| Operation | Steps | Cross-table? |
|---|---|---|
| **Get panel slice** | Scope → Fetch → Time filter → Align → Return | Yes (alignment across feature layers) |
| **Get universe members** | Time filter → Return | No (universe table only) |
| **Get reference data** | Scope → Time filter → Return | No (reference table only) |

Only get panel slice involves cross-table logic. The other two are single-table operations that live in the data service anyway because the narrow interface principle says all reads go through the same door.

---

### Service Contract

The data service makes explicit guarantees about what it returns. These are documented invariants that consumers can rely on without verifying themselves.

#### Four Guarantees

| # | Guarantee | What it means |
|---|---|---|
| 1 | **Temporal safety** | Every row returned has knowledge time ≤ the simulation time passed in. No exceptions. If a consumer receives data from the data service, it is safe to use at that simulation time. |
| 2 | **Alignment completeness** | When get panel slice returns a row, that row contains data from all requested feature layers. No partial rows. The inner join rule is enforced before data reaches the consumer. |
| 3 | **Staleness transparency** | When alignment uses forward-fill, a staleness field is attached showing how old the forward-filled value is. The data service never silently presents stale data as fresh. |
| 4 | **Data integrity** | Every row returned has passed all hard reject quality constraints. The data service guarantees this by relying on the warehouse's gate checks — if it's in the warehouse, it's clean. |

#### Three Exclusions

| # | Not guaranteed | Why |
|---|---|---|
| 1 | **Completeness of data** | A coin with 12 candles out of 1000 possible is returned as-is. The data service does not fill in missing candles or warn about sparse data. That's the consumer's responsibility. |
| 2 | **Correctness of derived feature formulas** | The service guarantees it followed the formula, not that the formula is right. |
| 3 | **Freshness** | The data service serves what's in the warehouse. If the pipeline hasn't run recently, data may be stale relative to the real world. No real-time guarantees. |

---

### Data Service Implementation Decisions

#### DS1: Alignment Mechanics

**Decision:** Fetch separately, merge in Python (Option B).

Each feature layer queryset returns its rows filtered by `.as_of()`, then a Python function in the data service merges them into a single panel using the join key rules. Forward-fill and staleness field computation happen in Python.

**Rationale:** Forward-fill logic with staleness tracking is the critical piece. Writing it in Python is far more readable, testable, and debuggable than SQL. Firms only move alignment into the database when performance demands it. Move to hybrid (SQL join + Python forward-fill) only if performance becomes a real problem.

#### DS2: Derived Features Timing

**Decision:** Computed on-the-fly during get panel slice (Option B).

Derived features are computed by the data service between the Align and Return steps. Nothing is stored — computation happens fresh every time.

**Rationale:** No derived features are defined yet. The priority at this stage is experimentation speed, not read performance. On-the-fly computation means adding a new derived feature is just adding a formula definition — no pipeline changes, no re-storage. Move individual derived features to pre-computed when they have stabilized.

#### DS3: Contract Enforcement

**Decision:** Trust on read, audit periodically (Option C).

The data service trusts the warehouse at read time — no re-checking of quality constraints on every read. Standalone audit commands run periodically to verify that everything in the warehouse passes quality constraints. Problems are flagged and investigated.

**Rationale:** The industry principle is "validate on write, trust on read, audit periodically." Each layer has one job: the pipeline validates, the data service serves, the audits verify. Re-validating on every read is redundant and slow if gate checks work correctly.

#### DS4: Error Handling

**Decision:** Errors for invalid requests, empty results for valid requests with no data.

| Situation | Behavior | Rationale |
|---|---|---|
| Asset not in universe | Raise error | Likely a bug in consumer code — surface it immediately |
| Time range outside observation window | Raise error | Likely a bug — surface it immediately |
| Valid request but no data exists | Return empty result | Real market outcome (coin died immediately) — not a bug |

**Key distinction:** Errors mean "you asked for something that shouldn't be asked for." Empty results mean "you asked a valid question and the answer is nothing."

---

## Complete Decision Summary

### Architecture
| ID | Decision | Choice |
|---|---|---|
| A1 | Django app structure | Two apps: `warehouse` (models + querysets) and `data_service` (three operations, no models) |
| A2 | Table categories | Three: universe (master data), feature layers (time series facts), reference (event facts) |
| A3 | Spec concept mapping | 3 of 7 become tables, 4 become behavior |

### Models
| ID | Decision | Choice |
|---|---|---|
| M1 | Primary key | Surrogate auto-increment + `unique_together` on asset + timestamp |
| M2 | Foreign key target | `to_field` pointing to mint address, not surrogate id |
| M3 | Nullable fields | All feature columns nullable, only compound key required |
| M4 | Abstract base models | Two bases — feature layer (end-of-interval PIT) and reference (event-time PIT) |
| M5 | Append-only | Feature layers and reference tables append-only; universe table allows updates |
| M6 | Quality constraints | Split — database CHECK for simple row-level, Python for cross-table, audit commands for warnings |
| M7 | Indexing | `unique_together` + separate timestamp index; add more on demand |

### Data Types
| ID | Field | Type |
|---|---|---|
| DT1 | Prices | `DecimalField` (high precision) |
| DT2 | Volume | `DecimalField` (Solana tokens are fractional) |
| DT3 | Market cap | `DecimalField` |
| DT4 | Timestamps | `DateTimeField` (UTC always) |
| DT5 | Asset identifiers | `CharField` |
| DT6 | Holder counts | `BigIntegerField` |

### Data Service
| ID | Decision | Choice |
|---|---|---|
| DS1 | Alignment mechanics | Fetch separately, merge in Python |
| DS2 | Derived features timing | On-the-fly during get panel slice |
| DS3 | Contract enforcement | Trust on read, audit periodically |
| DS4 | Error handling | Raise error for invalid requests, return empty for valid requests with no data |

### Spec Update Required
| Item | Change |
|---|---|
| PIT-001 | Add `Knowledge time assumption` attribute: knowledge time equals as-of time |
