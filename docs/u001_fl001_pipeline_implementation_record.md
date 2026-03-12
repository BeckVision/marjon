# Pipeline Implementation Record: U-001 / FL-001

**Feature Layer:** FL-001 — OHLCV Price Data
**Dataset:** U-001 — Graduated Pump.fun Tokens — Early Lifecycle
**Source:** GeckoTerminal API (OHLCV endpoint) — switched from DexPaprika 2026-03-11
**Reference:** pipeline_implementation_guide.md (for decision point definitions and option details)
**Reference:** u001_data_specification.md (for data contract: universe, feature layers, join keys, PIT rules, quality constraints)
**Reference:** u001_fl001_api_exploration_findings.md (for verified API behavior and conformance requirements)

---

## Decision Selections

Each row references a decision point (DP) from the Pipeline Implementation Guide.

| DP | Decision | Selected Option | Reasoning |
|---|---|---|---|
| **PDP1** | Extract Strategy | **C: Windowed incremental** | Efficient use of API rate limits. Overlap window provides safety margin against edge cases (e.g. a candle missed at the watermark boundary). Idempotent write (delete-write) handles duplicates from the overlap. Bootstrap (new coin) automatically falls back to full-window fetch when watermark returns nothing. |
| **PDP2** | ETL vs ELT | **A: ETL (transform before load)** | Both APIs (DexPaprika, GeckoTerminal) allow free historical re-fetching. DexPaprika provides 1 year of history, GeckoTerminal provides 6 months. Observation window is only 3.47 days. If a conformance bug is discovered, re-fetching is cheap. No staging tables needed — keeps the schema simple. |
| **PDP3** | Idempotency Mechanism | **Universe: A (Upsert). Feature layers & reference tables: B (Delete-write).** | Aligned with warehouse WDP5 (append-only for time series, updates for master data). Universe table (MigratedCoin) allows updates — upsert is the natural fit for metadata enrichment. Feature layers are append-only — delete-write is self-correcting on re-run while maintaining append-only semantics (remove and replace within a transaction, not modify in place). Important during early stage when conformance bugs are likely. |
| **PDP4** | Watermark Strategy | **A: Derive from warehouse** | Query `MAX(timestamp)` per asset from OHLCVCandle table. Always consistent with actual data. No drift risk. No extra table to maintain. One grouped query before each run is sufficient at current scale. |
| **PDP5** | Rate Limit Handling | **A: Serial with sleep** | `time.sleep(6)` between paginated calls respects the ~10 requests/minute rate limit. Exponential backoff on transient errors (aligns with PDP6). Simple and sufficient at current scale — no task queue infrastructure needed. |
| **PDP6** | Error Handling | **D: Retry with backoff, then fail** | Early-stage system — silent skipping is more dangerous than blocked runs. Persistent failures should surface immediately so they can be investigated. Connector's `_request_with_retry()` handles transient errors (network blips, temporary 429s) with exponential backoff. After max retries, the exception propagates and the run fails. Can soften to Option C (skip-and-continue) later when the system is stable and monitoring is in place. |
| **PDP7** | Reconciliation Strategy | **C: Count + boundary** | Both count and boundary checks, logged as informational reports. Count: how many candles loaded vs theoretical maximum for the time range. Boundary: first candle at/near T0, last candle at expected position. Must account for legitimate sparsity — a memecoin with 4 candles out of 1000 possible is normal. The pattern across many coins reveals systemic issues (e.g. 100 coins all returning 0 candles = API outage). |
| **PDP8** | Provenance Tracking | **B: Row-level ingest timestamp** | Add `ingested_at` field to warehouse models (OHLCVCandle, MigratedCoin). Plus run-level logging (start/end time, assets processed, record counts, errors). Cheap to add now — one column per model. Expensive to add retroactively after data exists. Distinguishes backfill rows from daily ingestion rows. |
| **PDP9** | Multi-Source Handling | **A: Single source (GeckoTerminal)** | Switched from DexPaprika 2026-03-11. Source comparison across 5 coins and 28 hours proved DexPaprika has critical data quality issues for Pumpswap tokens: 41% flat candles (O=H=L=C), 69 missing/null volumes, integer-truncated volumes. GeckoTerminal had zero flat candles, zero missing volumes, 100% coverage of all timestamps. DexPaprika connector kept for pool mapping discovery only. |
| **PDP10** | Scheduling | **A: Manual (management commands)** | `python manage.py fetch_ohlcv` for individual runs. `python manage.py orchestrate` for batch runs with step ordering. Same code path for both — only the parameters differ (code path unification). Automated scheduling via cron or external scheduler can be added later. |
| **PDP11** | Dimension Table Location | **A: Warehouse app owns all tables** | All database tables (paradigm models + dimension tables like pool mapping) live in the warehouse app. Pipeline app owns only code (source connectors, conformance functions, management commands). Simpler FK relationships. One app for all models, one app for all pipeline logic. |

---

## Data Specification Changes

Gaps discovered during API exploration that require updates to the data specification.

| Change | Spec affected | Detail |
|---|---|---|
| **Remove `market_cap` from FL-001 feature set** | u001_data_specification.md, FL-001 | Neither DexPaprika nor GeckoTerminal provides market_cap per candle. Market cap can be added later as a derived feature (DF-001) if needed. |
| **Rename FL-001 feature fields with `_price` suffix** | u001_data_specification.md, FL-001 | FL-001 feature set becomes: `open_price, high_price, low_price, close_price, volume`. Avoids shadowing Python's `open` builtin. Self-documenting field names. |
| **Add explicit denomination to FL-001** | u001_data_specification.md, FL-001 | Prices (open_price, high_price, low_price, close_price) are in USD. Volume is in USD. Verified against TRUMP token at known price for both DexPaprika and GeckoTerminal. |
| **Add pool mapping dimension table** | New table (not in current spec) | Pipeline needs mint_address → pool_address mapping to query DexPaprika. Separate table, not a field on MigratedCoin. Supports multiple pools per token for future flexibility. |
| **Add `ingested_at` field to warehouse models** | models.py | Row-level provenance. `DateTimeField(auto_now_add=True)` on OHLCVCandle and MigratedCoin. |

---

## GeckoTerminal Source Configuration

### API Connection

| Property | Value |
|---|---|
| Base URL | `https://api.geckoterminal.com` |
| Auth | None required |
| Rate limit | ~10 requests/minute (free tier) |
| OHLCV endpoint | `/api/v2/networks/solana/pools/{pool_address}/ohlcv/minute` |
| Aggregate | `5` (5-minute candles) |
| Max records per call | 1000 (default: 100) |
| Max date range per call | 6 months |

### Required API Parameters for FL-001

| Parameter | Value | Reasoning |
|---|---|---|
| `aggregate` | `5` | 5-minute candles. Matches FL-001 temporal resolution. |
| `before_timestamp` | Unix epoch of `end` | GeckoTerminal paginates backward from this point |
| `limit` | `1000` | Maximum per call to minimize API calls |
| `currency` | `usd` | Returns prices and volume in USD |

### Pagination Strategy

GeckoTerminal paginates backward using `before_timestamp`. The connector fetches pages until the earliest timestamp in a page is at or before the requested `start`. A full 5000-minute observation window at 5-minute resolution = 1000 candles maximum = 1 API call. `time.sleep(6)` between paginated calls respects the ~10/min rate limit.

### Gateway Rotation (IP Diversity)

4 AWS API Gateway HTTP proxies rotate via `itertools.cycle` in the connector. Each gateway proxies requests through a different AWS region IP, reducing the chance of IP-based rate limiting from GeckoTerminal.

| # | Region | Endpoint |
|---|--------|----------|
| 1 | ap-southeast-2 (Sydney) | `GATEWAY_URL_1` in `.env` |
| 2 | us-east-1 (Virginia) | `GATEWAY_URL_2` in `.env` |
| 3 | eu-west-1 (Ireland) | `GATEWAY_URL_3` in `.env` |
| 4 | ap-northeast-1 (Tokyo) | `GATEWAY_URL_4` in `.env` |

**Wiring:** `marjon/settings.py` reads `GATEWAY_URL_1` through `GATEWAY_URL_4` from `.env` into a `GATEWAY_URLS` list. The connector uses `itertools.cycle(settings.GATEWAY_URLS)` to round-robin across gateways. If no gateways are configured, falls back to the direct GeckoTerminal URL (`https://api.geckoterminal.com`).

### Rate Limit Budget

~10 requests/minute = ~600/hour = ~14,400/day. At 1 call per coin for bootstrap (1000 candles fits in one call), this allows ~14,400 coins per day. Rate-limited by minute, not daily total — must throttle with `time.sleep(6)` between calls.

---

## Conformance Mapping: GeckoTerminal → FL-001

### Field Mapping Table

| Warehouse field | GeckoTerminal field | Transformation |
|---|---|---|
| `timestamp` | `ohlcv_list[n][0]` | Convert Unix epoch integer to UTC-aware `datetime`. Docs confirm this is interval start (matches WDP9). |
| `open_price` | `ohlcv_list[n][1]` | `Decimal(str(float))`. Source returns JSON float. |
| `high_price` | `ohlcv_list[n][2]` | `Decimal(str(float))`. |
| `low_price` | `ohlcv_list[n][3]` | `Decimal(str(float))`. |
| `close_price` | `ohlcv_list[n][4]` | `Decimal(str(float))`. |
| `volume` | `ohlcv_list[n][5]` | `Decimal(str(float))`. GeckoTerminal always provides volume — zero missing across 632 candles in source comparison. |
| `coin` (FK) | Not in OHLCV response | Resolved from pool mapping dimension table: pool_address → mint_address. |
| `ingested_at` | Not in OHLCV response | Set by the model's `auto_now_add=True`. |

### Semantic Decisions

| Decision | Choice | Reasoning |
|---|---|---|
| **Timestamp interpretation** | `ohlcv_list[n][0]` = interval start | GeckoTerminal docs confirm the single timestamp is the interval start. Matches warehouse WDP9. |
| **Price denomination** | USD | `currency=usd` parameter. Verified across 5 coins in source comparison — prices match expected micro-cap memecoin values (10^-5 to 10^-6 range). |
| **Volume denomination** | USD | `currency=usd` parameter. Volume is always present and precise (no integer truncation unlike DexPaprika). |
| **Sort order** | Connector reverses to ascending | GeckoTerminal returns descending (newest first). Connector reverses to ascending before returning. Conformance receives ascending order. |
| **Timezone** | UTC | Unix epoch is inherently UTC. `datetime.fromtimestamp(ts, tz=timezone.utc)` produces UTC-aware datetimes. |
| **Type casting** | `float → Decimal(str(float))` for all numeric fields | Non-negotiable in quant systems. `str()` intermediate avoids float→Decimal precision loss. |

### Identifier Mapping

GeckoTerminal OHLCV endpoint requires a **pool address**, not a mint address. The pipeline uses the pool mapping dimension table to resolve:

```
mint_address (warehouse identifier)
    → pool_address (query key, from PoolMapping dimension table)
        → GeckoTerminal OHLCV API call
            → canonical records with mint_address FK
```

Pool mapping is populated by the multi-source fallback chain documented in `u001_pool_mapping_pipeline_implementation_record.md`. GeckoTerminal OHLCV queries use the same pool addresses regardless of which source discovered the mapping.

---

## Previous Source: DexPaprika (deprecated for OHLCV)

DexPaprika was the original FL-001 source. Replaced by GeckoTerminal on 2026-03-11 after source comparison revealed critical data quality issues for Pumpswap tokens:

- **41% flat candles** (open=high=low=close) — DexPaprika showed no price movement where GeckoTerminal showed real trades
- **69 missing/null volumes** across 519 candles
- **Integer-truncated volumes** (vol=1, vol=7) vs GeckoTerminal's precise decimals (1.38, 7.33)
- **83% coverage** vs GeckoTerminal's 100%

DexPaprika connector (`pipeline/connectors/dexpaprika.py`) is kept for pool mapping discovery. DexPaprika conformance (`pipeline/conformance/fl001_dexpaprika.py`) is kept as archive.

---

## Pool Mapping Dimension Table

Pool mapping is documented in `u001_pool_mapping_pipeline_implementation_record.md`. FL-001 depends on pool mapping to resolve mint addresses to Pumpswap pool addresses for GeckoTerminal OHLCV queries. The pool mapping pipeline uses a Dexscreener batch → GeckoTerminal batch fallback chain.

---

## Idempotency Scope

Delete-write is scoped per coin, per time range being loaded. Exact scope definition, SQL, and transaction handling defined during implementation.

---

## Reconciliation Report

Count + boundary checks logged as informational reports after each coin's load. Must account for legitimate sparsity. Exact log fields and format defined during implementation.

---

## Code Path Unification

Same code path for scheduled runs, bootstrap (new coin), and manual re-fill. The difference is only the input parameters (which coins, what time range). Exact parameters, defaults, and dispatch logic defined during implementation.

---

## Windowed Incremental Overlap

Overlap window size and exact behavior defined during implementation. Starting point: a small overlap (e.g. a few candles) as safety margin, tuned based on operational experience.

---

## Open Items

| Item | Status | Impact |
|---|---|---|
| FL-002 (Holder Snapshots) pipeline | ✅ Done | Separate pipeline record: `u001_fl002_pipeline_implementation_record.md` |
| Pool mapping population process | ✅ Resolved | Separate pipeline using DexPaprika API (`/networks/solana/tokens/{mint_address}/pools`). Runs independently. OHLCV pipeline reads results. Detailed implementation defined during build. |
| DexPaprika volume denomination | ✅ Verified (moot) | Cross-referenced against GeckoTerminal. Both APIs return USD volume. DexPaprika volumes are integer-truncated for Pumpswap tokens — moot now that GeckoTerminal is the source. |
| DexPaprika data quality | ✅ Resolved | Source comparison (5 coins, 28h) revealed 41% flat candles, missing volumes, integer truncation. Switched to GeckoTerminal. DexPaprika kept for pool mapping discovery only. |
| Warehouse field names vs data spec | ✅ Resolved | Convention: `_price` suffix. Fields: `open_price, high_price, low_price, close_price, volume`. Conformance mapping already uses these names. Data spec FL-001 needs updating from `open, high, low, close` to `open_price, high_price, low_price, close_price`. |
| Apply spec changes to data specification | ✅ Done | Removed market_cap, renamed fields with `_price` suffix, added USD denomination to FL-001. Unblocked FL-002 gap handling. Removed market_cap from JK-001 example. Updated glossary. Updated warehouse record (data types, models summary, added ingested_at). Output: `u001_data_specification.md`, `u001_dataset_implementation_record.md`. |
