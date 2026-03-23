# U-002 API Exploration Findings

**Date:** 2026-03-14
**Universe:** U-002 — Major Crypto Assets (BTCUSDT, ETHUSDT, SOLUSDT)
**Purpose:** Document findings from real API calls before writing pipeline specs.

---

## Data Sources Explored

### 1. Binance Spot Public API (`data-api.binance.vision`)

**No API key required.** All endpoints return public market data. No authentication, no rate limit keys.

#### Klines (OHLCV+) — `/api/v3/klines`

- **Weight:** 1 per call (verified via `x-mbx-used-weight` header; Binance docs say 2 but `data-api.binance.vision` reports 1)
- **Max per call:** 1000 candles (silently caps if you request more)
- **Supported intervals:** 1s, 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M
- **Parameters:** `symbol`, `interval`, `startTime`, `endTime`, `limit`
- **Timestamps:** Milliseconds since epoch (Unix × 1000)

**Verified response fields (array of arrays):**

| Index | Field | Example (BTCUSDT) | Type |
|---|---|---|---|
| 0 | Kline open time | 1773491640000 | int (ms) |
| 1 | Open price | "70700.01000000" | string |
| 2 | High price | "70725.96000000" | string |
| 3 | Low price | "70681.75000000" | string |
| 4 | Close price | "70725.95000000" | string |
| 5 | Volume (base asset) | "44.19768000" | string |
| 6 | Kline close time | 1773491699999 | int (ms) |
| 7 | Quote asset volume (USDT) | "3124984.59476670" | string |
| 8 | Number of trades | 1359 | int |
| 9 | Taker buy base asset volume | "35.62574000" | string |
| 10 | Taker buy quote asset volume | "2518986.42787860" | string |
| 11 | Unused field | "0" | string (ignore) |

**Key observations:**
- All price/volume values returned as strings (need parsing to float/Decimal)
- Kline open time = start of candle, close time = end of candle (inclusive, ends in 999ms)
- Taker buy volume is included — sell volume can be derived as (total volume - taker buy volume)
- CVD (Cumulative Volume Delta) can be computed from this without additional data
- Verified working for all 3 pairs: BTCUSDT, ETHUSDT, SOLUSDT
- 1 day = 1440 candles at 1m resolution → ~2 API calls per pair per day

#### Order Book Depth — `/api/v3/depth`

- **Weight:** 2 (for limit ≤ 100, verified via `x-mbx-used-weight` header; Binance docs say 5 but `data-api.binance.vision` reports 2)
- **Max levels:** 5000 (but we use 20)
- **Parameters:** `symbol`, `limit`

**Verified response:**
```json
{
    "lastUpdateId": 90123912992,
    "bids": [["70633.92000000", "0.87646000"], ...],
    "asks": [["70633.93000000", "2.46965000"], ...]
}
```

**Key observations:**
- Returns current state only — no historical data, no time parameter
- Each level is [price, quantity] as strings
- 20 levels confirmed for all 3 pairs
- `lastUpdateId` can be used to detect staleness
- **No backfill possible** — forward-only collection via polling

#### Aggregate Trades — `/api/v3/aggTrades`

- **Weight:** 4
- **Max per call:** 1000 trades
- **Parameters:** `symbol`, `fromId`, `startTime`, `endTime`, `limit`
- **Backfillable:** Yes (has startTime/endTime)

Not tested in detail — documented as RD-001 (planned, not built).

---

### 2. Binance Public Data Downloads (`data.binance.vision`)

**This is the primary backfill mechanism.** Free bulk CSV downloads. No API key. No rate limits. No geo-restrictions.

#### URL patterns

```
# Spot daily klines
https://data.binance.vision/data/spot/daily/klines/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{YYYY-MM-DD}.zip

# Spot monthly klines
https://data.binance.vision/data/spot/monthly/klines/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{YYYY-MM}.zip

# Futures daily metrics (OI + ratios)
https://data.binance.vision/data/futures/um/daily/metrics/{SYMBOL}/{SYMBOL}-metrics-{YYYY-MM-DD}.zip

# Futures monthly funding rate
https://data.binance.vision/data/futures/um/monthly/fundingRate/{SYMBOL}/{SYMBOL}-fundingRate-{YYYY-MM}.zip

# Futures daily premium index klines
https://data.binance.vision/data/futures/um/daily/premiumIndexKlines/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{YYYY-MM-DD}.zip
```

#### Spot klines CSV format (no header row in daily files)

Columns in same order as API response:
`open_time, open, high, low, close, volume, close_time, quote_volume, count, taker_buy_volume, taker_buy_quote_volume, ignore`

**Verified:** 1440 rows per day (1-minute candles), ~65KB average per zip file per pair.

**Note:** Spot CSVs (both daily AND monthly) have NO header row. Futures CSVs (metrics, funding rate, premium index, klines) DO have header rows. The split is spot vs futures, not daily vs monthly.

**CRITICAL: Timestamp format change.** Spot kline CSVs changed timestamp format:
- Pre-2025 (through December 2024): Milliseconds (13 digits), e.g., `1704067200000`
- 2025 onward (from January 2025): Microseconds (16 digits), e.g., `1735689600000000`
- The parser MUST detect format by digit count and normalize to one standard.

#### Futures metrics CSV format (has header row)

```
create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio
2026-03-12 00:05:00,BTCUSDT,82691.651...,5800428196.926...,1.24722392,1.01140600,1.12810079,1.27903400
```

**Fields:**
- `create_time` — timestamp as "YYYY-MM-DD HH:MM:SS" string
- `sum_open_interest` — total OI in base asset (BTC)
- `sum_open_interest_value` — total OI in USDT
- `count_toptrader_long_short_ratio` — top traders by count L/S ratio
- `sum_toptrader_long_short_ratio` — top traders by position size L/S ratio
- `count_long_short_ratio` — all accounts L/S ratio
- `sum_taker_long_short_vol_ratio` — taker buy/sell volume ratio

**Resolution:** Every 5 minutes (288 rows/day). This is Binance's finest resolution for OI — cannot go finer.

**Verified:** 289 rows per daily file (288 intervals + header). Available for all 3 pairs.

#### Futures funding rate CSV format (has header row, monthly files)

```
calc_time,funding_interval_hours,last_funding_rate
1764547200000,8,0.00003593
```

**Resolution:** Every 8 hours (~3 per day, ~93 per month).

**Note:** Daily funding rate downloads return 404 — only monthly zips are available.

#### Premium index klines CSV format (has header row)

Same column structure as regular klines but values represent the premium/discount of futures vs spot price. Available at 1h resolution. 24 rows per daily file.

---

### 3. Binance Futures API (`fapi.binance.com`)

**GEO-RESTRICTED.** Returns error: "Service unavailable from a restricted location."

This means the pipeline cannot use the Futures API directly from the current server location. However, the `data.binance.vision` CSV downloads for futures data ARE accessible (different infrastructure, not geo-blocked).

**Implication for steady-state:** Daily updates for FL-003 and FL-004 must use CSV downloads, NOT the futures API, unless running from a non-restricted location.

---

## Historical Data Availability

| Pair | Spot klines (1m) | Futures metrics | Funding rate |
|---|---|---|---|
| BTCUSDT | 2020+ | 2021-12+ | Available |
| ETHUSDT | 2021+ | 2021-12+ | Available |
| SOLUSDT | 2020-09+ | 2021-12+ | Available |

All 3 pairs have 4+ years of spot klines and 4+ years of futures metrics. BTCUSDT spot has the deepest history (~6 years).

---

## Backfill Estimates (2 years, 3 pairs)

| Data type | Files to download | Rows | Size |
|---|---|---|---|
| Spot klines (daily zips) | ~2,190 | ~3,153,600 | ~139 MB (avg 65KB/file) |
| Futures metrics (daily zips) | ~2,190 | ~632,910 | ~50 MB (est.) |
| Funding rate (monthly zips) | ~72 | ~6,570 | < 1 MB |
| **Total** | **~4,452 files** | **~3,793,080 rows** | **~190 MB** |

Zero API calls needed for backfill. All via direct HTTP file download.

---

## Key Decisions Informed by Exploration

1. **CSV for backfill, API for daily updates** — CSV is faster, simpler, no rate limits for bulk historical. API fills in the most recent day where CSV may not be available yet.

2. **Futures API is blocked, but CSV downloads are not** — Steady-state updates for FL-003 and FL-004 must also use CSV downloads (available next day by ~06:00 UTC). This means a ~6-hour delay for metrics and funding data, which is acceptable for research.

3. **FL-003 is richer than planned** — Originally "open interest" only. The metrics CSV includes OI + 4 types of long/short ratios. More signal, same effort.

4. **5-minute is the finest resolution for OI** — Binance's limit, not a technical constraint. Cannot get 1-minute OI data from any delivery method.

5. **Spot CSVs have no headers, futures CSVs have headers** — The split is by market type (spot vs futures), not by time granularity (daily vs monthly). Parser must handle both.

6. **Order book (FL-002) is the only layer requiring real-time API polling** — Everything else can be batch-processed from CSV.

7. **Spot kline CSV timestamps changed format in Jan 2025** — Pre-2025 files use milliseconds (13 digits), 2025+ use microseconds (16 digits). Parser must detect and normalize. API always returns milliseconds.

8. **Four different timestamp formats across file types** — Spot klines (ms or μs depending on year), futures metrics (datetime string), funding rate (ms), premium index (ms). Each parser needs format-specific handling.

9. **Funding rate CSV has no symbol column** — Symbol must be inferred from the filename.

10. **Geo-restriction map** — `data-api.binance.vision` (spot API) works. `api.binance.com` and `fapi.binance.com` are blocked from this server. All `data.binance.vision` CSV downloads work regardless. Pipeline must use `data-api.binance.vision` for spot API calls, not `api.binance.com`.

---

## Open Items

| Item | Status | Notes |
|---|---|---|
| Verify SOL funding rate monthly files exist | **VERIFIED** | All 3 pairs have 8-hour intervals, 93 rows/month |
| Confirm CSV availability timing | Partially verified | Yesterday's files available at 13:06 UTC. Exact earliest availability (~06:00 UTC claim) not pinned. |
| Test API klines for steady-state (spot) | **VERIFIED** | Works via `data-api.binance.vision` |
| Aggregate trades exploration | Deferred | RD-001 is planned, not built. startTime/endTime verified working. |
| Bulk download throttling at scale (4,452 files) | Not tested | 10-file burst showed no throttling. Full-scale test needed on your server. |
| Crossed order book possibility | Unlikely | 60 snapshots showed zero crossed books. REST endpoint returns from memory, less likely to show transient crossed states. |
| Funding rate extreme range | **VERIFIED** | Historical min: -0.0004, max: +0.0007. Warning threshold of ±0.01 is conservative and safe. |
| `api.binance.com` vs `data-api.binance.vision` accessibility | **VERIFIED from this server** | Main API blocked, data-api works. Your server may differ. |
