# Phase 3: FL-002 Pipeline (Holder Snapshots)

## Context Header

**Project:** marjon — crypto quantitative research platform
**Prerequisite:** Phase 1 complete (Django project running, all models migrated). Can run in parallel with Phase 2 — they share MigratedCoin but write to different feature layer tables.
**Architecture:** Pipeline code lives in `pipeline/` Django app (same app as FL-001 pipeline). Three-stage flow: source connector → conformance function → loader.
**Key reference docs (all in `docs/`):**
- `u001_fl002_pipeline_implementation_record.md` — all 11 PDP selections, Moralis source config (API key, 50 CU/call, cursor pagination), conformance mapping (22 field transformations including nested field flattening), CU budget management, key differences from FL-001
- `u001_fl002_api_exploration_findings.md` — raw Moralis response samples, gap handling verified (every interval present), dead coin behavior, timestamp convention
- `pipeline_implementation_guide.md` — paradigm guide for pipeline architecture, conformance layer design
- `u001_data_specification.md` — FL-002 definition (5-min holder snapshots, Moralis source)

**Naming convention:** Files prefixed `u001_fl002_` are scoped to universe U-001, feature layer FL-002.

---

## Goal

A working pipeline that fetches 5-minute holder snapshots from Moralis for any graduated token and loads them into the HolderSnapshot table. Same three modes as FL-001 (bootstrap, steady-state, re-fill). Idempotent.

**What you can DO after this phase:** `python manage.py fetch_holders --coin <mint_address>` and see holder snapshots in the database. Every 5-minute interval populated — Moralis returns dense data (no gaps even when the coin is dead).

---

## Files/Code Produced

| Output | Description |
|---|---|
| `pipeline/connectors/moralis.py` | Source connector for Moralis API |
| `pipeline/conformance/fl002_moralis.py` | Conformance function: raw Moralis JSON → canonical HolderSnapshot dicts |
| `pipeline/loaders/fl002.py` | Load function: delete-write into HolderSnapshot table |
| `pipeline/management/commands/fetch_holders.py` | Management command |
| `pipeline/tests/fixtures/moralis_holders_sample.json` | Saved raw API response |
| `pipeline/tests/test_conformance_fl002.py` | Conformance tests |

---

## Key Differences from FL-001

These differences affect design decisions in every session below:

| Property | FL-001 (DexPaprika) | FL-002 (Moralis) |
|---|---|---|
| **Auth** | None | API key (`X-Api-Key` header), 50 CU/call |
| **Daily budget** | 10,000 requests (generous) | 800 calls (tight — key blocked if exceeded) |
| **Query key** | Pool address (needs PoolMapping lookup) | Mint address (direct — no mapping needed) |
| **Pagination** | `start`/`end` range, max 366/call, 3 calls for full window | Cursor-based, 100/page, 10 calls for full window |
| **Response structure** | Flat: `{"open": 83.5, "high": 84.0, ...}` | Nested: `{"holdersIn": {"whales": 0, "sharks": 5, ...}}` |
| **Sort order** | Ascending (oldest first) | Descending (newest first) — must reverse |
| **Gap behavior** | No candle if no trades (sparse, normal) | Every interval present (dense, zeros when inactive) |
| **Reconciliation** | Sparsity normal — 12 candles out of 1000 is fine | Missing intervals suspicious — expected count = `(end-start)/5min` exactly |

---

## Session 3.A — Brainstorm: FL-002 Pipeline Design (1-2 hours)

| # | Task | Reference |
|---|---|---|
| 3.A.1 | Design Moralis source connector interface. Input: mint_address (direct — no pool mapping), start (datetime), end (datetime). Output: raw JSON list (all pages concatenated, reversed to ascending order). Handles cursor-based pagination: follow `cursor` field until absent. Handles API key via env var (`MORALIS_API_KEY`). Must track CU consumption: 50 CU per call, 40,000 CU/day max. Full bootstrap = 10 calls = 500 CU per coin. | u001_fl002_pipeline_implementation_record.md "Moralis Source Configuration": base URL `https://solana-gateway.moralis.io`, endpoint `/token/mainnet/holders/{address}/historical`, params: `timeFrame=5min`, `fromDate`, `toDate`, `limit=100`, `cursor`. Auth: `X-Api-Key` header. |
| 3.A.2 | Design conformance function interface. Input: raw JSON list (from connector, already reversed to ascending), mint_address. Output: list of dicts matching HolderSnapshot field names. Transformations: `timestamp` (ISO 8601 with `.000Z` → UTC datetime, strip milliseconds), `totalHolders` → `total_holders` (int, direct), `netHolderChange` → `net_holder_change` (int), `holderPercentChange` → `holder_percent_change` (float → Decimal), flatten `newHoldersByAcquisition.swap/transfer/airdrop` → `acquired_via_swap/transfer/airdrop` (int), flatten `holdersIn.whales...shrimps` → `holders_in_whales...holders_in_shrimps` (int), same for `holdersOut` → `holders_out_*`. | u001_fl002_pipeline_implementation_record.md "Conformance Mapping: Moralis → FL-002" — the complete 22-row field mapping table |
| 3.A.3 | Design CU budget guard. Before starting a run, check remaining daily CU budget. If insufficient for at least one coin's bootstrap (500 CU), fail gracefully with a clear message instead of burning CUs and getting the key blocked. | u001_fl002_pipeline_implementation_record.md "CU Budget Management": 40,000 CU/day, key blocked if exceeded |
| 3.A.4 | Design reconciliation. FL-002 reconciliation is stricter than FL-001. Expected count = `(end - start) / timedelta(minutes=5)`. Actual count should match exactly (Moralis returns every interval). A significant mismatch means data loss, not legitimate sparsity. Log as warning, not just informational. | u001_fl002_pipeline_implementation_record.md (PDP7: "Missing intervals are suspicious") |

---

## Session 3.B — Implementation: Source Connector (2-3 hours)

| # | Task | Notes |
|---|---|---|
| 3.B.1 | Write `moralis.fetch_holders(mint_address, start, end)`. Makes GET to `https://solana-gateway.moralis.io/token/mainnet/holders/{mint_address}/historical` with params `timeFrame=5min`, `fromDate` (Unix timestamp or date string), `toDate` (same), `limit=100`. Adds `X-Api-Key` header from env var. Returns raw JSON `result` list. | u001_fl002_pipeline_implementation_record.md "Required API Parameters" |
| 3.B.2 | Add cursor pagination. After each call: if response contains `cursor` field, make next call with `cursor=<value>`. If no `cursor`, pagination complete. Concatenate all `result` arrays. | u001_fl002_api_exploration_findings.md: "`cursor` is present when more pages exist, absent when all data fits in one page" |
| 3.B.3 | Reverse the result list. Moralis returns descending (newest first). Warehouse convention expects ascending. | u001_fl002_pipeline_implementation_record.md semantic decision: "Sort order: Reverse on conformance" |
| 3.B.4 | Add retry with backoff. Moralis showed transient server errors during API exploration (HTTP 200 with error body). Check for error body in response, not just HTTP status. Retry up to 3 times with exponential backoff. | u001_fl002_api_exploration_findings.md: "first test call returned 'Internal server error occurred'" |
| 3.B.5 | Add CU tracking. Increment a counter for each API call (50 CU each). The daily budget guard (3.A.3) checks this before starting. | u001_fl002_pipeline_implementation_record.md (PDP5: "must track CU consumption, not just call count") |
| 3.B.6 | Test: call `fetch_holders` for TRUMP token with a 1-hour window. Verify response is a list of ~13 dicts (both boundaries inclusive) in ascending order with all expected fields. | u001_fl002_api_exploration_findings.md: "A 12:00–13:00 query at 5-min resolution returns 13 records (both boundaries included)" |

---

## Session 3.C — Implementation: Conformance Function + Tests (1-2 hours)

| # | Task | Notes |
|---|---|---|
| 3.C.1 | Save a real Moralis response to `pipeline/tests/fixtures/moralis_holders_sample.json`. Include at least 5 snapshots covering active and dead-coin intervals. | You already have `moralis_holders_5min_raw.json` from the API exploration |
| 3.C.2 | Write `conform_moralis_fl002(raw_response, mint_address)`. Pure function. For each record: parse `timestamp` to UTC datetime (strip `.000Z` milliseconds), extract `totalHolders` → `total_holders`, `netHolderChange` → `net_holder_change`, `holderPercentChange` → `holder_percent_change` (Decimal), extract 3 nested fields from `newHoldersByAcquisition` → `acquired_via_*`, extract 7 nested fields from `holdersIn` → `holders_in_*`, extract 7 from `holdersOut` → `holders_out_*`, set `coin` = mint_address, set `ingested_at` = now(UTC). | u001_fl002_pipeline_implementation_record.md "Field Mapping Table" — 22 field transformations |
| 3.C.3 | Write conformance test. Load fixture, call conformance function, assert: correct keys, `timestamp` is UTC-aware datetime, `holder_percent_change` is Decimal, nested fields correctly flattened (e.g., `holders_in_shrimps` extracted from `holdersIn.shrimps`), `coin` equals test mint. Assert specific values against fixture. | |
| 3.C.4 | Run tests: `python manage.py test pipeline.tests.test_conformance_fl002`. | |

---

## Session 3.D — Implementation: Loader + Wiring + End-to-End Test (2-3 hours)

| # | Task | Notes |
|---|---|---|
| 3.D.1 | Write `load_fl002(mint_address, start, end, canonical_records)`. Same delete-write pattern as FL-001 but for HolderSnapshot table. Inside `transaction.atomic()`: delete existing rows in range, `bulk_create` new ones. | Same PDP3 decision as FL-001 |
| 3.D.2 | Write `get_watermark_fl002(mint_address)`. Returns `HolderSnapshot.objects.filter(coin=mint_address).aggregate(Max('timestamp'))['timestamp__max']`. | Same PDP4 as FL-001 but different table |
| 3.D.3 | Write `fetch_holders` management command. Accepts `--coin <mint>`, optional `--start`, `--end`. Logic: (1) no pool mapping needed — use mint_address directly, (2) query watermark for time range (same bootstrap/steady-state/re-fill logic as FL-001), (3) call Moralis connector, (4) call conformance, (5) call loader. | Simpler than FL-001: no pool mapping step |
| 3.D.4 | Add reconciliation logging. After load: compare `loaded_count` to `expected_count = (end - start) / timedelta(minutes=5)`. If they don't match, log warning (not just informational — missing intervals are suspicious for FL-002). | FL-002 reconciliation is stricter than FL-001 |
| 3.D.5 | End-to-end test: pick the same token used in Phase 2 (already has a MigratedCoin row). Run `fetch_holders --coin <mint>`. Verify: ~1000 snapshots (full window), every 5-minute interval present, dead-coin intervals show `net_holder_change=0`. Run again — idempotent. | |

---

## Session 3.E — Implementation: Celery Integration (Later, 2-3 hours)

Same pattern as Phase 2 Session 2.G but with tighter rate limiting.

| # | Task | Notes |
|---|---|---|
| 3.E.1 | Write `fetch_holders_task(mint_address, start=None, end=None)` Celery task. Same logic as management command. Uses `self.retry(countdown=...)`. | |
| 3.E.2 | Add CU-based rate limiting. Unlike FL-001 (simple request count), FL-002 must track CU consumption. Celery rate limiter capped at 800 calls/day (40,000 CU ÷ 50 CU/call). Add a pre-run check: if estimated CU for this run would exceed remaining daily budget, defer to next day. | u001_fl002_pipeline_implementation_record.md (PDP5: "must track CU consumption") |
| 3.E.3 | Configure Celery beat schedule. Must coordinate with FL-001 schedule — both run independently but FL-002 has a much tighter budget. | u001_fl002_pipeline_implementation_record.md (PDP10: "Must coordinate CU budget between scheduled runs and manual backfills") |

---

## Verification Criteria

After all sessions (3.A–3.D) are complete:

- [ ] `fetch_holders --coin <mint>` (bootstrap) populates HolderSnapshot table with real data
- [ ] Every 5-minute interval is present — no gaps
- [ ] Running `fetch_holders` again (steady-state) produces no duplicates — same count
- [ ] `fetch_holders --coin <mint> --start X --end Y` (re-fill) refreshes only the specified range
- [ ] `holder_percent_change` is `Decimal` type, not `float`
- [ ] Nested fields are correctly flattened (e.g., `holdersIn.shrimps` → `holders_in_shrimps`)
- [ ] All timestamps are UTC-aware datetimes in ascending order
- [ ] Reconciliation log warns if loaded count doesn't match expected count
- [ ] Conformance tests pass: `python manage.py test pipeline.tests.test_conformance_fl002`
- [ ] CU tracking: console output shows CU consumed per run

After 3.E (Celery):

- [ ] `fetch_holders_task.delay(mint_address)` executes asynchronously
- [ ] CU-based rate limiter prevents exceeding 40,000 CU/day
- [ ] Pre-run budget check rejects runs when insufficient CU remains

---

## Known Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Moralis API key gets blocked for exceeding daily CU | Pipeline down for 24 hours | CU budget guard (3.A.3) prevents over-consumption. Track CU in every run. |
| Moralis returns HTTP 200 with error body (observed during exploration) | Silent data loss — pipeline thinks it succeeded | Check response body for error indicators, not just HTTP status (3.B.4) |
| Moralis timestamp convention assumed to be interval-start (not verified) | PIT enforcement off by one interval | u001_fl002_api_exploration_findings.md flags this as unverified. Test with a known-time event to confirm. |
| 22 nested fields in conformance — easy to miss or misname one | HolderSnapshot model has wrong data in wrong column | Conformance test (3.C.3) must assert specific values from the fixture for every field, especially nested ones. |
| Cursor pagination returns overlapping records at page boundaries | Duplicate snapshots in database | Delete-write idempotency handles duplicates within the time range. Still worth checking cursor behavior. |
| 800 calls/day limits bootstrap throughput to ~80 coins/day | Initial data population takes weeks for large universe | Start with a small test universe (5-10 coins). Plan multi-day bootstrap. |

---

## Estimated Effort

5 sessions: 1 brainstorm (1-2h) + 3 implementation (1-3h each) + 1 Celery session later (2-3h).
