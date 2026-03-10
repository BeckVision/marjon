# Pipeline Implementation Record: U-001 / FL-001

**Feature Layer:** FL-001 — OHLCV Price Data
**Dataset:** U-001 — Graduated Pump.fun Tokens — Early Lifecycle
**Source:** DexPaprika API (OHLCV endpoint)
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
| **PDP5** | Rate Limit Handling | **C: Queue with rate limiter (Celery)** | Django project already uses Celery. Built-in retry with exponential backoff (aligns with PDP6). Rate limiting via Celery's built-in mechanisms or token bucket middleware. Infrastructure (Redis/RabbitMQ) justified because it also serves PDP10 (scheduling via Celery beat). |
| **PDP6** | Error Handling | **D: Retry with backoff, then fail** | Early-stage system — silent skipping is more dangerous than blocked runs. Persistent failures should surface immediately so they can be investigated. Celery's `self.retry(countdown=...)` handles transient errors (network blips, temporary 429s). After N retries, the task raises an exception and the run fails. Can soften to Option C (skip-and-continue) later when the system is stable and monitoring is in place. |
| **PDP7** | Reconciliation Strategy | **C: Count + boundary** | Both count and boundary checks, logged as informational reports. Count: how many candles loaded vs theoretical maximum for the time range. Boundary: first candle at/near T0, last candle at expected position. Must account for legitimate sparsity — a memecoin with 4 candles out of 1000 possible is normal. The pattern across many coins reveals systemic issues (e.g. 100 coins all returning 0 candles = API outage). |
| **PDP8** | Provenance Tracking | **B: Row-level ingest timestamp** | Add `ingested_at` field to warehouse models (OHLCVCandle, MigratedCoin). Plus run-level logging (start/end time, assets processed, record counts, errors). Cheap to add now — one column per model. Expensive to add retroactively after data exists. Distinguishes backfill rows from daily ingestion rows. |
| **PDP9** | Multi-Source Handling | **A: Single source (DexPaprika)** | One source connector, one conformance mapping. No risk of mixing data from different aggregation methods for the same coin. DexPaprika chosen over GeckoTerminal because: 10,000 requests/day is more generous than ~10/minute, and DexPaprika returns both `time_open` and `time_close` per candle which eliminates timestamp ambiguity. Architecture supports adding GeckoTerminal later as an additive change (new connector + new conformance function, no restructuring). |
| **PDP10** | Scheduling | **B + A: Scheduled + Manual** | Celery beat for automated scheduled runs (steady-state). Manual task triggers for ad-hoc backfills and debugging. Celery beat is already part of the stack from PDP5. Same code path for both — only the time range parameters differ (code path unification). |
| **PDP11** | Dimension Table Location | **A: Warehouse app owns all tables** | All database tables (paradigm models + dimension tables like pool mapping) live in the warehouse app. Pipeline app owns only code (source connectors, conformance functions, Celery tasks). Simpler FK relationships. One app for all models, one app for all pipeline logic. |

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

## DexPaprika Source Configuration

### API Connection

| Property | Value |
|---|---|
| Base URL | `https://api.dexpaprika.com` |
| Auth | None required |
| Rate limit | 10,000 requests/day |
| OHLCV endpoint | `/networks/solana/pools/{pool_address}/ohlcv` |
| Interval | `5m` |
| Max records per call | 366 (default: 1) |
| Max date range per call | 1 year |

### Required API Parameters for FL-001

| Parameter | Value | Reasoning |
|---|---|---|
| `start` | Watermark timestamp (or T0 for bootstrap) | Windowed incremental — start from where we left off |
| `end` | Current time (or T0+5000min if observation window has closed) | Fetch up to now or end of window |
| `interval` | `5m` | Matches FL-001 temporal resolution |
| `limit` | `366` | Maximum per call to minimize API calls |
| `inversed` | `true` | Pumpswap pools have token[0]=SOL, token[1]=memecoin. `inversed=true` returns memecoin price in USD. **Verified with TRUMP at known price.** |

### Pagination Strategy

A full 5000-minute observation window at 5-minute resolution = 1000 candles maximum. At 366 records per call, this requires 3 API calls per coin for a full bootstrap (366 + 366 + 268). Steady-state incremental runs typically require 1 call per coin.

### Rate Limit Budget

10,000 requests/day. At 3 calls per coin for full bootstrap, this allows ~3,333 coins per day for backfill. At 1 call per coin for steady-state, this allows 10,000 coins per day for incremental updates. Both are well within expected universe size.

---

## Conformance Mapping: DexPaprika → FL-001

### Field Mapping Table

| Warehouse field | DexPaprika field | Transformation |
|---|---|---|
| `timestamp` | `time_open` | Parse ISO 8601 string (`"2026-03-07T16:30:00Z"`) to UTC-aware `datetime`. Matches warehouse WDP9 (interval-start convention). |
| `open_price` | `open` | Cast `float` to `Decimal`. Source returns JSON `number, double`. |
| `high_price` | `high` | Cast `float` to `Decimal`. |
| `low_price` | `low` | Cast `float` to `Decimal`. |
| `close_price` | `close` | Cast `float` to `Decimal`. |
| `volume` | `volume` | Cast `int` to `Decimal`. Source returns JSON `integer, int64` per OpenAPI schema. |
| `coin` (FK) | Not in OHLCV response | Resolved from pool mapping dimension table: pool_address → mint_address. |
| `ingested_at` | Not in OHLCV response | Set to `datetime.now(UTC)` at load time. |

### Semantic Decisions

| Decision | Choice | Reasoning |
|---|---|---|
| **Timestamp interpretation** | `time_open` = interval start | DexPaprika provides both `time_open` and `time_close`. Using `time_open` matches warehouse WDP9 (interval-start convention). `time_close` is available for validation but not stored. |
| **Price denomination** | USD | DexPaprika with `inversed=true` returns memecoin price in USD. Verified with TRUMP (~$3.02) across multiple pool types. |
| **Volume denomination** | USD | DexPaprika with `inversed=true` returns volume in USD. Value differs from non-inversed mode for the same candle. Using inversed mode consistently. |
| **Pair direction** | `inversed=true` | Pumpswap pools list SOL as token[0] and memecoin as token[1]. `inversed=true` charts the memecoin (token[1]) perspective in USD. |
| **Timezone** | UTC | Source timestamps include `Z` suffix (UTC). No conversion needed. |
| **Type casting** | `float → Decimal` for prices, `int → Decimal` for volume | Non-negotiable in quant systems. Python floats have rounding errors that compound across calculations. |

### Identifier Mapping

DexPaprika OHLCV endpoint requires a **pool address**, not a mint address. The pipeline uses the pool mapping dimension table to resolve:

```
mint_address (warehouse identifier)
    → pool_address (DexPaprika query key)
        → OHLCV API call
            → canonical records with mint_address FK
```

The pool mapping must be populated before the OHLCV pipeline can run. Source: DexPaprika pool detail endpoint or token pools endpoint.

---

## Pool Mapping Dimension Table

### Purpose

Maps token mint addresses to their Pumpswap pool addresses. The OHLCV pipeline reads this table during the discovery stage to determine which pool_address to query for each coin.

### Schema (preliminary)

| Field | Type | Description |
|---|---|---|
| `mint_address` | `CharField(max_length=50)` | FK to MigratedCoin. The warehouse's asset identifier. |
| `pool_address` | `CharField(max_length=50)` | The Pumpswap pool address used to query DexPaprika. |
| `dex` | `CharField` | Which DEX this pool belongs to (e.g. `"pumpswap"`). |
| `source` | `CharField` | Which API discovered this mapping (e.g. `"dexpaprika"`). |
| `created_at` | `DateTimeField` | When the pool was created on-chain. From pool detail response. |
| `discovered_at` | `DateTimeField` | When the pipeline discovered this mapping. |

### Population

The pool mapping is populated by a separate discovery process — either a dedicated management command or the first stage of the OHLCV pipeline. Source: DexPaprika's token pools endpoint (`/networks/solana/tokens/{mint_address}/pools`) or DEX pools endpoint (`/networks/solana/dexes/pumpswap/pools`).

### Pool Selection Strategy

This is a U-001-specific decision. Other universes may use different DEX filters or pool selection strategies.

**Filter:** `dex == 'pumpswap'`. U-001 targets tokens that graduated from pump.fun to Pumpswap, so only Pumpswap pools are relevant. Pools on other DEXes (Raydium, Orca, etc.) are discovered by the API but excluded during population.

**Selection:** When multiple Pumpswap pools exist for the same token, the pipeline selects the one with the earliest `created_at` — this is the graduation pool (the pool created when the token migrated from pump.fun's bonding curve to Pumpswap). Later pools may represent re-listings or community-created pools and do not reflect the token's primary lifecycle.

**Known limitation:** Tokens that graduated before the Pumpswap era (migrated to Raydium instead) will have no Pumpswap pool mapping and therefore no OHLCV data in FL-001. This is an intentional scope boundary for U-001, not a bug. The U-001 universe definition targets the Pumpswap graduation pathway; pre-Pumpswap tokens fall outside this scope. If coverage of Raydium-era graduates is needed, it would require a separate universe definition with its own pool selection strategy.

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
| Pool mapping population process | ✅ Resolved | Separate pipeline using GeckoTerminal API. Runs independently. OHLCV pipeline reads results. Detailed implementation defined during build. |
| DexPaprika volume denomination | ✅ Verified | Cross-referenced against GeckoTerminal. Both APIs return USD volume. GT `currency=token` (SOL) × SOL price = GT `currency=usd` value. DexPaprika returns same USD-scale values with `inversed=true`. |
| Warehouse field names vs data spec | ✅ Resolved | Convention: `_price` suffix. Fields: `open_price, high_price, low_price, close_price, volume`. Conformance mapping already uses these names. Data spec FL-001 needs updating from `open, high, low, close` to `open_price, high_price, low_price, close_price`. |
| Apply spec changes to data specification | ✅ Done | Removed market_cap, renamed fields with `_price` suffix, added USD denomination to FL-001. Unblocked FL-002 gap handling. Removed market_cap from JK-001 example. Updated glossary. Updated warehouse record (data types, models summary, added ingested_at). Output: `u001_data_specification.md`, `u001_dataset_implementation_record.md`. |
