# U-002 Data Specification — Major Crypto Assets

**Version:** 1.0
**Created:** 2026-03-14
**Status:** Draft

---

## Overview

U-002 is a dataset of major cryptocurrency assets traded on Binance spot, with supplementary derivatives data from Binance futures. It targets a small, manually curated set of high-liquidity coins for quantitative research across multiple feature layers.

This specification defines the dataset using the 7 concepts from the quantitative trading paradigm.

---

## Concept 01: Universe Definition

**Version:** 1.0

| Attribute | Value |
|---|---|
| **Universe ID** | U-002 |
| **Name** | Major Crypto Assets |
| **Universe type** | Calendar-driven |
| **Entity type** | Binance spot trading pair |
| **Entities** | BTCUSDT, ETHUSDT, SOLUSDT |
| **Membership rule** | Fixed list, manually curated. No discovery pipeline. |
| **Anchor event** | N/A — calendar-driven, no per-asset T0 |
| **Observation window** | Open-ended in both directions (no fixed start or end) |
| **Initial backfill target** | ~2 years (March 2024 → March 2026). Data available much further back: spot klines from 2020 (BTC) / 2020-09 (SOL), futures metrics from 2021-12 for all 3 pairs. |
| **Exclusion criteria** | N/A — fixed list |
| **Entity count** | 3 |

### Notes

- Unlike U-001 (event-driven, per-asset anchor), U-002 uses the same absolute time range for all assets.
- The observation window can be extended backward (more backfill) or forward (daily updates) at any time.
- Adding or removing coins requires a manual update to the entity list and is treated as a new version of the universe definition.

---

## Concept 02: Feature Layers

### FL-001: OHLCV+ (Spot Klines)

**Version:** 1.0

| Attribute | Value |
|---|---|
| **Layer ID** | FL-001 |
| **Universe ID** | U-002 |
| **Name** | OHLCV+ (Spot Klines) |
| **Feature set** | open, high, low, close, volume (base asset, verified), quote_volume (USDT, verified: vol × avg_price ≈ quote_vol with ratio ~1.0000), trade_count, taker_buy_volume (verified: always ≤ total volume), taker_buy_quote_volume |
| **Temporal resolution** | 1-minute candles |
| **Gap handling** | No candle created if exchange returns no data for that interval. Verified: 21 daily files across all 3 pairs (7 days each, March 2026) all had exactly 1440 rows. Gaps are expected to be extremely rare for BTC/ETH/SOL but possible during exchange maintenance. |
| **Data source (backfill)** | Binance public CSV downloads (`data.binance.vision`, spot daily klines) |
| **Data source (steady-state)** | Binance Spot API via `data-api.binance.vision` (`/api/v3/klines`) or CSV download. Note: `api.binance.com` may be geo-restricted; always use `data-api.binance.vision`. |
| **Availability rule** | End-of-interval — a 1m candle timestamped 10:00:00 becomes available at 10:01:00 |
| **Refresh policy** | Daily |
| **Version** | 1.0 |

### FL-002: Order Book Snapshots

**Version:** 1.0

| Attribute | Value |
|---|---|
| **Layer ID** | FL-002 |
| **Universe ID** | U-002 |
| **Name** | Order Book Snapshots |
| **Feature set** | Per-row: side (bid/ask), level (1-20), price, quantity. Plus lastUpdateId (monotonically increasing, verified: ~136 updates/second for BTC — usable for staleness detection). Stored normalized: one row per level per side per snapshot (40 rows per snapshot per asset). |
| **Temporal resolution** | 1-minute polling |
| **Gap handling** | No snapshot created if poll fails. Gaps indicate connectivity or exchange issues. |
| **Data source** | Binance Spot API via `data-api.binance.vision` (`/api/v3/depth?limit=20`). Note: `api.binance.com` may be geo-restricted. |
| **Availability rule** | Event-time — available the moment the snapshot is captured |
| **Refresh policy** | Continuous (every 1 minute) |
| **Backfillable** | No — forward-only. Historical order book data not available from Binance public API. |
| **Version** | 1.0 |

### FL-003: Futures Metrics (Open Interest + Ratios)

**Version:** 1.0

| Attribute | Value |
|---|---|
| **Layer ID** | FL-003 |
| **Universe ID** | U-002 |
| **Name** | Futures Metrics |
| **Feature set** | sum_open_interest (base asset, verified), sum_open_interest_value (USDT, verified: OI × price ≈ OI_value), count_toptrader_long_short_ratio, sum_toptrader_long_short_ratio, count_long_short_ratio, sum_taker_long_short_vol_ratio |
| **Temporal resolution** | 5-minute snapshots (Binance's finest resolution for this data) |
| **Gap handling** | No row created if data missing for an interval. |
| **Data source (backfill)** | Binance public CSV downloads (`data.binance.vision`, futures daily metrics) |
| **Data source (steady-state)** | Binance public CSV downloads (available next day ~06:00 UTC) |
| **Availability rule** | Publication-time — data becomes available when Binance publishes the CSV, typically ~6 hours after the end of the day |
| **Refresh policy** | Daily (next-day) |
| **Version** | 1.0 |

#### Notes

- The Binance Futures API (`fapi.binance.com`) is geo-restricted and cannot be used from all locations. The main spot API (`api.binance.com`) may also be restricted. Use `data-api.binance.vision` for spot API calls and `data.binance.vision` for CSV downloads — both are accessible.
- 5-minute resolution is the finest available from Binance for open interest data. The API supports periods: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d. This is a Binance limitation, not a pipeline choice.
- Metrics data is available from December 2021 for all 3 pairs.

### FL-004: Funding Rate

**Version:** 1.0

| Attribute | Value |
|---|---|
| **Layer ID** | FL-004 |
| **Universe ID** | U-002 |
| **Name** | Funding Rate |
| **Feature set** | funding_interval_hours, last_funding_rate |
| **Temporal resolution** | Every 8 hours (verified: all 3 pairs use 8-hour intervals consistently, 93 entries per month) |
| **Gap handling** | Missing funding events indicate exchange issues. Very rare. |
| **Data source (backfill)** | Binance public CSV downloads (`data.binance.vision`, futures monthly funding rate) |
| **Data source (steady-state)** | Binance public CSV downloads (monthly files) |
| **Availability rule** | Publication-time — same as FL-003 |
| **Refresh policy** | Daily or weekly (low frequency data) |
| **Version** | 1.0 |

#### Notes

- Funding rate is a perpetual futures concept. Positive = longs pay shorts (bullish sentiment). Negative = shorts pay longs.
- Only monthly CSV files are available (daily files return 404). The pipeline must download the current month's file and extract new entries.
- Historical funding rates for BTC/ETH/SOL range from approximately -0.0004 to +0.0007 based on 2024-2025 data.
- The funding rate CSV has no symbol column — the symbol must be inferred from the filename.

---

## Concept 03: Join Key

**Version:** 1.0

| Attribute | Value |
|---|---|
| **Join Key ID** | JK-001 |
| **Universe ID** | U-002 |
| **Join dimensions** | (asset_id, timestamp) |
| **Asset alignment** | All layers share the same entity identifier (Binance symbol: BTCUSDT, ETHUSDT, SOLUSDT) |
| **Temporal alignment** | Layers have different resolutions (1m, 5m, ~8h). Alignment requires resampling or forward-fill at strategy time. |
| **Cross-layer join type** | Left join from FL-001 (finest resolution) to coarser layers, with forward-fill and staleness limits |

### Notes

- FL-001 (1m) and FL-002 (1m) align naturally on the same timestamp grid.
- FL-003 (5m) and FL-004 (~8h) are coarser. A strategy operating at 1m resolution would forward-fill the most recent FL-003/FL-004 value into each minute. Staleness limits should be defined at strategy time.
- This is the same paradigm concept as U-001's JK-001, but with more resolution diversity across layers.

---

## Concept 04: Point-in-Time Semantics

| Layer | Availability Rule | When data becomes "known" |
|---|---|---|
| FL-001 (OHLCV+) | End-of-interval | At the close of the 1-minute candle |
| FL-002 (Order Book) | Event-time | The instant the snapshot is captured |
| FL-003 (Futures Metrics) | Publication-time | When Binance publishes the CSV (~next day 06:00 UTC) |
| FL-004 (Funding Rate) | Publication-time | When Binance publishes the monthly CSV |

### Notes

- FL-003 and FL-004 have a publication delay. In backtesting, this means a strategy should not "see" a 5-minute OI reading until it has been published. For daily-resolution research, this delay is negligible. For intraday strategies, it matters — the Futures API (if accessible) would provide near-real-time data.
- In practice, FL-001 and FL-002 are the only layers usable for sub-daily strategy signals without look-ahead bias concerns.

---

## Concept 05: Derived Features

None defined yet. The following are planned to be computed from raw feature layers:

| Feature | Computed from | Logic | Status |
|---|---|---|---|
| CVD (Cumulative Volume Delta) | FL-001 | Running sum of (taker_buy_volume − sell_volume) per candle, where sell_volume = volume − taker_buy_volume | Planned |
| Liquidity (bid/ask depth within X%) | FL-002 | Sum of bid/ask quantities within X% of mid price from order book snapshot | Planned |

Derived features will be formalized when a strategy requires them.

---

## Concept 06: Data Quality Constraints

### FL-001 (OHLCV+)

| Constraint | Rule | Severity |
|---|---|---|
| Price ordering | high ≥ max(open, close) AND low ≤ min(open, close) | Hard reject |
| Non-negative volume | volume ≥ 0, quote_volume ≥ 0 | Hard reject |
| Taker buy ≤ total | taker_buy_volume ≤ volume | Hard reject |
| No duplicate timestamps | One candle per (symbol, open_time) | Hard reject |
| Positive prices | open, high, low, close > 0 | Hard reject |

### FL-002 (Order Book — normalized, one row per level)

| Constraint | Rule | Severity |
|---|---|---|
| Non-crossed book | Best bid price (level=1, side=bid) < best ask price (level=1, side=ask) for same snapshot | Hard reject (crossed book = corrupt data) |
| Level ordering | Bid prices descending by level, ask prices ascending by level within each snapshot | Hard reject |
| Non-negative quantities | quantity ≥ 0 | Hard reject |
| Positive prices | price > 0 | Hard reject |
| Expected row count | 40 rows per snapshot (20 levels × 2 sides) | Warning (fewer levels = thin book, not corrupt) |
| No duplicate levels | One row per (symbol, timestamp, side, level) | Hard reject |

### FL-003 (Futures Metrics)

| Constraint | Rule | Severity |
|---|---|---|
| Non-negative OI | sum_open_interest ≥ 0, sum_open_interest_value ≥ 0 | Hard reject |
| Positive ratios | All ratio fields > 0 | Warning (zero ratio is unusual but theoretically possible) |
| No duplicate timestamps | One row per (symbol, create_time) | Hard reject |

### FL-004 (Funding Rate)

| Constraint | Rule | Severity |
|---|---|---|
| Reasonable range | -0.01 ≤ last_funding_rate ≤ 0.01 | Warning (historical range for BTC/ETH/SOL was -0.0004 to +0.0007 over 2024-2025; threshold is intentionally conservative) |
| No duplicate timestamps | One row per (symbol, calc_time) | Hard reject |

---

## Concept 07: Reference Dataset

### RD-001: Aggregate Trades (Planned)

**Version:** 0.1

| Attribute | Value |
|---|---|
| **Reference ID** | RD-001 |
| **Universe ID** | U-002 |
| **Name** | Aggregate Trades |
| **Record type** | Single aggregated trade (fills at same time/price/order aggregated) |
| **Feature set** | price, quantity, first_trade_id, last_trade_id, timestamp, is_buyer_maker |
| **Timestamp field** | Exact trade timestamp |
| **Availability rule** | Event-time — visible at the moment the trade occurs |
| **Data source** | Binance Spot API via `data-api.binance.vision` (`/api/v3/aggTrades`). Verified: startTime/endTime parameters work for historical queries. |
| **Backfillable** | Yes (has startTime/endTime parameters) |
| **Refresh policy** | TBD |
| **Version** | 0.1 |
| **Status** | Planned — not built |

---

## Pipeline Architecture Summary

| Mode | FL-001 | FL-002 | FL-003 | FL-004 |
|---|---|---|---|---|
| **Backfill** | CSV download | N/A (forward-only) | CSV download | CSV download |
| **Steady-state** | API or CSV | API polling (1/min) | CSV download (next-day) | CSV download (monthly) |

### Key architectural differences from U-001

1. **No discovery pipeline** — fixed entity list, no universe population step.
2. **CSV-first backfill** — bulk downloads, no API rate limit management for historical data.
3. **Geo-restriction on Futures API** — steady-state for FL-003/FL-004 must use CSV downloads, not API. Spot API must use `data-api.binance.vision`, not `api.binance.com`.
4. **Forward-only FL-002** — order book snapshots cannot be backfilled; history starts when collection begins.
5. **Multi-resolution layers** — 1m, 5m, and ~8h layers must be aligned at strategy time, not pipeline time.

### Critical CSV parsing notes

**Timestamp format varies by file type and date:**

| File type | Timestamp field | Format |
|---|---|---|
| Spot klines CSV (pre-2025) | Column 0 (open_time) | Milliseconds, 13 digits |
| Spot klines CSV (2025+) | Column 0 (open_time) | Microseconds, 16 digits |
| Futures metrics CSV | `create_time` | Datetime string "YYYY-MM-DD HH:MM:SS" |
| Funding rate CSV | `calc_time` | Milliseconds, 13 digits |
| Premium index CSV | `open_time` | Milliseconds, 13 digits |
| Spot API response | Array index 0 | Milliseconds, 13 digits |

The spot klines timestamp format changed between December 2024 (milliseconds) and January 2025 (microseconds). The parser MUST detect the format by checking digit count and normalize to a single convention. Failure to handle this will silently corrupt timestamps for all pre-2025 or post-2024 data.

**Header row presence:**

| File type | Has header? |
|---|---|
| Spot klines (daily and monthly) | No |
| Futures metrics | Yes |
| Futures funding rate | Yes |
| Futures premium index | Yes |
| Futures klines | Yes |

The split is spot vs futures, NOT daily vs monthly.

**Funding rate CSV has no symbol column.** The symbol must be inferred from the filename.

---

## Blocked Items

| Item | Blocked on | Impact |
|---|---|---|
| FL-002 historical data | No free source for historical order book | Cannot backtest strategies that require order book depth before collection start date |

---

## Adding to This Specification

- **New feature layer:** Copy any FL template, assign next ID (FL-005, etc.)
- **New entity:** Add to the entity list and increment version. Backfill historical data for the new entity.
- **New reference dataset:** Copy RD template, assign next ID
- **New derived feature:** Define when a strategy needs it

---

## Glossary (U-002 specific terms)

**Calendar-driven universe** — A universe where the observation window is an absolute time range, the same for all assets. No per-asset anchor event.

**OHLCV+** — Standard OHLCV candle data plus additional fields from Binance klines: quote asset volume, trade count, taker buy base volume, and taker buy quote volume.

**Taker buy volume** — The portion of total volume where the buyer was the taker (aggressor). Sell volume = total volume − taker buy volume. This enables CVD computation without trade-level data.

**Futures metrics** — A composite dataset from Binance that includes open interest and multiple long/short ratio measurements in a single file.

**Funding rate** — A periodic payment mechanism in perpetual futures that keeps the contract price close to spot. Positive = longs pay shorts (market leans bullish). Negative = shorts pay longs.

**Forward-only layer** — A feature layer that cannot be backfilled from historical data. Collection starts when the pipeline begins running. FL-002 (order book snapshots) is the only forward-only layer in U-002.
