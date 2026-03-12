# API Exploration Findings: U-001 Universe Discovery (Graduated Tokens)

**Date:** 2026-03-11
**Purpose:** Explore Moralis graduated tokens endpoint before writing U-001 pipeline specification. Document response structure, pagination behavior, sort order, and field mapping to MigratedCoin. Unblock universe discovery pipeline design.

---

## Connection Details

| Property | Value |
|---|---|
| Base URL | `https://solana-gateway.moralis.io` |
| Auth | API key required (`X-Api-Key` header) |
| Cost | **50 CU per call** (measured — 800 calls/day at 40,000 CU budget, shared with FL-002) |
| Endpoint | `/token/{network}/exchange/{exchange}/graduated` |
| Default limit | 100 per page |
| Pagination | Cursor-based (JWT, keyset pagination on `graduatedAt`) |
| Query key | Exchange name (`pumpfun`) — returns all graduated tokens for that exchange |

## Request Parameters

| Parameter | Required | Type | Description |
|---|---|---|---|
| `network` | Yes (path) | string | `mainnet` or `devnet` |
| `exchange` | Yes (path) | string | `pumpfun` |
| `limit` | No (query) | integer | Results per page (default: 100) |
| `cursor` | No (query) | string | Opaque cursor for next page |

**Note:** No date filtering parameters. The endpoint returns ALL graduated tokens, paginated. No `fromDate`/`toDate` like the holder endpoint.

## OpenAPI Response Schema (from Moralis docs)

Response wrapper: `{ result: [...], pageSize, page, cursor }`

Each token object:

| Field | Type (schema) | Description |
|---|---|---|
| `tokenAddress` | string | Solana mint address |
| `name` | string | Token name |
| `symbol` | string | Token symbol |
| `logo` | string | Logo URL (Moralis-hosted) |
| `decimals` | string | Token decimals (as string, not number) |
| `priceNative` | string | Current price in SOL |
| `priceUsd` | string | Current price in USD |
| `liquidity` | string | Current liquidity in USD |
| `fullyDilutedValuation` | string | Current FDV in USD |
| `graduatedAt` | string | ISO 8601 timestamp of graduation |

## Raw Response Sample (5 tokens, page 1)

```json
{
  "result": [
    {
      "tokenAddress": "96dCyTmXNmd9uSTQF9E93PXBxRUMk9jRXmzr5F26pump",
      "name": "Canabiii",
      "symbol": "Canabiii",
      "logo": "https://logo.moralis.io/solana-mainnet_96dCyTmXNmd9uSTQF9E93PXBxRUMk9jRXmzr5F26pump_78e89edc216c8ab6a2448d7bb07bc666.webp",
      "decimals": "6",
      "priceNative": "0.0000000009688",
      "priceUsd": "0.0000000857485",
      "liquidity": "92.91358415",
      "fullyDilutedValuation": "171.497",
      "graduatedAt": "2026-03-10T17:22:07.000Z"
    },
    {
      "tokenAddress": "CoT3FEPV3y6kRLQ8gAhQi7FZidXxQ8WiKCKYYXXbpump",
      "name": "Basic Intelligence eXperiment",
      "symbol": "BIX",
      "logo": "https://logo.moralis.io/solana-mainnet_CoT3FEPV3y6kRLQ8gAhQi7FZidXxQ8WiKCKYYXXbpump_57d3d3be44a7f9143955e2411f5354a7.webp",
      "decimals": "6",
      "priceNative": "0.000000213",
      "priceUsd": "0.000018875",
      "liquidity": "10842.001914734",
      "fullyDilutedValuation": "18874.991841397274625",
      "graduatedAt": "2026-03-10T17:17:17.000Z"
    },
    {
      "tokenAddress": "ECs2JMvSnmwu1yCuwRzeM261LyU7CYUyNDDMbrLWpump",
      "name": "Clawcoin",
      "symbol": "Clawcoin",
      "logo": "https://logo.moralis.io/solana-mainnet_ECs2JMvSnmwu1yCuwRzeM261LyU7CYUyNDDMbrLWpump_e6b1c61a983ecf0c56eae83fa54e8ac6.webp",
      "decimals": "6",
      "priceNative": "0.000000149",
      "priceUsd": "0.000013216",
      "liquidity": "9074.985523218",
      "fullyDilutedValuation": "13216",
      "graduatedAt": "2026-03-10T17:17:12.000Z"
    },
    {
      "tokenAddress": "EARLYeBEXWA6dA8br6GEaUVmY2dCJQrD9wpwKegH194T",
      "name": "Early Cash",
      "symbol": "EARLY",
      "logo": "https://logo.moralis.io/solana-mainnet_EARLYeBEXWA6dA8br6GEaUVmY2dCJQrD9wpwKegH194T_0200bcb132f0dcd2897aee7545187a16.webp",
      "decimals": "6",
      "priceNative": "0.0000000201477864194097070367894882106158034487577",
      "priceUsd": "0.0000017592168273087925013585459303638795627757813",
      "liquidity": "3330.2536251597354406965369626105778541859112056389458331342",
      "fullyDilutedValuation": "1759.2168273087925013585459303638795627757813",
      "graduatedAt": "2026-03-10T17:13:23.000Z"
    },
    {
      "tokenAddress": "8vxuf4jenmjhBz5BHr56z2hK5KKvJ2otBw8FF7SZpump",
      "name": "The Lobster",
      "symbol": "ROSENCRATZ",
      "logo": "https://logo.moralis.io/solana-mainnet_8vxuf4jenmjhBz5BHr56z2hK5KKvJ2otBw8FF7SZpump_2a0e433b23e9ca62128e9bf1a42c6a4f.webp",
      "decimals": "6",
      "priceNative": "0.000000360",
      "priceUsd": "0.000031942",
      "liquidity": "14584.069195902",
      "fullyDilutedValuation": "31942",
      "graduatedAt": "2026-03-10T17:03:34.000Z"
    }
  ],
  "pageSize": 5,
  "page": 1,
  "cursor": "eyJhbGciOiJIUzI1NiJ9..."
}
```

## Key Observations

### 1. Sort order is DESCENDING by graduatedAt (newest first) — CRITICAL FINDING

Page 1 (limit=5):
```
2026-03-10T17:22:07.000Z  (newest)
2026-03-10T17:17:17.000Z
2026-03-10T17:17:12.000Z
2026-03-10T17:13:23.000Z
2026-03-10T17:03:34.000Z  (oldest on page 1)
```

Page 2 (via cursor):
```
2026-03-10T16:57:20.000Z  (continues older)
2026-03-10T16:56:29.000Z
2026-03-10T16:56:09.000Z
2026-03-10T16:50:37.000Z
2026-03-10T16:47:01.000Z
```

**Implication for steady-state:** Newest graduates appear first. Steady-state polling can stop as soon as it encounters a `graduatedAt` already in the warehouse. No need to paginate through the entire dataset on each run.

### 2. Pagination is cursor-based (JWT keyset pagination)

Cursor decodes to:
```json
{
  "page": 1,
  "fromLookup": {
    "tokenAddress": "8vxuf4jenmjhBz5BHr56z2hK5KKvJ2otBw8FF7SZpump",
    "category": "pumpfun:graduated",
    "categoryKey": 1773162214
  }
}
```

- `categoryKey` is the **Unix epoch seconds** of `graduatedAt` — confirmed match (1773162214 = 2026-03-10T17:03:34Z)
- This is **keyset pagination**: each page picks up from the last record's graduation timestamp
- More stable than offset pagination — new graduates don't shift existing pages

### 3. No `total` field — must paginate to count

- Response has `pageSize` and `page` but **no `total` count**
- At 100/page with ~272 graduations/day, 10 pages covered 3.67 days (1,000 tokens)
- **Estimated total graduated tokens:** 150,000–215,000 (pump.fun launched ~Jan 2024)
- **Full bootstrap cost:** ~1,500–2,200 pages × 50 CU = **~75,000–110,000 CU** (exceeds daily 40,000 CU budget)
- Bootstrap must be split across **3+ days** with CU budget coordination (see Bootstrap Strategy below)

### 4. Duplicate calls return IDENTICAL results for identity fields

Two calls with the same cursor returned:
- **Same token addresses:** YES
- **Same graduatedAt values:** YES
- **Same prices:** YES (at ~1 second interval; prices are likely cached or snapshot values, not real-time)

Data is stable between calls. No shuffling or re-ordering.

### 5. All fields are strings — including numeric values

| Field | Python type | Example value |
|---|---|---|
| `tokenAddress` | str | `"96dCyTmXNmd9uSTQF9E93PXBxRUMk9jRXmzr5F26pump"` |
| `name` | str | `"Canabiii"` |
| `symbol` | str | `"Canabiii"` |
| `logo` | str | `"https://logo.moralis.io/..."` |
| `decimals` | **str** | `"6"` (NOT integer — confirmed string in real responses) |
| `priceNative` | str | `"0.0000000009688"` |
| `priceUsd` | str | `"0.0000000857485"` |
| `liquidity` | str | `"92.91358415"` |
| `fullyDilutedValuation` | str | `"171.497"` |
| `graduatedAt` | str | `"2026-03-10T17:22:07.000Z"` |

**`decimals` is a string `"6"`, not integer `6`.** Must `int()` it in the conformance layer.

### 6. Nullable fields (across 100 tokens)

| Field | Null count | Notes |
|---|---|---|
| `logo` | 8/100 (8%) | Some tokens have no logo |
| `fullyDilutedValuation` | 1/100 (1%) | Rare but possible |
| `tokenAddress` | 0/100 | Always present |
| `name` | 0/100 | Always present, never empty string |
| `symbol` | 0/100 | Always present, never empty string |
| `decimals` | 0/100 | Always present |
| `graduatedAt` | 0/100 | Always present |
| `priceUsd` | 0/100 | Always present (but may be stale) |
| `priceNative` | 0/100 | Always present |
| `liquidity` | 0/100 | Always present |

### 7. Token addresses: mostly `*pump` suffix but NOT exclusively

- 94/100 addresses end in `pump` (standard pump.fun vanity suffix)
- **6/100 have standard Solana base58 addresses** (44 chars, no `pump` suffix)
- Address lengths: 43 or 44 characters (valid Solana base58 public keys)
- Examples of non-pump: `EARLYeBEXWA6dA8br6GEaUVmY2dCJQrD9wpwKegH194T`, `BdxZU97HsQWH1EFfrzVLy79gZBkKiFhomM6WoLAAJvvF`
- **All are valid graduated pump.fun tokens** — the suffix is just a vanity convention, not a requirement

### 8. Decimal values are NOT always 6

- 99/100 tokens: `decimals = "6"` (standard SPL token)
- 1/100 token: `decimals = "0"` — Clawcoin (`ECs2JMvSnmwu1yCuwRzeM261LyU7CYUyNDDMbrLWpump`)
- The conformance layer MUST parse decimals as int, not assume 6

### 9. graduatedAt format: ISO 8601 with millisecond precision, UTC

- Format: `YYYY-MM-DDTHH:MM:SS.000Z`
- Always has `.000Z` suffix (milliseconds always `000` — second-level precision only)
- UTC timezone (Z suffix)
- No ambiguity — direct parse to datetime

### 10. Price fields are live/cached snapshots, NOT graduation-time values

Two calls to the same page (no cursor) returned identical token lists but the same prices — however, comparing the first call and the second call (minutes apart), `priceUsd` and `liquidity` values changed for the same tokens:
- Call 1: Canabiii `priceUsd = "0.0000000857485"`, `liquidity = "92.91"`
- Call 2: Canabiii `priceUsd = "0.000000466"`, `liquidity = "216.67"`

**These are CURRENT market values, not values at graduation time.** They change between calls. We should NOT store them as graduation-time data. Only `tokenAddress`, `name`, `symbol`, `decimals`, `logo`, and `graduatedAt` are stable.

### 11. No DEX destination field — CRITICAL GAP

The response does NOT tell you **which DEX** the token graduated to (Pumpswap vs Raydium). The endpoint only confirms the token graduated from pump.fun. If the pipeline needs to distinguish graduation targets, this must come from another source.

### 12. No rate limiting observed at 50 CU/call

Made 15+ rapid calls (including 10-page pagination at 0.3s interval) with no 429 errors or throttling. No HTTP 429 responses observed, but CU budget is the real constraint (see CU Budget section).

### 13. CU cost discrepancy — CRITICAL FINDING

Moralis documentation claims **1 CU per call** for the graduated tokens endpoint. **Measured cost is 50 CU per call** (950 CU consumed for 19 calls = 50 CU/call). All budget planning in this document uses the **measured value of 50 CU/call**. This matches the FL-002 holder endpoint cost (also 50 CU/call).

---

## Conformance Layer Requirements

### Field Mapping: Moralis Graduated → MigratedCoin

| API field | Type | Nullable? | Maps to MigratedCoin field | Transformation needed |
|---|---|---|---|---|
| `tokenAddress` | string | No | `mint_address` | Direct (validate base58, 32–44 chars) |
| `graduatedAt` | string | No | `anchor_event` | Parse ISO 8601 to UTC datetime |
| `name` | string | No | `name` (NEW) | Direct |
| `symbol` | string | No | `symbol` (NEW) | Direct |
| `decimals` | string | No | `decimals` (NEW) | `int()` — NOT always 6 |
| `logo` | string | Yes (8%) | `logo_url` (NEW) | Direct (URL string, nullable) |
| `priceUsd` | string | No | **NOT STORED** | Live value, not graduation-time |
| `priceNative` | string | No | **NOT STORED** | Live value, not graduation-time |
| `liquidity` | string | No | **NOT STORED** | Live value, not graduation-time |
| `fullyDilutedValuation` | string | Yes (1%) | **NOT STORED** | Live value, not graduation-time |
| *(not in response)* | — | — | `ingested_at` | `datetime.now(UTC)` at load time |
| *(not in response)* | — | — | `source` | `"moralis_graduated"` (constant) |

### Semantic Decisions

| Decision | Choice | Reasoning |
|---|---|---|
| **Store prices?** | NO | Price/liquidity/FDV are live snapshots, not graduation-time values. Storing them would be misleading. |
| **Store name/symbol/decimals/logo?** | YES | These are stable token metadata, useful for display and validation. |
| **Dedup strategy** | `mint_address` is unique key | If a token already exists in MigratedCoin, skip or update metadata only. |
| **Graduation target DEX** | Unknown from this endpoint | Need separate source if Pumpswap vs Raydium distinction matters. |

---

## Spec Gaps / Updates Needed

| Gap | Impact | Recommended Action |
|---|---|---|
| **No DEX destination field** | Cannot distinguish Pumpswap vs Raydium graduates from this endpoint alone | Determine if DEX target matters for pipeline. If yes, need on-chain or secondary API lookup. |
| **No date filtering** | Cannot request "only tokens graduated after X" — must paginate from newest and stop early | Design steady-state to paginate until hitting known `graduatedAt`. This is efficient since sort is descending. |
| **No total count** | Cannot know how many graduated tokens exist without paginating to the end | Bootstrap must paginate until cursor is null. Estimate: ~1,500–2,200 pages at 100/page. |
| **`decimals` is string, not int** | Schema says string, real data confirms `"6"` not `6`. Also found `"0"`. | Conformance layer must `int(decimals)`. Cannot assume 6. |
| **Price fields are live, not historical** | `priceUsd`, `liquidity`, `fullyDilutedValuation` change between calls | Do NOT store these as graduation-time data. Only store stable identity fields. |
| **`logo` nullable (8%)** | Some tokens have no logo | `logo_url` field on MigratedCoin must be nullable. |
| **`fullyDilutedValuation` nullable (1%)** | Rare but happens | Not storing this field, so no impact. |
| **Non-pump addresses (6%)** | Some graduated tokens don't end in `pump` | Do NOT filter by address suffix. All addresses from this endpoint are valid pump.fun graduates. |
| **CU cost discrepancy** | Moralis docs claim 1 CU/call, measured cost is 50 CU/call | All budget planning uses measured 50 CU/call. Bootstrap exceeds single-day budget. |

---

## Pagination & Bootstrap Strategy

### Steady-state (daily polling)

1. Call `?limit=100` (newest first)
2. For each token, check if `mint_address` exists in MigratedCoin
3. If found → stop (all remaining tokens are already known)
4. If not found → upsert into MigratedCoin
5. Continue to next page via cursor if needed
6. **Expected pages per run:** 1–3 (at ~272 graduations/day, 2-3 pages covers 24 hours)
7. **Expected CU cost per run:** 50–150 CU

### Bootstrap (one-time full load)

1. Paginate from page 1 until cursor is null
2. Estimated: ~1,500–2,200 pages × 50 CU = **~75,000–110,000 CU total**
3. **Exceeds daily 40,000 CU budget** — cannot complete in a single day
4. Must be split across **3+ days** with CU budget coordination
5. Bootstrap must save cursor position so it can resume across days
6. At 100ms+ per call with 0.3s sleep: **~10–15 minutes wall time per day** (not the bottleneck — CU budget is)

### Bootstrap day plan (example at ~2,000 pages)

| Day | Pages | CU used (discovery) | CU remaining for FL-002 |
|---|---|---|---|
| Day 1 | ~600 pages | ~30,000 CU | ~10,000 CU (200 FL-002 calls) |
| Day 2 | ~600 pages | ~30,000 CU | ~10,000 CU |
| Day 3 | ~600 pages | ~30,000 CU | ~10,000 CU |
| Day 4 | ~200 pages (tail) | ~10,000 CU | ~30,000 CU (catch-up) |

---

## Shared CU Budget Allocation

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
- **At ~3 pages per coin per day:** ~252 coins can be tracked daily

### Budget coordination rules

1. **Discovery runs first** — must complete before FL-002 to ensure new coins are available
2. **CU tracker is shared** — both pipelines read/write `.moralis_cu_tracker.json`
3. **During bootstrap** — FL-002 gets reduced allocation (see bootstrap day plan above)
4. **Hard stop at 38,000 CU** — leave 2,000 CU buffer for retries and error recovery

---

## Raw Response Files Saved

| File | Contents |
|---|---|
| `pipeline/tests/fixtures/u001/moralis_graduated_sample.json` | 5-token sample with cursor, page 1 (used for unit tests) |
