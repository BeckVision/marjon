# Data Specification Guide

This document defines dataset specifications using the **quantitative trading paradigm**. Each dataset is structured as a **universe definition** with **feature layers**, **join keys**, **point-in-time semantics**, **derived features**, **data quality constraints**, and **reference datasets**.

---

## Concepts Overview

A data specification contains 7 concepts. Each one answers a different question about the dataset:

| # | Concept | Question it answers |
|---|---|---|
| 1 | Universe Definition | Which assets, what time scope? |
| 2 | Feature Layer | What are we measuring? |
| 3 | Join Key | How do layers align to each other? |
| 4 | Point-in-Time Semantics | When does data become visible to a strategy? |
| 5 | Derived Feature | What new numbers are computed from raw layers? |
| 6 | Data Quality Constraint | What guarantees does the data make about itself? |
| 7 | Reference Dataset | What granular data lives outside the time grid? |

**How a backtest uses these:** Load universe → Load feature layers → Apply join key → Apply point-in-time rules → Compute derived features → Validate quality constraints → Reference datasets available on demand.

---

## Concept Attribute Definitions

### 1. Universe Definition

Describes the scope — **what assets and what time scope**. Defined once, reused across layers. The quantitative trading paradigm recognizes two types of universes:

| Type | Description | Anchor event | Observation window |
|---|---|---|---|
| **Event-driven** | Each asset has its own reference point (T0) | Per-asset — each asset has its own T0 | Offsets relative to anchor (t₁, t₂) |
| **Calendar-driven** | All assets share the same absolute time range | None — no per-asset anchor | Absolute start and end times |

**Terminology note:** The paradigm uses "asset" as the default term for the entities tracked by a universe. In some universes, the entity may not be a traditional financial asset (e.g., a market event, a protocol, a liquidity pool). The paradigm structure is the same regardless of entity type.

| Attribute | Description |
|---|---|
| **Universe ID** | Unique identifier (e.g. U-001) |
| **Name** | Human-readable name |
| **Universe** | Inclusion criteria — which assets qualify |
| **Universe type** | Event-driven or calendar-driven |
| **Anchor event** | The reference point (T0) for relative time. Per-asset for event-driven universes. Not applicable for calendar-driven universes. |
| **Observation window start** | Where data collection begins — offset from anchor (event-driven) or absolute time (calendar-driven) |
| **Observation window end** | Where data collection ends — offset from anchor (event-driven) or absolute time (calendar-driven). None = unbounded. |
| **Exclusion criteria** | Disqualification rules, if any |
| **Membership end** | When an asset leaves the universe. Per-asset, optional. None = asset remains a member indefinitely. Not applicable for universes where membership is permanent. |
| **Version** | Spec version for tracking changes |

**Observation window convention:** Follows the event study convention from the quantitative trading paradigm (MacKinlay, 1997). Two boundaries define the window. For event-driven universes, boundaries are offsets from the anchor event using (t₁, t₂) notation. A forward-only window is the special case where t₁ = 0.

### 2. Feature Layer

Describes **what is being measured** within a universe. Each layer is independent — its own resolution, source, and rules. Each layer declares its own **availability rule**, which determines when its data becomes visible to a strategy (point-in-time semantics).

| Attribute | Description |
|---|---|
| **Layer ID** | Unique identifier (e.g. FL-001) |
| **Universe ID** | Which universe this layer belongs to |
| **Name** | Human-readable name |
| **Feature set** | Variables captured per observation |
| **Temporal resolution** | Interval between observations |
| **Availability rule** | When an observation becomes visible to a strategy (end-of-interval, event-time, or publication-time) |
| **Gap handling** | Behavior when no data exists for an interval |
| **Applicability condition** | Optional. When present, states the condition under which this layer produces data. When absent, the layer applies to all assets in the universe. |
| **Data source** | API or system that provides the raw data |
| **Refresh policy** | Static snapshot vs. rolling update |
| **Version** | Layer version for tracking changes |

### 3. Join Key

Defines **how feature layers align to each other** when a strategy requests multiple layers.

| Attribute | Description |
|---|---|
| **Join Key ID** | Unique identifier (e.g. JK-001) |
| **Universe ID** | Which universe this join key applies to |
| **Key fields** | The fields used to align rows |
| **Resolution mismatch rule** | How to handle layers with different temporal resolutions |
| **Null handling** | What happens when one layer has a row but another doesn't |

### 4. Point-in-Time Semantics

Defines **when a data point becomes visible to a strategy**. Prevents look-ahead bias. The availability rule is declared per-layer (see Feature Layer and Reference Dataset). The PIT spec defines the overall rules and assumptions that apply across layers.

| Attribute | Description |
|---|---|
| **PIT ID** | Unique identifier (e.g. PIT-001) |
| **Layer ID** | Which feature layer(s) and/or reference dataset(s) this rule applies to |
| **Lag** | Any additional delay before data is usable |
| **Knowledge time assumption** | Whether data is final at availability time, or subject to revision/delay |
| **Look-ahead protection** | Explicit guarantee that no future data leaks into past observations |

**Common availability rule types** (declared per-layer, not per-PIT spec):

| Type | When data is available | Use case |
|---|---|---|
| End-of-interval | At the close of the interval (T+5min for a 5-min candle) | Candles, periodic snapshots |
| Event-time | At the exact moment it occurs | Individual transactions |
| Publication-time | When published, not when measured | Reports, announcements, delayed data sources |

**Why the availability rule is per-layer:** Different layers may use different data sources with different availability characteristics. A feature layer from a real-time data feed uses end-of-interval. A feature layer from a data source that publishes with delay would use publication-time. The PIT spec defines the shared assumptions (lag, knowledge time, look-ahead protection); the availability rule is a property of the layer itself.

### 5. Derived Feature

**Computed from raw feature layers.** Not raw data — transformations.

| Attribute | Description |
|---|---|
| **Derived ID** | Unique identifier (e.g. DF-001) |
| **Name** | Human-readable name (e.g. "20-candle SMA") |
| **Source layers** | Which feature layer(s) this is computed from |
| **Formula** | Exact computation logic |
| **Parameters** | Configurable inputs (e.g. window size = 20) |
| **Output fields** | The resulting variable names |
| **Warm-up period** | How many observations are needed before the first valid output |

**Why derived features need a spec:**

- **Consistency** — one definition, every strategy gets the same calculation
- **Warm-up period** — explicitly states when the feature starts producing valid output (e.g. a 20-candle MA has no value for the first 19 candles — that's expected, not an error)
- **Traceability** — you can see exactly which raw layer and formula produced a number

### 6. Data Quality Constraint

**Guarantees the dataset makes about itself.** Properties of the data, not how the pipeline enforces them.

| Attribute | Description |
|---|---|
| **Constraint ID** | Unique identifier (e.g. DQ-001) |
| **Scope** | What it applies to (universe, specific layer, or derived feature) |
| **Rule** | The invariant that must always be true |
| **Severity** | Hard rejection (data is corrupt) vs. warning (data is unusual but valid) |
| **Validation method** | How the constraint is checked |

**Important distinction:**

- **Hard rejection** = data is impossible or corrupt (e.g. `high < low`). Cannot enter a backtest.
- **Warning** = data is valid but unusual (e.g. an asset only has 12 observations out of 1000 possible). This is not corrupt — the asset may have ceased trading. Sparse data is not corrupt data.

### 7. Reference Dataset

**Granular data outside the time-aligned grid**, queried on demand. Not joined automatically. Each reference dataset declares its own **availability rule**.

| Attribute | Description |
|---|---|
| **Reference ID** | Unique identifier (e.g. RD-001) |
| **Universe ID** | Which universe this belongs to |
| **Name** | Human-readable name |
| **Record type** | What each row represents (e.g. a single transaction) |
| **Feature set** | Fields per record |
| **Timestamp field** | Which field is used when querying by time range |
| **Availability rule** | When an event becomes visible to a strategy (end-of-interval, event-time, or publication-time) |
| **Access pattern** | How strategies query it |
| **Data source** | API or system providing the data |
| **Refresh policy** | Static snapshot vs. rolling update |
| **Version** | Spec version |

**When to use a feature layer vs a reference dataset:** If data arrives at a regular interval (even if some intervals are empty), use a feature layer — gap handling covers the empty intervals. If data arrives irregularly with no fixed cadence, use a reference dataset. When in doubt, consider whether the data makes sense on a fixed time grid: daily governance votes could be a feature layer with daily resolution (most days empty), while individual swap transactions are naturally a reference dataset.

---

## Adding New Definitions

- **New universe:** Copy an existing universe template, assign next ID
- **New feature layer:** Copy any FL template, assign next ID, set universe ID
- **New reference dataset:** Copy RD template, assign next ID, set universe ID
- **New join key:** Typically one per universe unless specific layer combinations need different rules
- **New derived feature:** Define when a strategy needs a computed field
- **New quality constraint:** Define when a new invariant is identified

---

## Glossary

**Anchor event** — The specific moment that defines T0 for each asset in an event-driven universe. All time references in the observation window are relative to this. Not applicable for calendar-driven universes.

**Availability rule** — A per-layer attribute that determines when a data point becomes "known" and usable by a strategy. The quantitative trading paradigm defines three types: end-of-interval, event-time, and publication-time. Each feature layer and reference dataset declares its own availability rule.

**Calendar-driven universe** — A universe type where the observation window is an absolute time range, the same for all assets. There is no per-asset anchor event. Example: "BTC, ETH, SOL from Jan 2024 to Jan 2026." Contrast with event-driven universe.

**Data quality constraint** — An invariant that must always be true about the data. Violations mean the data is corrupt (hard reject) or unusual (warning). Quality constraints apply to rows that exist — missing rows are handled by gap handling, not quality rules.

**Data specification** — A structured metadata contract that fully describes a dataset's composition, scope, and semantics. Contains 7 concepts: universe, feature layers, join keys, point-in-time semantics, derived features, data quality constraints, and reference datasets.

**Derived feature** — A value computed from one or more raw feature layers, not stored directly from an API. Examples: moving averages, RSI, volume ratios. Each derived feature has a formula, parameters, and a warm-up period.

**End-of-interval** — An availability rule type where data becomes visible at the close of its time interval. A candle covering 1:00–1:05 becomes available at 1:05. The most common rule for interval-based data.

**Event-driven universe** — A universe type where each asset has its own anchor event (T0), and the observation window is defined as offsets relative to that anchor. Example: "All newly listed tokens, from listing date to T0 + 30 days." Contrast with calendar-driven universe.

**Event-time** — An availability rule type where data becomes visible at the exact moment it occurs. Applies to event-based data like individual transactions, not interval-based data like candles.

**Event window** — In the quantitative trading paradigm, a time range defined by two offsets (t₁, t₂) from an anchor event (MacKinlay, 1997). The observation window in an event-driven universe follows this convention.

**Exclusion criteria** — Rules defining which assets are deliberately left out of a universe. Example: exclude assets with less than $1M daily volume. Setting exclusion criteria to "none" avoids survivorship bias.

**Feature layer** — A time-aligned series of measurements within a universe. Each layer has its own temporal resolution, availability rule, data source, and gap handling rules. Examples: OHLCV price candles, holder count snapshots, funding rate series.

**Feature set** — The specific variables captured per observation in a feature layer. Examples: {open, high, low, close, volume} for a price layer, {total_holders, net_change} for a holder layer, {rate, open_interest} for a funding rate layer.

**Forward-fill** — A resolution mismatch strategy where the last known value from a slower layer is carried forward to fill gaps on the faster grid. A 1:00 hourly snapshot fills the 1:05, 1:10, 1:15... slots until the next actual observation at 2:00.

**Gap handling** — The rule defining what happens when no data exists for a given time interval. Examples: "no row created if no trades occurred" or "carry forward last known value" or "insert row with nulls."

**Hard rejection** — A data quality severity level meaning the data is impossible or corrupt and cannot enter a backtest. Example: `high_price < low_price`.

**Inner join** — A null handling strategy where a row only exists in the joined result when ALL requested feature layers have data at that timestamp. If one layer has a row at 1:05 but another doesn't, the 1:05 row is dropped from the joined result.

**Invariant** — A property that must always be true. Used in data quality constraints. Example: "volume is always >= 0."

**Join key** — The rule defining how multiple feature layers align to each other. Specifies which fields match rows (asset + timestamp), how resolution mismatches are handled (forward-fill), and what happens with missing data (inner join).

**Knowledge time** — The moment a data point becomes known and usable. In the simplest case, knowledge time equals as-of time — once the interval closes, the data is immediately available with no revision or delay. Sources that publish with delay or revise data retroactively would need an explicit knowledge time offset.

**Look-ahead bias** — A backtesting error where a strategy uses data that would not have been available at the simulated point in time. Point-in-time semantics exist specifically to prevent this.

**Observation window** — The time range of data collected per asset. For event-driven universes, defined as two offsets (t₁, t₂) from the anchor event following the event study convention (MacKinlay, 1997). For calendar-driven universes, defined as absolute start and end times.

**Point-in-time semantics (PIT)** — Rules that make a backtest behave like real time by controlling when each data point becomes visible to a strategy. Prevents look-ahead bias. In live trading, time enforces this naturally. In backtesting, PIT rules enforce it artificially. Each layer declares its own availability rule; the PIT spec defines shared assumptions (lag, knowledge time, look-ahead protection).

**Publication-time** — An availability rule type where data becomes visible when published, not when measured. Relevant for reports, announcements, and data sources that revise historical values or publish with delay.

**Quantitative trading** — A subset of quantitative finance focused specifically on using mathematical and statistical methods to make trading decisions — finding signals, backtesting strategies, and executing trades systematically.

**Reference dataset** — Granular, event-based data that doesn't fit the fixed time-interval grid of feature layers. Strategies query it on demand ("get all transactions for asset X between T1 and T2") rather than receiving it auto-joined into every row. Each reference dataset declares its own availability rule.

**Resolution mismatch rule** — The rule within a join key that handles feature layers with different temporal resolutions. Common approach: forward-fill the slower layer to the faster grid, and attach a staleness field.

**Row-level existence** — The rule that a row "counts" for inner join purposes if it exists in the table, regardless of whether individual fields within the row are null. A candle with a null feature field still counts as existing.

**Staleness field** — A field automatically added by the join key when forward-filling a slower layer. Named `{layer_id}_{short_name}_staleness_minutes` (e.g. `fl_001_ohlcv_staleness_minutes`). Value is 0 for actual observations, and increases with each forward-filled interval. Lets strategies decide how fresh the data needs to be.

**Survivorship bias** — A bias introduced by excluding failed or dead assets from a dataset. If you only backtest against assets that "survived," your results look artificially good because in live trading you'd encounter assets that fail, delist, or go to zero. Avoided by including all qualifying assets with no exclusion criteria.

**Temporal resolution** — The time interval between observations in a feature layer. Examples: 5-minute candles, 1-hour snapshots, daily summaries.

**Universe** — The inclusion criteria defining which assets belong in a dataset. Examples: all assets in a market-cap-weighted index, all tokens listed on a specific exchange, all liquidation events exceeding a threshold.

**Universe definition** — The top-level scope of a dataset. Defines which assets, what time scope, and what exclusion criteria apply. Can be event-driven (per-asset anchor with relative observation window) or calendar-driven (absolute time range). Feature layers, join keys, and reference datasets all attach to a universe.

**Universe type** — Whether a universe is event-driven (each asset has its own anchor event T0, observation window is relative offsets) or calendar-driven (no per-asset anchor, observation window is absolute times).

**Warm-up period** — The number of observations a derived feature needs before it can produce its first valid output. A 20-candle moving average has a warm-up period of 19 candles — the first 19 rows have no value for this field, which is expected behavior, not an error.

**Warning** — A data quality severity level meaning the data is valid but unusual. Example: an asset with only 12 observations in 5,000 minutes is not corrupt — the asset may have ceased trading or been delisted.