# Paradigm Audit Report

Generated 2026-03-10. Covers Prep A (terminology), Prep B (cross-check), and Task 2 (paradigm file audit).

---

## Prep A: Master Terminology Reference

Organized by category. Where a term appears in multiple sources, all are listed. `WG` = warehouse_implementation_guide.md, `PG` = pipeline_implementation_guide.md, `DS` = u001_data_specification.md (paradigm sections only).

### Core Paradigm Concepts (the 7 data spec concepts)

| Term | Source(s) | Line(s) | Definition |
|---|---|---|---|
| Universe Definition | DS | 27 | Which assets, what time scope — the top-level scope of a dataset |
| Feature Layer | DS, WG | DS:50, WG:153, WG:485 | Time-aligned measurements within a universe at a fixed temporal resolution |
| Join Key | DS | 67 | Rule defining how multiple feature layers align to each other |
| Point-in-Time Semantics (PIT) | DS, WG | DS:79, WG:507 | Rules controlling when data becomes visible to a strategy; prevents look-ahead bias |
| Derived Feature | DS, WG | DS:101, WG:473 | Value computed from raw feature layers, not stored directly from a source |
| Data Quality Constraint | DS | 121 | Invariant that must always be true about the data (hard reject or warning) |
| Reference Dataset / Reference Table | DS, WG | DS:138, WG:515 | Granular event data outside the fixed time grid, queried on demand |

### Table Categories (warehouse classification)

| Term | Source | Line | Definition |
|---|---|---|---|
| Universe table / Master data | WG | 67, 495 | One row per asset — identity and anchor event; referenced by all other tables |
| Feature layer table / Time series facts | WG | 67, 527 | One observation per asset per time interval; compound key is asset + timestamp |
| Reference table / Event facts | WG | 67, 477 | One discrete event per row with its own exact timestamp; no fixed interval |

### Universe Types

| Term | Source(s) | Line(s) | Definition |
|---|---|---|---|
| Event-driven universe | DS, WG | DS:33, WG:481 | Each asset has its own anchor event (T0); observation window is relative offsets |
| Calendar-driven universe | DS, WG | DS:34, WG:463 | No per-asset anchor; observation window is an absolute time range for all assets |

### Observation Window & Time

| Term | Source(s) | Line(s) | Definition |
|---|---|---|---|
| Anchor event | DS, WG | DS:42, WG:143 | The reference point (T0) for each asset in an event-driven universe |
| Observation window | DS, WG | DS:366, WG:499 | Time range of data per asset — offsets (t1, t2) for event-driven, absolute times for calendar-driven |
| Event window | DS, WG | DS:342, WG:483 | Same concept — time range defined by two offsets from anchor (MacKinlay, 1997) |
| Observation window start (t1) | DS, WG | DS:43, WG:121 | Where data collection begins; can be negative (pre-event) |
| Observation window end (t2) | DS, WG | DS:44, WG:122 | Where data collection ends; None = unbounded |
| Calendar time | WG | 461 | Wall clock time including weekends/holidays — for 24/7 markets |
| Trading time | WG | 529 | Market hours only, skipping non-trading periods — for stocks/futures |
| Temporal resolution | DS | 384 | Time interval between observations in a feature layer |
| Timestamp convention | WG | 525 | Whether timestamp stores interval start or end (DP9) |

### Availability Rules

| Term | Source(s) | Line(s) | Definition |
|---|---|---|---|
| Availability rule | DS, WG | DS:324, WG:459 | Per-layer attribute: when a data point becomes visible to a strategy |
| End-of-interval | DS, WG | DS:336, WG:475 | Data visible at the close of its time interval |
| Event-time | DS, WG | DS:340, WG:479 | Data visible at the exact moment it occurs |
| Publication-time | DS, WG | DS:370, WG:511 | Data visible when published, not when measured |

### PIT & Knowledge

| Term | Source(s) | Line(s) | Definition |
|---|---|---|---|
| As-of time | WG | 457 | When the event occurred in the market (first of two timestamps) |
| Knowledge time | DS, WG | DS:362, WG:491 | When data became known (second of two timestamps) |
| Look-ahead bias | DS, WG | DS:364, WG:493 | Backtesting error: using data not available at simulated time |

### Join & Alignment

| Term | Source(s) | Line(s) | Definition |
|---|---|---|---|
| Alignment | WG | 453 | Joining multiple feature layer tables into a single panel |
| Forward-fill | DS, WG | DS:350, WG:487 | Carry last known value from slower layer to fill faster grid |
| Staleness field | DS, WG | DS:380, WG:521 | Field attached during forward-fill showing how old a value is |
| Resolution mismatch rule | DS | 376 | How join key handles layers with different temporal resolutions |
| Inner join | DS | 356 | Row exists only when ALL requested layers have data |
| Row-level existence | DS | 378 | A row "counts" if present in the table regardless of null fields |
| Panel data | WG | 501 | Standard quant structure: asset x time x feature in wide format |
| Wide format | WG | 533 | One row per asset per timestamp, features as columns |

### Quality

| Term | Source(s) | Line(s) | Definition |
|---|---|---|---|
| Hard rejection | DS | 354 | Data is impossible/corrupt — cannot enter a backtest |
| Warning | DS | 394 | Data is valid but unusual — not corrupt |
| Invariant | DS | 358 | Property that must always be true |
| Gap handling | DS | 352 | Rule for when no data exists for a given interval |
| Survivorship bias | DS | 382 | Bias from excluding failed/dead assets |

### Warehouse Architecture

| Term | Source | Line | Definition |
|---|---|---|---|
| Data warehouse / Data store | WG | 471 | Storage layer — models, QuerySets, constraints |
| Data service / Feature service | WG | 469 | Access layer — three read-only operations, no models |
| Narrow interface | WG | 497 | Three entry points — consumers cannot bypass PIT or alignment |
| Separation between storage and access | WG | 517 | Foundational principle: best storage != best access format |
| Service contract / Data contract | WG | 519 | Guarantees the data service makes about its output |
| Query pipeline | WG | 513 | Sequence of steps data passes through in a data service operation |
| Gate check | WG, PG | WG:489, PG:553 | Validation rejecting corrupt data at write time |
| Post-insert audit | WG | 509 | Periodic checks for suspicious-but-valid patterns |
| Append-only | WG | 455 | Time series rows never updated/deleted after insertion |

### Warehouse Model Concepts

| Term | Source | Line | Definition |
|---|---|---|---|
| Abstract base model | WG | 451 | Django abstract model encoding paradigm structure |
| Per-definition constant | WG | 503 | Same value for every row — class-level constant |
| Per-row field | WG | 505 | Different value per row — database column |
| Surrogate key | WG | 523 | Meaningless auto-increment PK; natural key enforced separately |
| Feature set | DS | 348 | Variables captured per observation in a feature layer |

### Pipeline Architecture

| Term | Source | Line | Definition |
|---|---|---|---|
| ETL | PG | 543 | Extract, Transform, Load — transform before load |
| Source connector | PG | 593 | Outermost pipeline layer touching external APIs |
| Anti-corruption layer | PG | 509 | Boundary preventing foreign conventions from leaking in |
| Staging area / Bronze layer | PG | 515, 595 | Where raw data lands before transformation |
| Canonicalization layer / Conformance layer | PG | 519, 527 | Where semantic transformations happen — source-specific to canonical |
| Conformance function | PG | 525 | Pure deterministic function: raw response to canonical records |
| Conformance test | PG | 529 | Test feeding saved raw responses to conformance function |
| Conformed dimensions | PG | 531 | Kimball: every source mapped to shared definitions |
| Canonical form | PG | 517 | Single standard representation all variants are reduced to |
| Field mapping table | PG | 549 | Explicit doc of how each source field becomes a warehouse field |
| Dimension table | PG | 541 | Supporting lookup table for the pipeline, not the consumer |
| DAG | PG | 533 | Directed Acyclic Graph — task dependency ordering |
| Adapter pattern | PG | 507 | Source connector = extractor adapter; conformance = transform adapter |

### Pipeline Transformations

| Term | Source | Line | Definition |
|---|---|---|---|
| Semantic transformation | PG | 589 | Changes what data claims to represent — must be centralized/versioned |
| Computational transformation | PG | 523 | Derived quantities from established data — placement decision |
| Time conformance | PG | 603 | Converting timestamps to canonical format |
| Type conformance | PG | 605 | Casting source types to warehouse types |
| Symbol conformance | PG | 599 | Mapping source identifiers to warehouse identifiers |
| Value conformance | PG | 609 | Normalizing values to warehouse denomination |

### Pipeline Three Timelines

| Term | Source | Line | Definition |
|---|---|---|---|
| Observation time / Event time | PG | 569 | When the market produced the observable |
| Processing time / Ingestion time | PG | 575 | When the pipeline received and stored the data |
| Decision time | PG | 537 | When a strategy commits to an action |

### Pipeline Properties

| Term | Source | Line | Definition |
|---|---|---|---|
| Idempotency | PG | 559 | Same inputs to same result, no matter how many times run |
| Idempotency scope | PG | 561 | Data range one pipeline run claims ownership over |
| Backfill | PG | 511 | Retroactively loading historical data through the same code path |
| Bootstrap | PG | 513 | Initial load for a new asset — full-window fetch |
| Watermark / High-water mark | PG | 611 | Latest ingested data point per asset |
| Reconciliation | PG | 583 | Post-load verification: received matches expected |
| Provenance | PG | 577 | Tracking where each data piece came from |
| Data lineage | PG | 535 | Full path from source to warehouse |
| Raw fidelity | PG | 581 | Staging area principle: store exactly what API returned |
| Re-transformability | PG | 587 | Ability to re-process raw data without re-fetching |
| Code path unification | PG | 521 | Daily runs and backfills use same implementation |
| Pipeline divergence | PG | 573 | Anti-pattern: separate backfill script diverges from main pipeline |

### Pipeline Idempotency Mechanisms

| Term | Source | Line | Definition |
|---|---|---|---|
| Upsert | PG | 607 | Insert new, update existing — simple but conflicts with append-only |
| Delete-write | PG | 539 | Delete target scope, insert fresh in one transaction |
| Skip-existing | PG | 591 | Only insert rows not already present — strictest append-only |

### Pipeline Extract Strategies

| Term | Source | Line | Definition |
|---|---|---|---|
| Full load | PG | 551 | Every run fetches entire dataset from scratch |
| Incremental load | PG | 563 | Fetch only data newer than high-water mark |
| Windowed incremental | PG | 613 | Incremental with overlap margin as safety net |
| Steady-state | PG | 597 | Normal operation after initial bootstrap |
| Re-fill | PG | 585 | Re-run for specific asset/time range to replace bad data |

### Pipeline Failure Modes

| Term | Source | Line | Definition |
|---|---|---|---|
| Temporal integrity risk | PG | 601 | Timestamp convention mismatch, timezone confusion, format inconsistency |
| Heterogeneity risk | PG | 555 | Same field name, different meaning across sources |
| Operational risk | PG | 571 | Infrastructure failures producing valid-looking incomplete data |
| Data leakage risk | PG | 499-501 | Vendor revises historical value after ingestion |

### Warehouse Decision Points (DP1-DP13)

| DP | Name | Source | Line |
|---|---|---|---|
| DP1 | Primary Key Strategy | WG | 216 |
| DP2 | Foreign Key Target | WG | 224 |
| DP3 | Nullable Fields | WG | 231 |
| DP4 | Abstract Base Models | WG | 238 |
| DP5 | Append-Only Convention | WG | 250 |
| DP6 | Quality Constraint Placement | WG | 258 |
| DP7 | Indexing Strategy | WG | 266 |
| DP8 | Data Types | WG | 276 |
| DP9 | Timestamp Convention | WG | 291 |
| DP10 | Alignment Mechanics | WG | 407 |
| DP11 | Derived Feature Timing | WG | 417 |
| DP12 | Contract Enforcement | WG | 427 |
| DP13 | Error Handling | WG | 437 |

### Pipeline Decision Points (DP1-DP11)

| DP | Name | Source | Line |
|---|---|---|---|
| DP1 | Extract Strategy | PG | 305 |
| DP2 | ETL vs ELT | PG | 315 |
| DP3 | Idempotency Mechanism | PG | 325 |
| DP4 | Watermark Strategy | PG | 341 |
| DP5 | Rate Limit Handling | PG | 349 |
| DP6 | Error Handling | PG | 359 |
| DP7 | Reconciliation Strategy | PG | 370 |
| DP8 | Provenance Tracking | PG | 381 |
| DP9 | Multi-Source Handling | PG | 391 |
| DP10 | Scheduling | PG | 401 |
| DP11 | Dimension Table Location | PG | 411 |

**Note: Both guides use "DP" numbering independently.** Warehouse DP1-DP13, Pipeline DP1-DP11. The namespaces overlap (both have DP1, DP2, etc.) but refer to different decisions. Dataset records disambiguate by referencing the correct guide in their header.

---

## Prep B: Cross-Check Between the Two Paradigm Guides

### Shared Terms — Consistency Check

| Term | Warehouse Guide | Pipeline Guide | Verdict |
|---|---|---|---|
| **Feature layer** | L485: "time-aligned series of measurements within a universe" | Used throughout but no glossary entry; body text at L128 consistent | Consistent |
| **Reference table** | L515: "Granular, event-based data outside the fixed time-interval grid" | Used at L252, L331; consistent | Consistent |
| **Universe** | L531: "top-level scope table...defines which assets exist" | Used at L146, L248, L329; consistent | Consistent |
| **Gate check** | L489: "Validation that runs when data enters the warehouse" | L553: "Validation from the warehouse's Four Shelves of Logic...the pipeline's last line of defense" | Consistent (different perspectives, same concept) |
| **Observation time** | Not in glossary; "as-of time" used (L457) | L569: "When the market produced the observable" | **Different name for related concept.** WG uses "as-of time" for when the event occurred. PG uses "observation time." Same thing, different labels. |
| **Backfill** | Not in glossary; not mentioned in body | L511: "Retroactively loading historical data through the same code path" | No conflict (pipeline-owned concept) |
| **Append-only** | L455: "rows in time series and event tables are never updated or deleted" | L327, L339: references "warehouse's append-only convention (Warehouse Implementation Guide DP5)" | Consistent; pipeline defers to warehouse |
| **Derived feature** | L473: "computed from one or more raw feature layers. Not stored directly from a data source." | Not in glossary; DP11 discusses derived feature timing | Consistent |
| **PIT** | L507: "Filters on knowledge time <= simulation time" | L76: references "PIT enforcement in the data service (`.as_of()`)" | Consistent |
| **Reconciliation** | Not in glossary; mentioned at L401 as "not guaranteed: Completeness" | L583: full definition as pipeline concept | No conflict (correctly owned by pipeline) |
| **Data service** | L469: full definition as access layer | L60, L76, L238: references consistently | Consistent |

### Inconsistencies Found

**B-1: "Observation window" glossary definition incomplete in warehouse guide**

| | Warehouse Guide (L499) | Dataset Spec (L366) |
|---|---|---|
| Definition | "defined by two offsets (t1, t2) from the anchor event" | "For event-driven universes, defined as two offsets (t1, t2) from the anchor event...For calendar-driven universes, defined as absolute start and end times." |

The warehouse guide glossary only describes the event-driven case. The dataset spec correctly covers both. The warehouse guide body text (L144-145) does cover both, but the glossary entry is incomplete.

**B-2: "As-of time" vs "Observation time" — different terms for overlapping concepts**

The warehouse guide defines "as-of time" (L457) as "when the event actually occurred in the market." The pipeline guide defines "observation time" (L569) as "when the market produced the observable." These describe the same thing but use different names. Neither guide acknowledges the other term.

Additionally, the warehouse guide says "as-of time" is 1:05 for a 1:00-1:05 candle (the interval END), while the pipeline guide's "observation time" is "when trades in a 1:00-1:05 candle actually happened" (the interval DURING). Subtle semantic difference.

**B-3: DP numbering collision**

Both guides use "DP" prefix with overlapping numbers (DP1-DP9+ in both). There is no namespace prefix. The dataset records disambiguate by referencing the correct guide in their header, but the collision could cause confusion. For example, "DP3" is "Nullable Fields" in the warehouse guide but "Idempotency Mechanism" in the pipeline guide.

**B-4: Pipeline guide uses "market event" in conformance section — warehouse doesn't define it**

Pipeline guide L457: "identical semantics for the same market event." The term "market event" isn't defined in either glossary. In context it means "the same real-world occurrence (e.g., the same 5-min candle)," but it could be confused with event-driven universes or event-time availability.

### Concepts That Should Appear in Both But Don't

| Concept | Present in WG? | Present in PG? | Gap |
|---|---|---|---|
| **Three table categories** (universe, feature layer, reference) | Core topic | Referenced but not defined | PG assumes familiarity — fine since it references WG |
| **Availability rule** | Defined | Not in glossary, referenced at L76 | Minor gap — PG could reference WG definition |
| **Observation window** | Defined | Referenced at L228, L309 | PG uses it but doesn't define — acceptable |
| **Reconciliation** | Mentioned but not defined | Core topic | WG could note that reconciliation is a pipeline responsibility |
| **Dimension table** | Not mentioned | Defined | WG should acknowledge dimension tables exist (referenced in WG DP11 discussion but not defined) |

### Verdict

The two guides are **largely consistent**. They were designed with clean separation (warehouse owns storage/access, pipeline owns ETL). The issues found are: one incomplete glossary definition (B-1), one terminology mismatch (B-2), one structural collision (B-3), and one undefined term (B-4). None are contradictions — they're gaps and ambiguities.

---

## Task 2: Paradigm File Audit

Files audited (latest paradigm files only):
1. `warehouse_implementation_guide.md` — pure paradigm
2. `pipeline_implementation_guide.md` — pure paradigm
3. `u001_data_specification.md` — paradigm sections (L1-156, L309-395)
4. `models.py` — paradigm code

---

### Category 1: Paradigm Leaks (dataset-specific content in paradigm files)

#### In `warehouse_implementation_guide.md`

| # | Line(s) | Issue | Severity |
|---|---|---|---|
| PL-1 | 134 | Event-driven universe example: "All tokens that graduated from pump.fun, from graduation to T0 + 5000 min" | Minor — labeled as example, but uses U-001 verbatim rather than a generic example |
| PL-2 | 481 | Glossary "Event-driven universe" example: "All graduated tokens, from T0 to T0 + 5000 minutes" | Minor — same U-001 example baked into the glossary |
| PL-3 | 287 | Data types table: "Counts (holders, transactions)" | Borderline — "holders" is a reasonably generic example, "transactions" is fully generic. Acceptable. |

#### In `pipeline_implementation_guide.md`

| # | Line(s) | Issue | Severity |
|---|---|---|---|
| PL-4 | 273 | "duplicate candles inflate volume calculations. Duplicate holder snapshots corrupt change-over-time computations" | Minor — uses FL-001 and FL-002 as inline examples without IDs, but "candles" and "holder snapshots" are U-001 language |
| PL-5 | 541 | Glossary "Dimension table" example: "source mapping (mint -> pool address)" | Moderate — "mint" and "pool address" are Solana/U-001 specific. A paradigm glossary should use a generic example like "warehouse asset ID -> source query key" |

#### In `u001_data_specification.md` (paradigm sections: L1-156, L309-395)

| # | Line(s) | Issue | Severity |
|---|---|---|---|
| PL-6 | 136 | "coin only has 12 candles out of 1000 possible. This is not corrupt — the coin just died." | Moderate — "coin" is U-001 language; "coin died" is memecoin-specific behavior. Paradigm should use "asset" and a generic example. |
| PL-7 | 311 | "Copy U-001 template, assign next ID (U-002, etc.)" | Minor — references U-001 by name in paradigm instructions |
| PL-8 | 328 | Glossary "Candle-aligned inclusive": "Chosen to preserve the first moments after graduation." | **Severe** — This entire glossary entry is U-001 specific. "Candle-aligned inclusive" is a U-001 boundary rule (chosen for its observation window), not a paradigm concept. It should not be in the paradigm glossary. |
| PL-9 | 338 | Glossary "Event-driven universe" example: "All graduated tokens, from T0 to T0 + 5000 minutes" | Minor — U-001 as example (same as PL-2) |
| PL-10 | 344 | Glossary "Exclusion criteria": "In U-001, this is set to 'none' to avoid survivorship bias." | Moderate — U-001 specific reference in paradigm definition |
| PL-11 | 346 | Glossary "Feature layer" examples: "OHLCV candles (FL-001), holder snapshots (FL-002)" | Moderate — U-001 layer IDs in paradigm definition |
| PL-12 | 348 | Glossary "Feature set": "For FL-001: open_price, high_price, low_price, close_price, volume (all in USD)." | Moderate — U-001 feature set in paradigm definition |
| PL-13 | 352 | Glossary "Gap handling": "FL-001's rule: no candle is created if no trades occurred." | Moderate — U-001 specific rule as the paradigm example |
| PL-14 | 356 | Glossary "Inner join": "If FL-001 has a row at 1:05 but FL-002 doesn't, the 1:05 row is dropped." | Moderate — U-001 layer IDs in paradigm definition |
| PL-15 | 360 | Glossary "Join key": "coin + timestamp" | Moderate — "coin" is U-001 entity name. Paradigm should use "asset + timestamp" |
| PL-16 | 362 | Glossary "Knowledge time": "In PIT-001, knowledge time equals as-of time" | Moderate — U-001 PIT ID in paradigm definition |
| PL-17 | 374 | Glossary "Reference dataset": "get all transactions for coin X" | Minor — "coin" is U-001 language |
| PL-18 | 376 | Glossary "Resolution mismatch rule": "Current rule: forward-fill..." | Minor — "Current rule" implies a specific implementation, not a paradigm definition |
| PL-19 | 382 | Glossary "Survivorship bias": "U-001 avoids this by including all graduated tokens with no exclusion criteria." | Moderate — U-001 specific in paradigm definition |
| PL-20 | 384 | Glossary "Temporal resolution": "FL-001 and FL-002 both use 5-minute resolution." | Moderate — U-001 specific in paradigm definition |
| PL-21 | 386 | Glossary "Universe": "In U-001: all tokens launched on pump.fun and migrated to Pumpswap." | **Severe** — The paradigm definition of "Universe" literally names pump.fun and Pumpswap |
| PL-22 | 394 | Glossary "Warning": "This is normal for memecoins." | Minor — memecoin-specific framing |

#### In `models.py`

| # | Line(s) | Issue | Severity |
|---|---|---|---|
| — | — | **No paradigm leaks found.** All constants are None, all comments describe paradigm-level concepts without U-001 specifics. | Clean |

#### Summary: Paradigm Leaks

| Severity | Count | Files affected |
|---|---|---|
| Severe | 2 | u001_data_specification.md (PL-8, PL-21) |
| Moderate | 10 | u001_data_specification.md (PL-6, PL-10-16, PL-19-20), pipeline_implementation_guide.md (PL-5) |
| Minor | 7 | warehouse_implementation_guide.md (PL-1, PL-2), pipeline_implementation_guide.md (PL-4), u001_data_specification.md (PL-7, PL-9, PL-17, PL-18, PL-22) |
| **Total** | **19** | |

The biggest problem is the `u001_data_specification.md` glossary: **15 of 19 leaks are in lines 320-395** (the glossary that's supposed to be paradigm-level). The glossary was clearly written with U-001 in mind and uses it as the sole source of examples.

---

### Category 2: Missing Concepts

Issues that would surface when trying to define U-002 through U-005 using only the current paradigm files.

#### MC-1: No concept for assets LEAVING a universe (membership exit)

**Affected files:** warehouse guide (L126-151, L333, L361), dataset spec (L27-48), models.py (L52-78)

**Problem:** The paradigm only models assets ENTERING a universe (via `anchor_event`). There is no concept of an asset exiting — no `exit_event`, no `membership_end`, no membership validity range.

**U-002 impact:** A blue chip index has quarterly rebalancing. BTC might be in the index from Jan 2024 to Sep 2025, removed, then re-added in Jan 2026. The current paradigm has no way to represent this. The "Get universe members" operation (WG L361: "anchor_event <= simulation_time") would say BTC is a member forever after its first entry.

**U-005 impact:** Liquidation cascade events have a natural end. The "event" is over when the cascade stops. But there's no concept of the entity ceasing to be relevant.

**Suggested fix:** Add optional `exit_event` or `membership_end` per-row field to UniverseBase. Or: add a membership validity model (asset + valid_from + valid_to) for universes with rebalancing. Either way, "Get universe members" needs a two-sided filter: `anchor_event <= T AND (exit_event IS NULL OR exit_event > T)`.

#### MC-2: Calendar-driven "Get universe members" is undefined

**Affected files:** warehouse guide (L333, L357-362)

**Problem:** The guide says: "For the 'Get universe members' operation, the filter is `anchor_event <= simulation_time`." But for calendar-driven universes, `anchor_event` is null for all assets (WG L143: "Null for calendar-driven universes"). The filter `NULL <= simulation_time` would return nothing.

**U-002, U-004 impact:** Both are calendar-driven. The guide doesn't explain how membership works when there's no per-asset anchor event.

**Suggested fix:** Add calendar-driven case to the membership operation: "For calendar-driven universes, all qualifying assets are members for the duration of the observation window. Membership is determined by the inclusion criteria, not by anchor_event." Or: define a different `active_from` field that isn't tied to the event-driven concept.

#### MC-3: Conditional feature layers not addressed

**Affected files:** dataset spec (L50-66), warehouse guide (L153-175)

**Problem:** The paradigm assumes every feature layer applies to every asset in the universe. There is no concept of a layer that only applies to a subset of assets based on a condition.

**U-003 impact:** A CEX new listing universe might have a "funding rate" feature layer. But funding rates only exist if a perpetual futures market has been created for that token. Not all listed tokens get perp markets. This layer would have no data for some assets — not because of gaps, but because the data source literally doesn't exist for that asset.

**Current workaround:** The inner join rule would drop these rows, and gap handling covers the "no data" case. But the CONCEPT isn't articulated. A researcher looking at the spec would think "missing data means the pipeline failed" rather than "missing data means this layer doesn't apply to this asset."

**Suggested fix:** Add a "Layer applicability condition" optional attribute to the Feature Layer concept. When present, it states the condition under which this layer produces data. When absent (default), the layer applies to all assets.

#### MC-4: Entity type — "asset" used exclusively, no generalization

**Affected files:** All paradigm files. Warehouse guide uses "asset" 37 times, dataset spec 9 times. Neither uses "entity."

**Problem:** The paradigm uses "asset" as the term for the entity being tracked. This implies a tradable financial instrument.

**U-005 impact:** The entity in a liquidation cascade study is an EVENT (a cascade), not an asset (a token). The "asset identity field" would hold a cascade ID. This works mechanically but creates confusion: a cascade isn't an "asset," and phrases like "all assets at time T" read strangely when the "asset" is a market event.

**Severity:** Low-to-moderate. Mechanically, the paradigm handles it — the identity field is a generic CharField. But the language creates friction and potential confusion.

**Suggested fix:** Either (a) adopt "entity" as the paradigm term (replacing "asset" everywhere), or (b) add a note in the Universe concept: "The paradigm uses 'asset' as the default term for the entities tracked by a universe. In some universes, the entity may not be a traditional financial asset (e.g., a market event, a protocol, a liquidity pool). The paradigm structure is the same regardless of entity type."

#### MC-5: Open-ended observation windows — pipeline implications not addressed

**Affected files:** pipeline guide (all reconciliation and extract strategy sections)

**Problem:** Both guides mention "None = unbounded" for observation window end (WG L124, L145; DS L44). But the pipeline guide doesn't address:
- How does reconciliation work when there's no expected end? Count-based reconciliation needs an expected count, which requires a finite window.
- How does the pipeline know when to stop fetching for steady-state? With a fixed window (U-001), the pipeline stops at T0 + 5000 minutes. With an open-ended window (U-002, U-004), it fetches "up to now" forever.
- What happens to watermark-based incremental loading when the window has no end?

**U-002, U-004 impact:** Both have open-ended observation windows. The pipeline guide's extract strategy section (DP1) assumes an observation window with a known end: "Pipeline fetches the full observation window" (L228). For unbounded windows, there is no "full" window.

**Suggested fix:** Add a section to the pipeline guide addressing open-ended windows: "For unbounded observation windows (end = None), steady-state runs fetch from the watermark to the current time. Reconciliation uses count-based checks relative to the fetched range, not the observation window. The pipeline runs indefinitely on schedule."

#### MC-6: Pre-event observation (negative t1) — partially addressed, pipeline implications missing

**Affected files:** Pipeline guide

**Problem:** The warehouse guide explicitly supports negative t1 (L121: "t1 = -60 means '60 units before anchor'"). But the pipeline guide doesn't address how pre-event data is collected. The pipeline's bootstrap scenario (PG L228) says "Pipeline fetches the full observation window" — which for negative t1 means fetching data from BEFORE the anchor event. This requires the pipeline to know the anchor time in advance, which raises a question: when the anchor event hasn't happened yet, the asset might not even be discoverable. The pipeline guide doesn't discuss this timing challenge.

**U-005 impact:** Liquidation cascade studies need pre-cascade market data. The pipeline must fetch data from before the cascade was identified as such.

**Severity:** Moderate. The concepts are consistent, but the pipeline guide offers no guidance for this pattern.

**Suggested fix:** Add a note to the pipeline guide's extract strategy section: "For negative t1 offsets, the pipeline fetches data from before the anchor event. This implies the anchor event is identified retroactively — the asset enters the universe after data collection would normally have started. Pre-event data must be backfilled after the asset is discovered."

#### MC-7: Feature layers with no fixed resolution — paradigm boundary unclear

**Affected files:** Warehouse guide (L155), dataset spec (L50)

**Problem:** The feature layer definition says "at a fixed temporal resolution" (WG L155). The reference dataset is for "data outside the fixed time-interval grid" (WG L176). But what about data that IS regularly sampled but at a resolution that doesn't evenly divide into intervals? Or data that's conceptually time-series-like but arrives irregularly?

**U-004 impact:** DeFi protocol governance votes are irregular events but might be best modeled as a feature layer with daily resolution where most days have no data (gap handling covers this). Alternatively, they could be a reference dataset queried on demand. The paradigm gives guidance (feature layer = fixed resolution, reference = no fixed resolution), but the boundary isn't discussed for edge cases.

**Severity:** Low. The paradigm's distinction between feature layers and reference datasets handles this reasonably. This is an edge case that could be addressed with a note.

#### Category 2 Summary

| ID | Missing Concept | Severity | Universes Affected |
|---|---|---|---|
| MC-1 | Membership exit / rebalancing | **High** | U-002, U-005 |
| MC-2 | Calendar-driven membership operation | **High** | U-002, U-004 |
| MC-3 | Conditional feature layers | **Moderate** | U-003 |
| MC-4 | Entity type generalization | **Low-Moderate** | U-005 |
| MC-5 | Open-ended window pipeline implications | **Moderate** | U-002, U-004 |
| MC-6 | Pre-event data pipeline guidance | **Moderate** | U-005 |
| MC-7 | Feature layer / reference dataset boundary for irregular data | **Low** | U-004 |

---

### Category 3: Structural Issues

#### ST-1: `u001_data_specification.md` sandwich structure

**Problem:** The file has a paradigm-U001-paradigm sandwich:

```
Lines   1-156:  Paradigm (concept definitions, attribute templates)
Lines 158-307:  U-001 specific (all defined specs, DQs, blocked items)
Lines 309-317:  Paradigm (adding new definitions)
Lines 320-395:  Paradigm glossary (BUT contaminated — 15 U-001 references)
```

**Impact:** When you add U-002, you face a bad choice:
1. **Add U-002 to the same file** — file grows into an unmanageable mix of paradigm template + multiple datasets
2. **Create a separate U-002 file** — the paradigm sections (concept definitions, glossary) are stuck in a file named `u001_data_specification.md` that also contains U-001

The glossary contamination (Category 1 findings PL-6 through PL-22) makes this worse — a new contributor reading the glossary would think terms like "coin," "mint_address," and "FL-001" are paradigm concepts.

**Recommended fix:** Split into three files:
- `data_specification_guide.md` — paradigm only (lines 1-156, 309-317, 320-395 with U-001 examples cleaned out)
- `u001_data_specification.md` — U-001 defined specs (lines 158-307)
- Glossary stays in the guide, but cleansed of all U-001 references

#### ST-2: Paradigm guides have no glossary cross-references

**Problem:** The warehouse guide and pipeline guide each have their own glossary. Neither references the other. Terms like "gate check" appear in both but with different perspectives. Terms like "observation time" (PG) vs "as-of time" (WG) describe overlapping concepts with no cross-reference.

**Impact:** A reader of the pipeline guide won't find "availability rule" or "feature layer" in its glossary. A reader of the warehouse guide won't find "conformance layer" or "watermark." Each guide assumes familiarity with the other.

**Recommended fix:** Add a "see also: [other guide] glossary" note to each guide. Or: create a single shared glossary that both guides reference. Since the guides were designed for different audiences (warehouse = data modeling, pipeline = ETL), separate glossaries with cross-references may be better than a merged one.

#### ST-3: DP numbering collision between guides

**Problem (from Prep B-3):** Both guides use "DP" prefix starting at 1. Warehouse has DP1-DP13, pipeline has DP1-DP11. "DP3" means "Nullable Fields" in one and "Idempotency Mechanism" in the other.

**Impact:** When a dataset record says "DP3: Option B," the reader must check which guide it references (header line) to know what DP3 means. Currently works because each record references its guide, but error-prone.

**Recommended fix:** Prefix with guide initials: WDP1-WDP13 for warehouse, PDP1-PDP11 for pipeline. Or: number continuously (warehouse DP1-DP13, pipeline DP14-DP24).

---

## Full Audit Summary

| Category | Count | Severity Breakdown |
|---|---|---|
| **1. Paradigm Leaks** | 19 | 2 severe, 10 moderate, 7 minor |
| **2. Missing Concepts** | 7 | 2 high, 3 moderate, 2 low |
| **3. Structural Issues** | 3 | All moderate-to-high impact on scalability |
| **Total** | **29** | |

### Recommended Priority Order

1. **ST-1** — Split `u001_data_specification.md` (blocks clean U-002+ creation)
2. **MC-1** — Add membership exit concept (blocks U-002 definition)
3. **MC-2** — Define calendar-driven membership operation (blocks U-002, U-004)
4. **PL-8, PL-21** — Remove severe paradigm leaks from dataset spec glossary
5. **PL-6, PL-10-16, PL-19-20** — Clean remaining glossary contamination
6. **MC-3** — Address conditional feature layers (needed for U-003)
7. **MC-5** — Add open-ended window pipeline guidance (needed for U-002, U-004)
8. **MC-6** — Add pre-event pipeline guidance (needed for U-005)
9. **ST-3** — Rename DPs to avoid collision
10. **B-1, B-2** — Fix glossary inconsistencies between guides
11. **MC-4** — Generalize "asset" terminology (desirable but low urgency)
12. **PL-1, PL-2, PL-4, PL-5** — Clean minor paradigm leaks in guides
13. **MC-7, ST-2** — Low-priority gap and structural fixes
