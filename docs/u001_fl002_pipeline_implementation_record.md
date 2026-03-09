# Pipeline Implementation Record: U-001 / FL-002

**Feature Layer:** FL-002 — Holder Snapshots
**Dataset:** U-001 — Graduated Pump.fun Tokens — Early Lifecycle
**Source:** Moralis API (Historical Token Holders endpoint)
**Reference:** pipeline_implementation_guide.md (for decision point definitions and option details)
**Reference:** u001_data_specification.md (for data contract)
**Reference:** u001_fl002_api_exploration_findings.md (for verified API behavior and conformance requirements)

---

## Decision Selections

Each row references a decision point (DP) from the Pipeline Implementation Guide.

| DP | Decision | Selected Option | Reasoning |
|---|---|---|---|
| **PDP1** | Extract Strategy | **C: Windowed incremental** | Moralis returns every interval (no gaps), so the overlap window is less critical for catching missed data. However, windowed incremental is still valuable for robustness against transient API errors and for consistency with FL-001. The CU budget is tight (800 calls/day on free plan), making full load wasteful after initial bootstrap. |
| **PDP2** | ETL vs ELT | **A: ETL (transform before load)** | Moralis allows historical re-fetching with `fromDate`/`toDate` parameters. If a conformance bug is discovered, re-fetching is possible within CU budget. No staging tables needed. |
| **PDP3** | Idempotency Mechanism | **HolderSnapshot (feature layer): B (Delete-write)** | HolderSnapshot is a feature layer — append-only per warehouse WDP5. Delete-write is self-correcting on re-run. Same reasoning as FL-001 feature layers. Universe table (MigratedCoin) is shared with FL-001 — upsert, already established. |
| **PDP4** | Watermark Strategy | **A: Derive from warehouse** | Query `MAX(timestamp)` per asset from HolderSnapshot table. Same approach as FL-001 — always consistent with actual data, no drift, no extra table. |
| **PDP5** | Rate Limit Handling | **C: Queue with rate limiter (Celery)** | Moralis free plan: 40,000 CU/day, 1,000 CU/s. At 50 CU per call = 800 calls/day. Celery rate limiter must track **CU consumption**, not just call count. Must not exceed daily CU cap or keys are blocked until next day. Rate limiting is more critical here than FL-001 because the budget is tighter (800 calls/day vs DexPaprika's 10,000/day) and exceeding it has an immediate consequence (key suspension). |
| **PDP6** | Error Handling | **D: Retry with backoff, then fail** | Same reasoning as FL-001 — early-stage system, persistent failures should surface immediately. Moralis showed transient server errors during API exploration (HTTP 200 with error body), confirming that retry logic is essential. Celery's `self.retry(countdown=...)` handles these. |
| **PDP7** | Reconciliation Strategy | **C: Count + boundary** | Critical difference from FL-001: Moralis returns **every interval**, even when no holder change occurred. A missing interval in FL-002 is genuinely suspicious — unlike FL-001 where sparsity is normal (coin died, no trades). Reconciliation for FL-002 should treat missing intervals as a warning, not just informational. Count check: expected = `(end - start) / 5 minutes`, actual should match exactly (minus possible boundary edge cases). Boundary check: first snapshot at/near T0, last at end of fetched range. |
| **PDP8** | Provenance Tracking | **B: Row-level ingest timestamp** | `ingested_at` already added to warehouse models (shared decision from FL-001). Plus run-level logging. |
| **PDP9** | Multi-Source Handling | **A: Single source (Moralis)** | Moralis is the only identified source for holder distribution data with size tiers and acquisition method breakdowns. No alternative API provides equivalent data. |
| **PDP10** | Scheduling | **B + A: Scheduled + Manual** | Celery beat for scheduled runs, manual for backfills. Must coordinate CU budget between scheduled runs and manual backfills — both consume from the same 40,000 CU/day pool. |
| **PDP11** | Dimension Table Location | **A: Warehouse app owns all tables** | All database tables live in the warehouse app — paradigm models (HolderSnapshot, MigratedCoin) and dimension tables (pool mapping, run logs). Pipeline app owns only code (Celery tasks, conformance functions, source connectors). FL-002 doesn't need a pool mapping table — Moralis queries by mint address directly. |

---

## Moralis Source Configuration

### API Connection

| Property | Value |
|---|---|
| Base URL | `https://solana-gateway.moralis.io` |
| Auth | API key required (`X-Api-Key` header) |
| Cost | 50 CU per call |
| Daily CU budget (free plan) | 40,000 CU = 800 calls/day |
| Rate limit | 1,000 CU/s |
| Endpoint | `/token/mainnet/holders/{address}/historical` |
| TimeFrame | `5min` |
| Default limit | 100 per page |
| Pagination | Cursor-based (opaque string) |
| Query key | Token address (mint address — no pool mapping needed) |

### Required API Parameters for FL-002

| Parameter | Value | Reasoning |
|---|---|---|
| `network` | `mainnet` | Solana mainnet |
| `address` | Mint address from MigratedCoin | Direct query by warehouse identifier — no mapping needed |
| `timeFrame` | `5min` | Matches FL-002 temporal resolution |
| `fromDate` | Watermark timestamp (or T0 for bootstrap) | Windowed incremental — start from where we left off |
| `toDate` | Current time (or T0+5000min if observation window closed) | Fetch up to now or end of window |
| `limit` | `100` (default) | Default page size. Pagination via cursor handles larger ranges. |

### Pagination Strategy

A full 5000-minute observation window at 5-minute resolution = 1000 snapshots per coin (Moralis returns every interval). At 100 records per page = 10 API calls per coin for full bootstrap. At 50 CU per call = 500 CU per coin for bootstrap.

Daily budget: 40,000 CU = 80 coins per day for full backfill. Steady-state incremental runs (fetching a few hours of new data) require 1-2 calls per coin = 400-800 coins per day.

### CU Budget Management

| Scenario | Calls per coin | CU per coin | Coins per day |
|---|---|---|---|
| Full bootstrap | 10 | 500 | 80 |
| Steady-state (few hours) | 1-2 | 50-100 | 400-800 |
| Mixed (50 bootstrap + steady-state) | — | ~25,000 + remaining | Varies |

**Important:** CU budget is shared across all Moralis API usage in the project, not just FL-002. If other endpoints consume CUs, the FL-002 budget shrinks. The pipeline must track cumulative CU consumption per day.

**Consequence of exceeding budget:** API key is blocked until the next day. The pipeline must check remaining CU budget before starting a run and fail gracefully if insufficient budget remains.

---

## Conformance Mapping: Moralis → FL-002

### Field Mapping Table

**Note:** Warehouse field names are preliminary. The data spec describes the feature set but exact Django model field names will be finalized during implementation.

| Warehouse field | Moralis field | Transformation |
|---|---|---|
| `timestamp` | `timestamp` | Parse ISO 8601 string (`"2025-03-08T12:00:00.000Z"`) to UTC datetime. Remove milliseconds. |
| `total_holders` | `totalHolders` | Direct — integer. camelCase → snake_case. |
| `net_holder_change` | `netHolderChange` | Direct — integer. camelCase → snake_case. |
| `holder_percent_change` | `holderPercentChange` | Cast to Decimal. camelCase → snake_case. |
| `acquired_via_swap` | `newHoldersByAcquisition.swap` | Nested field extraction. Integer. |
| `acquired_via_transfer` | `newHoldersByAcquisition.transfer` | Nested field extraction. Integer. |
| `acquired_via_airdrop` | `newHoldersByAcquisition.airdrop` | Nested field extraction. Integer. |
| `holders_in_whales` | `holdersIn.whales` | Nested field extraction. Integer. |
| `holders_in_sharks` | `holdersIn.sharks` | Nested field extraction. Integer. |
| `holders_in_dolphins` | `holdersIn.dolphins` | Nested field extraction. Integer. |
| `holders_in_fish` | `holdersIn.fish` | Nested field extraction. Integer. |
| `holders_in_octopus` | `holdersIn.octopus` | Nested field extraction. Integer. |
| `holders_in_crabs` | `holdersIn.crabs` | Nested field extraction. Integer. |
| `holders_in_shrimps` | `holdersIn.shrimps` | Nested field extraction. Integer. |
| `holders_out_whales` | `holdersOut.whales` | Nested field extraction. Integer. |
| `holders_out_sharks` | `holdersOut.sharks` | Nested field extraction. Integer. |
| `holders_out_dolphins` | `holdersOut.dolphins` | Nested field extraction. Integer. |
| `holders_out_fish` | `holdersOut.fish` | Nested field extraction. Integer. |
| `holders_out_octopus` | `holdersOut.octopus` | Nested field extraction. Integer. |
| `holders_out_crabs` | `holdersOut.crabs` | Nested field extraction. Integer. |
| `holders_out_shrimps` | `holdersOut.shrimps` | Nested field extraction. Integer. |
| `coin` (FK) | `address` (from request path) | The mint address used in the API call — same as warehouse identifier. |
| `ingested_at` | Not in response | Set to `datetime.now(UTC)` at load time. |

### Semantic Decisions

| Decision | Choice | Reasoning |
|---|---|---|
| **Timestamp interpretation** | Assumed interval start — NOT verified by Moralis docs | Moralis docs do not explicitly state whether timestamp represents interval start or end. Assumed interval-start to match warehouse WDP9 convention. Needs verification by cross-referencing holder changes against known on-chain events. |
| **Timezone** | UTC | Source timestamps include `.000Z` suffix (UTC). No conversion needed. |
| **Identifier** | Mint address directly | Moralis queries by token address, which is the warehouse's mint_address. No pool mapping needed. |
| **Field naming** | camelCase → snake_case | Moralis uses camelCase (`totalHolders`). Warehouse uses snake_case (`total_holders`). Conformance handles the conversion. |
| **Nested field extraction** | Flatten nested objects into individual columns | Moralis returns `holdersIn: { whales: 5, sharks: 12, ... }`. Warehouse stores as flat columns: `holders_in_whales`, `holders_in_sharks`, etc. |
| **Sort order** | Reverse on conformance | Moralis returns descending (newest first). Warehouse convention and load logic expect chronological order. Conformance reverses the array. |

---

## Key Differences from FL-001

| Property | FL-001 (DexPaprika → OHLCVCandle) | FL-002 (Moralis → HolderSnapshot) |
|---|---|---|
| **Auth** | None | API key + CU cost |
| **Daily budget** | 10,000 requests | 800 calls (40,000 CU ÷ 50 CU/call) |
| **Query key** | Pool address (needs pool mapping table) | Mint address (direct, no mapping) |
| **Pagination** | `start`/`end` range, max 366 per call | Cursor-based, 100 per page |
| **Gap behavior** | No candle if no trades (sparse) | Every interval present (dense, zeros when inactive) |
| **Reconciliation** | Sparsity is normal | Missing intervals are suspicious |
| **Sort order** | Ascending (oldest first) | Descending (newest first) — must reverse |
| **Response structure** | Flat fields | Nested objects (must flatten) |
| **Dimension tables needed** | Pool mapping table | None |
| **Budget consequence** | No published consequence | Key blocked until next day |

---

## Open Items

| Item | Status | Impact |
|---|---|---|
| Moralis timestamp convention | Not verified | Docs don't state if timestamp is interval-start or interval-end. Assumed interval-start. Cross-reference against on-chain events needed. |
| FL-002 warehouse field names | Preliminary | Data spec says "holders_in/out by size tier" without exact column names. Mapping uses `holders_in_whales` etc. Finalize during model implementation. |
| CU consumption tracking | Not designed | Pipeline needs mechanism to track daily CU usage and fail gracefully when budget is insufficient. Defined during implementation. |
| Moralis API key management | Not designed | API key storage, rotation, and security. Defined during implementation. |
