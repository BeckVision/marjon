# API Exploration Findings: Pool Discovery Sources

**Date:** 2026-03-12
**Purpose:** Evaluate Dexscreener and GeckoTerminal as pool discovery sources for U-001, replacing DexPaprika.
**Context:** DexPaprika misses a large number of graduated pump.fun tokens (45% of universe). We need sources with better coverage, and ideally batch capability.

---

## 1. Dexscreener

### 1.1 Connection Details

| Property | Value |
|---|---|
| Base URL | `https://api.dexscreener.com` |
| Auth | None required (free API) |
| Rate limit | 300 req/min for `/tokens/v1/` (documented in endpoint heading). No 429 observed in 10 rapid-fire calls. No rate limit headers in response. |
| Response format | JSON |
| Docs | `https://docs.dexscreener.com/api/reference` |

### 1.2 Batch Endpoint

#### `GET /tokens/v1/{chainId}/{tokenAddresses}`

| Property | Value |
|---|---|
| Path | `/tokens/v1/solana/{comma-separated-addresses}` |
| Batch support | Yes — comma-separated token addresses in the URL path |
| Max batch size | **30 addresses** per call (send ≤30 to stay within response cap) |
| Max results per call | **30 pairs** (hard cap regardless of input size) |
| Pagination | None — 30-pair cap is a hard limit, no cursor/page params |
| Response | Flat JSON array of pair objects (not wrapped in `{pairs: [...]}`) |

### 1.3 Response Structure

Each pair object in the `/tokens/v1/` response:

```json
{
  "chainId": "solana",
  "dexId": "pumpswap",
  "pairAddress": "FzXPKq9fGpJ6EHtBGsMX31nmNSPWEaSx7t4453Rdyc3j",
  "baseToken": {
    "address": "7voKerVHPvbXde3bEpyNcdodVEufq17tW6eZLUUkpump",
    "name": "Dotcom",
    "symbol": "Y2K"
  },
  "quoteToken": {
    "address": "So11111111111111111111111111111111111111112",
    "name": "Wrapped SOL",
    "symbol": "SOL"
  },
  "pairCreatedAt": 1773172966000,
  "liquidity": {"usd": 56079.61, "base": 54576414, "quote": 324.5493},
  "volume": {"h24": 7159.16, "h6": 205.36, "h1": 37.49, "m5": 0}
}
```

**Fixtures:**
- Batch response (3 pumpswap pairs from 15-token batch): `pipeline/tests/fixtures/u001/dexscreener_token_pools_sample.json`

### 1.4 Field Mapping: Dexscreener → PoolMapping

| Dexscreener field | PoolMapping field | Transformation |
|---|---|---|
| `baseToken.address` | `coin_id` (FK) | Direct — mint address |
| `pairAddress` | `pool_address` | Direct — Solana pool address |
| `dexId` | `dex` | Direct — `"pumpswap"` matches warehouse canonical name |
| (constant) | `source` | Set to `"dexscreener"` |
| `pairCreatedAt` | `created_at` | Unix millis → UTC datetime: `datetime.fromtimestamp(v/1000, tz=utc)` |

---

## 2. GeckoTerminal

### 2.1 Connection Details

| Property | Value |
|---|---|
| Base URL | `https://api.geckoterminal.com` |
| Auth | None required (free API). Higher limits via CoinGecko API paid plans. |
| Rate limit | ~10 req/min (free tier). 429 observed after 5 rapid-fire calls. Higher limits via CoinGecko API paid plans. |
| Response format | JSON:API |
| Docs | GeckoTerminal API reference (accessible via browser, not via automated fetch) |

### 2.2 Batch Token Endpoint

#### `GET /networks/{network}/tokens/multi/{addresses}`

| Property | Value |
|---|---|
| Path | `/api/v2/networks/solana/tokens/multi/{comma-separated-addresses}` |
| Batch support | Yes — comma-separated token addresses in the URL path (up to 30) |
| Query params | `include=top_pools` (returns top pools for each token), `include_inactive_source` (boolean, default false) |
| Response | JSON:API format with sideloading: token objects in `data[]`, pool details in `included[]` |

### 2.3 Per-Token Pool Discovery Endpoint

#### `GET /networks/{network}/tokens/{address}/pools`

| Property | Value |
|---|---|
| Path | `/api/v2/networks/solana/tokens/{mint_address}/pools` |
| Batch support | No — single token per call |
| Results | All pools for the token, sorted by liquidity (highest first) |
| Response | JSON:API format: `{data: [{id, type: "pool", attributes: {...}, relationships: {...}}]}` |

### 2.4 Response Structure

**Batch endpoint** (`/tokens/multi/` with `include=top_pools`):

```json
{
  "data": [
    {
      "id": "solana_BwSCHSzT24abUP9SyfniaZJxe9WYy1FozWR7uPYdpump",
      "type": "token",
      "attributes": {"address": "BwSCHSzT24abUP9SyfniaZJxe9WYy1FozWR7uPYdpump", "name": "RUSSELL", "symbol": "RUSSELL"},
      "relationships": {
        "top_pools": {
          "data": [{"id": "solana_14STJA14Hfmvni6L4A6qCqPqVSuFDTVQ5wCef4KLSqPu", "type": "pool"}]
        }
      }
    }
  ],
  "included": [
    {
      "id": "solana_14STJA14Hfmvni6L4A6qCqPqVSuFDTVQ5wCef4KLSqPu",
      "type": "pool",
      "attributes": {
        "address": "14STJA14Hfmvni6L4A6qCqPqVSuFDTVQ5wCef4KLSqPu",
        "name": "RUSSELL / SOL",
        "pool_created_at": "2026-03-01T11:57:23Z",
        "reserve_in_usd": "..."
      },
      "relationships": {
        "base_token": {"data": {"id": "solana_BwSCHSzT24abUP9SyfniaZJxe9WYy1FozWR7uPYdpump", "type": "token"}},
        "quote_token": {"data": {"id": "solana_So11111111111111111111111111111111111111112", "type": "token"}},
        "dex": {"data": {"id": "pumpswap", "type": "dex"}}
      }
    }
  ]
}
```

**Per-token endpoint** (`/tokens/{address}/pools`):

```json
{
  "data": [
    {
      "id": "solana_FzXPKq9fGpJ6EHtBGsMX31nmNSPWEaSx7t4453Rdyc3j",
      "type": "pool",
      "attributes": {
        "address": "FzXPKq9fGpJ6EHtBGsMX31nmNSPWEaSx7t4453Rdyc3j",
        "name": "Y2K / SOL",
        "pool_created_at": "2026-03-10T20:02:46Z",
        "reserve_in_usd": "56482.3571",
        "volume_usd": {"m5": "1.53", "h1": "58.34", "h6": "151.69", "h24": "6295.35"}
      },
      "relationships": {
        "base_token": {"data": {"id": "solana_7voKerVHPvbXde3bEpyNcdodVEufq17tW6eZLUUkpump", "type": "token"}},
        "quote_token": {"data": {"id": "solana_So11111111111111111111111111111111111111112", "type": "token"}},
        "dex": {"data": {"id": "pumpswap", "type": "dex"}}
      }
    }
  ]
}
```

**Fixture:** Batch response (3 tokens, 2 with pools, 1 without): `pipeline/tests/fixtures/u001/geckoterminal_token_pools_sample.json`

### 2.5 Field Mapping: GeckoTerminal → PoolMapping

| GeckoTerminal field | PoolMapping field | Transformation |
|---|---|---|
| `relationships.base_token.data.id` | `coin_id` (FK) | Strip `solana_` prefix |
| `attributes.address` | `pool_address` | Direct — Solana pool address |
| `relationships.dex.data.id` | `dex` | Direct — `"pumpswap"` matches warehouse canonical name |
| (constant) | `source` | Set to `"geckoterminal"` |
| `attributes.pool_created_at` | `created_at` | ISO 8601 UTC string → `datetime.fromisoformat(v)` |

---

## 3. DEX Identifier Mapping

All three APIs use consistent naming for Pumpswap:

| Source API | Raw DEX identifier | Canonical name (warehouse) |
|---|---|---|
| DexPaprika | `pumpswap` | `pumpswap` |
| Dexscreener | `pumpswap` | `pumpswap` |
| GeckoTerminal | `pumpswap` | `pumpswap` |

Other DEX identifiers observed:

| Source | Identifier | Meaning |
|---|---|---|
| Dexscreener | `pumpfun` | Bonding curve pool (pre-graduation) — not relevant |
| Dexscreener | `meteora` | Meteora DEX pool |
| Dexscreener | `raydium` | Raydium DEX pool |
| GeckoTerminal | `pump-fun` | Bonding curve pool (pre-graduation) — not relevant |
| GeckoTerminal | `meteora` | Meteora DLMM pool |
| GeckoTerminal | `meteora-damm-v2` | Meteora dynamic AMM v2 pool |
| GeckoTerminal | `orca` | Orca DEX pool |

**Filter rule:** Only store pairs where DEX identifier is `"pumpswap"`. Bonding curve pools (`pumpfun` / `pump-fun`) are pre-migration — not relevant for post-graduation OHLCV.

---

## 4. Timestamp Verification

Both sources represent `created_at` in UTC:

| Source | Field | Format | Example | Parsed UTC |
|---|---|---|---|---|
| Dexscreener | `pairCreatedAt` | Unix milliseconds | `1773172966000` | `2026-03-10T20:02:46+00:00` |
| GeckoTerminal | `pool_created_at` | ISO 8601 with Z suffix | `"2026-03-10T20:02:46Z"` | `2026-03-10T20:02:46+00:00` |

**Verification:** Same pool (Y2K/SOL, address `FzXPKq9fGpJ6EHtBGsMX31nmNSPWEaSx7t4453Rdyc3j`) returns identical UTC timestamps from both sources. No timezone conversion needed — both are UTC.

---

## 5. Key Observations

1. **Dexscreener batch works.** Comma-separated addresses in the URL path. No auth needed. Send ≤30 addresses per call.

2. **30-address input cap.** The `/tokens/v1/` endpoint accepts up to 30 comma-separated addresses (documented). Sending 15 known-mapped tokens returned 12 pairs — the 3 missing tokens have Pumpswap pools on GeckoTerminal but are not indexed by Dexscreener (coverage gap, not a cap issue).

3. **No pagination on Dexscreener batch.** No cursor or page parameter. The 30-pair cap is a hard limit.

4. **Pool addresses match across all sources.** 9/9 Pumpswap pool addresses from Dexscreener matched DexPaprika exactly. GeckoTerminal returns the same pool address for the same token. The Solana pool address is deterministic — same pool regardless of which aggregator indexes it.

5. **No rate limit headers on Dexscreener.** Responses include no `X-RateLimit-*` or `Retry-After` headers. 10 rapid calls all returned 200. Docs specify 300 req/min for `/tokens/v1/`.

6. **GeckoTerminal rate-limits aggressively.** ~10 calls/min on free tier. 429 observed after 5 rapid-fire calls. Higher limits available via CoinGecko API paid plans.

7. **Multi-DEX tokens are common.** A single token can have pools on pumpswap, meteora, meteora-damm-v2, orca, etc. ATBR4i19...pump had 20 pools across 5 DEXes on GeckoTerminal. Filter to `pumpswap` only.

8. **GeckoTerminal covers tokens Dexscreener misses.** 15-token batch of unmapped tokens: 12/15 had pumpswap pools on GeckoTerminal (3 had no pools at all). All pools returned were `pumpswap`.

9. **GeckoTerminal also has batch.** `/networks/{network}/tokens/multi/{addresses}` takes up to 30 token addresses with `include=top_pools`. Response uses JSON:API sideloading — pool details in `included[]` array, referenced by ID from token objects. This makes the fallback stage batch too (~31 calls for 927 tokens instead of 927).

10. **`top_pools` returns only the highest-liquidity pool.** Tested 4 multi-DEX tokens (pumpswap + meteora/pumpfun) via batch with `include=top_pools`. All 4 returned only 1 pool each — the Pumpswap pool, which had the highest liquidity in every case. **Risk:** if a token's Pumpswap pool has lower liquidity than another DEX's pool, `top_pools` may exclude it. For pump.fun graduates Pumpswap is typically dominant, but the per-token endpoint (`/tokens/{address}/pools`) returns all pools and is the safer fallback if `top_pools` misses Pumpswap.

---

## 6. Coverage Comparison

**Universe size:** 3,674 tokens (as of 2026-03-12).

### Methodology

Coverage was measured in stages:
1. **DexPaprika baseline:** `populate_pool_mapping` command ran against all 3,674 universe tokens (1 API call per token). Measured: 2,024 mapped.
2. **Dexscreener batch:** The 3,674 tokens were sent to `/tokens/v1/solana/` in batches of 30. Results filtered for `dexId == "pumpswap"`. Measured against the full universe AND specifically against the 1,650 DexPaprika misses.
3. **GeckoTerminal batch sample:** 15 tokens from the 1,650 unmapped set were sent to `/api/v2/networks/solana/tokens/multi/` with `include=top_pools`. 12/15 returned pumpswap pools, 3 had no pools. Full-universe GeckoTerminal scan not performed due to rate limit (~10 req/min).

### Per-Source Results

| Source | Batch | Rate limit | Tokens found | Coverage |
|---|---|---|---|---|
| DexPaprika | No (1/call) | 10,000 req/day | 2,024 / 3,674 | 55.1% |
| Dexscreener | Yes (30/call) | 300 req/min | 2,747 / 3,674 | 74.8% |
| GeckoTerminal | Yes (30/call) | ~10 req/min | 12/15 sampled misses | (sample — 80%) |

### Cumulative Coverage

| Source | Unique pool discoveries | Cumulative |
|---|---|---|
| Dexscreener batch | 2,747 | 2,747 (74.8%) |
| + GeckoTerminal fallback | ~741 estimated (80% of 927 misses) | ~3,488 (95.0%) |
| Still missing after both | ≤927 | — |

### Source Overlap

| Comparison | Count |
|---|---|
| Both Dexscreener + DexPaprika | 1,796 |
| Dexscreener only | 723 |
| DexPaprika only | 228 |
| Neither (of 3,674) | 927 |

Dexscreener is NOT a superset of DexPaprika (misses 228 tokens). GeckoTerminal batch covered 12/15 (80%) of sampled Dexscreener misses, suggesting it captures most of the remaining 927.

### What about the tokens still missing?

Tokens with no pool on any source likely:
- Never graduated to Pumpswap (still on bonding curve or failed)
- Graduated very recently and haven't been indexed yet
- Had their liquidity removed / pool closed
