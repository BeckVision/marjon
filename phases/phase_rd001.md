# Phase RD-001: Raw Transaction Pipeline (Shyft API)

## Context Header

**Project:** marjon — crypto quantitative research platform
**Prerequisite:** Phase 1 complete (RawTransaction stub model exists, MigratedCoin populated, PoolMapping populated). Shyft API key available (same key used in meme_analyzer via `SHYFT_API_KEY` env var).
**Architecture:** Pipeline code in `pipeline/` app. Three-stage flow: source connector → conformance function → loader.
**Data Source:** Shyft API — `GET /sol/v1/transaction/history`. Already used in `~/Desktop/projects/meme_analyzer` for swap parsing. Future: Helius as secondary/fallback source.
**Key reference code (do NOT copy — patterns only):**
- `~/Desktop/projects/meme_analyzer/meme_analyzer/clients/shyft.py` — Shyft client with pagination, retry, rate limiting
- `~/Desktop/projects/meme_analyzer/transactions/parsers.py` — 4-layer trade detection (BUY/SELL actions, SWAP actions, events, balance changes)
- `~/Desktop/projects/meme_analyzer/transactions/types.py` — ParsedTransaction dataclass fields

**Key paradigm docs (all in `docs/`):**
- `data_specification_guide.md` — reference table definition (concept 7)
- `warehouse_implementation_guide.md` — WDP1-WDP13, reference table key structure
- `pipeline_implementation_guide.md` — PDP1-PDP11, connector/conformance/loader contracts
- `u001_data_specification.md` — RD-001 stub (v0.1, feature set TBD)

**Naming convention:** Files prefixed `u001_rd001_` are scoped to universe U-001, reference dataset RD-001.

---

## Goal

A working pipeline that fetches individual swap/trade transactions from Shyft for graduated pump.fun tokens and loads them into the RawTransaction table. Same three modes as FL-001/FL-002 (bootstrap, steady-state, refill). Idempotent via delete-write.

**What you can DO after this phase:** `python manage.py fetch_transactions --coin <mint_address>` and see individual swap records in the database. `get_reference_data(asset_id, start, end, simulation_time)` returns real transaction rows with PIT enforcement.

---

## Files/Code Produced

| Output | Description |
|---|---|
| `docs/u001_rd001_api_exploration_findings.md` | Shyft API findings: endpoint, params, response structure, edge cases |
| `docs/u001_rd001_pipeline_implementation_record.md` | All 11 PDP selections, conformance mapping, DQ constraints |
| `warehouse/models.py` (updated) | RawTransaction with actual fields, constraints, indexes. New SkippedTransaction model for unparsed transactions. |
| `warehouse/migrations/NNNN_*.py` | Migration for new RawTransaction fields + SkippedTransaction table |
| `pipeline/connectors/shyft.py` | Source connector for Shyft transaction/history API |
| `pipeline/conformance/rd001_shyft.py` | Conformance: raw Shyft JSON → (parsed RawTransaction dicts, skipped SkippedTransaction dicts) |
| `pipeline/loaders/rd001.py` | Loader: delete-write into RawTransaction + SkippedTransaction tables |
| `pipeline/management/commands/fetch_transactions.py` | Management command (bootstrap/steady-state/refill) |
| `pipeline/tests/fixtures/shyft_transactions_sample.json` | Saved raw API response for testing |
| `pipeline/tests/test_conformance_rd001.py` | Conformance tests |
| `pipeline/universes/u001.py` (updated) | New `raw_transactions` step |
| `pipeline/orchestration/handlers.py` (updated) | New `run_raw_transactions` handler |

---

## Key Differences from FL-001 / FL-002

| Property | FL-001 (GeckoTerminal) | FL-002 (Moralis) | RD-001 (Shyft) |
|---|---|---|---|
| **Table category** | Feature layer (time grid) | Feature layer (time grid) | Reference table (event facts) |
| **Time structure** | Fixed 5-min intervals | Fixed 5-min intervals | Irregular (one row per swap event) |
| **Auth** | None (gateway rotation) | API key, 50 CU/call | API key, 1 req/sec |
| **Query key** | Pool address (needs PoolMapping) | Mint address (direct) | Mint address (direct) — verify in Session A |
| **Pagination** | `before_timestamp` (range-based) | Cursor-based | `before_tx_signature` (cursor-based) |
| **Response format** | Positional arrays `[ts,o,h,l,c,v]` | Nested camelCase JSON | Deeply nested JSON (actions, events, balance changes) |
| **Rows per coin** | ~1000 candles (observation window) | ~1000 snapshots | Varies wildly: 10 to 100,000+ trades |
| **Gap behavior** | Sparse (no candle = no trades) | Dense (every interval present) | N/A (no fixed interval) |
| **Reconciliation** | Count informational (sparse normal) | Count strict (dense expected) | Count informational (trade volume varies) |
| **Unique key** | (coin, timestamp) | (coin, timestamp) | (coin, tx_signature) — one row per on-chain transaction |
| **Auto-join** | Yes (panel slice) | Yes (panel slice) | No (queried on-demand via get_reference_data) |

---

## Dependency Graph

```
Session A (API Exploration)
    │
    ▼
Session B (Specification)
    │
    ├──────────────┬──────────────┐
    ▼              ▼              ▼
Session C       Session D     Session E
(Model)       (Connector)   (Conformance)
    │              │              │
    └──────────────┴──────────────┘
                   │
                   ▼
             Session F
        (Loader + Command)
                   │
                   ▼
             Session G
        (Orchestrator Integration)
                   │
                   ▼
             Session H
        (Verification + Docs)
```

**Sessions C, D, E are parallel** — they share no code dependencies:
- C writes to `warehouse/models.py` (fields, constraints, migration)
- D writes to `pipeline/connectors/shyft.py` (API calls, pagination)
- E writes to `pipeline/conformance/rd001_shyft.py` (field transformation)

All three depend only on Session B's decisions (feature set, conformance mapping, scope).

---

## Session A — API Exploration (research, no code)

Verify Shyft's transaction/history endpoint works for marjon's use case. Save raw responses. Document findings.

**Reference:** meme_analyzer uses this same endpoint. But marjon's context is different (batch pipeline vs real-time scraper), so verify behavior independently.

| # | Task | Notes |
|---|---|---|
| A.1 | Make test API call: `GET https://api.shyft.to/sol/v1/transaction/history` with params `network=mainnet-beta`, `account=<mint_address>`, `tx_num=10`, `enable_events=true`, `enable_raw=false`. Use a token already in MigratedCoin. Examine raw JSON response. | Auth: `x-api-key` header with `SHYFT_API_KEY` from env. |
| A.2 | Document response structure: top-level keys (`success`, `message`, `result`), per-transaction fields (`signatures`, `timestamp`, `type`, `status`, `fee`, `fee_payer`, `protocol`, `actions`, `events`, `token_balance_changes`). Note which fields are always present vs optional. | meme_analyzer found: `type` can be SWAP, BUY, SELL, BUY_EXACT_QUOTE_IN, TOKEN_TRANSFER, TOKEN_BURN, etc. |
| A.3 | Identify which transaction types contain swap/trade data for our tokens. Look for: (1) `type=BUY`/`SELL` with `protocol.name="Pump.fun AMM"` (Layer 1), (2) `type=SWAP` with Jupiter routing (Layer 2), (3) events with `BuyEvent`/`SellEvent` (Layer 2.5). Document what percentage of transactions are swaps vs noise. | meme_analyzer uses 4-layer detection. Marjon may need fewer layers — Session B decides. |
| A.4 | Test pagination: fetch 2 pages using `before_tx_signature` (set to last tx's `signatures[0]`). Verify: no duplicates at boundary, no gaps. Confirm stop condition: response returns fewer than `tx_num` results when exhausted. | Max 100/request. |
| A.5 | Verify time filtering: does Shyft support `fromDate`/`toDate` params, or must we paginate backward and stop when `tx.timestamp < start`? This determines connector's time-bounding strategy. | Critical for incremental runs — if no server-side time filter, connector must paginate until timestamp is out of range. |
| A.6 | Check rate limits empirically: make 5 rapid calls, observe if 429 is returned. Confirm 1 req/sec REST limit. Check if there's a daily call limit (unlike Moralis's 40,000 CU/day). | meme_analyzer uses 0.3s between calls (conservative). |
| A.7 | Check edge cases: (1) dead coin with zero recent trades — what does empty response look like? (2) very active coin — how many total transactions in a 3.47-day window? (3) failed transactions (`status != "Success"`) — are they included? | Volume estimate needed for bootstrap planning. |
| A.8 | Save 2 raw responses to `pipeline/tests/fixtures/shyft_transactions_sample.json`. Must include: at least one BUY and one SELL action from Pump.fun AMM, at least one SWAP (Jupiter), and ideally a non-swap transaction (TOKEN_TRANSFER) to test filtering. | Fixture for conformance tests in Session E. |
| A.9 | Write `docs/u001_rd001_api_exploration_findings.md`. Cover: endpoint URL, auth, params, response format, pagination method, rate limits, transaction types observed, volume estimates per coin, edge cases, time filtering capability. | Same format as `u001_fl002_api_exploration_findings.md`. |

**Output:** `docs/u001_rd001_api_exploration_findings.md`, `pipeline/tests/fixtures/shyft_transactions_sample.json`

---

## Session B — Specification (decisions, no code)

Resolve all design decisions. Every decision references a specific paradigm doc or API finding.

| # | Task | Reference |
|---|---|---|
| B.1 | **Define trade detection scope.** meme_analyzer uses 4-layer detection (Layer 1: BUY/SELL actions, Layer 2: SWAP actions, Layer 2.5: on-chain events, Layer 3: balance change fallback). For marjon's quant research: decide which layers to include. Consider: Layer 1 (Pump.fun AMM direct trades) captures the dominant trading activity for newly graduated tokens. Layers 2-4 add Jupiter aggregator routes and edge cases. Trade-off: simpler conformance vs completeness. | Session A findings: what percentage of trades are Layer 1 vs others? |
| B.2 | **Define feature set** — which fields per transaction to store in RawTransaction. Candidate fields (from meme_analyzer's ParsedTransaction): `tx_signature` (unique ID), `timestamp`, `trade_type` (BUY/SELL), `wallet_address` (trader), `token_amount` (Decimal), `sol_amount` (Decimal, gross), `price_per_token` (Decimal, in SOL), `fee_sol` (Decimal, total fees), `pool_address`, `protocol_name`, `detection_layer` (diagnostics). Decide which are essential for quant research vs noise. | meme_analyzer/transactions/types.py — ParsedTransaction fields. Paradigm: feature set is per-row data specific to this reference table. |
| B.3 | **Define unique constraint.** Transaction signature is globally unique on Solana. Options: (a) `unique_together = [("coin", "tx_signature")]` — compound, safer for FK integrity. (b) `tx_signature` alone (unique=True). Consider: one transaction could involve multiple tokens (multi-hop swap). If we store one row per (coin, tx), compound key is correct. | WDP1: Surrogate PK + unique_together. |
| B.4 | **Define DQ constraints.** CHECK constraints for hard rejects: `token_amount > 0`, `sol_amount >= 0`, `trade_type IN ('BUY', 'SELL')`. Any clean() validations? Timestamp within observation window? | WDP6: Split by severity. Follow OHLCVCandle pattern for constraint naming (e.g., `rd001_token_amount_positive`). |
| B.5 | **Resolve PDP1 (Extract).** Windowed incremental: paginate backward from newest, stop at watermark. Overlap safety margin (like FL-001's 30 min). Time range = [watermark - overlap, now]. | Same strategy as FL-001/FL-002. |
| B.6 | **Resolve PDP3 (Idempotency).** Delete-write per coin per time range. Within `transaction.atomic()`: delete all RawTransaction rows for coin in [start, end], then bulk_create new ones. | Delete-write chosen because: (1) transactions are immutable on-chain, (2) re-fetching the same range should produce identical data, (3) upsert would require per-row update_or_create which is slow for thousands of trades. |
| B.7 | **Resolve PDP4 (Watermark).** Derive from warehouse: `MAX(timestamp)` per coin from RawTransaction table. | Same as FL-001/FL-002. |
| B.8 | **Resolve PDP5 (Rate Limit).** 1 req/sec for Shyft REST. Strategy: `time.sleep(1.0)` between calls in connector. No gateway rotation (single endpoint). If daily limit exists (from A.6 findings), add budget guard like Moralis CU tracker. | Unlike GeckoTerminal (6 gateways), Shyft is single-endpoint. |
| B.9 | **Resolve remaining PDPs.** PDP2 (ETL), PDP6 (error: retry then fail), PDP7 (reconciliation: count informational), PDP8 (provenance: row-level ingested_at), PDP9 (multi-source: Shyft primary, Helius future), PDP10 (scheduling: manual + orchestrator), PDP11 (dimension tables: warehouse owns all). | Follow existing selections — same reasoning applies. |
| B.10 | **Define conformance mapping table.** For each field: source JSON path → target model field → transformation. Example: `result[i].signatures[0]` → `tx_signature` → `str`, `result[i].timestamp` → `timestamp` → `parse ISO → UTC datetime`, `actions[j].info.quote_amount_out` → `sol_amount` → `Decimal(str(lamports)) / 10**9`. | Same format as FL-001/FL-002 conformance mapping tables in their implementation records. |
| B.11 | **Estimate bootstrap volume.** Using A.7 findings: avg trades per coin × total coins in universe. Calculate: total API calls needed, time at 1 req/sec, feasibility of full-universe bootstrap. If impractical, define prioritization (e.g., start with coins that have FL-001 data). | FL-001 bootstrap was ~3 calls/coin × 150K coins. Transaction volume per coin varies wildly. |
| B.12 | **Write `docs/u001_rd001_pipeline_implementation_record.md`.** Document all 11 PDP selections with reasoning, feature set, conformance mapping table, DQ constraints, unique constraint, bootstrap plan. | Follow exact structure of `u001_fl001_pipeline_implementation_record.md`. |

**Output:** `docs/u001_rd001_pipeline_implementation_record.md`

---

## Session C — Warehouse: Model Update (depends on B, parallel with D and E)

| # | Task | Notes |
|---|---|---|
| C.1 | Update `RawTransaction` in `warehouse/models.py`. Add all per-row fields from B.2 feature set. Update per-definition constants: `DATA_SOURCE = "Shyft"`, `REFRESH_POLICY` (from B.9), `VERSION = "1.0"`. Remove TBD placeholders. | Field types follow WDP8: DecimalField(38,18) for prices/amounts, CharField for addresses/signatures, CharField with choices for trade_type. |
| C.2 | Add unique constraint from B.3 decision. Add to `Meta.constraints` list (e.g., `UniqueConstraint(fields=["coin", "tx_signature"], name="rd001_unique_tx_per_coin")`). | Follow OHLCVCandle's `unique_together` pattern. |
| C.3 | Add CHECK constraints from B.4 (e.g., `CheckConstraint(condition=Q(token_amount__gt=0), name="rd001_token_amount_positive")`). | Follow existing constraint naming: `rd001_` prefix. |
| C.4 | Add indexes for reference table access pattern: `Index(fields=["coin", "timestamp"])` for range queries (the primary access pattern: "all trades for coin X between T1 and T2"), `Index(fields=["tx_signature"])` for dedup/lookup. | WDP7: compound + timestamp. |
| C.5 | Create `SkippedTransaction` model in `warehouse/models.py`. Fields: tx_signature, timestamp, coin (FK), pool_address, tx_type, tx_status, skip_reason (choices: no_trade_event, failed, parse_error), raw_json (JSONField), ingested_at. Unique constraint: (coin, tx_signature). Not a paradigm table — operational/diagnostic for storing unparsed transactions. | See implementation record: "Skipped Transaction Capture" section. |
| C.6 | Generate migration: `python manage.py makemigrations warehouse`. Review generated SQL. Apply: `python manage.py migrate`. | Adding columns to existing RawTransaction table + creating new SkippedTransaction table. |
| C.7 | Run existing tests: `python manage.py test warehouse`. Verify no regressions — existing ReferenceDataHappyPathTest should still pass (it only uses coin + timestamp). | |

**Output:** Updated `warehouse/models.py`, new migration file.

---

## Session D — Pipeline: Connector (depends on B, parallel with C and E)

| # | Task | Notes |
|---|---|---|
| D.1 | Create `pipeline/connectors/shyft.py`. Define module-level constants: `BASE_URL = "https://api.shyft.to/sol/v1"`, `ENDPOINT = "/transaction/history"`, `MAX_PER_PAGE = 100`, `RATE_LIMIT_SLEEP = 1.0`. Import API key from `settings.SHYFT_API_KEY` (add to settings if not present). | Follow geckoterminal.py / moralis.py module structure. |
| D.2 | Write `fetch_transactions(mint_address, start=None, end=None)`. Makes GET to `{BASE_URL}{ENDPOINT}` with params from A.1. Returns `(raw_transactions_list, metadata_dict)`. Metadata: `{'api_calls': int}`. | Connector contract: return raw data + metadata, no transformation. |
| D.3 | Add cursor pagination. After each page: if `len(result) < tx_num`, stop. Otherwise, set `before_tx_signature = result[-1]['signatures'][0]` and fetch next page. Concatenate all `result` arrays. | Shyft paginates backward in time. Stop when: no more results, OR oldest tx timestamp < start (if start provided). |
| D.4 | Add time-bounding. If `start` is provided: after each page, check if oldest transaction `timestamp < start`. If so, filter out-of-range transactions from last page and stop. If `end` is provided: set it as the initial bound (skip newer transactions — or filter post-fetch if Shyft doesn't support `toDate`). | Based on A.5 findings: server-side vs client-side time filtering. |
| D.5 | Add type filtering (from B.1 scope decision). Only include transactions whose `type` matches the selected detection layers. Filter before returning — connector returns only relevant transactions. | Pre-filter reduces data passed to conformance. |
| D.6 | Use `request_with_retry()` from `pipeline/connectors/http.py`. Pass Shyft-specific headers: `{'x-api-key': settings.SHYFT_API_KEY}`. Add `validate_response` callback that checks `response['success'] == True` (Shyft returns 200 with `success: false` on errors). | Reuse existing retry/backoff infrastructure. |
| D.7 | Add rate limiting: `time.sleep(RATE_LIMIT_SLEEP)` after each API call. | Shyft REST = 1 req/sec. Conservative approach matches meme_analyzer's pattern. |
| D.8 | If A.6 found a daily call limit: add budget guard (like Moralis CU tracker in `moralis.py`). Track daily calls in `.shyft_call_tracker.json`. | Only if rate limit exists. Skip if Shyft has no daily cap. |

**Output:** `pipeline/connectors/shyft.py`

---

## Session E — Pipeline: Conformance + Tests (depends on B, parallel with C and D)

| # | Task | Notes |
|---|---|---|
| E.1 | Create `pipeline/conformance/rd001_shyft.py`. Define `conform(raw_transactions, mint_address)` → `(parsed_records, skipped_records)`. Returns two lists: parsed dicts matching RawTransaction fields, and skipped dicts matching SkippedTransaction fields. Pure function: no DB access, no API calls, no side effects. | Follow pattern of `fl001_geckoterminal.py` but with dual output for parsed/skipped split. |
| E.2 | Implement field transformations from B.10 mapping table. Key transforms: (1) `signatures[0]` → `tx_signature` (str), (2) `timestamp` ISO → UTC datetime, (3) extract trade_type from BuyEvent/SellEvent name, (4) extract raw integer amounts from event data (no conversion — raw on-chain values), (5) set `coin_id = mint_address`. For skipped transactions: capture tx_signature, timestamp, tx_type, tx_status, skip_reason, and full JSON blob. | Crash on malformed data (PDP6) only for parseable transactions. Skipped transactions go to SkippedTransaction — `parse_error` reason if BuyEvent/SellEvent found but extraction fails. |
| E.3 | Handle transaction classification. For each transaction: (1) if `status != "Success"` → skip with reason `failed`, (2) if no BuyEvent/SellEvent in events → skip with reason `no_trade_event`, (3) if BuyEvent/SellEvent found but extraction raises → skip with reason `parse_error` (wrap in try/except, log warning), (4) otherwise → parse into RawTransaction dict. One row per transaction (Pumpswap emits exactly one BuyEvent/SellEvent per swap). | |
| E.4 | Verify fixture from A.8 covers test cases: BUY (SellEvent), BUY (BuyEvent + SwapsEvent), direct BUY (TOKEN_TRANSFER), first-time BUY (CREATE_TOKEN_ACCOUNT + SwapsEvent). Create `pipeline/tests/test_conformance_rd001.py`. Load fixture, call `conform()`, assert: correct field count, int types for amounts/fees/reserves, UTC-aware timestamps, trade_type is 'BUY' or 'SELL', tx_signature is non-empty. Assert specific raw values from fixture. Also test that non-trade transactions produce skipped_records with correct skip_reason. | Follow test pattern of `test_conformance_fl002.py`. |
| E.5 | Run tests: `python manage.py test pipeline.tests.test_conformance_rd001`. | |

**Output:** `pipeline/conformance/rd001_shyft.py`, `pipeline/tests/test_conformance_rd001.py`

---

## Session F — Pipeline: Loader + Command (depends on C, D, E)

| # | Task | Notes |
|---|---|---|
| F.1 | Create `pipeline/loaders/rd001.py`. Write `load(mint_address, start, end, parsed_records, skipped_records)`. Delete-write pattern inside `transaction.atomic()`: delete existing RawTransaction AND SkippedTransaction rows for coin in [start, end], then `bulk_create` both. Raise ValueError if both lists are empty (no data at all — suspicious). Allow parsed_records to be empty if skipped_records has data (legitimate — some time ranges may have only non-trade activity). | Same delete-write pattern as `fl001.py` and `fl002.py`, extended for dual table writes. |
| F.2 | Write `get_watermark(mint_address)`. Returns `RawTransaction.objects.filter(coin_id=mint_address).aggregate(Max('timestamp'))['timestamp__max']`. | Same watermark pattern as FL-001/FL-002. |
| F.3 | Create `pipeline/management/commands/fetch_transactions.py`. Arguments: `--coin` (required, mint_address), `--start` (optional), `--end` (optional). | Follow `fetch_ohlcv.py` / `fetch_holders.py` argument pattern. |
| F.4 | Implement three modes in command: (1) **BOOTSTRAP** — no watermark, no start/end: fetch from `coin.anchor_event` to `coin.anchor_event + OBSERVATION_WINDOW_END`. (2) **STEADY_STATE** — watermark exists, no start/end: fetch from `watermark - overlap` to now (or window end if mature). (3) **REFILL** — explicit start/end provided. | Same mode logic as FL-001/FL-002. Overlap value from B.5 decision. |
| F.5 | Wire three stages: (1) call connector `fetch_transactions()`, (2) call conformance `conform()`, (3) call loader `load()`. Handle empty results: if connector returns 0 transactions, log and return (no error — valid for dead coins). | Unlike FL-001/FL-002 where empty might be suspicious, zero trades in a time range is normal for reference tables. |
| F.6 | Add pipeline run tracking (PDP8). Create `U001PipelineRun` at start with `layer_id=RawTransaction.REFERENCE_ID` ("RD-001"). Update on success: `status=COMPLETE`, `records_loaded`, `time_range_start/end`. Update on failure: `status=ERROR`, `error_message`. Update `U001PipelineStatus`: `status`, `watermark`, `last_run`, `last_run_at`. | Same tracking pattern as FL-001/FL-002. |
| F.7 | Add reconciliation logging (PDP7). Log: coin, time range, transactions loaded, API calls made. For reference tables, count is informational only (no expected count — trade volume varies). | Unlike FL-002 where missing intervals are suspicious, variable trade counts are normal. |
| F.8 | End-to-end test: pick a token already in MigratedCoin with known trading activity. Run `python manage.py fetch_transactions --coin <mint>`. Verify: transactions in database, timestamps UTC, prices Decimal, trade_type correct. Run again — idempotent (same count, delete-write replaces). | Manual verification — not automated test. |

**Output:** `pipeline/loaders/rd001.py`, `pipeline/management/commands/fetch_transactions.py`

---

## Session G — Orchestrator Integration (depends on F)

| # | Task | Notes |
|---|---|---|
| G.1 | Add `raw_transactions` step to `pipeline/universes/u001.py` UNIVERSE config. Set: `name='raw_transactions'`, `layer_id='RD-001'`, `handler='pipeline.orchestration.handlers.run_raw_transactions'`, `per_coin=True`, `source='shyft'`, `rate_limit_sleep=1.0`. | Follow existing step structure in u001.py. |
| G.2 | Set `depends_on`. Based on connector query key: if querying by mint_address (likely), `depends_on='discovery'` (only needs MigratedCoin). If querying by pool_address, `depends_on='pool_mapping'`. | Session A determines this — mint_address is expected. |
| G.3 | Set `skip_if`. Options: `'window_complete'` (skip if RD-001 status is WINDOW_COMPLETE for this coin). Or `'window_complete_or_immature'` (also skip if coin hasn't matured). Decide based on whether we want to fetch in-progress windows. | FL-001 uses `window_complete_or_immature`. RD-001 may want to fetch in-progress windows since trades happen immediately. |
| G.4 | Write `run_raw_transactions(coin, config)` handler in `pipeline/orchestration/handlers.py`. Delegates to the fetch logic from `fetch_transactions` command. Returns result dict matching handler contract. | Same pattern as `run_ohlcv()` / `run_holders()`. |
| G.5 | Test dry-run: `python manage.py orchestrate --universe u001 --steps raw_transactions --coins 1 --dry-run`. Verify: step appears in plan, dependency resolved, skip logic applied. | |
| G.6 | Test real run: `python manage.py orchestrate --universe u001 --steps raw_transactions --coins 1`. Verify: transactions loaded, pipeline status updated, batch run recorded. | |

**Output:** Updated `pipeline/universes/u001.py`, updated `pipeline/orchestration/handlers.py`.

---

## Session H — Verification & Documentation (depends on G)

| # | Task | Notes |
|---|---|---|
| H.1 | Run full pipeline for 3-5 tokens with varying activity levels (active, moderate, dead). Verify: timestamps UTC, amounts Decimal, no duplicate tx_signatures per coin, BUY/SELL correctly classified, constraint violations caught. | |
| H.2 | Verify `get_reference_data()` returns real data. Test: call with a coin that has transactions, verify PIT enforcement (set simulation_time before some transactions — those should be excluded). | This operation was already implemented in Phase 4 — now it returns actual rows instead of empty. |
| H.3 | Run all tests: `python manage.py test warehouse data_service pipeline`. Verify no regressions. | Existing ReferenceDataHappyPathTest should still pass. |
| H.4 | Update `docs/u001_data_specification.md`: fill in RD-001 section with actual fields, source, constraints. Replace all TBD markers. | |
| H.5 | Update `docs/u001_dataset_implementation_record.md`: update RawTransaction row in the model summary table with actual fields and feature set. | Was: "Planned — feature set not yet defined." |
| H.6 | Add `SHYFT_API_KEY` to `marjon/settings.py` (loaded from env var, same pattern as other API keys). Document in project setup instructions. | If not already done in Session D. |

**Output:** Updated spec docs, verified data pipeline.

---

## Verification Criteria

After all sessions (A–H) are complete:

- [ ] `fetch_transactions --coin <mint>` (bootstrap) populates RawTransaction table with real swap data
- [ ] Running again (steady-state) loads only new transactions — idempotent
- [ ] `fetch_transactions --coin <mint> --start X --end Y` (refill) refreshes only the specified range
- [ ] All amounts are `Decimal` type, not `float`
- [ ] All timestamps are UTC-aware datetimes
- [ ] `tx_signature` is unique per coin (constraint enforced at DB level)
- [ ] DQ constraints reject invalid data (e.g., negative token_amount)
- [ ] `get_reference_data(asset, start, end, sim_time)` returns real transaction rows with PIT enforcement
- [ ] Conformance tests pass: `python manage.py test pipeline.tests.test_conformance_rd001`
- [ ] All existing tests still pass: `python manage.py test warehouse data_service pipeline`
- [ ] Pipeline run tracking: U001PipelineRun and U001PipelineStatus updated correctly
- [ ] Orchestrator: `--steps raw_transactions` works in dry-run and real mode
- [ ] Spec docs updated: no more TBD markers for RD-001

---

## Known Risks

| Risk | Impact | Mitigation |
|---|---|---|
| High transaction volume per coin | Bootstrap takes very long at 1 req/sec (100 txns/page). A coin with 50K trades = 500 calls = ~8 minutes per coin. Full universe bootstrap may be impractical initially. | B.11 estimates volume. Prioritize coins with FL-001 data. Consider batch-level concurrency later (multiple coins in parallel, each respecting 1 req/sec). |
| Shyft API key rate limit or daily cap | Pipeline blocked or key suspended. | A.6 verifies limits. Add budget guard if daily cap exists (like Moralis CU tracker). |
| Shyft returns 200 with `success: false` | Silent data loss — pipeline thinks it succeeded. | D.6: validate_response callback checks `success` field. Same pattern as Moralis 200-with-error handling. |
| No server-side time filtering | Cannot limit results to a time range server-side. Must paginate backward and filter client-side. Wastes API calls for incremental runs. | A.5 verifies this. If no time filter: connector stops pagination when tx.timestamp < start. |
| Multi-hop swaps (Jupiter) produce complex action arrays | Conformance must handle nested swap chains, intermediate tokens, split routes. | B.1 scopes detection layers. If Layer 1 only (Pump.fun AMM), most complexity avoided. |
| Transaction involves multiple tokens in universe | One Shyft transaction could be a swap between two graduated tokens. Would create ambiguity in coin FK. | B.3 unique constraint: (coin, tx_signature) allows same tx to appear under different coins if both are in universe. |
| Shyft API changes or deprecates endpoint | Pipeline breaks silently or loudly. | Version pin in spec doc. Helius as planned backup source (PDP9). |
| Failed transactions in response | `status != "Success"` transactions included — storing failed swaps would pollute data. | D.5 or E.2: filter out failed transactions (only store successful swaps). |

---

## Future: Helius as Secondary Source (PDP9)

The multi-source decision (PDP9) reserves Helius as a future secondary source. When added:

1. New connector: `pipeline/connectors/helius.py`
2. New conformance: `pipeline/conformance/rd001_helius.py` (maps Helius response → same RawTransaction fields)
3. Loader unchanged (same model, same delete-write pattern)
4. Fallback chain (like pool mapping's DexScreener → GeckoTerminal pattern): try Shyft first, fall back to Helius on failure
5. Add `detection_source` field to RawTransaction if provenance tracking needed

This is out of scope for this phase. Noted here for architectural awareness.
