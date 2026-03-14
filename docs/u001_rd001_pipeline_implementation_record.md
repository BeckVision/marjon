# Pipeline Implementation Record: U-001 / RD-001

**Reference Table:** RD-001 — Raw Transaction Data
**Dataset:** U-001 — Graduated Pump.fun Tokens — Early Lifecycle
**Source:** Shyft API (Transaction History endpoint)
**Reference:** pipeline_implementation_guide.md (for decision point definitions and option details)
**Reference:** u001_data_specification.md (for data contract: universe, reference tables, access patterns, PIT rules, quality constraints)
**Reference:** u001_rd001_api_exploration_findings.md (for verified API behavior and conformance requirements)

---

## Decision Selections

Each row references a decision point (DP) from the Pipeline Implementation Guide.

| DP | Decision | Selected Option | Reasoning |
|---|---|---|---|
| **PDP1** | Extract Strategy | **C: Windowed incremental** | Paginate backward from newest via `before_tx_signature`, stop when `tx.timestamp < watermark - overlap`. Overlap: 5 minutes safety margin. Bootstrap (no watermark) fetches the full observation window. No server-side time filtering (confirmed in Session A) — connector must paginate backward and check timestamps client-side. |
| **PDP2** | ETL vs ELT | **A: ETL (transform before load)** | Shyft allows free historical re-fetching — no daily CU limit observed. Observation window is only 3.47 days. If a conformance bug is discovered, re-fetching is cheap. No staging tables needed. |
| **PDP3** | Idempotency Mechanism | **B: Delete-write** | Reference table — delete-write within `transaction.atomic()`: delete all RawTransaction rows for coin in [start, end], then bulk_create new ones. Transactions are immutable on-chain, so re-fetching the same range produces identical data. Upsert would require per-row `update_or_create` which is slow for thousands of trades. |
| **PDP4** | Watermark Strategy | **A: Derive from warehouse** | Query `MAX(timestamp)` per coin from RawTransaction table. Always consistent with actual data, no drift. Same approach as FL-001/FL-002. |
| **PDP5** | Rate Limit Handling | **B: Concurrent with key rotation** | 3 Shyft API keys available (`SHYFT_API_KEY`, `SHYFT_API_KEY_2`, `SHYFT_API_KEY_4`). Each key rate-limited at 1 req/sec. Key rotation via `itertools.cycle` + `threading.Lock` — same pattern as FL-001 gateway rotation. Effective throughput: ~3 req/sec. Configurable via `settings.SHYFT_API_KEYS` list. Falls back to single key if only one configured. |
| **PDP6** | Error Handling | **D: Retry with backoff, then fail** | Early-stage system — silent skipping is more dangerous than blocked runs. Shyft returns HTTP 200 with `success: false` on errors (same pattern as Moralis). Connector must validate response body (`success == true`), not just HTTP status. `_request_with_retry()` handles transient errors with exponential backoff. |
| **PDP7** | Reconciliation Strategy | **Count informational** | Trade count is informational only — volume varies wildly per coin (11 trades/hr for dead coins, 800 trades/hr for active). No expected count unlike FL-002's dense intervals. Log: coin, time range, transactions loaded, API calls made. The pattern across many coins reveals systemic issues (e.g., all coins returning 0 trades = API outage). |
| **PDP8** | Provenance Tracking | **B: Row-level ingest timestamp** | `ingested_at` field on RawTransaction (`auto_now_add=True`). Plus run-level logging via `U001PipelineRun` with `layer_id='RD-001'`. Same as FL-001/FL-002. |
| **PDP9** | Multi-Source Handling | **A: Single source (Shyft)** | Shyft is the primary source. Helius reserved as future secondary — would require a new connector (`helius.py`) and conformance (`rd001_helius.py`) but same loader and model. Adding Helius is out of scope for v1.0. |
| **PDP10** | Scheduling | **A: Manual (management commands)** | `python manage.py fetch_transactions` for individual runs. `python manage.py orchestrate --steps raw_transactions` for batch runs. Same code path for bootstrap, steady-state, and refill — only parameters differ. |
| **PDP11** | Dimension Table Location | **A: Warehouse app owns all tables** | RawTransaction lives in warehouse app. Pipeline app owns connector, conformance, loader, and management command code. Pool mapping dimension table (already in warehouse) is used for query key resolution. |

---

## Shyft Source Configuration

### API Connection

| Property | Value |
|---|---|
| Base URL | `https://api.shyft.to/sol/v1` |
| Auth | `x-api-key` header |
| Rate limit | 1 req/sec per key |
| Daily limit | None observed |
| Endpoint | `/transaction/history` |
| Max per page | 100 |
| Pagination | Cursor-based (`before_tx_signature`) |
| Query key | Pool address (from PoolMapping dimension table) |
| Keys available | 3 (`SHYFT_API_KEY`, `SHYFT_API_KEY_2`, `SHYFT_API_KEY_4`) |

### Required API Parameters for RD-001

| Parameter | Value | Reasoning |
|---|---|---|
| `network` | `mainnet-beta` | Solana mainnet |
| `account` | Pool address (from PoolMapping) | Query by pool_address for cleaner results — only swap activity, minimal noise. Adds PoolMapping dependency. |
| `tx_num` | `100` | Maximum per page to minimize API calls |
| `enable_events` | `true` | Required — BuyEvent/SellEvent in events array is the primary data extraction source |
| `enable_raw` | `false` | Raw transaction bytes not needed for v1.0 (would be needed for priority fee breakdown in v1.1) |
| `before_tx_signature` | Last tx's `signatures[0]` | Pagination cursor — set after each page |

### Pagination Strategy

Shyft returns results newest-first (descending timestamp). The connector paginates backward using `before_tx_signature`. Stop conditions:

1. `len(result) < tx_num` — last page reached
2. `result[-1].timestamp < start` — passed the time bound (filter out-of-range transactions from last page)

A typical token has 5,000–10,000 trades in the 3.47-day observation window = 50–100 API calls. Active tokens (~66,000 trades) require ~660 calls.

### Key Rotation (Rate Limit Throughput)

3 Shyft API keys rotate via `itertools.cycle` in the connector, protected by `threading.Lock` for concurrent workers. Each key is rate-limited at 1 req/sec — with 3 keys, effective throughput is ~3 req/sec.

| # | Key | Source |
|---|---|---|
| 1 | `SHYFT_API_KEY` | `.env` (primary) |
| 2 | `SHYFT_API_KEY_2` | `.env` |
| 3 | `SHYFT_API_KEY_4` | `.env` |

**Wiring:** `marjon/settings.py` reads `SHYFT_API_KEY`, `SHYFT_API_KEY_2`, `SHYFT_API_KEY_4` from `.env` into a `SHYFT_API_KEYS` list. The connector uses `itertools.cycle(settings.SHYFT_API_KEYS)` to round-robin across keys. `time.sleep(1.0)` after each call per key. If only one key is configured, falls back to single-key serial operation.

### Rate Limit Budget

| Scenario | Calls per coin (avg) | Time per coin (3 keys) | Coins per hour |
|---|---|---|---|
| Full bootstrap (avg token) | 50–100 | 17–33 sec | 110–210 |
| Full bootstrap (active token) | 660 | ~3.5 min | ~17 |
| Steady-state increment | 1–5 | <2 sec | 1800+ |

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

From Session A volume measurements across active, mid, and dead tokens:

| Token Activity | Trades in Window | API Calls (100/page) | Time (1 key) | Time (3 keys) |
|---|---|---|---|---|
| Active (~800 trades/hr) | ~66,000 | ~660 | ~11 min | ~3.5 min |
| Mid (~56 trades/hr) | ~4,700 | ~47 | ~47 sec | ~16 sec |
| Dead (~11 trades/hr) | ~900 | ~9 | ~9 sec | ~3 sec |

**Full universe (5,113 tokens):**

| Metric | Estimate |
|---|---|
| Total API calls | ~25,000–50,000 |
| Single key (1 req/sec) | ~7–14 hours |
| Three keys (3 req/sec) | ~2–5 hours |
| Feasibility | Feasible — run overnight with 3-key rotation |

**Bootstrap strategy:**
1. Start with coins that already have FL-001 data (pipeline resources already invested)
2. Process via orchestrator: `python manage.py orchestrate --steps raw_transactions --workers 3`
3. Each worker uses a different API key via key rotation
4. Skip coins without PoolMapping (depends_on=pool_mapping)

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

| Property | FL-001 (GeckoTerminal) | FL-002 (Moralis) | RD-001 (Shyft) |
|---|---|---|---|
| **Table category** | Feature layer (time grid) | Feature layer (time grid) | Reference table (event facts) |
| **Time structure** | Fixed 5-min intervals | Fixed 5-min intervals | Irregular (one row per swap event) |
| **Auth** | None (gateway rotation for IPs) | API key, 50 CU/call | API key, 1 req/sec/key |
| **Daily limit** | None (IP rotation) | 40,000 CU (800 calls) | None observed |
| **Query key** | Pool address (PoolMapping) | Mint address (direct) | Pool address (PoolMapping) |
| **Pagination** | `before_timestamp` | Cursor (opaque) | `before_tx_signature` |
| **Time filtering** | Server-side (`before_timestamp`) | Server-side (`fromDate`/`toDate`) | None — client-side only |
| **Rows per coin** | ~1,000 candles | ~1,000 snapshots | 900–66,000 trades |
| **Gap behavior** | Sparse (no candle = no trades) | Dense (every interval) | N/A (no fixed interval) |
| **Reconciliation** | Count informational (sparse normal) | Count strict (dense expected) | Count informational (volume varies) |
| **Unique key** | (coin, timestamp) | (coin, timestamp) | (coin, tx_signature) |
| **Data service access** | `get_panel_slice()` — auto-joined | `get_panel_slice()` — auto-joined | `get_reference_data()` — on-demand |
| **Concurrency** | 6 workers × 6 gateway IPs | Serial (CU budget) | 3 workers × 3 API keys |
| **Amount storage** | Decimal (converted, USD) | Integer (direct) | Integer (raw on-chain units) |

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

---

## Open Items

| Item | Status | Impact |
|---|---|---|
| Priority fee breakdown | Deferred to v1.1 | Requires `enable_raw=true` + ComputeBudgetInstruction parsing. Top-level `tx_fee` captures combined base+priority for now. |
| Jito tip detection | Deferred to v1.1 | Requires scanning inner instructions for transfers to known Jito tip addresses. Medium complexity. |
| `price_usd` derived feature | Deferred to DF-001 | Requires SOL/USD price series in warehouse. When added: `price_usd = price_sol × sol_usd_rate`. |
| Helius as secondary source | Deferred (PDP9) | New connector + conformance, same loader. Fallback chain pattern (like pool mapping's DexScreener → GeckoTerminal). |
| Multi-hop swap edge cases | Monitor | Jupiter-routed trades through Pumpswap pools emit BuyEvent/SellEvent correctly. If edge cases emerge (missing events, duplicate events), conformance logging will surface them. |
