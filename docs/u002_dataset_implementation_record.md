# Dataset Implementation Record: U-002

**Dataset:** Major Crypto Assets — BTCUSDT, ETHUSDT, SOLUSDT on Binance
**Reference:** warehouse_implementation_guide.md (for decision point definitions and option details)
**Reference:** u002_data_specification.md (for data contract: universe, feature layers, join keys, PIT rules, quality constraints)
**Reference:** u002_api_exploration_findings.md (for verified API behavior and CSV format details)

---

## Decision Selections

Each row references a decision point (DP) from the Warehouse Implementation Guide.

| DP | Decision | Selected Option | Reasoning |
|---|---|---|---|
| **DP1** | Primary Key Strategy | **A: Surrogate + unique_together** | Same as U-001. Battle-tested Django pattern. Full ORM compatibility. |
| **DP2** | Foreign Key Target | **B: FK to natural identifier** | Queries think in symbols (BTCUSDT), not surrogate IDs. Using `to_field="symbol"` keeps the ORM aligned with domain. Binance symbols are unique and stable. |
| **DP3** | Nullable Fields | **B: All feature columns nullable** | Same rationale as U-001. Row existence determines data presence. Null handling is the strategy's responsibility. |
| **DP4** | Abstract Base Models | **A: Three abstract bases** | Already implemented. U-002 concrete models inherit from the same paradigm bases as U-001. |
| **DP5** | Append-Only Convention | **B: Append-only for time series, updates for master data** | All four feature layer tables are append-only. The universe table (BinanceAsset) allows updates for metadata, though updates will be rare with only 3 static rows. |
| **DP6** | Quality Constraint Placement | **C: Split by severity and complexity** | See detail below. Same split strategy as U-001 but with different constraints per layer. |
| **DP7** | Indexing Strategy | **B: Compound + timestamp** | Same as U-001. `unique_together` on (asset FK, timestamp) covers per-asset time range queries. Separate timestamp index for cross-sectional queries. |
| **DP8** | Data Types | See detail below | Based on Binance data format and verified API responses. |
| **DP9** | Timestamp Convention | **A: Interval start** | Binance klines use `open_time` as the candle start (verified: aligned to minute boundaries, close_time = open_time + 59999ms). Consistent with U-001. |
| **DP10** | Alignment Mechanics | **B: Application code** | Same as U-001. Multi-resolution alignment (1m, 5m, 8h) requires forward-fill with staleness limits. Python is more readable and testable than SQL for this logic. |
| **DP11** | Derived Feature Timing | **B: On-the-fly** | CVD and liquidity metrics are the only planned derived features. On-the-fly keeps experimentation fast — no pipeline changes to try a new computation. |
| **DP12** | Contract Enforcement | **C: Trust on read, audit periodically** | Same as U-001. Pipeline validates on write (conformance checks). Data service serves without re-checking. Periodic audits catch drift. |
| **DP13** | Error Handling | **Raise error for invalid, empty for valid-no-data** | Asset symbol not in universe → raise error. Valid time range with no data → return empty (e.g., exchange maintenance gap). |

---

## Data Type Selections (DP8)

### FL-001: OHLCV+ (Spot Klines)

| Field | Django Field | Configuration | Reasoning |
|---|---|---|---|
| open, high, low, close | `DecimalField` | `max_digits=20, decimal_places=8` | BTC trades at ~$70,000 (5 integer digits), SOL at ~$87 (2 digits). Binance returns 8 decimal places for prices. 20 total digits provides generous headroom. |
| volume (base asset) | `DecimalField` | `max_digits=20, decimal_places=8` | Binance returns 8 decimal places for quantities. BTC daily volumes are in the thousands — 12 integer digits handles any scale. |
| quote_volume (USDT) | `DecimalField` | `max_digits=20, decimal_places=8` | Same precision as volume. Values can be billions of USDT for BTC. |
| trade_count | `IntegerField` | — | Whole number. Max observed in single 1m candle: ~8,000 (well within IntegerField range of ~2.1 billion). |
| taker_buy_volume | `DecimalField` | `max_digits=20, decimal_places=8` | Same as volume. Always ≤ total volume (verified). |
| taker_buy_quote_volume | `DecimalField` | `max_digits=20, decimal_places=8` | Same as quote_volume. |
| timestamp | `DateTimeField` | UTC | Interval start time. See DP9. |

**Note on precision difference from U-001:** U-001 uses `max_digits=38, decimal_places=18` because memecoins trade at values like 0.000000001. U-002 doesn't need that — Binance's maximum precision is 8 decimal places. Using 20/8 is more storage-efficient and matches the source data exactly.

### FL-002: Order Book Snapshots (Normalized — one row per level)

| Field | Django Field | Configuration | Reasoning |
|---|---|---|---|
| side | `CharField` | `max_length=3, choices=[('bid','bid'),('ask','ask')]` | Which side of the book. |
| level | `PositiveSmallIntegerField` | — | 1 = best bid/ask, 20 = deepest. |
| price | `DecimalField` | `max_digits=20, decimal_places=8` | Same precision as kline prices. |
| quantity | `DecimalField` | `max_digits=20, decimal_places=8` | Same precision as volumes. |
| last_update_id | `BigIntegerField` | — | Monotonically increasing. Observed values ~90 billion — requires BigIntegerField. Same value for all 40 rows of one snapshot. |
| timestamp | `DateTimeField` | UTC | Time when the snapshot was captured by the pipeline. |

**Storage math:** 20 levels × 2 sides = 40 rows per snapshot. At 1 snapshot/minute × 3 pairs = 172,800 rows/day, ~63M rows/year. More rows than a wide table but each row is simple (6 fields). Follows the same normalized pattern as all other feature layers: one row per observation.

**Unique constraint:** `unique_together = (asset, timestamp, side, level)` — one price/qty per level per side per snapshot per asset.

### FL-003: Futures Metrics

| Field | Django Field | Configuration | Reasoning |
|---|---|---|---|
| sum_open_interest | `DecimalField` | `max_digits=20, decimal_places=10` | Base asset units (BTC). Verified: values like 82691.651. 10 decimal places to match Binance precision. |
| sum_open_interest_value | `DecimalField` | `max_digits=24, decimal_places=10` | USDT. Verified: values like 5,800,428,196.93. Needs more integer digits than OI. |
| count_toptrader_long_short_ratio | `DecimalField` | `max_digits=12, decimal_places=8` | Ratio values like 1.24722392. 4 integer digits + 8 decimal covers any ratio. |
| sum_toptrader_long_short_ratio | `DecimalField` | `max_digits=12, decimal_places=8` | Same as above. |
| count_long_short_ratio | `DecimalField` | `max_digits=12, decimal_places=8` | Same as above. |
| sum_taker_long_short_vol_ratio | `DecimalField` | `max_digits=12, decimal_places=8` | Same as above. |
| timestamp | `DateTimeField` | UTC | Metrics snapshot time. |

### FL-004: Funding Rate

| Field | Django Field | Configuration | Reasoning |
|---|---|---|---|
| funding_interval_hours | `IntegerField` | — | Always 8 for BTC/ETH/SOL (verified). But stored as a field in case Binance changes intervals in the future. |
| last_funding_rate | `DecimalField` | `max_digits=12, decimal_places=10` | Historical range: -0.0004 to +0.0007 (verified). 10 decimal places to capture the full precision Binance provides. |
| timestamp | `DateTimeField` | UTC | Funding settlement time. |

### Universe: BinanceAsset

| Field | Django Field | Configuration | Reasoning |
|---|---|---|---|
| symbol | `CharField` | `max_length=20, unique=True` | Binance trading pair string. "BTCUSDT" = 7 chars. 20 chars provides headroom for longer pairs. Used as FK target (DP2). |
| base_asset | `CharField` | `max_length=10` | "BTC", "ETH", "SOL". Useful for display and grouping. |
| quote_asset | `CharField` | `max_length=10` | Always "USDT" for this universe. Stored for consistency if other quote assets are added. |
| anchor_event | `DateTimeField` | `null=True` | Inherited from UniverseBase. NULL for U-002 because it's calendar-driven — no per-asset T0. |

**Note on anchor_event:** The paradigm base requires this field. For calendar-driven universes it's NULL — the observation window is defined at the universe level, not per-asset. The `is_within_window()` method on the base should handle NULL anchor_event gracefully (return True for any timestamp, or delegate to universe-level window config). This needs to be verified against the current abstract base implementation.

---

## Quality Constraint Placement (DP6)

### FL-001 (OHLCV+)

| Constraint | Enforcement | Location |
|---|---|---|
| high ≥ max(open, close) AND low ≤ min(open, close) | Database CHECK | Model `Meta.constraints` |
| volume ≥ 0, quote_volume ≥ 0 | Database CHECK | Model `Meta.constraints` |
| taker_buy_volume ≤ volume | Database CHECK | Model `Meta.constraints` |
| No duplicate (symbol, timestamp) | Database UNIQUE | `unique_together` |
| Positive prices | Database CHECK | Model `Meta.constraints` |

### FL-002 (Order Book)

| Constraint | Enforcement | Location |
|---|---|---|
| Non-crossed book: best bid < best ask (level=1 bid price < level=1 ask price for same snapshot) | Audit command | Cross-row comparison — cannot be a single-row CHECK |
| Level ordering: bid prices descending by level, ask prices ascending by level | Audit command | Cross-row comparison |
| All quantities ≥ 0 | Database CHECK | Model `Meta.constraints` |
| All prices > 0 | Database CHECK | Model `Meta.constraints` |
| No duplicate (symbol, timestamp, side, level) | Database UNIQUE | `unique_together` |
| Expected 40 rows per snapshot (20 levels × 2 sides) | Audit command | Warning severity — fewer levels = thin book, not corrupt |

### FL-003 (Futures Metrics)

| Constraint | Enforcement | Location |
|---|---|---|
| sum_open_interest ≥ 0 | Database CHECK | Model `Meta.constraints` |
| sum_open_interest_value ≥ 0 | Database CHECK | Model `Meta.constraints` |
| No duplicate (symbol, timestamp) | Database UNIQUE | `unique_together` |
| Positive ratios | Audit command | Warning severity only |

### FL-004 (Funding Rate)

| Constraint | Enforcement | Location |
|---|---|---|
| No duplicate (symbol, timestamp) | Database UNIQUE | `unique_together` |
| Rate within ±0.01 | Audit command | Warning severity — historical data confirmed within range, but extreme events possible |

---

## Time Representation

Crypto markets run 24/7 — calendar time and trading time are identical. Same as U-001. All timestamps stored in UTC.

**However:** U-002 has multiple feature layers at different resolutions (1m, 5m, 8h). The temporal resolution per layer is:

| Layer | Resolution | Representation |
|---|---|---|
| FL-001 | 1 minute | `timedelta(minutes=1)` |
| FL-002 | 1 minute | `timedelta(minutes=1)` |
| FL-003 | 5 minutes | `timedelta(minutes=5)` |
| FL-004 | 8 hours | `timedelta(hours=8)` |

Each concrete feature layer model defines its own `TEMPORAL_RESOLUTION` class constant.

---

## Observation Window

U-002 is calendar-driven with an open-ended window. There is no per-asset T0 and no fixed T1.

| Attribute | Value |
|---|---|
| Window type | Open-ended (no fixed start or end) |
| t₁ offset | N/A — window extends backward as far as backfilled data exists |
| t₂ offset | N/A — window extends forward as daily data is added |
| Per-asset anchor | NULL (calendar-driven) |

This is structurally different from U-001 (event-driven, fixed 5000-minute window per asset). The abstract base's `is_within_window()` method must handle the case where anchor_event is NULL. For U-002, any timestamp with data is within the window.

---

## CSV Parsing Requirements

The pipeline must handle these verified format variations:

| File type | Has header? | Timestamp format | Symbol column? |
|---|---|---|---|
| Spot klines (pre-2025) | No | Milliseconds (13 digits) | No (from filename) |
| Spot klines (2025+) | No | Microseconds (16 digits) | No (from filename) |
| Futures metrics | Yes | Datetime string "YYYY-MM-DD HH:MM:SS" | Yes (`symbol` column) |
| Futures funding rate | Yes | Milliseconds (13 digits) | No (from filename) |
| Spot API response | N/A (JSON) | Milliseconds (13 digits) | N/A (from request param) |

**Critical:** Spot kline CSV timestamps changed from milliseconds to microseconds in January 2025. The parser must detect format by checking the digit count of the first timestamp value and normalize accordingly.

---

## Models Summary

| Model | Inherits from | Table role | Key fields |
|---|---|---|---|
| BinanceAsset | UniverseBase | Universe | symbol (PK target), base_asset, quote_asset |
| U002OHLCVCandle | FeatureLayerBase | Feature layer (1m) | FK to BinanceAsset via symbol, open/high/low/close/volume + enriched fields |
| U002OrderBookSnapshot | FeatureLayerBase | Feature layer (1m) | FK to BinanceAsset, side, level, price, quantity, last_update_id |
| U002FuturesMetrics | FeatureLayerBase | Feature layer (5m) | FK to BinanceAsset, OI + 4 ratio fields |
| U002FundingRate | FeatureLayerBase | Feature layer (8h) | FK to BinanceAsset, funding_interval_hours, last_funding_rate |

---

## Differences from U-001

| Aspect | U-001 | U-002 |
|---|---|---|
| Universe type | Event-driven (per-asset T0) | Calendar-driven (no T0) |
| Entity identifier | Solana mint address (44 chars) | Binance symbol (7-20 chars) |
| Observation window | Fixed: T0 → T0+5000min | Open-ended: no boundaries |
| Feature layers | 2 (OHLCV, holders) | 4 (OHLCV+, order book, metrics, funding) |
| Resolutions | Single (5m) | Multiple (1m, 5m, 8h) |
| Entity count | ~200,000+ (dynamic, discovered) | 3 (static, hardcoded) |
| Discovery pipeline | Required (daily) | Not needed |
| Backfill method | API calls with rate limits | CSV bulk download (free, no limits) |
| Data type precision | max_digits=38, decimal_places=18 | max_digits=20, decimal_places=8 |
| anchor_event | Required (graduation time) | NULL (calendar-driven) |

---

## Open Questions

| Question | Impact | Status |
|---|---|---|
| Does the abstract base `is_within_window()` handle NULL anchor_event? | BinanceAsset will have NULL. Method must not crash. | Check against current code |
| Should FL-002 collection start immediately? | Every day without collection = lost data that can never be recovered | **YES — fast-track FL-002 polling pipeline** |
