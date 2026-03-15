# API Exploration Findings: RD-001 Helius (Historical Gap Fill)

**Date:** 2026-03-15
**Purpose:** Explore Helius Enhanced Transactions API as a secondary source for RD-001 historical data that Shyft cannot provide (Shyft's 3-4 day retention limit). Determine if Helius can fill the observation window for coins whose windows closed >4 days ago.
**Test pools:** 5 coins across different ages: ~1d, ~3d, ~8d (window closed 5d), ~11d (window closed 8d), ~15d (oldest in universe, window closed 12d).

---

## Helius Enhanced Transactions API

### Connection Details

| Property | Value |
|---|---|
| Base URL | `https://api-mainnet.helius-rpc.com` |
| Auth | API key in query string (`?api-key=<key>`) |
| Rate limit | 2 req/sec Enhanced APIs (free tier), 10 req/sec RPC |
| Credits | 100 per enhanced tx call, 10 per `getSignaturesForAddress` |
| Free tier | 1,000,000 credits/month |
| Data retention | **Full history since genesis** ([Helius docs](https://www.helius.dev/historical-data): "Solana's entire history since genesis") |

### API Keys Available

| # | Key | Source |
|---|---|---|
| 1 | `HELIUS_API_KEY` | `.env` |
| 2 | `HELIUS_API_KEY_2` | `.env` |
| 3 | `HELIUS_API_KEY_3` | `.env` |
| 4 | `HELIUS_API_KEY_4` | `.env` |

---

## GET /v0/addresses/{address}/transactions

### Request Parameters (from [Helius docs](https://www.helius.dev/docs/api-reference/enhanced-transactions/gettransactionsbyaddress))

| Parameter | Required | Type | Description |
|---|---|---|---|
| `address` | Yes (path) | string | Solana address to query |
| `api-key` | Yes (query) | string | Helius API key |
| `limit` | No | number (1–100) | Transactions per page |
| `before-signature` | No | string | Pagination cursor — fetch txs before this sig |
| `after-signature` | No | string | Fetch txs after this sig |
| `sort-order` | No | string | `desc` (default) or `asc` |
| `gt-time` | No | number | Only txs with blockTime > this Unix timestamp |
| `gte-time` | No | number | Only txs with blockTime >= this Unix timestamp |
| `lt-time` | No | number | Only txs with blockTime < this Unix timestamp |
| `lte-time` | No | number | Only txs with blockTime <= this Unix timestamp |
| `source` | No | string | Filter by TransactionSource (PUMP_AMM, JUPITER, etc.) |
| `type` | No | string | Filter by TransactionType (SWAP, TRANSFER, etc.) |
| `commitment` | No | string | `finalized` (default) or `confirmed` |

**Server-side time filtering available** via `gt-time`/`lt-time`. Unlike Shyft which has no server-side filtering.

### Raw Response

```json
[
  {
    "description": "",
    "type": "SWAP",
    "source": "PUMP_AMM",
    "fee": 1005000,
    "feePayer": "B95BuZugo1kRtsd5wXGRjxXmqVsp6xKaT7M4YYHjaajH",
    "signature": "Y4BmgTsF5WBYxSF7hYAyyEfY1jqAtvRQSLR97FGk...",
    "slot": 406549012,
    "timestamp": 1773573692,
    "tokenTransfers": [
      {
        "fromTokenAccount": "8bqDphKLLNtRJaZhEWE1LJhfiyKbBnL627vNiHnbq8Qf",
        "toTokenAccount": "2BXMabsEYxCCbw2v9hm8LjyWmx7u6t7mrYNSMEWyaPSt",
        "fromUserAccount": "B95BuZugo1kRtsd5wXGRjxXmqVsp6xKaT7M4YYHjaajH",
        "toUserAccount": "5rUSFxPuMcGVCBFkfD6NbA39iMeRSrnZkNW2JXAZ8Vt6",
        "tokenAmount": 83333333.333333,
        "mint": "46HrKBBJSHaPUEAADfSNG989zrzvYmRCbGHgLm6Cipump",
        "tokenStandard": "Fungible"
      }
    ],
    "nativeTransfers": [
      {
        "fromUserAccount": "B95BuZugo1kRtsd5wXGRjxXmqVsp6xKaT7M4YYHjaajH",
        "toUserAccount": "DDdk7mbivprXpm9rxSKqfPVBoTDpqQdNRCS9SZXwyhoB",
        "amount": 1000000
      }
    ],
    "accountData": [...],
    "transactionError": null,
    "instructions": [...],
    "events": {}
  }
]
```

### Response Schema (from [Helius docs](https://www.helius.dev/docs/api-reference/enhanced-transactions/gettransactions))

| Field | Type | Description |
|---|---|---|
| `description` | string | Human-readable interpretation |
| `type` | TransactionType | Classification (SWAP, TRANSFER, UNKNOWN, etc.) |
| `source` | TransactionSource | Platform/program (PUMP_AMM, JUPITER, DFLOW, etc.) |
| `signature` | string | Transaction signature |
| `slot` | integer | Solana slot number |
| `timestamp` | integer | Unix timestamp (seconds) |
| `fee` | integer | Network fee in lamports |
| `feePayer` | string | Fee payer address |
| `nativeTransfers` | array | SOL transfers: `{fromUserAccount, toUserAccount, amount}` |
| `tokenTransfers` | array | Token transfers: `{fromTokenAccount, toTokenAccount, fromUserAccount, toUserAccount, tokenAmount, mint, tokenStandard}` |
| `accountData` | array | Account balance changes |
| `transactionError` | object\|null | Error details if failed |
| `instructions` | array | Decoded instructions |
| `events` | object | Parsed events (`swap`, `nft`, `compressed`, `setAuthority`) |
| `lighthouseData` | object | Internal Helius data |

### Key Observations

1. **Full historical access confirmed.** Fetched transactions back to 15 days (oldest coin in universe). Helius returned data for every page — no truncation. Shyft stops at ~3.8 days for the same pool.

2. **`type` is reliable for Pump.fun swaps.** All 2500 transactions across 5 coins were classified as `SWAP` by Helius. Unlike Shyft where 90% show `TOKEN_TRANSFER`.

3. **`source` identifies the routing program.** Distribution across test coins:

    | Source | Frequency | Description |
    |---|---|---|
    | `PUMP_AMM` | ~50-70% | Direct Pumpswap trades |
    | `JUPITER` | ~10-30% | Jupiter-routed trades |
    | `DFLOW` | ~7-10% | DFlow order flow |
    | `OKX_DEX_ROUTER` | ~7-9% | OKX DEX aggregator |
    | `RAYDIUM` | <1% | Raydium-routed |
    | `PUMP_FUN` | <1% | Legacy pump.fun |
    | `TITAN` | <1% | Titan DEX |

4. **`events.swap` is MOSTLY EMPTY for PUMP_AMM.** Only 13-30% of transactions have `events.swap` populated. Almost exclusively Jupiter-routed trades have swap events. Direct PUMP_AMM transactions have `events: {}`.

    | Coin age | Has events.swap | Total |
    |---|---|---|
    | ~1d | 66 (13%) | 500 |
    | ~3d | 51 (10%) | 500 |
    | ~8d | 102 (20%) | 500 |
    | ~11d | 107 (21%) | 500 |
    | ~15d | 152 (30%) | 500 |

5. **`nativeTransfers` and `tokenTransfers` are ALWAYS present.** Every transaction has these arrays populated with SOL amounts, token amounts, mint addresses, and from/to accounts. These are the primary data source for Helius.

6. **Old transactions parse with reduced detail.** For the 11-day-old coin, POST `/v0/transactions` returned `type: "UNKNOWN"` for all 100 oldest in-window transactions (vs `type: "SWAP"` for recent ones). `events.swap` was empty on all of them. Basic transfer data (`nativeTransfers`, `tokenTransfers`) was still present.

7. **Server-side time filtering via `gt-time`/`lt-time`.** Unlike Shyft, Helius supports filtering by Unix timestamp directly in the query. No need for client-side pagination past unwanted data.

8. **Pagination uses `before-signature` cursor.** Same concept as Shyft but with additional `after-signature` for forward pagination and sort order control.

---

## POST /v0/transactions

### Request Parameters (from [Helius docs](https://www.helius.dev/docs/api-reference/enhanced-transactions/gettransactions))

| Parameter | Required | Type | Description |
|---|---|---|---|
| `transactions` | Yes | array of strings | Transaction signatures (max 100) |
| `commitment` | No | string | `finalized` (default) or `confirmed` |

**Auth:** API key in query string: `POST /v0/transactions?api-key=<key>`

### Key Observations

1. **Max 100 signatures per call.** Same limit as Shyft's `parse_selected`.

2. **Old signature parsing works** — but with degraded quality:

    | Coin window closed | Type classification | events.swap present |
    |---|---|---|
    | 5d ago (sigs ~8d old) | 93% SWAP | 2/100 (2%) |
    | 8d ago (sigs ~11d old) | 100% UNKNOWN | 0/100 (0%) |
    | 12d ago (sigs ~14d old) | 99% SWAP | 6/100 (6%) |

3. **Response format identical to GET endpoint.** Same EnhancedTransaction schema.

---

## Helius RPC getSignaturesForAddress

### Connection Details

| Property | Value |
|---|---|
| Endpoint | `POST https://mainnet.helius-rpc.com/?api-key=<key>` |
| Protocol | JSON-RPC 2.0 (standard Solana RPC) |
| Max limit | 1000 per call |
| Credit cost | 10 per call |
| Rate limit | 10 req/sec (free tier) |

### Historical Depth vs Shyft

| Coin | Anchor | Helius oldest sig | Shyft oldest sig |
|---|---|---|---|
| ~3d ago | Mar 12 | 2.4d (still paginating) | Within window |
| ~8d ago | Mar 7 | **8.0d (reached window)** | 3.8d max |
| ~11d ago | Mar 4 | **10.5d (in window)** | 0 in-window sigs |
| ~15d ago (oldest) | Feb 27 | **12.0d (still paginating toward window)** | 0 in-window sigs |

**Helius RPC returns full history.** For the 8-day-old coin, Helius found **8,004 in-window signatures** across 9 RPC pages. Shyft returned 0 in-window signatures. For the 11-day-old coin: **9,903 in-window signatures**.

### Failed Transactions

Significant percentage of failed txs in RPC results:

| Coin | Failed txs | Total | Percentage |
|---|---|---|---|
| ~3d | 1,158 | 5,000 | 23% |
| ~8d | 720 | 5,000 | 14% |
| ~11d | 296 | 5,000 | 6% |
| ~15d | 339 | 5,000 | 7% |

Higher than Shyft's ~0.5% because Helius RPC returns ALL sigs including failed. Pre-filtering `err != null` before parsing saves significant credits.

---

## Field Mapping Analysis: Helius → RD-001

### Critical Finding: tokenTransfers contain wrapped SOL fee breakdown

Helius `events.swap` is empty for PUMP_AMM trades. But `tokenTransfers` contains ALL SOL movements as **wrapped SOL** (`So11111111111111111111111111111111111111112`), including individual fee transfers to protocol and creator addresses. This was discovered by comparing the same transaction across both APIs.

### BUY Pattern (verified against Shyft BuyEvent)

```
tokenTransfers for a BUY:
  [0] wSOL: trader → POOL     = 1,382,993 lamports  (quote_amount_in + lp_fee)
  [1] wSOL: trader → protocol = 12,860 lamports      (protocol_fee ✓ exact match)
  [2] wSOL: trader → creator  = 4,149 lamports       (coin_creator_fee ✓ exact match)
  [3] token: POOL → trader    = 5,328,122,218 raw    (base_amount_out ✓ exact match)

Shyft BuyEvent:
  quote_amount_in: 1,382,716   (= wSOL_to_pool - lp_fee)
  quote_amount_in_with_lp_fee: 1,382,993  (= wSOL_to_pool ✓)
  lp_fee: 277                  (= wSOL_to_pool × 2/10002 ≈ 277)
  protocol_fee: 12,860         ✓
  coin_creator_fee: 4,149      ✓
  base_amount_out: 5,328,122,218  ✓
```

### SELL Pattern (verified against Shyft SellEvent)

```
tokenTransfers for a SELL:
  [0] wSOL: POOL → trader     = 225,990,451 lamports (user_quote_amount_out ✓)
  [1] wSOL: POOL → protocol   = 2,128,316 lamports   (protocol_fee ✓ exact match)
  [2] wSOL: POOL → creator    = 686,554 lamports      (coin_creator_fee ✓ exact match)
  [3] token: trader → POOL    = 878,902,913,672 raw   (base_amount_in ✓ exact match)

Shyft SellEvent:
  quote_amount_out: 228,851,092   (gross = sum_all_wSOL + lp_fee)
  user_quote_amount_out: 225,990,451  (= wSOL_to_trader ✓)
  lp_fee: 45,771                 (= gross - sum_visible_transfers)
  protocol_fee: 2,128,316        ✓
  coin_creator_fee: 686,554       ✓
  base_amount_in: 878,902,913,672 ✓
```

### Trade Direction Detection

| Direction | Signal |
|---|---|
| **BUY** | Non-wSOL token transfer where `fromUserAccount == pool_address` |
| **SELL** | Non-wSOL token transfer where `toUserAccount == pool_address` |

### Fee Derivation from tokenTransfers

For PUMP_AMM trades, wSOL tokenTransfers to non-pool, non-trader addresses are fees:

| Fee | BUY identification | SELL identification |
|---|---|---|
| **protocol_fee** | wSOL from trader to address that is NOT pool (larger amount, 93 bps) | wSOL from pool to address that is NOT trader (larger amount) |
| **coin_creator_fee** | wSOL from trader to address that is NOT pool (smaller amount, 30 bps) | wSOL from pool to address that is NOT trader (smaller amount) |
| **lp_fee** | Computed: `wSOL_to_pool × lp_bps / (10000 + lp_bps)` where lp_bps=2 | Computed: `gross_sol × lp_bps / 10000` where gross_sol = visible_sum × 10000/9998 |

Known Pump.fun AMM fee basis points (fixed):
- `lp_fee_basis_points`: 2 (0.02%)
- `protocol_fee_basis_points`: 93 (0.93%)
- `coin_creator_fee_basis_points`: 30 (0.30%)

### Token Amount Precision

`tokenTransfers[].tokenAmount` is a **float** (e.g., `5328.122218`). To get raw integer amounts, multiply by `10^decimals` (6 for most pump.fun tokens, 9 for SOL). Alternative: use `accountData[].tokenBalanceChanges[].rawTokenAmount.tokenAmount` which provides the integer as a string.

### RD-001 Field Coverage (Updated)

| RD-001 Field | Helius Source | Method | Available? |
|---|---|---|---|
| `tx_signature` | `signature` | Direct | **YES** |
| `timestamp` | `timestamp` | Unix → UTC datetime | **YES** |
| `trade_type` | Token flow direction vs pool | BUY if token FROM pool, SELL if TO pool | **YES** |
| `wallet_address` | `feePayer` | Direct | **YES** |
| `token_amount` | `tokenTransfers` (non-wSOL, pool involved) | Float × 10^decimals → int | **YES** |
| `sol_amount` | `tokenTransfers` (wSOL to/from pool) | Float × 10^9 → lamports | **YES** |
| `pool_address` | Request context | Passed by caller | **YES** |
| `tx_fee` | `fee` | Direct (already lamports) | **YES** |
| `lp_fee` | Computed from sol_amount + known basis points (2 bps) | Derivation | **YES** |
| `protocol_fee` | `tokenTransfers` wSOL to non-pool, non-trader (larger) | Identify by amount pattern | **YES** |
| `coin_creator_fee` | `tokenTransfers` wSOL to non-pool, non-trader (smaller) | Identify by amount pattern | **YES** |
| `pool_token_reserves` | Inner instruction event data (base58-encoded) | Would need Anchor IDL decode | **NO** |
| `pool_sol_reserves` | Inner instruction event data (base58-encoded) | Would need Anchor IDL decode | **NO** |

**11 of 13 fields extractable.** Only `pool_token_reserves` and `pool_sol_reserves` are missing — these are post-trade pool state that exists in the Pump AMM's Anchor event log (visible as inner instruction data) but requires custom decoding.

### Old Transaction Quality (10-day-old coin)

Server-side time filtered (`gte-time`/`lte-time`) to observation window:

| Metric | Value |
|---|---|
| Total txs | 500 (5 pages) |
| type=SWAP | 499/500 |
| source=PUMP_AMM | 490/500 |
| Has tokenTransfers | **500/500 (100%)** |
| Has nativeTransfers | 487/500 |
| Has events | 9/500 (Jupiter only) |

**tokenTransfers are present on ALL old transactions.** The extraction strategy works regardless of transaction age.

### All Sources Have Trade Data via tokenTransfers

| Source | Has BuyEvent/SellEvent in Shyft | Has tokenTransfers in Helius | Extractable? |
|---|---|---|---|
| PUMP_AMM | Yes | Yes | Yes — same pattern |
| JUPITER | Yes | Yes | Yes — same pattern |
| DFLOW | Yes | Yes | Yes — same pattern |
| OKX_DEX_ROUTER | Yes | Yes | Yes — same pattern |
| TITAN | Not tested | Yes | Likely yes |

---

## Credit Budget Analysis

### Exploration Cost

| Operation | Calls | Credits/call | Total |
|---|---|---|---|
| GET /v0/addresses/.../transactions | 25 pages | 100 | 2,500 |
| POST /v0/transactions | 3 batches | 100 | 300 |
| getSignaturesForAddress (RPC) | 49 calls | 10 | 490 |
| Rate limit test | 10 | 100 | 1,000 |
| **Total** | | | **4,290** |

### Historical Backfill Estimate

For ~3,770 coins with closed observation windows:

| Phase | Calls | Credits/call | Total credits |
|---|---|---|---|
| Phase 1: getSignaturesForAddress | ~3,770 × ~5 pages | 10 | ~188,500 |
| Phase 2: parse transactions | ~3,770 × ~30 batches | 100 | ~11,310,000 |

**Phase 2 would cost ~11M credits.** Free tier provides 1M/month. Would require ~$55 in additional credits ($5 per 1M) or multiple months.

### Rate Limits

| Tier | Enhanced APIs | RPC | Credits/month |
|---|---|---|---|
| Free | 2 req/sec | 10 req/sec | 1,000,000 |
| Developer ($49/mo) | 10 req/sec | 50 req/sec | 10,000,000 |

Free tier rate limit test: 10 rapid calls, 10/10 succeeded, 2.3 req/sec effective.

---

## Key Decisions

1. **Helius solves the historical data problem.** Full history via `getSignaturesForAddress` + full `tokenTransfers` on parsed transactions regardless of age. Can fill observation windows for all 3,770 coins with closed windows that Shyft cannot reach.

2. **11 of 13 RD-001 fields extractable from Helius.** Fee breakdown (protocol_fee, coin_creator_fee) is available via wSOL tokenTransfers. LP fee derivable from known basis points. Only pool reserves (pool_token_reserves, pool_sol_reserves) require Anchor event decoding.

3. **tokenTransfers are the primary data source, not events.** `events.swap` is empty for ~80% of PUMP_AMM trades. But `tokenTransfers` (which includes wrapped SOL movements) is present on 100% of transactions at all ages.

4. **Different conformance function needed.** Helius response format is structurally different from Shyft. Requires `rd001_helius.py` conformance module that extracts from `tokenTransfers` instead of Shyft's BuyEvent/SellEvent.

5. **Server-side time filtering available.** `gte-time`/`lte-time` params enable direct observation window queries — no client-side pagination waste. Significant advantage over Shyft.

6. **Pre-filter failed sigs before parsing.** 6-23% of sigs are failed transactions. Filter `err != null` at the RPC level to save credits (100 credits per parse call).

7. **Pool reserves decision.** Accept NULL for `pool_token_reserves` and `pool_sol_reserves` on Helius-sourced records. These are available in the PUMP_AMM inner instruction event data (base58-encoded Anchor log) but decoding adds complexity. Can be backfilled later if needed.

---

## Fixtures

| File | Contents |
|---|---|
| `pipeline/tests/fixtures/helius_transactions_sample.json` | 36 samples across 8 categories: recent/3d/8d/11d/oldest from GET endpoint, parse_selected for old in-window sigs |
| `pipeline/tests/fixtures/helius_shyft_comparison.json` | Side-by-side Helius + Shyft for same transactions: BUY (PUMP_AMM direct), SELL (PUMP_AMM direct), JUPITER-routed. Used to verify field mapping. |
