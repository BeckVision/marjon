# Dataset Implementation Record: U-001

**Dataset:** Graduated Pump.fun Tokens — Early Lifecycle
**Reference:** warehouse_implementation_guide.md (for decision point definitions and option details)
**Reference:** u001_data_specification.md (for data contract: universe, feature layers, join keys, PIT rules, quality constraints)

---

## Decision Selections

Each row references a decision point (DP) from the Warehouse Implementation Guide.

| DP | Decision | Selected Option | Reasoning |
|---|---|---|---|
| **WDP1** | Primary Key Strategy | **A: Surrogate + unique_together** | Most battle-tested Django pattern. Full ORM and third-party compatibility. The wasted `id` column is trivial cost for reliability. |
| **WDP2** | Foreign Key Target | **B: FK to natural identifier** | Queries think in mint addresses, not meaningless numbers. Using `to_field="mint_address"` keeps ORM aligned with the domain. Solana mint addresses are unique and stable. |
| **WDP3** | Nullable Fields | **B: All feature columns nullable** | JK-001 states row-level existence is determined by the row being present, regardless of null fields. Null handling is the strategy's responsibility. |
| **WDP4** | Abstract Base Models | **A: Three abstract bases** | The quantitative trading paradigm defines three table categories with distinct structures. Universe base provides anchor_event and observation window logic. Feature layer base provides timestamp and PIT filtering. Reference table base provides timestamp and PIT filtering with different key structure. Each layer declares its own availability rule. |
| **WDP5** | Append-Only Convention | **B: Append-only for time series, updates for master data** | Candles and holder snapshots are historical facts — immutable. The MigratedCoin universe table needs status updates and metadata enrichment. |
| **WDP6** | Quality Constraint Placement | **C: Split by severity and complexity** | DQ-002 (high ≥ low), DQ-003 (open/close between high/low), DQ-004 (volume ≥ 0) → database CHECK constraints. DQ-005 (timestamp within observation window) → Django model `clean()` because it requires cross-table comparison to MigratedCoin.anchor_event. DQ-001 (no duplicates) → already enforced by `unique_together` DB index. DQ-006 and warnings → audit commands. |
| **WDP7** | Indexing Strategy | **B: Compound + timestamp** | `unique_together` covers per-asset time range queries. Separate timestamp index covers cross-sectional queries (all assets at one time). Additional column indexes added only on demand. |
| **WDP8** | Data Types | See detail below | Based on Solana SPL token characteristics. |
| **WDP9** | Timestamp Convention | **A: Interval start** | Most crypto data sources (DexPaprika, GeckoTerminal) deliver candles with the interval start timestamp. PIT computation adds TEMPORAL_RESOLUTION to get interval end. |
| **WDP10** | Alignment Mechanics | **B: Application code** | Forward-fill with staleness tracking is the critical piece. Python implementation is more readable, testable, and debuggable than SQL. Scale doesn't warrant database-level alignment yet. |
| **WDP11** | Derived Feature Timing | **B: On-the-fly** | No derived features defined yet. Priority is experimentation speed. On-the-fly means adding a new feature is just a formula definition — no pipeline changes. |
| **WDP12** | Contract Enforcement | **C: Trust on read, audit periodically** | Pipeline validates on write. Data service serves without re-checking. Audit commands verify periodically. Each layer has one job. |
| **WDP13** | Error Handling | **Raise error for invalid, empty for valid-no-data** | Asset not in universe or time range outside window → raise error (likely consumer bug). Valid request with no data → return empty (coin died, legitimate outcome). |

---

## Time Representation

Crypto markets run 24/7 — calendar time and trading time are identical. All time values in this dataset (observation window offsets, temporal resolution) use Python `timedelta`, which measures calendar time. A stock or futures dataset would need a different representation.

---

## Data Type Selections (WDP8)

| Field | Django Field | Configuration | Reasoning |
|---|---|---|---|
| Prices (open_price, high_price, low_price, close_price) | `DecimalField` | `max_digits=38, decimal_places=18` | Memecoins trade at extremely small values. High precision covers the full range. All prices in USD. |
| Volume | `DecimalField` | High precision | Solana SPL tokens support 0–9 decimal places, so volumes are fractional. Volume in USD. |
| Timestamps | `DateTimeField` | `USE_TZ=True`, stored in UTC | Standard quant convention. No exceptions. |
| Mint address (asset identifier) | `CharField` | `max_length=50` | Solana addresses are base58 strings, 32–44 characters. Small buffer for safety. |
| Holder counts (FL-002) | `BigIntegerField` | — | Whole numbers by definition. |
| Graduation time (anchor event) | `DateTimeField` | `USE_TZ=True`, stored in UTC | Same as all timestamps. |
| Ingested at (provenance) | `DateTimeField` | `auto_now_add=True`, stored in UTC | Row-level provenance. Records when the pipeline wrote each row. Added to all warehouse models (MigratedCoin, OHLCVCandle, HolderSnapshot). |

---

## Per-Definition Constants (from quantitative trading paradigm attributes)

Values for the per-definition constants declared on each abstract base.

### Universe: MigratedCoin

| Constant | Value |
|---|---|
| `UNIVERSE_ID` | `"U-001"` |
| `NAME` | `"Graduated Pump.fun Tokens — Early Lifecycle"` |
| `INCLUSION_CRITERIA` | `"All tokens launched on pump.fun and migrated to Pumpswap"` |
| `UNIVERSE_TYPE` | `"event-driven"` |
| `OBSERVATION_WINDOW_START` | `timedelta(0)` — window starts at anchor (graduation) |
| `OBSERVATION_WINDOW_END` | `timedelta(minutes=5000)` — window ends 5000 minutes after anchor |
| `EXCLUSION_CRITERIA` | `None` |
| `VERSION` | `"1.0"` |

### Feature Layer: OHLCVCandle

| Constant | Value |
|---|---|
| `LAYER_ID` | `"FL-001"` |
| `UNIVERSE_ID` | `"U-001"` |
| `NAME` | `"OHLCV Price Data"` |
| `TEMPORAL_RESOLUTION` | `timedelta(minutes=5)` |
| `AVAILABILITY_RULE` | `"end-of-interval"` |
| `GAP_HANDLING` | `"No candle created if no trades occurred in the interval"` |
| `DATA_SOURCE` | `"DexPaprika / GeckoTerminal"` |
| `REFRESH_POLICY` | `"Daily"` |
| `VERSION` | `"1.0"` |

### Feature Layer: HolderSnapshot

| Constant | Value |
|---|---|
| `LAYER_ID` | `"FL-002"` |
| `UNIVERSE_ID` | `"U-001"` |
| `NAME` | `"Holder Snapshots"` |
| `TEMPORAL_RESOLUTION` | `timedelta(minutes=5)` |
| `AVAILABILITY_RULE` | `"end-of-interval"` |
| `GAP_HANDLING` | `"Every interval has a snapshot — Moralis returns data for every interval even when no holder change occurred. Dead coins show netHolderChange=0 with stable totalHolders. No gaps from source."` |
| `DATA_SOURCE` | `"Moralis API"` |
| `REFRESH_POLICY` | `"Daily"` |
| `VERSION` | `"1.0"` |

### Reference Table: RawTransaction

| Constant | Value |
|---|---|
| `REFERENCE_ID` | `"RD-001"` |
| `UNIVERSE_ID` | `"U-001"` |
| `NAME` | `"Raw Transaction Data"` |
| `RECORD_TYPE` | `"Single trade (buy or sell)"` |
| `AVAILABILITY_RULE` | `"event-time"` |
| `ACCESS_PATTERN` | `"Get all trades for coin X between T1 and T2"` |
| `DATA_SOURCE` | `"TBD"` |
| `REFRESH_POLICY` | `"TBD"` |
| `VERSION` | `"0.1"` |

---

## Models Summary

| Table Category | Inherits From | Model Name | Spec Reference | Key Fields |
|---|---|---|---|---|
| Universe (master data) | `UniverseBase` | `MigratedCoin` | U-001 | `mint_address` (unique natural identifier), `anchor_event` (inherited from base, mapped to graduation time) |
| Feature layer (time series facts) | `FeatureLayerBase` | `OHLCVCandle` | FL-001 | FK to `MigratedCoin` via mint_address, `timestamp` (inherited from base), features: open_price, high_price, low_price, close_price, volume (all in USD) |
| Feature layer (time series facts) | `FeatureLayerBase` | `HolderSnapshot` | FL-002 | FK to `MigratedCoin` via mint_address, `timestamp` (inherited from base), features: total_holders, net_holder_change, holder_percent_change, acquisition method breakdowns, size tier breakdowns |
| Reference (event facts) | `ReferenceTableBase` | `RawTransaction` | RD-001 | FK to `MigratedCoin` via mint_address, `timestamp` (inherited from base), event identifier (TBD). Planned — feature set not yet defined. |

---

## QuerySet Summary

| Abstract Base | PIT Logic | Applies To |
|---|---|---|
| Universe base | Anchor-event: `.as_of(timestamp)` filters on `anchor_event` ≤ simulation time | `MigratedCoin` |
| Feature layer base | Uses declared `AVAILABILITY_RULE` — for FL-001 and FL-002: end-of-interval, `.as_of(timestamp)` filters on interval end ≤ simulation time | `OHLCVCandle`, `HolderSnapshot` |
| Reference table base | Uses declared `AVAILABILITY_RULE` — for RD-001: event-time, `.as_of(timestamp)` filters on event timestamp ≤ simulation time | `RawTransaction` |
