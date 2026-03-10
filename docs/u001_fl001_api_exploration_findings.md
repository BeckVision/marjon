# API Exploration Findings: FL-001 OHLCV Sources

**Date:** 2026-03-08
**Purpose:** Explore DexPaprika and GeckoTerminal APIs before writing pipeline specification. See what they actually return. Identify conformance layer requirements and spec gaps.

---

## DexPaprika

### Connection Details

| Property | Value |
|---|---|
| Base URL | `https://api.dexpaprika.com` |
| Auth | None required |
| Rate limit | 10,000 requests per day (free tier, no API key needed) |
| OHLCV endpoint | `/networks/solana/pools/{pool_address}/ohlcv` |
| Supported intervals | 1m, 5m, 10m, 15m, 30m, 1h, 6h, 12h, 24h |
| Max records per call | 366 (`limit` parameter, default: 1) |
| Max date range per call | 1 year |

### OHLCV Request Parameters

| Parameter | Required | Description |
|---|---|---|
| `start` | Yes | Beginning of data window. Accepts: Unix timestamp, RFC3339 (`2023-10-27T08:07:20Z`), or date (`2023-10-27`, interpreted as 00:00 UTC) |
| `end` | No | End of data window. Same formats as `start`. If omitted, returns data for `start` date only. Max range: 1 year from start. |
| `interval` | No | Candle size. Enum: `1m, 5m, 10m, 15m, 30m, 1h, 6h, 12h, 24h`. Default: `24h` |
| `limit` | No | Max records to return. Min: 1, max: 366, default: 1 |
| `inversed` | No | Flips price ratio from token0/token1 to token1/token0. Default: false |

### Raw OHLCV Response (5-min candles, Pumpswap pool)

```json
[
    {
        "time_open": "2026-03-07T16:30:00Z",
        "time_close": "2026-03-07T16:35:00Z",
        "open": 83.57495517948755,
        "high": 83.6686090262332,
        "low": 83.57462330179919,
        "close": 83.6334478488349,
        "volume": 26344957
    }
]
```

### OpenAPI Response Schema (source of truth)

From the official OpenAPI spec (`OHLCVRecord`):

| Field | Type | Description |
|---|---|---|
| `time_open` | `string, date-time` (required) | Opening timestamp of the OHLCV period |
| `time_close` | `string, date-time` (required) | Closing timestamp of the OHLCV period |
| `open` | `number, double` (required) | Opening price for the period |
| `high` | `number, double` (required) | Highest price during the period |
| `low` | `number, double` (required) | Lowest price during the period |
| `close` | `number, double` (required) | Closing price for the period |
| `volume` | `integer, int64` (required) | Total volume traded during the period |

All 7 fields are required per schema. No `market_cap` field exists.

### Key Observations

1. **Both `time_open` AND `time_close` are provided.** No ambiguity about timestamp convention. DexPaprika gives both boundaries of every candle.

2. **Timestamps are ISO 8601 with `Z` suffix (UTC).** No timezone ambiguity.

3. **DexPaprika ALWAYS returns USD prices — VERIFIED.** The `inversed` flag controls which token's USD price you get: `inversed=false` → token[0] price in USD, `inversed=true` → token[1] price in USD. Tested with TRUMP (~$3) across TRUMP/USDC and TRUMP/SOL pools — both modes produced correct USD values. For Pumpswap pools (token[0]=SOL, token[1]=memecoin), use `inversed=true` to get memecoin USD price.

4. **Volume is USD — VERIFIED via cross-reference.** Cross-referenced against GeckoTerminal: GT `currency=token` returns SOL volume (~16.25 SOL), GT `currency=usd` returns USD volume (~$1,335). SOL vol × SOL price ($82) = USD vol. DexPaprika returns ~$1,081 for the same candle — USD-scale, not token-scale. Values differ between APIs (different aggregation) but both are clearly USD. DexPaprika volume differs slightly between `inversed` modes (~1081 vs ~1085) for reasons unclear, but both are USD-scale. Using `inversed=true` consistently.

5. **Gap handling confirmed:** No candle is created for intervals with no trades. A coin that traded for 20 minutes then died returns only 4 candles out of 156 possible. Matches FL-001 gap handling rule.

6. **No `market_cap` field.** The OHLCV response contains only: time_open, time_close, open, high, low, close, volume. **FL-001 spec includes `market_cap` but DexPaprika does not provide it per candle.** This is a spec gap.

7. **Pool-based, not token-based.** OHLCV data is fetched per pool, not per token. Since a graduated token can have multiple pools (different DEXes, different pairs), the pipeline must know which pool to fetch for each token.

8. **First candle starts at next boundary after pool creation.** Pool created at 16:27:41Z, first candle at 16:30:00Z. The API respects clean 5-minute boundaries.

9. **Pagination needed for large windows.** Max 366 records per call. A full 5000-minute observation window at 5-minute resolution = 1000 candles max = 3 API calls (366 + 366 + 268).

### DexPaprika Pool Detail Response (selected fields)

```json
{
    "id": "Eht9rHnn92FhbWLqd7iQuBdkbsJF9qU9QZsdNrhPcXGS",
    "dex_id": "pumpswap",
    "created_at": "2026-03-07T16:27:41Z",
    "tokens": [
        {"id": "So11111111111111111111111111111111111111112", "symbol": "SOL", "fdv": 744229984},
        {"id": "DHu9NJw6eHvA87G9K6FcXhfsUetsX46tCuggGfeNffcX", "symbol": "COIN", "fdv": 106768}
    ],
    "last_price_usd": 83.03238042349037,
    "price_stats": { "ath": 83.67, "ath_date": "2026-03-07T16:34:00Z" }
}
```

### DexPaprika Token Detail Response (selected fields)

```json
{
    "id": "DHu9NJw6eHvA87G9K6FcXhfsUetsX46tCuggGfeNffcX",
    "price_usd": 1.0676893383621689e-06,
    "fdv": 106768.93376788477,
    "total_supply": 99999999936
}
```

**Note:** Token detail has `fdv` (fully diluted valuation) and `total_supply` but NOT `market_cap`. FDV = price × total supply. Market cap = price × circulating supply. For pump.fun tokens, these may be the same (all supply circulating at graduation).

### DexPaprika Token Pools Response

**Endpoint:** `GET /networks/solana/tokens/{mint_address}/pools`

**Date explored:** 2026-03-11

**Purpose:** Used by `populate_pool_mapping` and `discover_graduates` to find Pumpswap pools for newly discovered tokens. This exploration was missing from the original API exploration — the function was written assuming a bare list response, but the endpoint wraps pools in an object.

**Response structure:**

```json
{
    "pools": [
        {
            "id": "Eht9rHnn92FhbWLqd7iQuBdkbsJF9qU9QZsdNrhPcXGS",
            "dex_id": "pumpswap",
            "chain_id": "solana",
            "created_at": "2026-03-07T16:27:41Z",
            "tokens": [
                {
                    "id": "So11111111111111111111111111111111111111112",
                    "name": "Wrapped SOL",
                    "symbol": "SOL"
                },
                {
                    "id": "DHu9NJw6eHvA87G9K6FcXhfsUetsX46tCuggGfeNffcX",
                    "name": "Test Coin",
                    "symbol": "COIN"
                }
            ],
            "last_price_usd": 83.03238042349037,
            "volume_usd": 152340.23
        }
    ],
    "page_info": {
        "current_page": 1,
        "total_pages": 1,
        "total_items": 2,
        "items_per_page": 10
    }
}
```

**Response schema:**

| Field | Type | Description |
|---|---|---|
| `pools` | array | List of pool objects |
| `pools[].id` | string | Pool address (on-chain) — use as `pool_address` in PoolMapping |
| `pools[].dex_id` | string | DEX identifier (`pumpswap`, `raydium`, etc.) — filter on this |
| `pools[].chain_id` | string | Always `solana` for our queries |
| `pools[].created_at` | string, date-time | Pool on-chain creation time (ISO 8601 with Z suffix) |
| `pools[].tokens` | array | Token pair; `tokens[0]` is typically SOL for Pumpswap pools |
| `pools[].tokens[].id` | string | Token mint address |
| `pools[].tokens[].name` | string | Token name |
| `pools[].tokens[].symbol` | string | Token symbol |
| `pools[].last_price_usd` | number | Current price in USD |
| `pools[].volume_usd` | number | Trading volume in USD |
| `page_info` | object | Pagination metadata |
| `page_info.current_page` | integer | Current page number |
| `page_info.total_pages` | integer | Total number of pages |
| `page_info.total_items` | integer | Total pool count across all pages |
| `page_info.items_per_page` | integer | Items per page (default 10) |

**Key observations:**

1. **Response is an object, NOT a bare list.** The pool array is nested under the `pools` key. The connector must extract `data['pools']`, not return `data` directly. (This was the original bug in `fetch_token_pools`.)

2. **`dex_id` is the field name, not `dexId`.** The response uses snake_case (`dex_id`), consistent with other DexPaprika endpoints. The `dexId` camelCase check in callers was defensive but unnecessary for DexPaprika.

3. **Pool address is in `id`, not `address`.** The pool's on-chain address is the `id` field. There is no separate `address` field.

4. **`created_at` uses ISO 8601 with Z suffix.** Same format as OHLCV timestamps. Used to determine the graduation pool (oldest Pumpswap pool).

5. **Pagination via `page_info`.** For tokens with many pools, results are paginated. Most graduated pump.fun tokens have 1-3 pools (Pumpswap + possibly Raydium), so pagination is rarely needed.

6. **Multiple DEXes possible.** A single token can have pools on both Pumpswap and Raydium. The pipeline filters for `dex_id == 'pumpswap'` and selects the oldest pool by `created_at`.

**Fixture saved:** `pipeline/tests/fixtures/dexpaprika_token_pools_sample.json`

---

## GeckoTerminal

### Connection Details

| Property | Value |
|---|---|
| Base URL | `https://api.geckoterminal.com` |
| Auth | None required (Beta, free) |
| Rate limit | ~10 calls/minute (may fluctuate based on network traffic). Higher limits via CoinGecko API paid plans. |
| OHLCV endpoint | `/api/v2/networks/{network}/pools/{pool_address}/ohlcv/{timeframe}` |
| Timeframe options | `day`, `hour`, `minute` (separate path segments, not a query param) |
| Aggregate options | day: 1. hour: 1, 4, 12. minute: 1, 5, 15. second: 1, 15, 30. Default: 1 |
| Max records per call | Default: 100, max: 1000 |
| Max date range per call | 6 months. Use `before_timestamp` to paginate older data. |
| Pagination | `before_timestamp` (Unix epoch integer) — returns data before this timestamp |

### OHLCV Request Parameters (from docs)

| Parameter | Required | Description |
|---|---|---|
| `timeframe` | Yes (path) | `day`, `hour`, or `minute` |
| `aggregate` | No | Time period to aggregate. E.g. `/minute?aggregate=15` for 15-min candles. See aggregate options above. |
| `before_timestamp` | No | Return data before this Unix timestamp (integer seconds since epoch) |
| `limit` | No | Number of results (default: 100, max: 1000) |
| `currency` | No | Return OHLCV in `usd` or `token` (default: `usd`) |
| `token` | No | Return OHLCV for `base` or `quote` token, or a specific token address. Use this to invert the chart. |

### Raw OHLCV Response (5-min candles, same Pumpswap pool)

```json
{
    "data": {
        "attributes": {
            "ohlcv_list": [
                [1772901000, 0.0008398777, 0.005037535, 1.032917e-05, 4.578479e-05, 27191591]
            ]
        }
    },
    "meta": {
        "base": {"name": "BLACKHOUSE", "symbol": "COIN", "address": "DHu9NJw..."},
        "quote": {"name": "Wrapped SOL", "symbol": "SOL", "address": "So111..."}
    }
}
```

### Response Schema (from CoinGecko/GeckoTerminal docs)

Each OHLCV array (under `ohlcv_list`) consists of 6 elements in this order:

| Position | Field | Description |
|---|---|---|
| 0 | Timestamp | Unix epoch representing the **start** of the time interval |
| 1 | Open | Opening price at the beginning of the interval |
| 2 | High | Highest price during the interval |
| 3 | Low | Lowest price during the interval |
| 4 | Close | Price at the end of the interval |
| 5 | Volume | Volume traded during the interval |

Response structure: `data.attributes.ohlcv_list` (array of arrays). `meta` contains `base` and `quote` token info including address, name, symbol, coingecko_coin_id.

### Key Observations

1. **Single Unix timestamp per candle.** No `time_close`. Only one integer timestamp. **Docs confirm it represents the interval START.** (Same as DexPaprika's `time_open`.)

2. **Array format, not named objects.** OHLCV is positional: `[timestamp, open, high, low, close, volume]`. No field names — position matters.

3. **Returned in descending order** (newest first). DexPaprika returns ascending.

4. **Default currency is USD — VERIFIED.** The `currency` parameter defaults to `usd`. Tested with TRUMP/SOL pool: default gave ~$3.04 (TRUMP USD price), `currency=token` gave ~0.037 (TRUMP in SOL). GeckoTerminal Pumpswap pools have base=memecoin, quote=SOL, so **default params give memecoin USD price directly.**

5. **Has inversion parameters — VERIFIED.** `token` param: `base` (default), `quote`, or specific address. `currency` param: `usd` (default) or `token`. Tested all 4 combinations with TRUMP/SOL. For Pumpswap pools, defaults work because base=memecoin.

6. **Includes partial candles.** GeckoTerminal returned a candle at 16:25 (before the pool's 16:27 creation time). DexPaprika did not.

7. **Gap handling same concept, different results.** Both APIs skip empty intervals, but the exact candles returned differ slightly for the same pool and time range.

8. **Pagination uses `before_timestamp`.** Unlike DexPaprika which uses `start`/`end` range, GeckoTerminal paginates backward from a timestamp. To get older data, pass the oldest timestamp from your current result as `before_timestamp` for the next call.

9. **Max range: 6 months per call.** Older data (back to September 2021) available for paid CoinGecko API subscribers.

10. **Data freshness: cached for 1 minute.** All endpoints cached for 1 min. Data updated 2-3 seconds after blockchain confirmation, subject to network availability.

11. **API versioning recommended.** Set `Accept: application/json;version=20230203` header. Without it, latest version is used, which may change without notice (Beta API).

---

## Side-by-Side Comparison

| Property | DexPaprika | GeckoTerminal |
|---|---|---|
| **Timestamp format** | ISO 8601 with Z (`"2026-03-07T16:30:00Z"`) | Unix epoch integer (`1772901000`) |
| **Timestamp meaning** | Both `time_open` and `time_close` provided | Single timestamp = interval start (confirmed by docs) |
| **Response format** | Array of named objects | Nested: `data.attributes.ohlcv_list` as positional arrays |
| **Sort order** | Ascending (oldest first) | Descending (newest first) |
| **Price denomination** | USD always. `inversed` controls which token (false=token[0], true=token[1]). **Verified with TRUMP.** | USD by default (`currency=usd`). `token` param controls base/quote. **Verified with TRUMP.** |
| **Volume denomination** | USD — verified via cross-reference with GeckoTerminal. `int64` per schema. | USD by default (`currency=usd`) — verified via `currency=token` × SOL price cross-check. |
| **Inversion** | `inversed` parameter (boolean) flips pair direction | `token` parameter (`base`/`quote`/address) controls which token is charted. `currency` controls USD vs token. |
| **Partial candles** | Not returned before pool creation | Returned before pool creation |
| **Market cap** | Not in OHLCV response | Not in OHLCV response |
| **Rate limit** | 10,000 requests/day | ~10 calls/minute (free). Higher on paid CoinGecko API plans. |
| **Max per call** | 366 records (default: 1) | 1000 records (default: 100) |
| **Max date range** | 1 year | 6 months per call |
| **Pagination** | `start`/`end` range parameters | `before_timestamp` (backward from a point) |

---

## Conformance Layer Requirements (for pipeline spec)

Based on these findings, the conformance layer for FL-001 must handle:

### Time Normalization

| Source → Canonical | Transformation needed |
|---|---|
| DexPaprika `time_open` (ISO Z string) → `timestamp` (DateTimeField) | Parse ISO, already UTC. Use `time_open` to match WDP9 interval-start convention. |
| GeckoTerminal Unix epoch → `timestamp` (DateTimeField) | Convert Unix int to UTC datetime. Already interval-start. |

### Field Mapping

| Canonical (FL-001) | DexPaprika | GeckoTerminal |
|---|---|---|
| `timestamp` | `time_open` | `ohlcv_list[n][0]` (Unix epoch, docs confirm = interval start) |
| `open_price` | `open` (with `inversed` TBD) | `ohlcv_list[n][1]` (USD default, `token` param TBD) |
| `high_price` | `high` (with `inversed` TBD) | `ohlcv_list[n][2]` |
| `low_price` | `low` (with `inversed` TBD) | `ohlcv_list[n][3]` |
| `close_price` | `close` (with `inversed` TBD) | `ohlcv_list[n][4]` |
| `volume` | `volume` (denomination TBD) | `ohlcv_list[n][5]` (denomination TBD) |
| `market_cap` | **NOT AVAILABLE** | **NOT AVAILABLE** |

### Type Normalization

| Field | DexPaprika type | GeckoTerminal type | Canonical type |
|---|---|---|---|
| Prices | JSON `number, double` (Python float) | JSON float (Python float) | DecimalField(38, 18) |
| Volume | JSON `integer, int64` per OpenAPI schema | JSON float (no schema published, observed as float) | DecimalField |
| Timestamp | ISO string (`time_open`, `time_close`) | Unix epoch integer (seconds) | DateTimeField (UTC) |

### Identifier Mapping

DexPaprika and GeckoTerminal both use **pool address** as the query key, not mint address. The pipeline must maintain a mapping from mint_address (universe identifier) → pool_address (API query key). This mapping comes from the pool detail endpoints, where the token list includes mint addresses.

---

## Spec Gaps Discovered

| Gap | Impact | Recommended Action |
|---|---|---|
| **FL-001 includes `market_cap` but neither API provides it — RESOLVED** | Decision: remove `market_cap` from FL-001 feature set. FL-001 stores only what APIs return: open, high, low, close, volume. Market cap can be added later as a derived feature (DF-001) if needed. | Update FL-001 feature set to: `open, high, low, close, volume` (all in USD). |
| **Volume denomination not specified in FL-001 — RESOLVED & VERIFIED** | FL-001 specifies USD. Both APIs confirmed to return USD volume. DexPaprika verified via cross-reference with GeckoTerminal: GT `currency=token` (SOL) × SOL price = GT `currency=usd` value. DexPaprika returns same USD-scale values. | Update FL-001 spec: volume is in USD. |
| **Pool address not captured in data spec — RESOLVED** | Decision: create a separate pool mapping dimension table (not a field on MigratedCoin). Supports multiple pools per token. Pipeline uses mint_address → pool_address mapping to query APIs. | Design pool mapping table in pipeline record. |
| **Price denomination not specified in FL-001 — RESOLVED** | FL-001 will specify USD. DexPaprika returns USD (verified). GeckoTerminal returns USD by default (verified). Both APIs confirmed to produce memecoin-in-USD with correct parameter settings. | Update FL-001 spec: prices are in USD. DexPaprika: `inversed=true`. GeckoTerminal: defaults. |
| **Pair direction/inversion behavior — VERIFIED** | Both APIs return USD prices by default. DexPaprika: `inversed` controls which token's USD price (false=token[0], true=token[1]). Pumpswap has token[0]=SOL, so `inversed=true` gives memecoin USD price. GeckoTerminal: Pumpswap pools have base=memecoin, quote=SOL, so default params (`currency=usd`, `token=base`) give memecoin USD price. Volume is also USD-denominated in default/USD mode for both APIs. | **DexPaprika:** use `inversed=true` for Pumpswap pools. **GeckoTerminal:** use defaults. Document in pipeline record. |

---

## Raw Response Files Saved

| File | Contents |
|---|---|
| `dexpaprika_pumpswap_pools.json` | Top 5 Pumpswap pools by volume |
| `dexpaprika_ohlcv_5m_raw.json` | 5-min OHLCV, non-inversed |
| `dexpaprika_ohlcv_5m_inversed.json` | 5-min OHLCV, inversed=true |
| `dexpaprika_pool_detail.json` | Pool detail response |
| `dexpaprika_token_detail.json` | Token detail response |
| `geckoterminal_ohlcv_5m_raw.json` | GeckoTerminal 5-min OHLCV |
