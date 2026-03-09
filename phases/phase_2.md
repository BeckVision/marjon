# Phase 2: FL-001 Pipeline (OHLCV Data)

## Context Header

**Project:** marjon — crypto quantitative research platform
**Prerequisite:** Phase 1 complete (Django project running, all models migrated, empty tables in PostgreSQL)
**Architecture:** Pipeline code lives in a `pipeline/` Django app. No models in pipeline — it writes to `warehouse` models. Three-stage flow: source connector → conformance function → loader.
**Key reference docs (all in `docs/`):**
- `u001_fl001_pipeline_implementation_record.md` — all 11 PDP selections, DexPaprika source config, conformance mapping (7 field transformations), pool mapping table, pagination strategy
- `u001_fl001_api_exploration_findings.md` — raw API responses, OpenAPI schemas, verified behavior (`inversed=true`, USD denomination)
- `pipeline_implementation_guide.md` — paradigm guide for pipeline architecture, conformance layer design, code path unification
- `u001_data_specification.md` — FL-001 definition (5-min OHLCV, DexPaprika source)
- `u001_dataset_implementation_record.md` — WDP5 (delete-write for feature layers), data types

**Naming convention:** Files prefixed `u001_fl001_` are scoped to universe U-001, feature layer FL-001.

---

## Goal

A working pipeline that fetches 5-minute OHLCV candles from DexPaprika for any graduated token and loads them into the OHLCVCandle table. Handles bootstrap (full observation window), steady-state (incremental from watermark), and re-fill (explicit time range). Idempotent — safe to re-run.

**What you can DO after this phase:** `python manage.py fetch_ohlcv --coin <mint_address>` and see candles in the database. Run it again — no duplicates, same result. Query `OHLCVCandle.objects.filter(coin="<mint>").count()` and see real data.

---

## Files/Code Produced

| Output | Description |
|---|---|
| `pipeline/` app | Django app for all pipeline code — no models, only code |
| `pipeline/connectors/dexpaprika.py` | Source connector for DexPaprika API |
| `pipeline/conformance/fl001_dexpaprika.py` | Conformance function: raw DexPaprika JSON → canonical OHLCVCandle dicts |
| `pipeline/loaders/fl001.py` | Load function: delete-write canonical records into OHLCVCandle table |
| `pipeline/management/commands/fetch_ohlcv.py` | Management command for manual runs |
| `pipeline/management/commands/populate_pool_mapping.py` | Management command for pool discovery |
| `pipeline/tests/fixtures/dexpaprika_ohlcv_sample.json` | Saved raw API response for conformance tests |
| `pipeline/tests/test_conformance_fl001.py` | Conformance tests |

---

## Session 2.A — Brainstorm: Pipeline Architecture Decisions (1-2 hours)

Decide on module boundaries, function signatures, and error handling before writing code.

| # | Task | Reference |
|---|---|---|
| 2.A.1 | Design source connector interface. Input: pool_address, start (datetime), end (datetime). Output: raw JSON list (exactly what the API returned). Handles pagination internally: 1000 candles max ÷ 366 per call = 3 calls. Handles rate limiting (start with `time.sleep()` between calls — upgrade to Celery later). Raises on HTTP errors after retries. | u001_fl001_pipeline_implementation_record.md "DexPaprika Source Configuration": base URL `https://api.dexpaprika.com`, endpoint `/networks/solana/pools/{pool_address}/ohlcv`, params: `start`, `end`, `interval=5m`, `limit=366`, `inversed=true`. No auth. 10,000 req/day. |
| 2.A.2 | Design conformance function interface. Input: raw JSON list (from connector), mint_address (string, for FK resolution). Output: list of dicts matching OHLCVCandle field names. Pure function — no side effects, no DB writes, no API calls. Transformations: `time_open` (ISO 8601 string) → `timestamp` (UTC datetime). `open/high/low/close` (float) → `open_price/high_price/low_price/close_price` (Decimal). `volume` (int) → `volume` (Decimal). Add `coin` = mint_address. Add `ingested_at` = now(UTC). | u001_fl001_pipeline_implementation_record.md "Conformance Mapping: DexPaprika → FL-001" — the complete field mapping table |
| 2.A.3 | Design load function interface. Input: mint_address, time_range (start, end), list of canonical dicts. Behavior: within a single database transaction, delete all OHLCVCandle rows for this mint_address where timestamp is in the time_range, then `bulk_create` the new records. This is the delete-write idempotency mechanism (PDP3). | u001_fl001_pipeline_implementation_record.md (PDP3: delete-write for feature layers). Scope: per coin, per time range. |
| 2.A.4 | Design watermark query. `OHLCVCandle.objects.filter(coin=mint_address).aggregate(Max('timestamp'))`. Returns None for new coins (triggers bootstrap: fetch full window T0 to T0+5000min). Returns a datetime for existing coins (triggers incremental: fetch from watermark to now or window end). | u001_fl001_pipeline_implementation_record.md (PDP4: derive from warehouse) |
| 2.A.5 | Design code path unification. The management command accepts `--coin <mint>` and optionally `--start` / `--end`. Without explicit range: query watermark, derive range automatically (bootstrap if None, incremental if exists). With explicit range: use provided range (re-fill scenario). All three scenarios use the same connector → conformance → load path. | pipeline_implementation_guide.md "Code Path Unification": bootstrap, steady-state, and re-fill differ only in parameters |
| 2.A.6 | Design reconciliation logging. After each load: count candles loaded vs theoretical max (`(end - start) / 5 minutes`). Check first candle is at or near T0. Check last candle is at expected position. Log as informational — don't gate. Sparse data is normal (coin died). | u001_fl001_pipeline_implementation_record.md (PDP7: count + boundary) |

---

## Session 2.B — Implementation: Pool Mapping Population (1-2 hours)

The OHLCV pipeline cannot run without knowing which pool_address to query for a given mint_address. This must be built first.

| # | Task | Notes |
|---|---|---|
| 2.B.1 | Write pool discovery function. Calls DexPaprika endpoint `/networks/solana/tokens/{mint_address}/pools` to find Pumpswap pools for a token. Returns list of pool detail dicts. | u001_fl001_api_exploration_findings.md: DexPaprika token pools endpoint returns pool list including `id` (pool address), `dex_id`, `created_at` |
| 2.B.2 | Write `populate_pool_mapping` management command. Takes `--coin <mint_address>`. Calls pool discovery, filters for `dex_id == "pumpswap"`, creates `PoolMapping` row with mint_address, pool_address, dex, source, created_at, discovered_at. Uses `update_or_create` (pool mapping is not append-only). | PoolMapping schema from u001_fl001_pipeline_implementation_record.md |
| 2.B.3 | Test: run `populate_pool_mapping` for a known graduated token. Verify PoolMapping row exists in the database with correct pool_address. | Use a coin from the API exploration (e.g., BLACKHOUSE or any known graduated token) |

---

## Session 2.C — Implementation: Source Connector (1-2 hours)

| # | Task | Notes |
|---|---|---|
| 2.C.1 | Write `dexpaprika.fetch_ohlcv(pool_address, start, end)`. Makes GET request to `https://api.dexpaprika.com/networks/solana/pools/{pool_address}/ohlcv` with params `start` (ISO format), `end` (ISO format), `interval=5m`, `limit=366`, `inversed=true`. Returns raw JSON list. | Exact params from u001_fl001_pipeline_implementation_record.md "Required API Parameters" |
| 2.C.2 | Add pagination. A single call returns max 366 candles. If the time range spans more than 366×5 = 1830 minutes, the connector must paginate: call 1 gets candles 1-366, call 2 starts from the last timestamp of call 1, etc. Full 5000-min window needs 3 calls (366+366+268). Concatenate results. | u001_fl001_pipeline_implementation_record.md "Pagination Strategy" |
| 2.C.3 | Add retry with backoff. On HTTP 429 or 5xx: wait (exponential backoff starting at 2s), retry up to 3 times. After 3 failures: raise. On HTTP 4xx (non-429): raise immediately (bad request, not transient). | u001_fl001_pipeline_implementation_record.md (PDP6: retry with backoff, then fail) |
| 2.C.4 | Add rate limiting. Start simple: `time.sleep(0.5)` between paginated calls within a single coin. This stays well within 10,000 req/day. Celery-based rate limiting comes later (session 2.G). | Start with PDP5 Option A (serial with sleep), upgrade to Option C later |
| 2.C.5 | Test: call `fetch_ohlcv` for a known pool_address with a short time range (1 hour). Verify response is a list of dicts with `time_open`, `time_close`, `open`, `high`, `low`, `close`, `volume`. | Use a pool_address from the API exploration saved responses |

---

## Session 2.D — Implementation: Conformance Function + Tests (1-2 hours)

| # | Task | Notes |
|---|---|---|
| 2.D.1 | Save a real DexPaprika OHLCV response to `pipeline/tests/fixtures/dexpaprika_ohlcv_sample.json`. Include at least 5 candles. This is the conformance test fixture — a frozen snapshot of a real API response. | You already have `dexpaprika_ohlcv_5m_inversed.json` from the API exploration |
| 2.D.2 | Write `conform_dexpaprika_fl001(raw_response, mint_address)`. Pure function. For each record in the raw JSON list: parse `time_open` to UTC datetime (using `datetime.fromisoformat()`), cast `open/high/low/close` from float to `Decimal(str(value))` (never `Decimal(float)` — that preserves float imprecision), cast `volume` from int to `Decimal`, set `coin` = mint_address, set `ingested_at` = `datetime.now(timezone.utc)`. Return list of dicts. | u001_fl001_pipeline_implementation_record.md "Field Mapping Table". Key: `Decimal(str(value))` not `Decimal(value)` for float→Decimal. |
| 2.D.3 | Write conformance test. Load the fixture JSON, call `conform_dexpaprika_fl001(fixture, "test_mint")`, assert: all dicts have exactly the right keys, `timestamp` is a UTC-aware datetime, all price fields are `Decimal`, `volume` is `Decimal`, `coin` equals `"test_mint"`. Assert specific values against hand-calculated expected output from the fixture. | pipeline_implementation_guide.md: "Save a raw API response, feed it to the conformance function, verify output. The single highest-value test in a pipeline." |
| 2.D.4 | Run tests: `python manage.py test pipeline.tests.test_conformance_fl001`. | |

---

## Session 2.E — Implementation: Loader + Watermark + Wiring (2-3 hours)

| # | Task | Notes |
|---|---|---|
| 2.E.1 | Write `load_fl001(mint_address, start, end, canonical_records)`. Inside `transaction.atomic()`: delete `OHLCVCandle.objects.filter(coin=mint_address, timestamp__gte=start, timestamp__lte=end)`, then `OHLCVCandle.objects.bulk_create([OHLCVCandle(**record) for record in canonical_records])`. This is delete-write (PDP3). | u001_fl001_pipeline_implementation_record.md (PDP3: delete-write scoped per coin, per time range) |
| 2.E.2 | Write `get_watermark_fl001(mint_address)`. Returns `OHLCVCandle.objects.filter(coin=mint_address).aggregate(Max('timestamp'))['timestamp__max']`. Returns `None` for coins with no data. | u001_fl001_pipeline_implementation_record.md (PDP4: derive from warehouse) |
| 2.E.3 | Write `fetch_ohlcv` management command. Accepts `--coin <mint>`, optional `--start`, `--end`. Logic: (1) look up pool_address from PoolMapping, (2) if no explicit range: query watermark — None means bootstrap (start=T0 from MigratedCoin.anchor_event, end=T0+5000min or now), datetime means incremental (start=watermark, end=window end or now), (3) call connector, (4) call conformance, (5) call loader. | Code path unification: same path for bootstrap, steady-state, and re-fill |
| 2.E.4 | Add reconciliation logging to the management command. After load: `loaded_count = len(canonical_records)`, `theoretical_max = (end - start).total_seconds() / 300`, `first_ts = min(r['timestamp'] for r in canonical_records)`, `last_ts = max(...)`. Log all four values. If `loaded_count == 0` and the coin exists, log a warning. | u001_fl001_pipeline_implementation_record.md (PDP7: count + boundary, informational) |

---

## Session 2.F — End-to-End Test (1 hour)

| # | Task | Notes |
|---|---|---|
| 2.F.1 | Pick a real graduated token. Run `populate_pool_mapping --coin <mint>`. Verify PoolMapping row created. | |
| 2.F.2 | Manually create a `MigratedCoin` for that token in the shell (set mint_address and anchor_event to the pool's created_at time). | The pipeline doesn't yet auto-create MigratedCoin entries — that's a separate concern |
| 2.F.3 | Run `fetch_ohlcv --coin <mint>`. This is a bootstrap run — watermark is None, so it fetches the full observation window. Check: candles appear in OHLCVCandle table. Count matches reconciliation log. Timestamps are UTC. Prices are Decimal, not float. | |
| 2.F.4 | Run `fetch_ohlcv --coin <mint>` again immediately. This is a steady-state run — watermark exists. Check: no duplicates (delete-write handles overlap). Same total count in database. | Idempotency test |
| 2.F.5 | Run `fetch_ohlcv --coin <mint> --start <specific_time> --end <specific_time>`. This is a re-fill run. Check: only the specified range is refreshed, other data untouched. | Code path unification test |

---

## Session 2.G — Implementation: Celery Integration (Later, 2-3 hours)

Only after 2.A–2.F work end-to-end via management commands.

| # | Task | Notes |
|---|---|---|
| 2.G.1 | Write Celery task `fetch_ohlcv_task(mint_address, start=None, end=None)`. Same logic as the management command but as a Celery task. Uses `self.retry(countdown=...)` for transient errors. | u001_fl001_pipeline_implementation_record.md (PDP5: Celery queue, PDP6: retry with backoff then fail) |
| 2.G.2 | Add Celery rate limiting. DexPaprika: 10,000 req/day. Conservative: limit to ~7,000/day to leave headroom. Use Celery's `rate_limit` task decorator or a custom token bucket. | u001_fl001_pipeline_implementation_record.md (PDP5) |
| 2.G.3 | Configure Celery beat schedule for steady-state runs. Periodic task that: queries all MigratedCoin entries whose observation window hasn't closed yet, dispatches one `fetch_ohlcv_task` per coin. | u001_fl001_pipeline_implementation_record.md (PDP10: scheduled + manual) |
| 2.G.4 | Test: trigger the periodic task manually. Verify it dispatches tasks and data arrives in the database. | |

---

## Verification Criteria

After all sessions (2.A–2.F) are complete:

- [ ] `populate_pool_mapping --coin <mint>` creates a PoolMapping row with correct pool_address
- [ ] `fetch_ohlcv --coin <mint>` (bootstrap) populates OHLCVCandle table with real data
- [ ] Running `fetch_ohlcv` again (steady-state) produces no duplicates — same count
- [ ] `fetch_ohlcv --coin <mint> --start X --end Y` (re-fill) refreshes only the specified range
- [ ] All prices are `Decimal` type, not `float`
- [ ] All timestamps are UTC-aware datetimes
- [ ] Reconciliation log prints: loaded count, theoretical max, first/last timestamp
- [ ] Conformance tests pass: `python manage.py test pipeline.tests.test_conformance_fl001`

After 2.G (Celery):

- [ ] `fetch_ohlcv_task.delay(mint_address)` executes asynchronously
- [ ] Rate limiter stays within 10,000 req/day
- [ ] Celery beat dispatches tasks on schedule

---

## Known Risks

| Risk | Impact | Mitigation |
|---|---|---|
| DexPaprika API returns different field names or structure than explored | Conformance function breaks | Saved fixture (2.D.1) catches regressions. If API changes, update fixture + conformance. |
| `inversed=true` behavior differs for non-Pumpswap pools | Wrong price denomination for some tokens | Verify `inversed=true` on a known-price token during 2.F.1. Only query Pumpswap pools (filter in pool mapping). |
| Pagination edge case: last candle of page N duplicated as first candle of page N+1 | Duplicate candles loaded | Delete-write idempotency (PDP3) handles this — duplicates within the time range are deleted before re-insert. Still worth checking. |
| 10,000 req/day budget consumed during development/testing | Rate limit hit in production | Track requests during dev. Use short time ranges for testing (1 hour = 1 call, not 3). |
| `Decimal(float_value)` instead of `Decimal(str(float_value))` | Floating point imprecision preserved | Conformance test (2.D.3) must assert exact Decimal values. This is the whole point of the conformance test. |

---

## Estimated Effort

7 sessions: 1 brainstorm (1-2h) + 5 implementation (1-3h each) + 1 Celery session later (2-3h).
