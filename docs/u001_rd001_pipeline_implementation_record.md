# Pipeline Implementation Record: U-001 / RD-001

**Reference Table:** RD-001 — Raw Transaction Data
**Dataset:** U-001 — Graduated Pump.fun Tokens — Early Lifecycle
**Source:** Shyft API (primary, recent coins) + Helius API (secondary, historical backfill)
**Reference:** pipeline_implementation_guide.md (for decision point definitions and option details)
**Reference:** u001_data_specification.md (for data contract: universe, reference tables, access patterns, PIT rules, quality constraints)
**Reference:** u001_rd001_api_exploration_findings.md (for verified API behavior and conformance requirements)
**Reference:** u001_rd001_v2_api_exploration_findings.md (for two-phase connector evaluation: getSignaturesForAddress + parse_selected)
**Reference:** u001_rd001_helius_api_exploration_findings.md (for Helius secondary source: historical backfill, tokenTransfers-based conformance)

---

## Decision Selections

Each row references a decision point (DP) from the Pipeline Implementation Guide.

| DP | Decision | Selected Option | Reasoning |
|---|---|---|---|
| **PDP1** | Extract Strategy | **C: Windowed incremental** | Paginate backward from newest via `before_tx_signature`, stop when `tx.timestamp < watermark - overlap`. Overlap: 5 minutes safety margin. Bootstrap (no watermark) fetches the full observation window. No server-side time filtering (confirmed in Session A) — connector must paginate backward and check timestamps client-side. |
| **PDP2** | ETL vs ELT | **A: ETL (transform before load)** | Shyft allows free historical re-fetching — no daily CU limit observed. Observation window is only 3.47 days. If a conformance bug is discovered, re-fetching is cheap. No staging tables needed. |
| **PDP3** | Idempotency Mechanism | **B: Delete-write** | Reference table — delete-write within `transaction.atomic()`: delete all RawTransaction rows for coin in [start, end], then bulk_create new ones. Transactions are immutable on-chain, so re-fetching the same range produces identical data. Upsert would require per-row `update_or_create` which is slow for thousands of trades. |
| **PDP4** | Watermark Strategy | **A: Derive from warehouse** | Query `MAX(timestamp)` per coin from RawTransaction table. Always consistent with actual data, no drift. Same approach as FL-001/FL-002. |
| **PDP5** | Rate Limit Handling | **B: Concurrent with key rotation** | 4 Shyft API keys available (`SHYFT_API_KEY` through `SHYFT_API_KEY_4`). REST endpoints: 1 req/sec/key. RPC endpoint (`rpc.shyft.to`): no rate limit observed (tested 20 rapid calls, 3.3 req/sec throughput limited only by latency). Batch RPC supported — up to 250 calls per HTTP request tested. Key rotation via `itertools.cycle` + `threading.Lock`. Effective REST throughput: ~4 req/sec. Two-phase connector: Phase 1 (RPC, unlimited) → Phase 2 (REST, rate-limited). |
| **PDP6** | Error Handling | **D: Retry with backoff, then fail** | Early-stage system — silent skipping is more dangerous than blocked runs. Shyft returns HTTP 200 with `success: false` on errors (same pattern as Moralis). Connector must validate response body (`success == true`), not just HTTP status. `_request_with_retry()` handles transient errors with exponential backoff. |
| **PDP7** | Reconciliation Strategy | **Count informational** | Trade count is informational only — volume varies wildly per coin (11 trades/hr for dead coins, 800 trades/hr for active). No expected count unlike FL-002's dense intervals. Log: coin, time range, transactions loaded, API calls made. The pattern across many coins reveals systemic issues (e.g., all coins returning 0 trades = API outage). |
| **PDP8** | Provenance Tracking | **B: Row-level ingest timestamp** | `ingested_at` field on RawTransaction (`auto_now_add=True`). Plus run-level logging via `U001PipelineRun` with `layer_id='RD-001'`. Same as FL-001/FL-002. |
| **PDP9** | Multi-Source Handling | **B: Multi-source (Shyft primary, Helius secondary)** | Shyft is the primary source for recent coins (within 3-4 day retention). Helius is the secondary source for historical backfill (coins whose observation windows closed >4 days ago). Each source has its own connector (`shyft.py`, `helius.py`) and conformance (`rd001_shyft.py`, `rd001_helius.py`). Same loader and model — both produce identical RawTransaction records. Source selection: orchestrator checks coin age and routes to appropriate source. |
| **PDP10** | Scheduling | **A: Manual (management commands)** | `python manage.py fetch_transactions` for individual runs. `python manage.py orchestrate --steps raw_transactions` for batch runs. Same code path for bootstrap, steady-state, and refill — only parameters differ. |
| **PDP11** | Dimension Table Location | **A: Warehouse app owns all tables** | RawTransaction lives in warehouse app. Pipeline app owns connector, conformance, loader, and management command code. Pool mapping dimension table (already in warehouse) is used for query key resolution. |

---

## Shyft Source Configuration

### Two-Phase Connector Architecture

**Phase 1: Signature Discovery (Solana RPC)**

| Property | Value |
|---|---|
| Endpoint | `POST https://rpc.shyft.to?api_key=<key>` |
| Method | `getSignaturesForAddress` (JSON-RPC 2.0) |
| Max per call | 1000 signatures |
| Rate limit | None observed (latency-bound at ~3.3 req/sec) |
| Batch RPC | Yes — up to 250+ calls per HTTP request |
| Pagination | `before` cursor (exclusive, descending blockTime) |
| Incremental | `until` cursor (exclusive) — returns only sigs newer than boundary |
| Query key | Pool address (from PoolMapping dimension table) |
| Response | `{signature, blockTime, slot, err, memo, confirmationStatus}` |

**Phase 2: Transaction Parsing (Shyft REST)**

| Property | Value |
|---|---|
| Endpoint | `POST https://api.shyft.to/sol/v1/transaction/parse_selected` |
| Auth | `x-api-key` header |
| Rate limit | 1 req/sec per key |
| Daily limit | None observed |
| Max per call | 100 signatures (hard limit — 101+ returns "Validation failed!") |
| Response format | Identical to `/transaction/history` |
| Data retention | **3-4 days** ([Shyft docs](https://docs.shyft.to/solana-apis/transactions/transaction-apis)) — applies to both RPC and REST endpoints |

### Required Parameters

**Phase 1 (RPC):**

```json
{
  "jsonrpc": "2.0", "id": 1,
  "method": "getSignaturesForAddress",
  "params": ["<pool_address>", {"limit": 1000}]
}
```

Optional params: `before` (pagination cursor), `until` (incremental boundary).

**Phase 2 (REST):**

```json
{
  "network": "mainnet-beta",
  "transaction_signatures": ["sig1", "sig2", ...],
  "enable_events": true,
  "enable_raw": false
}
```

### Connector Flow

```
1. Phase 1: getSignaturesForAddress(pool_address, limit=1000)
   → Paginate with 'before' cursor until all sigs collected
   → For incremental: use 'until' = last processed signature
   → Pre-filter: drop sigs where blockTime outside [start, end]
   → Pre-filter: drop sigs where err != null (failed, no events)

2. Phase 2: parse_selected(filtered_sigs, batch_size=100)
   → POST batches of 100 signatures to REST endpoint
   → Key rotation across batches (round-robin 4 keys)
   → Response is identical to /transaction/history

3. Conformance: unchanged
   → Same conform() function works on parse_selected output
   → Same field mapping, same event extraction logic
```

### Pagination Strategy

**Phase 1:** getSignaturesForAddress returns signatures newest-first (descending blockTime). Paginate with `before = result[-1].signature`. Stop when `len(result) < limit` or `blockTime < start`.

For incremental updates: set `until = last_processed_signature` to only discover new signatures. This is more efficient than the old approach (paginate past all known data).

**Phase 2:** No pagination needed — submit exact signatures in batches of 100.

### Pre-filtering (Between Phase 1 and Phase 2)

Phase 1 returns `{signature, blockTime, err}` for each sig. This enables free filtering before expensive Phase 2 calls:

| Filter | Condition | Savings |
|---|---|---|
| Observation window | `blockTime < anchor_event` or `blockTime > window_end` | Varies by coin — low for pool_address queries (~0% noise) |
| Failed transactions | `err != null` | ~0.5% of sigs (5 per 1000) |
| Already processed | `blockTime <= watermark` (steady-state) | Eliminates all old sigs |

### Key Rotation (Rate Limit Throughput)

4 Shyft API keys rotate via `itertools.cycle` in the connector, protected by `threading.Lock`.

| # | Key | Source |
|---|---|---|
| 1 | `SHYFT_API_KEY` | `.env` (primary) |
| 2 | `SHYFT_API_KEY_2` | `.env` |
| 3 | `SHYFT_API_KEY_3` | `.env` |
| 4 | `SHYFT_API_KEY_4` | `.env` |

**Wiring:** `marjon/settings.py` reads all `SHYFT_API_KEY*` from `.env` into `SHYFT_API_KEYS` list. Phase 1 uses single key (RPC, no rate limit). Phase 2 rotates across all keys. `time.sleep(1.0)` between REST calls per key.

### Rate Limit Budget

| Scenario | Phase 1 (RPC) | Phase 2 (REST) | Total Time (4 keys) |
|---|---|---|---|
| Avg token (~297 sigs) | 1 call, ~0.3s | 3 calls, ~9s | ~9s |
| Active token (~5000 sigs) | 5 calls, ~1.5s | 50 calls, ~38s | ~39s |
| Dead token (~45 sigs) | 1 call, ~0.3s | 1 call, ~3s | ~3s |
| Steady-state increment | 1 call, ~0.3s | 0–1 calls | <4s |

### Comparison with Old Approach (/transaction/history)

| Property | Old (single-phase) | New (two-phase) |
|---|---|---|
| Discovery speed | 100 txs/call | 1000 sigs/call + batch RPC |
| Parse speed | 100 txs/call, ~1s | 100 sigs/call, ~3s |
| Rate limit | 1 req/sec/key (REST) | Phase 1: unlimited. Phase 2: 1 req/sec/key |
| Incremental | Paginate past known data | `until` cursor — skip known data |
| Pre-filtering | None (parse everything) | Filter by blockTime + err before parse |
| Conformance | Same | Same (identical response format) |
| Bootstrap (full universe) | ~92 min (4 keys) | ~249 min (4 keys) |
| Steady-state (per coin) | ~3-5s | ~3-4s |

**Tradeoff:** New approach is ~2.7x slower for full bootstrap but has architectural advantages for steady-state operations (incremental `until` cursor, pre-filtering, decoupled discovery).

**Data retention constraint:** Shyft keeps ~3-4 days of transaction history. Pipeline must process coins within ~4 days of graduation. Older coins fall through to Helius.

---

## Helius Source Configuration (Secondary — Historical Backfill)

### Purpose

Fill the historical gap for coins whose observation windows closed >4 days ago (beyond Shyft's retention). Helius provides full historical access since genesis.

### API Connection

| Property | Value |
|---|---|
| Enhanced TX endpoint | `GET https://api-mainnet.helius-rpc.com/v0/addresses/{address}/transactions` |
| Parse endpoint | `POST https://api-mainnet.helius-rpc.com/v0/transactions` |
| RPC endpoint | `POST https://mainnet.helius-rpc.com/?api-key=<key>` |
| Auth | API key in query string (`?api-key=<key>`) |
| Rate limit | 2 req/sec Enhanced APIs, 10 req/sec RPC (free tier) |
| Credits | 100 per enhanced tx call, 10 per `getSignaturesForAddress` |
| Free tier | 1,000,000 credits/month |
| Max per call | 100 transactions (Enhanced), 1000 signatures (RPC) |
| Data retention | **Full history since genesis** |

### Keys Available

| # | Key | Source |
|---|---|---|
| 1 | `HELIUS_API_KEY` | `.env` |
| 2 | `HELIUS_API_KEY_2` | `.env` |
| 3 | `HELIUS_API_KEY_3` | `.env` |
| 4 | `HELIUS_API_KEY_4` | `.env` |

### Connector Flow

```
1. Phase 1: getSignaturesForAddress via Helius RPC (10 credits/call)
   → Same as Shyft Phase 1 but with full historical depth
   → Paginate with 'before' cursor until anchor_event reached
   → Pre-filter: drop sigs where blockTime outside [anchor, window_end]
   → Pre-filter: drop sigs where err != null (6-23% of sigs are failed)

2. Phase 2: GET /v0/addresses/{pool}/transactions (100 credits/call)
   → Server-side time filtering via gte-time/lte-time (observation window)
   → OR: POST /v0/transactions with specific signatures
   → Response: EnhancedTransaction format (different from Shyft)

3. Conformance: rd001_helius.py (different from Shyft conformance)
   → Extract trade data from tokenTransfers (not events)
   → Wrapped SOL transfers encode fee breakdown
```

### Server-Side Time Filtering

Unlike Shyft, Helius supports filtering by timestamp directly:

| Parameter | Type | Description |
|---|---|---|
| `gte-time` | Unix timestamp | Only txs with blockTime >= this value |
| `lte-time` | Unix timestamp | Only txs with blockTime <= this value |

This eliminates client-side pagination waste — query directly for the observation window.

### Rate Limit Budget

| Tier | Enhanced APIs | RPC | Credits/month | Cost |
|---|---|---|---|---|
| Free | 2 req/sec | 10 req/sec | 1,000,000 | $0 |
| Developer | 10 req/sec | 50 req/sec | 10,000,000 | $49/mo |

Historical backfill estimate for ~3,770 coins with closed windows:

| Phase | Calls | Credits/call | Total credits |
|---|---|---|---|
| Phase 1: getSignaturesForAddress | ~18,850 | 10 | ~188,500 |
| Phase 2: Enhanced TX parsing | ~113,100 | 100 | ~11,310,000 |

Phase 2 exceeds free tier. Options: spread across months, use Developer tier ($49/mo for 10M credits), or buy additional credits ($5 per 1M).

---

## Conformance Mapping: Helius → RD-001

### Extraction Strategy: tokenTransfers (not events)

Helius `events.swap` is empty for ~80% of PUMP_AMM trades. Instead, `tokenTransfers` contains **wrapped SOL** (`So11111111111111111111111111111111111111112`) transfers that encode the full fee breakdown. Verified by comparing same transactions across both APIs.

### Field Mapping Table

| Warehouse Field | Helius Source | Transformation |
|---|---|---|
| `tx_signature` | `signature` | `str`, direct |
| `timestamp` | `timestamp` | Unix epoch (int) → UTC-aware datetime |
| `trade_type` | Token flow direction vs pool | BUY: non-wSOL token FROM pool. SELL: non-wSOL token TO pool. |
| `wallet_address` | `feePayer` | `str`, direct |
| `token_amount` | `tokenTransfers` (non-wSOL, pool involved) | `float × 10^decimals` → int. Use `accountData.tokenBalanceChanges.rawTokenAmount` for exact integers when available. |
| `sol_amount` | `tokenTransfers` (wSOL to/from pool) | `float × 10^9` → int (lamports). BUY: wSOL_to_pool = quote_amount_in_with_lp_fee. SELL: sum all wSOL from pool = quote_amount_out - lp_fee. |
| `pool_address` | Request context | Passed by caller — same as Shyft |
| `tx_fee` | `fee` | Direct — already in lamports (int). Convert to Decimal SOL: `Decimal(fee) / 10^9`. |
| `lp_fee` | Computed from known basis points (2 bps) | BUY: `wSOL_to_pool × 2 / 10002`. SELL: `gross_sol × 2 / 10000` where gross = visible_sum × 10000/9998. |
| `protocol_fee` | `tokenTransfers` wSOL to non-pool, non-trader address | Identify by amount pattern: larger of the two fee transfers (~93 bps). Exact match verified against Shyft. |
| `coin_creator_fee` | `tokenTransfers` wSOL to non-pool, non-trader address | Identify by amount pattern: smaller of the two fee transfers (~30 bps). Exact match verified against Shyft. |
| `pool_token_reserves` | **NULL** | Not available from tokenTransfers. Exists in inner instruction event data (Anchor log) but requires custom decoding. Accept NULL for Helius-sourced records. |
| `pool_sol_reserves` | **NULL** | Same — accept NULL. Can be backfilled later if Anchor IDL decoding is implemented. |
| `coin_id` (FK) | Not in response | Passed by caller — mint_address resolved from PoolMapping |
| `ingested_at` | Not in response | Model's `auto_now_add=True` |

### BUY Detection (verified)

```
tokenTransfers where:
  mint != wSOL AND fromUserAccount == pool_address  →  token going TO trader = BUY

Amounts:
  token_amount = tokenTransfers[non-wSOL, from pool].tokenAmount × 10^decimals
  sol_amount   = tokenTransfers[wSOL, to pool].tokenAmount × 10^9  (includes lp_fee)
  protocol_fee = tokenTransfers[wSOL, to non-pool non-trader].amount (larger)
  creator_fee  = tokenTransfers[wSOL, to non-pool non-trader].amount (smaller)
```

### SELL Detection (verified)

```
tokenTransfers where:
  mint != wSOL AND toUserAccount == pool_address  →  token going FROM trader = SELL

Amounts:
  token_amount = tokenTransfers[non-wSOL, to pool].tokenAmount × 10^decimals
  sol_amount   = sum(tokenTransfers[wSOL, from pool].amount) × 10^9  (net, excludes lp_fee)
  protocol_fee = tokenTransfers[wSOL, from pool, to non-trader].amount (larger)
  creator_fee  = tokenTransfers[wSOL, from pool, to non-trader].amount (smaller)
```

### Semantic Differences from Shyft Conformance

| Property | Shyft (`rd001_shyft.py`) | Helius (`rd001_helius.py`) |
|---|---|---|
| Trade detection | BuyEvent/SellEvent in `events` array | Token flow direction in `tokenTransfers` |
| Fee extraction | Direct from event data fields | Derived from wSOL transfer pattern |
| LP fee | Direct: `events.data.lp_fee` | Computed: basis points × sol_amount |
| Pool reserves | Direct: `events.data.pool_*_reserves` | **NULL** (not in tokenTransfers) |
| Token amount precision | Integer from event data | Float × 10^decimals (or accountData raw) |
| tx_fee format | Float SOL (e.g., `0.000005`) | Integer lamports (e.g., `5000`) |
| Timestamp format | ISO 8601 string | Unix epoch integer |
| Failed tx handling | `status != "Success"` → skip | `transactionError != null` → skip |
| All sources | Events present for all routers | tokenTransfers present for all routers |

### Data Quality for Old Transactions

Tested with 10-day-old coin (observation window [Mar 5 – Mar 8]):

| Metric | Value |
|---|---|
| Total txs (5 pages, server-side filtered) | 500 |
| type=SWAP | 499/500 |
| source=PUMP_AMM | 490/500 |
| Has tokenTransfers | **500/500 (100%)** |
| Has nativeTransfers | 487/500 |

**tokenTransfers are present on 100% of old transactions.** The extraction strategy works regardless of transaction age.

---

## Source Selection Logic

### Decision Tree

```
For each coin needing RD-001 data:

1. Check coin age:
   age = now - anchor_event

2. If age < 3 days (window open, within Shyft retention):
   → Use Shyft (primary): full 13 fields, BuyEvent/SellEvent

3. If 3 days <= age < 7 days (window closing/just closed):
   → Try Shyft first, fall back to Helius if Shyft returns no in-window data

4. If age >= 7 days (window closed, beyond Shyft retention):
   → Use Helius (secondary): 11 fields (pool reserves = NULL)
```

### Same Loader, Same Model

Both sources produce records conforming to the RawTransaction model. The loader (`pipeline/loaders/rd001.py`) is source-agnostic — it receives `(parsed_records, skipped_records)` from either conformance function. No loader changes needed.

Pool reserves being NULL for Helius-sourced records is safe because:
- `pool_token_reserves` and `pool_sol_reserves` have CHECK constraints `>= 0`
- NULL passes CHECK constraints (NULL is not < 0)
- Data service consumers must handle NULLs regardless (standard for optional fields)

---

## Feature Set (13 Fields)

All amounts stored as raw on-chain integers. Token amounts are in raw SPL units (divide by `10^decimals` for human-readable). SOL amounts are in lamports (divide by `10^9`). The only exception is `tx_fee` which Shyft returns as a float SOL value.

| # | Field | Django Type | Description |
|---|---|---|---|
| 1 | `tx_signature` | CharField(max_length=128) | Unique on-chain transaction signature |
| 2 | `timestamp` | DateTimeField | UTC datetime of the transaction (inherited from ReferenceTableBase) |
| 3 | `trade_type` | CharField(max_length=4, choices) | `'BUY'` or `'SELL'` — derived from event name |
| 4 | `wallet_address` | CharField(max_length=64) | Trader's wallet address |
| 5 | `token_amount` | BigIntegerField | Tokens traded — raw SPL units |
| 6 | `sol_amount` | BigIntegerField | Gross SOL traded — lamports |
| 7 | `pool_address` | CharField(max_length=64) | Pumpswap pool address |
| 8 | `tx_fee` | DecimalField(38,18) | Solana network fee (base + priority) — SOL, as returned by API |
| 9 | `lp_fee` | BigIntegerField | LP fee — lamports |
| 10 | `protocol_fee` | BigIntegerField | Protocol fee — lamports |
| 11 | `coin_creator_fee` | BigIntegerField | Coin creator fee — lamports |
| 12 | `pool_token_reserves` | BigIntegerField | Pool's token reserves post-trade — raw SPL units |
| 13 | `pool_sol_reserves` | BigIntegerField | Pool's SOL reserves post-trade — lamports |

**Inherited/existing fields** (not counted above):
- `coin` (FK to MigratedCoin) — resolved from pool_address via PoolMapping
- `ingested_at` (DateTimeField, auto_now_add=True)

**Amount interpretation guide:**
- Token human-readable: `token_amount / 10^coin.decimals` (most pump.fun tokens: decimals=6)
- SOL human-readable: `sol_amount / 10^9`
- Price (SOL/token): `(sol_amount / 10^9) / (token_amount / 10^coin.decimals)`
- Total AMM fee (lamports): `lp_fee + protocol_fee + coin_creator_fee`

**Fields deferred to v1.1:**
- Priority fee breakdown (base fee vs priority fee) — requires `enable_raw=true`
- Jito tips — requires scanning inner instructions for transfers to Jito tip addresses
- `price_usd` — derived feature (DF-001) joining RD-001 with a SOL/USD price series

---

## Trade Detection Scope

**Scope: BuyEvent/SellEvent from Pump.fun AMM events only.**

Every trade on a Pumpswap pool emits either a `BuyEvent` or `SellEvent` in the transaction's `events` array, regardless of the top-level `type` field. This is the single extraction path.

**Included:**
- `BuyEvent` — emitted on every buy, whether direct or Jupiter-routed
- `SellEvent` — emitted on every sell

**Excluded:**
- Top-level `type` field — unreliable (90% of trades show `TOKEN_TRANSFER`, not `SWAP`)
- Top-level `actions` — unreliable (`UNKNOWN` type for most trades)
- `SwapsEvent` / `SwapEvent` — Jupiter aggregator routing events, redundant with BuyEvent/SellEvent and less detailed (no fee breakdown, no reserves)
- Layer 3 balance change fallback — not needed when events are reliable on 100% of pool-address trades

**Filtering logic:**
1. Connector fetches all transactions for the pool address — no pre-filtering
2. Conformance iterates transactions, finds first `BuyEvent` or `SellEvent` in `events` array
3. Transactions with BuyEvent/SellEvent and `status == "Success"` → parsed into RawTransaction records
4. All other transactions → captured in SkippedTransaction with full JSON blob and skip reason (see Skipped Transaction Capture below)

**Multi-event handling:** A transaction may contain both `BuyEvent` and `SwapsEvent` (Jupiter-routed trades). Conformance takes only the `BuyEvent`/`SellEvent` — one row per transaction. No multi-action splitting needed because Pumpswap pools emit exactly one BuyEvent or SellEvent per swap.

---

## Conformance Mapping: Shyft → RD-001

### Field Mapping Table

| Warehouse Field | Source JSON Path (BuyEvent) | Source JSON Path (SellEvent) | Transformation |
|---|---|---|---|
| `tx_signature` | `result[i].signatures[0]` | same | `str`, direct |
| `timestamp` | `result[i].timestamp` | same | Parse ISO 8601 (`"2026-03-14T15:28:44.000Z"`) → UTC-aware datetime |
| `trade_type` | — | — | Map event name: `BuyEvent` → `'BUY'`, `SellEvent` → `'SELL'` |
| `wallet_address` | `events[j].data.user` | same | `str`, direct |
| `token_amount` | `events[j].data.base_amount_out` | `events[j].data.base_amount_in` | `int`, direct — raw SPL units |
| `sol_amount` | `events[j].data.quote_amount_in` | `events[j].data.quote_amount_out` | `int`, direct — lamports (gross, before fees) |
| `pool_address` | `events[j].data.pool` | same | `str`, direct |
| `tx_fee` | `result[i].fee` | same | `Decimal(str(float))` — SOL as returned by API |
| `lp_fee` | `events[j].data.lp_fee` | same | `int`, direct — lamports |
| `protocol_fee` | `events[j].data.protocol_fee` | same | `int`, direct — lamports |
| `coin_creator_fee` | `events[j].data.coin_creator_fee` | same | `int`, direct — lamports |
| `pool_token_reserves` | `events[j].data.pool_base_token_reserves` | same | `int`, direct — raw SPL units |
| `pool_sol_reserves` | `events[j].data.pool_quote_token_reserves` | same | `int`, direct — lamports |
| `coin_id` (FK) | Not in response | same | Passed by caller — mint_address resolved from PoolMapping before API call |
| `ingested_at` | Not in response | same | Model's `auto_now_add=True` |

### Semantic Decisions

| Decision | Choice | Reasoning |
|---|---|---|
| **Timestamp source** | Top-level `result[i].timestamp` (ISO) | More reliable than `events[j].data.timestamp` (Unix epoch). Both represent the same block time. ISO is easier to parse and less error-prone. |
| **Amount storage** | Raw on-chain integers | Preserves full precision. No conversion loss. Consumers derive human-readable values using `MigratedCoin.decimals`. SOL always 10^9. |
| **SOL amount semantics** | Gross (before AMM fees deducted) | `quote_amount_in` (BUY) and `quote_amount_out` (SELL) are the gross amounts flowing through the pool. Net amounts (`user_quote_amount_in`, `user_quote_amount_out`) are derivable: `sol_amount - lp_fee - protocol_fee - coin_creator_fee`. |
| **Event selection** | First BuyEvent or SellEvent in events array | Pumpswap pools emit exactly one per swap. If multiple are found (not observed), take the first and log a warning. |
| **Identifier resolution** | Caller resolves mint_address before calling connector | Flow: orchestrator has mint_address → looks up pool_address from PoolMapping → connector fetches by pool_address → conformance receives mint_address as parameter → sets `coin_id`. |
| **Non-trade transactions** | Captured in SkippedTransaction | Transactions without BuyEvent/SellEvent are non-trade pool activity (e.g., pool creation, account resizing). Stored with full JSON for future research. |
| **Failed transactions** | Captured in SkippedTransaction | Transactions with `status != "Success"` stored with full JSON and `skip_reason='failed'`. Not parsed into RawTransaction — failed swaps didn't execute on-chain. |

---

## Unique Constraint

```
UniqueConstraint(fields=["coin", "tx_signature"], name="rd001_unique_tx_per_coin")
```

**Reasoning:** Transaction signatures are globally unique on Solana. However, a single transaction could involve two tokens in our universe (e.g., swap between two graduated tokens). The compound key `(coin, tx_signature)` allows the same transaction to appear under different coins. Aligns with WDP1 (surrogate PK + unique_together).

---

## Skipped Transaction Capture

Transactions that the conformance function cannot parse into RawTransaction records are stored in a separate `SkippedTransaction` table with their full JSON blob. This preserves unparsed data for future research — discovering new transaction patterns, debugging edge cases, or expanding trade detection scope in later versions.

### SkippedTransaction Model

| Field | Django Type | Description |
|---|---|---|
| `tx_signature` | CharField(max_length=128) | On-chain transaction signature |
| `timestamp` | DateTimeField | Transaction time (UTC) |
| `coin` | FK(MigratedCoin) | Known from pipeline context (the coin being fetched) |
| `pool_address` | CharField(max_length=64) | Pool address that was queried |
| `tx_type` | CharField(max_length=64) | Top-level `type` from Shyft (e.g., `TOKEN_TRANSFER`, `SWAP`, `CREATE_TOKEN_ACCOUNT`) |
| `tx_status` | CharField(max_length=32) | Transaction status (`Success`, `Failed`, etc.) |
| `skip_reason` | CharField(max_length=32, choices) | Why it was skipped: `no_trade_event`, `failed`, `parse_error` |
| `raw_json` | JSONField | Full Shyft transaction JSON blob (~1-2KB per tx) |
| `ingested_at` | DateTimeField | auto_now_add=True |

### Skip Reasons

| Reason | When applied |
|---|---|
| `no_trade_event` | Transaction has `status == "Success"` but no BuyEvent/SellEvent in events array. Non-trade pool activity (pool creation, account resizing, etc.). |
| `failed` | Transaction has `status != "Success"`. Reverted or timed-out swap attempt. |
| `parse_error` | BuyEvent/SellEvent found but conformance raised an exception extracting fields. Indicates unexpected data shape — should be investigated. |

### Volume Estimate

Session A showed pool_address queries return mostly swap activity. Expected ~5-10% non-trade transactions per coin — a few hundred per coin, not thousands. Storage cost is modest: ~1-2KB JSON per transaction × a few hundred per coin × 5,113 coins ≈ 1-5 GB total for full universe.

### Pipeline Integration

- Connector passes ALL transactions to conformance (no pre-filtering)
- Conformance returns two lists: `(parsed_records, skipped_records)`
- Loader writes both within the same `transaction.atomic()` block: RawTransaction (delete-write) + SkippedTransaction (delete-write, same time range)
- Idempotency: delete-write scoped by (coin, time range) — same as RawTransaction

### Unique Constraint

```
UniqueConstraint(fields=["coin", "tx_signature"], name="rd001_skipped_unique_tx_per_coin")
```

Same reasoning as RawTransaction — compound key allows the same tx_signature to appear under different coins if a transaction involves multiple tokens.

---

## Data Quality Constraints

CHECK constraints at database level (Shelf 1 — gate check). Named with `rd001_` prefix.

| Constraint Name | Condition | Severity | Reasoning |
|---|---|---|---|
| `rd001_token_amount_positive` | `token_amount > 0` | Hard reject | Every trade must move tokens. Zero tokens = malformed event. |
| `rd001_sol_amount_non_negative` | `sol_amount >= 0` | Hard reject | SOL amount should be non-negative. Gross amount can theoretically be 0 for dust trades — use `>= 0` not `> 0`. |
| `rd001_trade_type_valid` | `trade_type IN ('BUY', 'SELL')` | Hard reject | Only two valid trade types from BuyEvent/SellEvent. |
| `rd001_pool_token_reserves_non_negative` | `pool_token_reserves >= 0` | Hard reject | Pool reserves cannot be negative. |
| `rd001_pool_sol_reserves_non_negative` | `pool_sol_reserves >= 0` | Hard reject | Pool reserves cannot be negative. |
| `rd001_lp_fee_non_negative` | `lp_fee >= 0` | Hard reject | Fees cannot be negative. |
| `rd001_protocol_fee_non_negative` | `protocol_fee >= 0` | Hard reject | Fees cannot be negative. |
| `rd001_coin_creator_fee_non_negative` | `coin_creator_fee >= 0` | Hard reject | Fees cannot be negative. |

No `clean()` validations needed — all constraints are expressible as CHECK constraints.

---

## Indexes

| Index | Fields | Reasoning |
|---|---|---|
| Primary access pattern | `(coin, timestamp)` | `get_reference_data(asset, start, end)` — the primary query: all trades for coin X between T1 and T2. |
| Dedup/lookup | `(tx_signature,)` | Fast signature-based lookups. Unique constraint on `(coin, tx_signature)` also creates an implicit index, but a standalone signature index supports cross-coin lookups. |

Inherited from `ReferenceTableBase`: `(timestamp,)` — general time-range queries.

---

## Bootstrap Volume Estimate

### Two-Phase Volume Estimates (from v2 exploration)

Sample of 20 random coins: avg 297 sigs/coin, range [0–1000].
Universe: 5,113 coins, 4,936 with pool mappings.

**Phase 1 — Signature Discovery (batch RPC):**

| Metric | Estimate |
|---|---|
| Total pools | 4,936 |
| Batch size | 10 pools per RPC request |
| RPC calls | ~494 |
| Latency per batch | ~0.5–1.4s |
| **Phase 1 total** | **~4 minutes** |

**Phase 2 — Transaction Parsing (REST):**

| Metric | Estimate |
|---|---|
| Total signatures | ~1,467,000 (297 avg × 4,936 pools) |
| REST calls (100/batch) | ~14,673 |
| Latency per call | ~3s |
| With 4 keys concurrent | ~1 call/sec effective |
| **Phase 2 total** | **~4 hours** |

**Comparison with old single-phase approach:**

| Approach | API Calls | Effective Throughput | Estimated Time |
|---|---|---|---|
| Old (/transaction/history, 4 keys) | ~14,808 | ~2.7 req/sec | ~92 min |
| New (two-phase, 4 keys) | ~494 RPC + ~14,673 REST | ~1 req/sec (REST bottleneck) | ~249 min |

**Bootstrap strategy (two-source):**
1. **Recent coins (age < 4 days) → Shyft:** Process via orchestrator with 4 Shyft keys. Full 13 fields.
2. **Historical coins (age > 4 days) → Helius:** Separate backfill command. 11 fields (pool reserves = NULL). Budget: ~11M credits.
3. **Priority:** Process recent coins first (before they age out of Shyft's retention), then backfill historical via Helius.
4. Skip coins without PoolMapping (depends_on=pool_mapping)
5. Shyft Phase 1 (batch RPC) completes recent universe discovery in ~4 minutes before Phase 2 begins

---

## Windowed Incremental Overlap

**Overlap window: 5 minutes.**

For steady-state incremental runs, the connector fetches from `watermark - 5 minutes` to capture any transactions near the boundary that may have been missed. Delete-write handles duplicates from the overlap — rows in the overlap range are deleted and re-inserted.

5 minutes is sufficient because:
- Solana block time is ~400ms — transactions finalize quickly
- Shyft indexes transactions promptly (observed latency < 1 minute)
- Larger overlap wastes API calls due to no server-side time filtering (must paginate past the overlap)

---

## Query Key Resolution

RD-001 queries by **pool_address** (not mint_address). This adds a PoolMapping dependency.

```
mint_address (warehouse identifier)
    → pool_address (from PoolMapping dimension table)
        → Shyft transaction/history API call
            → canonical records with mint_address FK
```

**Dependency:** `depends_on='pool_mapping'` in orchestrator step config. Tokens without PoolMapping are skipped with a warning.

**Consequence:** Pool mapping coverage determines RD-001 coverage. If a token has no pool mapping, its transactions cannot be fetched. Pool mapping pipeline should run before RD-001.

---

## Key Differences from FL-001 / FL-002

| Property | FL-001 (GeckoTerminal) | FL-002 (Moralis) | RD-001 Shyft (primary) | RD-001 Helius (secondary) |
|---|---|---|---|---|
| **Table category** | Feature layer (time grid) | Feature layer (time grid) | Reference table (event facts) | Same table (RawTransaction) |
| **Time structure** | Fixed 5-min intervals | Fixed 5-min intervals | Irregular (one row per swap) | Same |
| **Auth** | None (gateway rotation) | API key, 50 CU/call | API key, 1 req/sec/key | API key, 100 credits/call |
| **Daily limit** | None (IP rotation) | 40,000 CU (800 calls) | None observed | 1M credits/month (free) |
| **Data retention** | 6 months | Unknown | **3-4 days** | **Full history (genesis)** |
| **Query key** | Pool address | Mint address | Pool address | Pool address |
| **Pagination** | `before_timestamp` | Cursor (opaque) | `before` signature | `before-signature` |
| **Time filtering** | Server-side | Server-side | None — client-side | Server-side (`gte-time`/`lte-time`) |
| **Trade detection** | N/A | N/A | BuyEvent/SellEvent in events | Token flow in tokenTransfers |
| **Fee breakdown** | N/A | N/A | Direct from event data | Derived from wSOL transfers |
| **Pool reserves** | N/A | N/A | Direct from event data | **NULL** (not available) |
| **Fields available** | 5 | 20 | **13/13** | **11/13** |
| **Concurrency** | 6 workers × 6 IPs | Serial (CU budget) | 4 workers × 4 keys | 4 keys, 2 req/sec |
| **Amount storage** | Decimal (USD) | Integer (direct) | Integer (raw on-chain) | Float → Integer |

---

## Data Specification Changes

Gaps or clarifications discovered during Session A/B that require updates to the data specification.

| Change | Spec affected | Detail |
|---|---|---|
| **Update RD-001 from planned to v1.0** | u001_data_specification.md, RD-001 | Fill in feature set (13 fields), data source (Shyft), refresh policy (Daily), version (1.0). Remove all TBD markers. |
| **Add raw storage note to RD-001** | u001_data_specification.md, RD-001 | Amounts stored as raw on-chain integers (lamports for SOL, raw SPL units for tokens). Price derivable from amounts + `MigratedCoin.decimals`. |
| **Add PoolMapping dependency** | u001_data_specification.md, RD-001 | RD-001 queries by pool_address, requiring PoolMapping. Tokens without pool mapping cannot have transactions fetched. |
| **Add SHYFT_API_KEYS to settings** | marjon/settings.py | List of Shyft API keys loaded from `.env`. Follows gateway URL pattern. |
| **Add SkippedTransaction model** | warehouse/models.py | New model for unparsed/filtered transactions. Full JSON blob storage for future research. Not a paradigm table — operational/diagnostic. |
| **Add HELIUS_API_KEYS to settings** | marjon/settings.py | List of Helius API keys loaded from `.env`. Same pattern as `SHYFT_API_KEYS`. |
| **Update RD-001 data source** | u001_data_specification.md, RD-001 | Data source: "Shyft (primary) + Helius (secondary)". Note: Helius-sourced records have NULL pool reserves. |
| **Allow NULL pool reserves** | warehouse/models.py | Make `pool_token_reserves` and `pool_sol_reserves` nullable (`null=True`) for Helius-sourced records. CHECK constraints `>= 0` already pass NULL. |

---

## Open Items

| Item | Status | Impact |
|---|---|---|
| Priority fee breakdown | Deferred to v1.1 | Requires `enable_raw=true` + ComputeBudgetInstruction parsing. Top-level `tx_fee` captures combined base+priority for now. |
| Jito tip detection | Deferred to v1.1 | Requires scanning inner instructions for transfers to known Jito tip addresses. Medium complexity. |
| `price_usd` derived feature | Deferred to DF-001 | Requires SOL/USD price series in warehouse. When added: `price_usd = price_sol × sol_usd_rate`. |
| Helius connector implementation | Next | New connector (`helius.py`) + conformance (`rd001_helius.py`). Same loader. Extraction from `tokenTransfers`, not events. See Helius Source Configuration above. |
| Helius credit budget | Decision needed | Historical backfill of ~3,770 coins needs ~11M credits. Free tier provides 1M/month. Options: Developer plan ($49/mo), phased backfill, or additional credit purchase ($5/1M). |
| Pool reserves from Helius | Deferred | Helius-sourced records have NULL for `pool_token_reserves`/`pool_sol_reserves`. Could decode from PUMP_AMM inner instruction event data (Anchor log) if needed. Low priority — pool reserves are supplementary. |
| `HELIUS_API_KEYS` settings | Implementation | Add to `marjon/settings.py`, matching `SHYFT_API_KEYS` pattern. |
| Multi-hop swap edge cases | Monitor | Jupiter-routed trades through Pumpswap pools emit BuyEvent/SellEvent (Shyft) and have tokenTransfers (Helius) correctly. If edge cases emerge, conformance logging will surface them. |
| New event types | Monitor | `CloseUserVolumeAccumulatorEvent` and `TradeEvent` observed alongside BuyEvent/SellEvent. Current Shyft conformance ignores them correctly. |
| Pool reserves NULL handling | Implementation | Update CHECK constraints to allow NULL for `pool_token_reserves` and `pool_sol_reserves` (currently `>= 0` which passes NULL). Verify data service handles NULL gracefully. |
