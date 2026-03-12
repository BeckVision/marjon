# Pipeline Implementation Record: U-001 / Universe

**Scope:** Universe population — MigratedCoin discovery
**Dataset:** U-001 — Graduated Pump.fun Tokens — Early Lifecycle
**Source:** Moralis API (Graduated Tokens endpoint)
**Reference:** pipeline_implementation_guide.md (for decision point definitions and option details)
**Reference:** u001_data_specification.md (for data contract)
**Reference:** u001_universe_api_exploration_findings.md (for verified API behavior and conformance requirements)

---

## Decision Selections

Each row references a decision point (DP) from the Pipeline Implementation Guide.

| DP | Decision | Selected Option | Reasoning |
|---|---|---|---|
| **PDP1** | Extract Strategy | **B: Incremental load** | The graduated endpoint has no date filter — it returns ALL graduated tokens, sorted descending by `graduatedAt`. Incremental works differently here than for feature layers: instead of passing a watermark as a query parameter, the pipeline paginates from newest and stops when it hits a token whose `graduatedAt` < the warehouse watermark (strict less-than to avoid missing same-second graduates). Bootstrap (empty warehouse) paginates until cursor is null. Not windowed incremental (Option C) because there is no overlap mechanism — tokens are identified by `mint_address`, and upsert handles re-encounters naturally. |
| **PDP2** | ETL vs ELT | **A: ETL (transform before load)** | Moralis allows re-fetching at 50 CU/page — not free, but affordable for a universe of ~200K tokens across ~2,000 pages. If a conformance bug is discovered, re-fetching the affected pages is feasible within a few days of CU budget. No staging tables needed. |
| **PDP3** | Idempotency Mechanism | **A: Upsert** | Aligned with warehouse WDP5 (updates allowed for master data). MigratedCoin is a universe table, not a time series. `update_or_create()` on `mint_address`: insert if new, update metadata fields (`name`, `symbol`, `decimals`, `logo_url`) if the token already exists. This is the mechanism established in the FL-001 record (PDP3) for the universe table specifically. Not delete-write (Option B) — there is no scope to delete within; the unit of work is the individual token, not a time range. Not skip-existing (Option C) — we want metadata updates to flow through on re-runs. |
| **PDP4** | Watermark Strategy | **A: Derive from warehouse** | Query `MAX(anchor_event)` from MigratedCoin. This is the `graduatedAt` of the most recently discovered token. Steady-state stops paginating when it encounters a `graduatedAt` strictly less than this watermark. Always consistent with actual data. No drift risk. No extra table. Different from FL-001/FL-002 watermarks which are per-asset — the universe watermark is a single global value because the endpoint returns all tokens, not per-asset data. |
| **PDP5** | Rate Limit Handling | **A: Serial with CU budget tracking** | Moralis charges 50 CU per call from a shared 40,000 CU/day pool. The universe pipeline shares this pool with FL-002. CU consumption tracked via `.moralis_cu_tracker.json`. Discovery must coordinate with FL-002 — discovery runs first to ensure new tokens are available before FL-002 fetches their holders. |
| **PDP6** | Error Handling | **D: Retry with backoff, then fail** | Early-stage system — silent skipping is more dangerous than blocked runs. Moralis showed transient server errors (HTTP 200 with error body) during FL-002 exploration, and the same behavior is expected here. Connector's `_request_with_retry()` handles transient errors with exponential backoff. After max retries, the exception propagates and the run fails. The entire run is one paginated walk — partial progress is acceptable because the watermark is only updated after successful upserts, so re-running picks up where it left off. |
| **PDP7** | Reconciliation Strategy | **A: Count-based** | The graduated endpoint has no `total` field — no expected count is available from the source. Count-based reconciliation logs how many new tokens were discovered per run. Boundary check is also applied: the newest token's `graduatedAt` should be recent (within the expected polling interval, e.g. < 24 hours old). Not Option C (count + boundary) in the FL-001/FL-002 sense because there is no time range to validate against — the unit of work is "all tokens newer than the watermark," not "a fixed window." |
| **PDP8** | Provenance Tracking | **B: Row-level ingest timestamp** | `ingested_at` already exists on MigratedCoin (`auto_now_add=True`). Plus run-level logging (start/end time, tokens discovered, tokens updated, CU consumed). Same approach as FL-001/FL-002. |
| **PDP9** | Multi-Source Handling | **A: Single source (Moralis)** | Moralis graduated tokens endpoint is the only identified API that provides a structured list of pump.fun graduates with graduation timestamps. No alternative source provides equivalent data. Architecture supports adding sources later (e.g. on-chain indexing) as an additive change. |
| **PDP10** | Scheduling | **A: Manual (management commands)** | `python manage.py discover_graduates --mode steady-state` for daily runs. `python manage.py discover_graduates --mode bootstrap` for initial full load across multiple days. Discovery must run BEFORE FL-001 and FL-002 — new MigratedCoin rows must exist before feature layer pipelines can fetch data for them. Orchestrator command handles step ordering. |
| **PDP11** | Dimension Table Location | **A: Warehouse app owns all tables** | MigratedCoin already lives in the warehouse app. No new dimension tables needed for the universe pipeline — unlike FL-001 which needs PoolMapping, the universe pipeline writes directly to the existing MigratedCoin model. Pipeline app owns only code (connector, conformance function, management command). |

---

## Moralis Source Configuration

### API Connection

| Property | Value |
|---|---|
| Base URL | `https://solana-gateway.moralis.io` |
| Auth | API key required (`X-Api-Key` header) |
| Cost | **50 CU per call** (measured — see CU cost discrepancy note below) |
| Daily CU budget (free plan) | 40,000 CU = 800 calls/day (shared with FL-002 and all future Moralis endpoints) |
| Rate limit | 1,000 CU/s |
| Endpoint | `/token/{network}/exchange/{exchange}/graduated` |
| Default limit | 100 per page |
| Pagination | Cursor-based (JWT, keyset pagination on `graduatedAt` as Unix epoch) |
| Query key | Exchange name (`pumpfun`) — returns all graduated tokens for that exchange |
| Sort order | **Descending** — newest graduates first |

**CU cost discrepancy:** Moralis documentation claims 1 CU per call for the graduated tokens endpoint. Measured cost is 50 CU per call (950 CU consumed for 19 calls = 50 CU/call). This matches the FL-002 holder endpoint cost. All budget planning in this document uses the **measured value of 50 CU/call**.

### Required API Parameters for Universe Discovery

| Parameter | Value | Reasoning |
|---|---|---|
| `network` | `mainnet` | Solana mainnet |
| `exchange` | `pumpfun` | Pump.fun graduates |
| `limit` | `100` | Maximum per page — minimize calls to conserve CU |
| `cursor` | From previous page (or omit for page 1) | Keyset pagination on `graduatedAt` |

**Note:** No date filtering parameters exist on this endpoint. The endpoint returns ALL graduated tokens, paginated. The pipeline controls what it processes by paginating from newest and stopping when hitting the watermark — not by requesting a date range.

### Pagination Strategy

The endpoint returns all graduated tokens sorted by `graduatedAt` descending. Pagination uses cursor-based keyset pagination — each cursor encodes the last token's `graduatedAt` as a Unix epoch, so pages are stable even when new tokens graduate during pagination.

Estimated total graduated tokens: 150,000–215,000 (pump.fun launched ~Jan 2024). At 100 tokens per page = ~1,500–2,200 pages total. Current graduation rate: ~272 tokens/day = 2–3 pages/day of new tokens.

---

## Shared CU Budget Coordination

All Moralis endpoints draw from the same **40,000 CU/day** pool. Budget must be coordinated across pipelines.

### Daily CU budget allocation (steady-state)

| Pipeline | Endpoint | CU/call | Calls/day (est.) | Daily CU | Priority |
|---|---|---|---|---|---|
| **U-001 Discovery** | `/exchange/pumpfun/graduated` | 50 | 1–3 | 50–150 | High (runs first — new coins unblock FL pipelines) |
| **FL-002 Holders** | `/holders/{address}/historical` | 50 | varies by universe size | remainder | Medium (runs after discovery) |
| **Future endpoints** | TBD | 50 (assumed) | TBD | TBD | Low |
| **Reserve** | — | — | — | ~2,000 | Buffer for retries and ad-hoc queries |
| | | | **Total budget** | **40,000** | |

### FL-002 capacity at steady-state

With discovery using ~150 CU/day at steady-state:
- **Available for FL-002:** ~37,850 CU/day
- **FL-002 calls possible:** ~757 calls/day
- **At ~10 pages per coin (full bootstrap):** ~75 coins per day
- **At ~1–2 pages per coin (incremental):** ~378–757 coins per day

### Budget coordination rules

1. **Discovery runs first** — must complete before FL-002 to ensure new MigratedCoin rows exist for feature layer pipelines.
2. **CU tracker is shared** — both pipelines read/write `.moralis_cu_tracker.json`. The tracker records `{ date, cu_used }` and resets daily.
3. **During bootstrap** — FL-002 gets reduced allocation (see Bootstrap Strategy below).
4. **Hard stop at 38,000 CU** — leave 2,000 CU buffer for retries and error recovery.
5. **Consequence of exceeding budget** — API key is blocked until the next day. The pipeline must check remaining CU budget before starting a run and fail gracefully if insufficient budget remains.

---

## Conformance Mapping: Moralis Graduated → MigratedCoin

### Field Mapping Table

| Warehouse field | Moralis field | Transformation |
|---|---|---|
| `mint_address` | `tokenAddress` | Direct — string. Validate base58, 32–44 chars. Both `*pump` suffix and standard Solana addresses are valid (6% of tokens lack `pump` suffix). |
| `anchor_event` | `graduatedAt` | Parse ISO 8601 string (`"2026-03-10T17:22:07.000Z"`) to UTC-aware `datetime`. Strip `.000Z` milliseconds (always `000` — second-level precision). |
| `name` | `name` | Direct — string. Always present, never empty string (0/100 nulls observed). |
| `symbol` | `symbol` | Direct — string. Always present, never empty string (0/100 nulls observed). |
| `decimals` | `decimals` | `int()` cast from string. Source returns `"6"` not `6`. **NOT always 6** — observed `"0"` (Clawcoin) in exploration. |
| `logo_url` | `logo` | Direct — URL string. **Nullable** (8% null in exploration). Moralis-hosted `.webp` images. |
| `ingested_at` | Not in response | Set to `datetime.now(UTC)` at load time via `auto_now_add=True`. **Create-only** — subsequent upsert updates do NOT change this field, preserving the original discovery timestamp. |
| — | `priceUsd` | **NOT STORED** — live snapshot value, not graduation-time. Changes between calls. |
| — | `priceNative` | **NOT STORED** — live snapshot value. |
| — | `liquidity` | **NOT STORED** — live snapshot value. |
| — | `fullyDilutedValuation` | **NOT STORED** — live snapshot value. Nullable (1%). |

### Semantic Decisions

| Decision | Choice | Reasoning |
|---|---|---|
| **Timestamp interpretation** | `graduatedAt` = exact event time (not interval-based) | Unlike FL-001/FL-002 which use interval-start timestamps, `graduatedAt` is a point-in-time event — the exact second the token graduated. Maps directly to `anchor_event` with no interval convention needed. |
| **Timezone** | UTC | Source timestamps include `.000Z` suffix (UTC). No conversion needed. |
| **Identifier** | `tokenAddress` → `mint_address` (direct) | Moralis returns the Solana mint address directly. This is the warehouse's natural key for MigratedCoin. No mapping needed. |
| **Store prices?** | NO | `priceUsd`, `priceNative`, `liquidity`, `fullyDilutedValuation` are live market snapshots, not graduation-time values. They change between API calls. Storing them would create misleading data — consumers would assume they represent graduation-time values. |
| **Store metadata?** | YES (`name`, `symbol`, `decimals`, `logo_url`) | These are stable token identity fields. They do not change between calls. Useful for display, validation, and debugging. |
| **Decimals handling** | Parse as int, do not assume 6 | Schema returns string `"6"`. Exploration found `"0"`. Conformance must `int(decimals)`, not hardcode. |
| **Address filtering** | None — accept all addresses from endpoint | 6% of graduated tokens lack the `pump` suffix. All are valid pump.fun graduates. Do not filter by suffix. |
| **Upsert field policy** | `anchor_event` set on create only; metadata fields updated on re-encounter | `anchor_event` is the graduation timestamp — it is a historical fact that must not change. If the pipeline re-encounters a token, it updates `name`, `symbol`, `decimals`, `logo_url` (metadata may improve over time) but never overwrites `anchor_event`. This prevents graduation time from being silently modified by subsequent upserts. Implementation: `update_or_create(mint_address=..., defaults={metadata fields})` with `anchor_event` passed only in the create path. |

---

## MigratedCoin Model Changes

Four new fields to be added to the existing MigratedCoin model. All nullable because they are metadata, not required for backtesting or pipeline operation. Existing MigratedCoin rows (from manual testing and prior runs) will have null values until updated by the universe pipeline.

| Field | Django Field | Configuration | Reasoning |
|---|---|---|---|
| `name` | `CharField` | `max_length=200, null=True, blank=True` | Token name from Moralis. Always present in API (0/100 nulls), but nullable in model for backwards compatibility with existing rows. 200 chars covers longest observed names. |
| `symbol` | `CharField` | `max_length=50, null=True, blank=True` | Token symbol/ticker. Always present in API. 50 chars matches `mint_address` max_length. |
| `decimals` | `PositiveSmallIntegerField` | `null=True, blank=True` | SPL token decimal places. Usually 6 but observed 0 in exploration. `PositiveSmallIntegerField` (0–32767) is appropriate — SPL tokens support 0–9 decimals. |
| `logo_url` | `URLField` | `max_length=500, null=True, blank=True` | Moralis-hosted logo URL. 8% null in exploration. 500 chars accommodates Moralis's long URL format (`https://logo.moralis.io/solana-mainnet_{address}_{hash}.webp`). |

**No changes to existing fields.** `mint_address`, `anchor_event`, `membership_end`, and `ingested_at` remain unchanged.

**Upsert behavior:** When a token already exists in MigratedCoin (matched by `mint_address`), the universe pipeline updates only metadata fields: `name`, `symbol`, `decimals`, and `logo_url`. It does NOT update `anchor_event` (graduation time is a historical fact — set once on creation, never overwritten). It does NOT update `ingested_at` (managed by `auto_now_add=True`, which records the original creation time — subsequent upsert updates do not change this field, preserving the discovery timestamp). It does NOT update `membership_end` (managed separately by the observation window lifecycle).

---

## Steady-State Strategy

The "paginate newest-first, stop when hitting known token" pattern.

### Algorithm

1. **Read watermark:** Query `MAX(anchor_event)` from MigratedCoin → this is the global watermark.
2. **Fetch page 1:** Call Moralis graduated endpoint (`?limit=100`, no cursor). Returns newest graduates first.
3. **Process each token on the page:**
   - If `graduatedAt` < watermark → **stop pagination.** All remaining tokens (this page and subsequent pages) are already known.
   - If `graduatedAt` >= watermark → pass through conformance function → upsert into MigratedCoin.

   Uses strict less-than (`<`) to avoid missing tokens that graduated at the exact same second as the watermark. Multiple tokens can share a `graduatedAt` timestamp (second-level precision). The one token at the watermark boundary is re-encountered and upserted as a no-op — this is the correct tradeoff (one harmless duplicate vs a missed token).
4. **If all tokens on the page are new:** follow cursor to next page. Repeat step 3.
5. **After discovery completes:** trigger downstream processes:
   - Pool mapping population for newly discovered tokens (DexPaprika `/networks/solana/tokens/{mint_address}/pools`).
   - FL-001 and FL-002 pipelines can now fetch data for new MigratedCoin rows.
6. **Log run report:** tokens discovered (new), tokens updated (existing), pages fetched, CU consumed.

### Expected steady-state profile

| Metric | Value |
|---|---|
| Graduation rate | ~272 tokens/day |
| Pages per daily run | 2–3 (272 tokens ÷ 100 per page, plus safety margin) |
| CU per daily run | 100–150 |
| Wall time per daily run | < 5 seconds |

### Edge cases

- **Empty warehouse (first run after bootstrap completes):** Watermark returns `None` → treated as "no watermark" → same as bootstrap (paginate all pages).
- **No new tokens since last run:** First token on page 1 has `graduatedAt` < watermark → stop immediately. 1 page fetched, 50 CU consumed, 0 tokens discovered (the watermark-boundary token is re-encountered and upserted as a no-op).
- **New tokens graduated during pagination:** Keyset pagination is stable — new tokens graduating between page fetches appear on page 1 but don't shift tokens on pages the cursor already passed. They'll be picked up on the next run.

---

## Bootstrap Strategy

One-time full load of all ~150,000–215,000 graduated tokens. Exceeds daily CU budget — must be split across multiple days.

### Approach: Newest-to-oldest (natural endpoint order)

The endpoint returns newest graduates first. Bootstrap paginates in the same direction as steady-state — newest to oldest — until the cursor is null (all pages exhausted). This is the natural endpoint order and avoids any need to reverse or synthesize cursors.

Progress is tracked via the watermark: after each batch of pages, the oldest `graduatedAt` successfully upserted becomes the new watermark. If the bootstrap is interrupted (CU budget exhausted, error, etc.), the next run resumes from page 1 and quickly skips tokens already loaded until it reaches the frontier.

**Important:** Because bootstrap processes newest-first, the watermark advances backwards in time. Steady-state logic (stop when hitting watermark) does not apply during bootstrap — bootstrap paginates until cursor is null, regardless of watermark. The pipeline must distinguish between bootstrap mode (cursor exhaustion) and steady-state mode (watermark hit).

### CU budget and multi-day plan

| Metric | Value |
|---|---|
| Estimated total pages | ~1,500–2,200 |
| CU per page | 50 |
| Total CU cost | ~75,000–110,000 |
| Daily CU budget (40,000 shared) | ~30,000 allocated to bootstrap (leave 10,000 for FL-002) |
| Pages per day at 30,000 CU | ~600 |
| Days to complete bootstrap | 3–4 |
| Wall time per day | ~10–15 minutes (not the bottleneck — CU budget is) |

### Bootstrap day plan

| Day | Pages | CU used (discovery) | CU remaining for FL-002 |
|---|---|---|---|
| Day 1 | ~600 pages | ~30,000 CU | ~10,000 CU (200 FL-002 calls) |
| Day 2 | ~600 pages | ~30,000 CU | ~10,000 CU |
| Day 3 | ~600 pages | ~30,000 CU | ~10,000 CU |
| Day 4 | ~200 pages (tail) | ~10,000 CU | ~30,000 CU (FL-002 catch-up) |

### Bootstrap resumption

The bootstrap must handle interruption gracefully:

1. **Track cursor position:** Save the last cursor to a file or database so the next day's run can resume without re-fetching already-loaded pages.
2. **Alternative: rely on upsert idempotency:** Re-start from page 1 each day. Pages with already-loaded tokens result in upsert no-ops. CU is wasted on re-fetching, but the data is correct. At 50 CU/page, re-scanning 600 already-loaded pages costs 30,000 CU — this wastes an entire day. **Cursor persistence is the better approach.**
3. **Cursor file:** Save `{ date, cursor, pages_completed, cu_used }` to `.moralis_bootstrap_state.json` in the project root. Bootstrap reads this on startup and resumes from the saved cursor if present. File is deleted when bootstrap completes (cursor is null).
4. **Missing or corrupted state file:** If `.moralis_bootstrap_state.json` is missing or corrupted during a bootstrap-in-progress (i.e. bootstrap has not completed but the file cannot be read), the command exits with an error and instructions to either provide a valid state file or restart bootstrap from scratch with `--restart-bootstrap` flag. This prevents silent re-scanning of thousands of already-loaded pages. The `--restart-bootstrap` flag explicitly acknowledges the CU waste and starts pagination from page 1.

---

## Pool Mapping Integration

Pool mapping is documented in `u001_pool_mapping_pipeline_implementation_record.md`. After discovering new tokens, the pool mapping pipeline runs as a separate batch step to resolve mint addresses to Pumpswap pool addresses. FL-001 (OHLCV) depends on pool mapping; FL-002 (holders) does not (queries by mint address directly).

---

## Key Differences from FL-001 and FL-002

| Property | Universe (Moralis → MigratedCoin) | FL-001 (DexPaprika → OHLCVCandle) | FL-002 (Moralis → HolderSnapshot) |
|---|---|---|---|
| **Data type** | Master data (entity discovery) | Time series (price candles) | Time series (holder snapshots) |
| **Table category** | Universe table (updates allowed) | Feature layer (append-only) | Feature layer (append-only) |
| **Idempotency** | Upsert (PDP3-A) | Delete-write (PDP3-B) | Delete-write (PDP3-B) |
| **Watermark scope** | Global (single MAX across all tokens) | Per-asset (MAX per coin) | Per-asset (MAX per coin) |
| **Extract pattern** | Paginate from newest, stop at watermark | Windowed incremental per asset | Windowed incremental per asset |
| **Date filtering** | None — endpoint returns all tokens | `start`/`end` query params | `fromDate`/`toDate` query params |
| **Query key** | Exchange name (`pumpfun`) | Pool address (needs PoolMapping) | Mint address (direct) |
| **Auth** | API key + 50 CU/call | None | API key + 50 CU/call |
| **CU budget** | Shared with FL-002 (40,000/day) | Not applicable (DexPaprika is free) | Shared with discovery (40,000/day) |
| **Sort order** | Descending (newest first) | Ascending (oldest first) | Descending (newest first, reversed in conformance) |
| **Response structure** | Flat token objects | Flat candle objects | Nested objects (must flatten) |
| **Records per run** | 0–300 new tokens (steady-state) | 1–1000 candles per coin | 1–1000 snapshots per coin |
| **Reconciliation** | Count of new tokens + freshness check | Count + boundary per coin | Count + boundary per coin |
| **Downstream trigger** | Pool mapping + FL pipelines | None | None |

---

## Open Items

| Item | Status | Impact |
|---|---|---|
| MigratedCoin model migration | ✅ Done | Migration 0006 created. `name`, `symbol`, `decimals`, `logo_url` fields added. |
| Bootstrap cursor persistence | ✅ Done | `.moralis_bootstrap_state.json` implemented in `discover_graduates.py`. Saves `{ date, cursor, pages_completed, cu_used }`. |
| DEX destination (Pumpswap vs Raydium) | Unknown from this endpoint | Moralis graduated endpoint does not indicate which DEX the token graduated to. If distinction matters for filtering, need a secondary source (on-chain data or another API). Currently not blocking — all graduates are included. |
| CU cost verification at scale | Measured at 50 CU/call (19 calls) | Should re-verify during bootstrap when making hundreds of calls. If cost differs at scale, budget plan needs updating. |
| Bootstrap/steady-state mode detection | ✅ Done | `--mode` flag on `discover_graduates` command: `bootstrap` (paginate until cursor null) or `steady-state` (paginate until watermark hit). Presence of `.moralis_bootstrap_state.json` tracks bootstrap progress. |
| Conformance function | ✅ Done | `pipeline/conformance/u001_universe_moralis.py`: `conform_moralis_graduated(raw_tokens)`. Strict — raises on missing fields. Tested with fixture. |
| Daily scheduling order | ✅ Done | Orchestrator command (`python manage.py orchestrate`) handles step ordering via topological sort. DAG: discovery → pool mapping → FL-001; discovery → FL-002. |
