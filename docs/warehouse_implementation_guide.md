# Warehouse Implementation Guide

A dataset-agnostic reference for implementing a quantitative trading data warehouse and data service in Django. This document describes established patterns from the quantitative trading paradigm, decision points with all available options, and tradeoffs. It does not prescribe choices — those belong in a separate **Dataset Implementation Record** per dataset.

---

## Part 1: Foundational Concepts

Three core concepts from quantitative trading infrastructure inform every pattern in this guide.

### Panel Data

The standard structure for quantitative data: **asset × time × feature** in wide format. Also called a **cross-sectional time series** — cross-sectional because multiple assets exist at each point in time, time series because each asset has observations across time.

Feature layers are stored in separate tables (optimized for correctness and write efficiency) but presented as one merged panel through **alignment** (optimized for the researcher's mental model). A consumer asks "for asset X, at time T, what is the value of feature F?" and receives one row with all features merged.

This is the foundation of the **separation between storage and access** — the most important architectural principle in quantitative data infrastructure. The best way to store data is almost never the best way to use data.

### Point-in-Time (PIT) Enforcement

Every data point has two timestamps:

| Timestamp | Name | Meaning |
|---|---|---|
| **As-of time** | When the event occurred in the market | For a 1:00–1:05 candle, this is 1:05 |
| **Knowledge time** | When the data became known | Usually equals as-of time for market data, but may differ for delayed or revised data |

When a backtest asks "what did I know at simulated time T?", the system filters on **knowledge time ≤ T**. This prevents **look-ahead bias** — the most common and most dangerous error in backtesting. PIT makes this prevention structural rather than relying on individual researchers to be careful.

**Common availability rule types:**

| Type | When data is available | Use case |
|---|---|---|
| **End-of-interval** | At the close of the interval | Candles, periodic snapshots |
| **Event-time** | At the exact moment it occurs | Individual transactions, discrete events |
| **Publication-time** | When published, not when measured | Reports, announcements, revised data |

### Narrow Interface

The data layer exposes exactly **three read-only operations**:

| Operation | Purpose |
|---|---|
| **Get panel slice** | Returns features for asset(s) at time(s), with alignment and PIT enforced |
| **Get universe members** | Returns all assets belonging to a universe at a given time |
| **Get reference data** | Returns raw granular records for an asset in a time range |

All consumers go through these three doors. No direct table access for reads. Pipelines write data directly to models — that is a completely separate path. The narrow interface is the enforcement mechanism that makes panel structure and PIT rules actually work in practice.

---

## Part 2: Architecture

### Two-App Structure

| App | Quant term | Role | Contains |
|---|---|---|---|
| **`warehouse`** | Data warehouse / data store | Storage layer | Models, custom QuerySets, database constraints, model validation, audit commands |
| **`data_service`** | Data service / feature service | Access layer | Three read-only operations, no models |

### Three Table Categories

The warehouse contains three categories of tables:

| Category | Quant term | Description | Key structure |
|---|---|---|---|
| **Universe tables** | Master data | One row per asset, identity and anchor event | Asset identifier (unique) |
| **Feature layer tables** | Time series facts | One observation per asset per time interval | Asset + timestamp (compound key) |
| **Reference tables** | Event facts | One discrete event per row, no fixed interval | Asset + timestamp + event identifier |

### Data Specification Concept Mapping

A data specification contains 7 concepts. Each maps to either a table or behavior:

| Concept | Becomes | Lives in |
|---|---|---|
| Universe Definition | Table (model) | `warehouse` |
| Feature Layer | Table (model, one per layer) | `warehouse` |
| Reference Dataset | Table (model) | `warehouse` |
| Join Key | Alignment logic | `data_service` |
| Point-in-Time | Time filtering QuerySet method | `warehouse` (abstract base QuerySet) |
| Data Quality Constraint | Database constraints + model validation + audit commands | `warehouse` |
| Derived Feature | Computation logic | `data_service` |

---

## Part 3: Paradigm-Level Concept Structure

The quantitative trading paradigm defines what a universe is, what a feature layer is, and what a reference table is. These definitions are not design decisions — they are properties of the paradigm itself. Every dataset inherits these structures.

This section documents the paradigm-level attributes for the three table categories.

### Attribute Mapping Pattern

When a paradigm concept becomes a Django model, each attribute falls into one of two categories:

| Category | What it is | Where it lives in Django | Example |
|---|---|---|---|
| **Per-row data** | Different value for each row in the table | Database column (model field) | Anchor event — Asset A has T0 at 2:00 PM, Asset B has T0 at 5:30 PM |
| **Per-definition constant** | Same value for every row in a given concrete model | Class-level constant on the concrete model | Observation window — same window offsets for every asset in a given universe |

The abstract base defines the **per-row fields** that every concrete model inherits. It also declares the **per-definition constants** with no value (None) — each concrete model overrides them with its dataset-specific value.

### Time Representation

The quantitative trading paradigm recognizes two distinct time measurement systems:

| System | What it measures | When to use |
|---|---|---|
| **Calendar time** | Wall clock time, including weekends, holidays, non-trading hours | 24/7 markets (crypto, forex) |
| **Trading time** | Market hours only, skips non-trading periods | Markets with defined trading hours (stocks, futures) |

These are not interchangeable. "30 days" in calendar time includes weekends. "30 days" in trading time skips them. The paradigm does not prescribe which system to use — that depends on the market. Each dataset implementation record chooses the appropriate representation for its time values (observation window offsets, temporal resolution, etc.).

### Observation Window

The quantitative trading paradigm defines an observation window using the event study convention (MacKinlay, 1997): **two offsets from the anchor event (t₁, t₂)**, where t₁ is the start offset and t₂ is the end offset.

| Offset | Meaning | Example |
|---|---|---|
| **t₁ (start)** | Where the window begins, relative to anchor | t₁ = -60 means "60 units before anchor" |
| **t₂ (end)** | Where the window ends, relative to anchor | t₂ = +5000 means "5000 units after anchor" |

A forward-only window is the special case where t₁ = 0. An unbounded window uses None for one or both offsets.

### Universe

The quantitative trading paradigm defines a universe as: **which assets, what time scope.**

The paradigm recognizes two types of universes:

| Type | Anchor event | Observation window | Example |
|---|---|---|---|
| **Event-driven** | Per-row — each asset has its own T0 | Offsets relative to anchor (t₁, t₂) | "All newly listed tokens, from listing date to T0 + 30 days" |
| **Calendar-driven** | None — no per-asset anchor | Absolute time range, same for all assets | "BTC, ETH, SOL from Jan 2024 to Jan 2026" |

| Paradigm Attribute | Attribute Type | Notes |
|---|---|---|
| **Universe ID** | Per-definition constant | |
| **Name** | Per-definition constant | |
| **Universe** (inclusion criteria) | Per-definition constant | |
| **Universe type** | Per-definition constant | Event-driven or calendar-driven |
| **Anchor event** | Per-row field (event-driven only) | Each asset has its own T0. Null for calendar-driven universes. |
| **Observation window start** | Per-definition constant | Offset from anchor (event-driven) or absolute time (calendar-driven) |
| **Observation window end** | Per-definition constant | Same. None = unbounded. |
| **Exclusion criteria** | Per-definition constant | |
| **Membership end** | Per-row field (optional) | When this asset left the universe. Null = still a member. Not applicable for universes where membership is permanent. |
| **Version** | Per-definition constant | |

**What is shared across all universes:** Metadata (ID, name, version, inclusion/exclusion criteria), universe type, and observation window boundaries.

**What varies:** The asset identity field (different domains use different identifiers), whether anchor_event is populated (event-driven) or null (calendar-driven), whether membership_end is used (universes with rebalancing or natural exit) or null (permanent membership), and how the observation window is interpreted (relative offsets vs absolute times).

**Terminology note:** The paradigm uses "asset" as the default term for the entities tracked by a universe. In some universes, the entity may not be a traditional financial asset (e.g., a market event, a protocol, a liquidity pool). The paradigm structure is the same regardless of entity type — "asset" is used as a convenient shorthand.

### Feature Layer

The quantitative trading paradigm defines a feature layer as: **time-aligned measurements of an asset within a universe, at a fixed temporal resolution.**

| Paradigm Attribute | Attribute Type |
|---|---|
| **Layer ID** | Per-definition constant |
| **Universe ID** | Per-definition constant |
| **Name** | Per-definition constant |
| **Feature set** | Per-row fields — defined by each concrete layer |
| **Temporal resolution** | Per-definition constant |
| **Availability rule** | Per-definition constant — which PIT type this layer uses (end-of-interval, event-time, or publication-time) |
| **Gap handling** | Per-definition constant |
| **Applicability condition** | Per-definition constant (optional) | When present, states which assets this layer produces data for. Default: all assets in the universe. |
| **Data source** | Per-definition constant |
| **Refresh policy** | Per-definition constant |
| **Version** | Per-definition constant |

**What is shared across all feature layers:** Every feature layer has a timestamp (per-row), temporal resolution, availability rule, and metadata.

**What varies:** The feature set (what is being measured), the FK to the universe model (which universe this belongs to), and the values of all per-definition constants.

**Why the availability rule is per-layer, not per-category:** The paradigm defines three availability rule types (end-of-interval, event-time, publication-time). Which one applies depends on the data source, not the table category. A feature layer using a delayed data source would need publication-time PIT, not end-of-interval. Each concrete layer declares its own availability rule.

### Reference Table

The quantitative trading paradigm defines a reference table as: **granular event data outside the fixed time-interval grid, queried on demand.**

| Paradigm Attribute | Attribute Type |
|---|---|
| **Reference ID** | Per-definition constant |
| **Universe ID** | Per-definition constant |
| **Name** | Per-definition constant |
| **Record type** | Per-definition constant |
| **Feature set** | Per-row fields — defined by each concrete table |
| **Timestamp field** | Per-row field — the exact event time |
| **Availability rule** | Per-definition constant — which PIT type this table uses |
| **Access pattern** | Per-definition constant |
| **Data source** | Per-definition constant |
| **Refresh policy** | Per-definition constant |
| **Version** | Per-definition constant |

**What is shared across all reference tables:** Every reference table has a timestamp (per-row), an availability rule, and metadata.

**What varies:** The feature set, the event identifier field, the FK to the universe model, and the values of all per-definition constants.

**Why no uniqueness constraint at the paradigm level:** For feature layers, the compound key is always asset + timestamp. For reference tables, the compound key includes an event identifier that varies per concrete model. The paradigm cannot define the constraint because it doesn't know the event identifier field.

### Django Implementation Notes

The following are structural constraints that apply when implementing the paradigm concepts as Django abstract base models. These are not paradigm facts — they are consequences of how Django works.

**FK to the universe model lives on concrete models, not abstract bases.** Concrete models from different datasets point to different universe models. A feature layer in one dataset FKs to one universe model; the same type of feature layer in another dataset FKs to a different one. The abstract base has no way to know which universe model to target.

**Asset identity field lives on concrete universe models, not the abstract base.** Different domains use different identifier types and lengths. The base provides the anchor event and observation window; the concrete model adds the identity field.

**Uniqueness constraints for feature layers live on concrete models, not the abstract base.** The constraint includes the FK field, which isn't on the base.

---

## Part 4: Model-Level Decision Points

Each decision point lists all options with tradeoffs. Choices are made per dataset and recorded in the Dataset Implementation Record.

### WDP1: Primary Key Strategy

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: Surrogate + unique_together** | Django auto-generates an integer `id` PK. Add `unique_together` on asset + timestamp to enforce the natural key separately. | Most battle-tested Django pattern. Full compatibility with all ORM features and third-party packages. | Wastes a small amount of storage on the meaningless `id` column. Foreign keys reference the meaningless number. |
| **B: Computed single-field PK** | Combine asset + timestamp into one string (e.g. `ASSET_2024030512:05`) and use as PK. | PK has business meaning. No wasted column. | Must generate consistently everywhere — one formatting inconsistency creates duplicates. String PKs are slower than integer PKs for joins. Uncommon pattern. |
| **C: Django composite PK** | Use Django 5.2's `CompositePrimaryKey` to declare asset + timestamp directly as the PK. | Cleanest representation. No wasted column. | Very new (Django 5.2). Limited third-party support. Thin documentation and community experience. |

### WDP2: Foreign Key Target

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: FK to surrogate id** | Feature layer FK points to the universe model's auto-increment `id`. | Django default. Simple. | Queries must join through a meaningless number. The domain identifier isn't directly available on feature layer rows. |
| **B: FK to natural identifier** | Feature layer FK points to the universe model's domain identifier using Django's `to_field`. | Queries use the domain identifier directly. Aligned with how researchers think. | Requires `to_field` configuration. The natural identifier must be unique and stable. |

### WDP3: Nullable Fields

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: Per-field decision** | Decide nullable vs required for each feature column individually based on data source behavior. | Maximum precision — each field's constraint matches reality. | Requires detailed knowledge of every data source's behavior. More maintenance. |
| **B: All feature columns nullable** | Only compound key fields (asset + timestamp) are required. All feature columns accept null. | Simple, consistent. Matches the quant principle that null fields within a row are the consumer's responsibility. | Less strict — allows nulls where they might indicate a pipeline bug rather than legitimate missing data. |

### WDP4: Abstract Base Models

The quantitative trading paradigm defines the structure of three table categories (see Part 3). This decision point is about how to implement that paradigm structure in Django.

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: Three abstract bases** | Separate abstract base for universes, feature layers, and reference tables. Each base encodes the paradigm-level attributes for its category. | Full paradigm coverage. Shared fields and methods written once, inherited by all concrete models. Forces consistency. | More upfront code. Three bases to maintain. |
| **B: Two abstract bases (feature layer + reference only)** | Abstract bases for feature layers and reference tables only. Universe models are standalone with no shared base. | Simpler. Universe identity fields vary so much that a base may add little value. | No enforcement of shared universe structure (anchor event, observation window). Shared methods must be duplicated across universe models. |
| **C: No abstract bases** | Each concrete model defines its own skeleton. No inheritance. | Maximum flexibility per model. | Repetition across models. Inconsistency risk. Paradigm structure is implicit rather than enforced. |

**Tradeoff:** The more universes you plan to support, the more value Option A provides. With a single universe, Option B or C is sufficient. With three or more, the shared universe structure becomes worth enforcing.

### WDP5: Append-Only Convention

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: Strict append-only everywhere** | No updates or deletes on any warehouse table, including universe tables. Corrections are new rows with correction flags. | Maximum reproducibility. Full audit trail. | Universe metadata (status, enrichment) becomes awkward — requires event-sourcing pattern for simple attribute changes. |
| **B: Append-only for time series, updates for master data** | Feature layer and reference tables are append-only. Universe tables allow updates. | Time series observations (historical facts) are immutable. Master data (asset properties) can evolve naturally. | Universe table changes aren't tracked unless you add separate auditing. |
| **C: Updates allowed everywhere** | Any table can be updated or deleted. | Maximum flexibility. | Breaks reproducibility — a backtest run today and next month may produce different results from the same code. Violates PIT semantics for time series data. |

### WDP6: Quality Constraint Placement

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: All in database** | Every hard reject and warning becomes a database CHECK constraint. | Maximum safety — no code path can store bad data. | Warnings would incorrectly reject valid-but-unusual data. Cross-table checks cannot be expressed as CHECK constraints in most databases. |
| **B: All in Python** | Django model validation handles everything. Database stores whatever Python sends. | All logic in one place. Can handle complex cross-table checks. | Anything that bypasses Django ORM (raw SQL, bulk imports, admin fixes) skips validation. Corrupt data can enter silently. |
| **C: Split by severity and complexity** | Simple row-level hard rejects → database CHECK constraints. Cross-table hard rejects → Django model validation (`clean()` method). Warnings → standalone audit commands. | Database is last line of defense for simple corruption. Python handles what the database can't express. Warnings correctly don't block storage. | Validation logic lives in multiple places. Must maintain both. |

### WDP7: Indexing Strategy

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: Compound index only** | Only the `unique_together` index on asset + timestamp. | Minimum write overhead. Covers "all rows for one asset in a time range." | "All assets at one time" queries (cross-sectional) are slow — requires full table scan. |
| **B: Compound + timestamp** | `unique_together` index plus a separate index on timestamp alone. | Covers both primary query patterns: per-asset time range and cross-sectional. | Slightly more write overhead (two indexes to maintain). |
| **C: Compound + timestamp + asset** | Same as B plus a separate index on asset alone. | Maximum query flexibility for all three single-column patterns. | The compound index already covers asset-first queries efficiently (asset is the leading column). The separate asset index is redundant — write overhead with no query benefit. |

**General principle:** Index based on proven query patterns, not hypothetical ones. Additional column-specific indexes are added only when a concrete slow query proves the need. Most databases allow adding indexes to existing tables at any time without restructuring.

### WDP8: Data Types

Established quantitative trading conventions for field categories. The categories and recommended types are paradigm-level. The specific configurations (precision, max length) are dataset-specific and belong in the Dataset Implementation Record.

| Field category | Recommended type | Rationale |
|---|---|---|
| **Prices** | Fixed-point decimal (`DecimalField`) | Floating-point math has rounding errors that compound across thousands of calculations. Non-negotiable in quantitative trading. |
| **Volumes** | Depends on asset | Integer if the asset is always whole units. Decimal if fractional units are possible. Research the specific asset class. |
| **Monetary aggregates** (market cap, TVL) | Fixed-point decimal | Derived from price × quantity, both potentially decimal. |
| **Timestamps** | Timezone-aware datetime, stored in UTC | No exceptions. Mixing timezones in storage causes silent PIT enforcement failures. |
| **Asset identifiers** | String (CharField) | Identifiers are never numeric even if they look numeric. You never do math on them. |
| **Counts** (holders, transactions) | Integer (BigIntegerField) | Whole numbers by definition. |

**Note:** The field categories above are universal across all quantitative trading datasets. The specific configurations — `max_digits`, `decimal_places`, `max_length` — depend on the asset class and data source, and are recorded per dataset.

### WDP9: Timestamp Convention

Whether the timestamp field stores the **start** or **end** of the interval. This affects PIT computation.

| Option | Description | PIT computation | Pros | Cons |
|---|---|---|---|---|
| **A: Interval start** | Timestamp is when the interval begins (e.g. 1:00 for a 1:00–1:05 candle) | Interval end = timestamp + temporal_resolution | Matches how most data sources deliver candles. Natural for "what interval does this observation belong to?" | PIT computation requires adding temporal_resolution every time. |
| **B: Interval end** | Timestamp is when the interval closes (e.g. 1:05 for a 1:00–1:05 candle) | Interval end = timestamp | PIT computation is a simple comparison — no arithmetic. Matches the as-of time concept directly. | Less common in raw data feeds. Must subtract temporal_resolution to find interval start. |

**Critical:** Whichever option is chosen, it must be documented and consistent across all feature layers in a dataset. Mixing conventions causes silent PIT errors.

---

## Part 5: QuerySet Layer

The queryset layer handles **row-level operations** — things that apply to a single table independently, without needing to know about any other table. The industry calls these distinct from **cross-table operations** which belong in the data service.

### Four Shelves of Logic

Each type of warehouse logic lives at a specific level, following established quant warehouse conventions:

| Shelf | Quant term | Purpose | Django mechanism | When it runs |
|---|---|---|---|---|
| **1** | **Gate check (database)** | Simple hard rejects | `Meta.constraints` (CHECK constraints) | On every write, enforced by the database |
| **2** | **Gate check (application)** | Cross-table hard rejects | `clean()` method | Before saving, enforced by Python |
| **3** | **PIT filter** | Time filtering | `.as_of(timestamp)` on abstract base QuerySet | On every read through the data service |
| **4** | **Post-insert audit** | Warnings, anomaly detection | Standalone management commands | Periodically, on schedule |

### Time Filtering (`.as_of()`)

The `.as_of(timestamp)` method is the mechanical implementation of PIT enforcement. Key properties from the industry:

- **Stateless** — every call is independent. No cursor, no tracking of what was previously seen. "Given simulation time T, what is visible?" Statelessness is essential because backtests often jump around in time (parameter sweeps, different entry points) rather than moving strictly forward.
- **Lives on the abstract base QuerySet** — written once, inherited by every concrete model in that category.
- **Implementation depends on availability rule** — the `.as_of()` method uses the layer's declared availability rule and the dataset's timestamp convention to determine what is visible at a given simulation time. The abstract base provides the method; the concrete model's per-definition constants control the behavior.

| Availability rule | Filter logic |
|---|---|
| **End-of-interval** | Observation is visible when its interval has fully closed (depends on timestamp convention and temporal resolution) |
| **Event-time** | Event is visible at the exact moment it occurs (timestamp ≤ simulation time) |
| **Publication-time** | Observation is visible when published (publication timestamp ≤ simulation time) |

**Universe time filtering:** For the "Get universe members" operation, the filter depends on universe type:
- **Event-driven:** `anchor_event <= simulation_time AND (membership_end IS NULL OR membership_end > simulation_time)` — an asset is a member if its anchor event has occurred and it has not yet exited the universe.
- **Calendar-driven:** All assets matching the inclusion criteria are members for the full observation window. No per-asset time filter is applied — membership is defined by the inclusion criteria, not by individual entry timing.

---

## Part 6: Data Service Layer

The `data_service` app is the access layer — the narrow interface. It contains a standalone Python module with three functions (one per operation), no models. Everything outside this module goes through the data service for reads. Nothing outside imports warehouse models directly for reading.

### Query Pipelines

Each operation runs a well-defined sequence of steps called a **query pipeline**. Raw table data passes through these steps before reaching the consumer.

#### Operation 1: Get Panel Slice

| Step | Name | What it does |
|---|---|---|
| 1 | **Scope** | Validates that requested assets exist in the universe and time range falls within observation window |
| 2 | **Fetch** | Pulls raw rows from each requested feature layer table independently |
| 3 | **Time filter** | Applies `.as_of(simulation_time)` to each table's results (PIT enforcement) |
| 4 | **Align** | Joins results from multiple feature layers into a single panel — matching on join key fields, handling resolution mismatches, attaching staleness fields, enforcing null handling rules |
| 5 | **Return** | Consumer receives wide-format result — one row per asset per timestamp, columns from all requested feature layers |

**Critical ordering:** Time filtering happens **before** alignment. If alignment happened first, forward-fill could carry a value from a future row before PIT removes it.

#### Operation 2: Get Universe Members

| Step | Name | What it does |
|---|---|---|
| 1 | **Time filter** | **Event-driven:** Returns only assets whose anchor event occurred at or before simulation time T and whose membership_end is null or after T. **Calendar-driven:** Returns all assets matching the inclusion criteria (no per-asset time filter). |
| 2 | **Return** | Consumer receives list of assets with their master data |

**Why this exists separately:** An asset might exist in the universe (anchor event occurred) but have no observations yet (first interval hasn't closed). Strategies use this to decide which assets to evaluate, then call get panel slice for actual data.

#### Operation 3: Get Reference Data

| Step | Name | What it does |
|---|---|---|
| 1 | **Scope** | Validates asset and time range |
| 2 | **Time filter** | Applies PIT using the table's declared availability rule — only returns events visible at simulation time |
| 3 | **Return** | Consumer receives raw event records in chronological order |

**Why reference data stays separate:** Reference data is granular — potentially hundreds or thousands of events per time interval. Joining into the panel would explode row count and destroy the "one row per asset per timestamp" structure.

#### Pipeline Summary

| Operation | Steps | Cross-table? |
|---|---|---|
| **Get panel slice** | Scope → Fetch → Time filter → Align → Return | Yes |
| **Get universe members** | Time filter → Return | No |
| **Get reference data** | Scope → Time filter → Return | No |

### Service Contract

The data service makes explicit guarantees (**data contract**) about what it returns. Consumers rely on these without verifying themselves.

#### Four Guarantees

| # | Guarantee | Meaning |
|---|---|---|
| 1 | **Temporal safety** | Every row returned has knowledge time ≤ simulation time. No exceptions. |
| 2 | **Alignment completeness** | Get panel slice returns no partial rows. All requested feature layers are present in every returned row. |
| 3 | **Staleness transparency** | Forward-filled values always have an attached staleness field. Stale data is never silently presented as fresh. |
| 4 | **Data integrity** | Every row has passed all hard reject gate checks. If it's in the warehouse, it's clean. |

#### Three Exclusions

| # | Not guaranteed | Reason |
|---|---|---|
| 1 | **Completeness** | Sparse data is returned as-is. The data service does not fill gaps or warn about missing observations. Consumer's responsibility. |
| 2 | **Formula correctness** | The service guarantees it followed the derived feature formula, not that the formula is right. |
| 3 | **Freshness** | The service serves what's in the warehouse. No real-time guarantees. |

### Data Service Decision Points

#### WDP10: Alignment Mechanics

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: Database SQL** | The database handles all alignment — joins, forward-fill, staleness computation — in SQL. | Fast for large datasets. Databases are optimized for joins. | Forward-fill and staleness logic are awkward in SQL. Hard to read, test, and debug. |
| **B: Application code** | Each feature layer is fetched separately, then application code merges them in memory. | Forward-fill and staleness logic are straightforward in code. Readable, testable, debuggable. | Slower for very large datasets since all data must be loaded into memory. |
| **C: Hybrid** | Database handles the inner join (matching on key fields). Application code handles forward-fill and staleness on the joined result. | Balances database efficiency with code readability. | More complex implementation — logic split across two layers. |

**General principle:** Start with Option B. Move to C or A only when performance becomes a measured problem.

#### WDP11: Derived Feature Timing

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: Pre-computed** | Pipeline calculates derived features and stores them in the warehouse as additional columns or separate feature layer tables. | Fast reads — value already stored. Easy reproducibility — stored value never changes. | Every new feature requires pipeline changes. Formula changes require recomputing all historical values. Storage grows. |
| **B: On-the-fly** | Data service computes derived features during get panel slice, between Align and Return steps. Nothing stored. | Adding new features is just a formula definition. No pipeline changes. Fast experimentation. | Slower reads. Reproducibility breaks if formula changes between runs unless formulas are versioned. |
| **C: Split by maturity** | Stable, well-established features are pre-computed. Experimental features are on-the-fly. | Balances performance and flexibility. | Must manage two computation paths. Must decide when a feature is "stable enough" to pre-compute. |

**General principle:** Early-stage research systems lean toward Option B because experimentation speed is the priority. Migrate individual features to pre-computed as they stabilize.

#### WDP12: Contract Enforcement

| Option | Description | Pros | Cons |
|---|---|---|---|
| **A: Trust the warehouse** | Data service assumes all stored data has passed gate checks. No re-validation on read. | Fast. Clean separation of responsibility. | If a pipeline bug bypasses gate checks, corrupt data is served. |
| **B: Verify on read** | Data service re-checks quality constraints before returning data. | Defense in depth — catches corruption even with pipeline bugs. | Slow — validation on every read. Redundant if gate checks work. Blurs responsibility. |
| **C: Trust on read, audit periodically** | Data service trusts the warehouse at read time. Standalone audit commands verify periodically. Problems are flagged and investigated. | Industry standard. Each layer has one job: pipeline validates, data service serves, audits verify. | Corrupt data could be served between audit runs if a pipeline bug exists. |

**Industry convention:** Option C — validate on write, trust on read, audit periodically.

#### WDP13: Error Handling

The industry distinguishes between **invalid requests** (consumer bugs) and **valid requests with no data** (real market outcomes):

| Situation | Option A: Raise error | Option B: Return empty |
|---|---|---|
| Asset not in universe | **Industry convention** — likely a consumer bug, surface immediately | Hides the bug |
| Time range outside observation window | **Industry convention** — likely a consumer bug, surface immediately | Hides the bug |
| Valid request, no data exists | Overly strict — empty data is a real outcome | **Industry convention** — the answer is legitimately "nothing" |

---

## Glossary

*Terms specific to pipeline architecture are defined in the Pipeline Implementation Guide glossary.*

**Abstract base model** — A Django model with `abstract = True` that creates no database table. It defines shared fields, constraints, and methods that concrete models inherit. In this warehouse, abstract bases encode the quantitative trading paradigm's structure for each table category.

**Alignment** — The process of joining results from multiple feature layer tables into a single panel. Involves matching on join key fields, handling resolution mismatches (forward-fill), attaching staleness fields, and enforcing null handling rules.

**Append-only** — A warehouse convention where rows in time series and event tables are never updated or deleted after insertion. Observations are historical facts — modifying them would violate PIT semantics and break reproducibility.

**As-of time** — The first of two timestamps on every data point. Represents when the event actually occurred in the market. For a 1:00–1:05 candle, the as-of time is 1:05. See also: "observation time" in the Pipeline Implementation Guide, which describes the same concept from the pipeline perspective.

**Availability rule** — A per-layer attribute that defines when a data point becomes visible to a strategy. The quantitative trading paradigm defines three types: end-of-interval, event-time, and publication-time. Each concrete feature layer or reference table declares which rule applies.

**Calendar time** — Wall clock time, including weekends, holidays, and non-trading hours. Used for 24/7 markets (crypto, forex). Contrast with trading time.

**Calendar-driven universe** — A universe type where the observation window is an absolute time range, the same for all assets. There is no per-asset anchor event. Example: "BTC, ETH, SOL from Jan 2024 to Jan 2026." Contrast with event-driven universe.

**Cross-sectional time series** — Another name for panel data. "Cross-sectional" because multiple assets exist at each point in time. "Time series" because each asset has observations across time.

**Data contract** — See service contract.

**Data service** — The access layer that exposes the narrow interface. A standalone module with three read-only operations. All consumer reads go through the data service. Also called **feature service** when it specifically serves computed features.

**Data warehouse** — The storage layer where raw and validated data lives. Contains universe tables, feature layer tables, and reference tables. Also called **data store**.

**Derived feature** — A value computed from one or more raw feature layers. Not stored directly from a data source. Examples: moving averages, RSI, volume ratios. Each has a formula, parameters, and a warm-up period.

**End-of-interval** — An availability rule type where data becomes visible at the close of its time interval. A candle covering 1:00–1:05 becomes available at 1:05.

**Event facts** — The quant warehouse classification for reference tables. Each row represents one discrete event with its own exact timestamp. No fixed interval.

**Event-time** — An availability rule type where data becomes visible at the exact moment it occurs. Used for discrete events like individual transactions.

**Event-driven universe** — A universe type where each asset has its own anchor event (T0), and the observation window is defined as offsets relative to that anchor. Example: "All newly listed tokens, from listing date to T0 + 30 days." Contrast with calendar-driven universe.

**Event window** — In the quantitative trading paradigm, a time range defined by two offsets (t₁, t₂) from an anchor event. The observation window in a universe definition follows this convention.

**Feature layer** — A time-aligned series of measurements within a universe. Each layer has its own temporal resolution, data source, availability rule, and gap handling rules. Stored as a separate time series fact table in the warehouse.

**Forward-fill** — A resolution mismatch strategy where the last known value from a slower-resolution layer is carried forward to fill slots on the faster-resolution grid until the next actual observation arrives.

**Gate check** — Validation that runs when data enters the warehouse. Data that fails is rejected at the gate — it never enters the table. Implemented at the database level (CHECK constraints) and/or application level (model validation).

**Knowledge time** — The second of two timestamps on every data point. Represents when the data became known. For most real-time market data, knowledge time equals as-of time. For delayed or revised data, knowledge time may be later.

**Look-ahead bias** — A backtesting error where a strategy uses data that would not have been available at the simulated point in time. PIT enforcement exists specifically to prevent this.

**Master data** — The quant warehouse classification for universe tables. One row per asset. Contains identity and anchor event. Referenced by all other tables.

**Membership end** — An optional per-row field on the universe table. Records when an asset left the universe. Null means the asset is still a member (or the universe has permanent membership). The "Get universe members" operation uses a two-sided filter: anchor_event <= T AND (membership_end IS NULL OR membership_end > T). Not all universes need this — universes with permanent membership (no rebalancing, no exit events) leave it null for every asset.

**Narrow interface** — An architectural pattern where the data layer exposes a deliberately small number of entry points (three operations), each guaranteeing correctness. Consumers cannot bypass PIT enforcement or alignment because there is no alternative access path.

**Observation window** — The time range of data collected per asset. For event-driven universes, defined by two offsets (t₁, t₂) from the anchor event following the event study convention. For calendar-driven universes, defined as absolute start and end times shared by all assets.

**Panel data** — The standard structure for quantitative data: asset × time × feature. Feature layers stored in separate tables are merged into a single panel through alignment. The consumer sees one row per asset per timestamp with all features as columns.

**Per-definition constant** — A quantitative trading paradigm attribute whose value is the same for every row in a concrete model. Stored as a class-level constant in Django, not a database column.

**Per-row field** — A quantitative trading paradigm attribute whose value differs for each row in the table. Stored as a database column (Django model field).

**PIT (Point-in-Time)** — Rules that make a backtest behave like real time by controlling when each data point becomes visible to a strategy. Filters on knowledge time ≤ simulation time. In live trading, time enforces this naturally. In backtesting, PIT rules enforce it artificially.

**Post-insert audit** — Periodic checks that run against data already in the warehouse. Catches patterns that are suspicious but not corrupt (e.g. sparse data). Does not block data from entering — produces reports and flags anomalies.

**Publication-time** — An availability rule type where data becomes visible when published, not when measured. Relevant for reports, announcements, and data sources that revise historical values.

**Query pipeline** — The sequence of steps that raw table data passes through inside a data service operation before reaching the consumer. Each operation has its own pipeline.

**Reference table** — Granular, event-based data outside the fixed time-interval grid. Queried on demand, not auto-joined into the panel. Classified as event facts in the warehouse.

**Separation between storage and access** — The foundational architectural principle: the best way to store data (normalized tables, no redundancy) is different from the best way to use data (merged panel, PIT-enforced). The warehouse handles storage. The data service handles access.

**Service contract** — Explicit guarantees the data service makes about what it returns: temporal safety, alignment completeness, staleness transparency, and data integrity. Also states what is not guaranteed: completeness, formula correctness, and freshness.

**Staleness field** — A field automatically attached during alignment when forward-filling from a slower-resolution layer. Value is 0 for actual observations and increases with each forward-filled interval. Lets consumers decide how fresh the data needs to be.

**Surrogate key** — A meaningless auto-increment integer used as the primary key when the database or framework doesn't natively support composite keys. The natural key (asset + timestamp) is enforced separately via a uniqueness constraint.

**Timestamp convention** — Whether the timestamp field in a feature layer stores the start or the end of the interval. A dataset-level decision (WDP9) that must be consistent across all layers and is critical for correct PIT computation.

**Time series facts** — The quant warehouse classification for feature layer tables. Each row is one observation of one asset at one point in time. Compound key is always asset + timestamp.

**Trading time** — Market hours only, skipping weekends, holidays, and non-trading periods. Used for markets with defined trading sessions (stocks, futures). Contrast with calendar time.

**Universe table** — The top-level scope table in the warehouse. Defines which assets exist and their anchor events. All feature layer and reference tables reference the universe table via foreign key. Classified as master data.

**Wide format** — A panel layout where one row represents one asset at one timestamp, and every feature appears as a separate column. The standard format for research-facing data in quantitative trading. Contrast with long format where each feature is a separate row.
