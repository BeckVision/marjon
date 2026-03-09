# API Exploration Findings: FL-002 Holder Snapshot Source

**Date:** 2026-03-09
**Purpose:** Explore Moralis API before writing FL-002 pipeline specification. See what it actually returns. Identify conformance layer requirements and spec gaps. Unblock FL-002 gap handling decision.

---

## Moralis

### Connection Details

| Property | Value |
|---|---|
| Base URL | `https://solana-gateway.moralis.io` |
| Auth | API key required (`X-Api-Key` header) |
| Cost | 50 Compute Units per call |
| Endpoint | `/token/{network}/holders/{address}/historical` |
| Supported timeframes | `1min`, `5min`, `10min`, `30min`, `1h`, `4h`, `12h`, `1d`, `1w`, `1m` |
| Default limit | 100 per page |
| Pagination | Cursor-based (opaque cursor string, pass as `cursor` query param) |
| Query key | **Token address (mint address directly)** — no pool mapping needed |

### Request Parameters (from OpenAPI spec)

| Parameter | Required | Description |
|---|---|---|
| `network` | Yes (path) | `mainnet` or `devnet` |
| `address` | Yes (path) | Token address (mint address) |
| `timeFrame` | Yes | Interval: `1min`, `5min`, `10min`, `30min`, `1h`, `4h`, `12h`, `1d`, `1w`, `1m` |
| `fromDate` | Yes | Start date. Accepts seconds (Unix) or date string (momentjs format) |
| `toDate` | Yes | End date. Same formats. |
| `limit` | No | Results per page (default: 100) |
| `cursor` | No | Cursor for next page |

### OpenAPI Response Schema (source of truth)

`HolderTimelineItemDto` — all fields required:

| Field | Type | Description |
|---|---|---|
| `timestamp` | `string` | ISO 8601 timestamp (e.g. `"2025-03-08T12:00:00.000Z"`) |
| `totalHolders` | `number` | Total holder count at this snapshot |
| `netHolderChange` | `number` | Net change in holders during this interval |
| `holderPercentChange` | `number` | Percentage change in holders |
| `newHoldersByAcquisition` | object | Breakdown: `swap`, `transfer`, `airdrop` (all required, all `number`) |
| `holdersIn` | object | Holders entering, by size tier: `whales`, `sharks`, `dolphins`, `fish`, `octopus`, `crabs`, `shrimps` (all required, all `number`) |
| `holdersOut` | object | Holders leaving, by same 7 size tiers (all required, all `number`) |

Response wrapper: `{ cursor, result: [...], page }`. `cursor` is present when more pages exist, absent when all data fits in one page.

### Raw Response (5-min, TRUMP token, 1-hour window)

```json
{
    "result": [
        {
            "timestamp": "2025-03-08T13:00:00.000Z",
            "totalHolders": 656226,
            "netHolderChange": 1,
            "holderPercentChange": 0.00015,
            "newHoldersByAcquisition": {
                "swap": 8,
                "transfer": 8,
                "airdrop": 0
            },
            "holdersIn": {
                "whales": 0, "sharks": 0, "dolphins": 0, "fish": 0,
                "octopus": 0, "crabs": 0, "shrimps": 16
            },
            "holdersOut": {
                "whales": 0, "sharks": 0, "dolphins": 0, "fish": 0,
                "octopus": 0, "crabs": 0, "shrimps": 15
            }
        }
    ],
    "page": 1
}
```

### Key Observations

1. **Returns data for EVERY interval, even when nothing changed — CRITICAL FINDING.** A dead memecoin (BLACKHOUSE, traded for ~20 min then died) returned 25 records for a 2-hour window. After the coin died, every 5-min interval still shows `totalHolders=115, netHolderChange=0`. **This is different from DexPaprika OHLCV which skips intervals with no trades.** This answers the FL-002 gap handling blocker.

2. **Timestamps are ISO 8601 with milliseconds** (`.000Z` suffix). UTC, no ambiguity.

3. **Sort order is descending** (newest first). Same as GeckoTerminal, opposite of DexPaprika.

4. **Both `fromDate` and `toDate` are inclusive.** A 12:00–13:00 query at 5-min resolution returns 13 records (both boundaries included).

5. **Queries by token address (mint address) directly.** No pool mapping needed. The universe identifier is the query key. Simpler than FL-001 which requires mint → pool address mapping.

6. **Cursor-based pagination.** When more records exist than the limit, `cursor` field contains an opaque string. Pass it as `cursor` query param to get the next page. No cursor = last page.

7. **All fields present in real data.** Every field from the OpenAPI schema appears in actual responses. All size tiers (whales through shrimps) and acquisition methods (swap, transfer, airdrop) are populated.

8. **API key and compute units required.** Unlike DexPaprika (free, no auth), Moralis requires `X-Api-Key` header and costs 50 CU per call. CU budget must be factored into pipeline design.

9. **Transient server errors observed.** The first 5-min test call returned `"Internal server error occurred, please try again later"`. Subsequent identical call succeeded. Retry logic is essential.

---

## FL-002 Gap Handling — BLOCKER RESOLVED

**Blocker from data spec:** "BLOCKED — need to check what Moralis API actually returns (every interval, or only on change)"

**Answer: Moralis returns every interval.** When no holder change occurs, the snapshot still appears with `netHolderChange=0` and the previous `totalHolders` value carried forward.

**Implication for FL-002 gap handling:** Unlike FL-001 (no candle if no trades), FL-002 will have a snapshot for every 5-min interval in the observation window. There are no gaps from the source. The gap handling rule for FL-002 should be: "Every interval has a snapshot. No gaps expected."

**Dead coin behavior verified:**
```
16:00-16:15  totalHolders=0,  net=0   (before coin existed)
16:20        totalHolders=1,  net=+1  (first holder)
16:25-16:30  totalHolders=6→12, growing
17:00        totalHolders=115, net=+101 (big activity burst)
17:05-18:00  totalHolders=115, net=0   (coin died, holders frozen)
```

Every interval is present. The "death" of a coin is visible as `netHolderChange=0` with a stable `totalHolders`.

---

## Conformance Layer Requirements

### Field Mapping

**Note:** Warehouse field names below are preliminary. The data spec says `holders_in/out by size tier` without specifying exact column names. Exact Django model field names will be determined during implementation.

| Warehouse field (FL-002) | Moralis field | Transformation |
|---|---|---|
| `timestamp` | `timestamp` | Parse ISO 8601 string to UTC datetime. Remove milliseconds. |
| `total_holders` | `totalHolders` | Direct — already integer |
| `net_holder_change` | `netHolderChange` | Direct — already integer |
| `holder_percent_change` | `holderPercentChange` | Cast to Decimal |
| `acquired_via_swap` | `newHoldersByAcquisition.swap` | Direct — nested field extraction |
| `acquired_via_transfer` | `newHoldersByAcquisition.transfer` | Direct — nested field extraction |
| `acquired_via_airdrop` | `newHoldersByAcquisition.airdrop` | Direct — nested field extraction |
| `holders_in_whales` | `holdersIn.whales` | Direct — nested field extraction |
| `holders_in_sharks` | `holdersIn.sharks` | Direct |
| `holders_in_dolphins` | `holdersIn.dolphins` | Direct |
| `holders_in_fish` | `holdersIn.fish` | Direct |
| `holders_in_octopus` | `holdersIn.octopus` | Direct |
| `holders_in_crabs` | `holdersIn.crabs` | Direct |
| `holders_in_shrimps` | `holdersIn.shrimps` | Direct |
| `holders_out_whales` | `holdersOut.whales` | Direct — nested field extraction |
| `holders_out_sharks` | `holdersOut.sharks` | Direct |
| `holders_out_dolphins` | `holdersOut.dolphins` | Direct |
| `holders_out_fish` | `holdersOut.fish` | Direct |
| `holders_out_octopus` | `holdersOut.octopus` | Direct |
| `holders_out_crabs` | `holdersOut.crabs` | Direct |
| `holders_out_shrimps` | `holdersOut.shrimps` | Direct |
| `coin` (FK) | `address` (from request path) | The mint address used to query — same as warehouse identifier |
| `ingested_at` | Not in response | Set to `datetime.now(UTC)` at load time |

### Semantic Decisions

| Decision | Choice | Reasoning |
|---|---|---|
| **Timestamp interpretation** | Assumed interval start — NOT verified by Moralis docs | Moralis docs do not explicitly state whether the timestamp represents interval start or end. Assumed interval-start to match warehouse WDP9 convention. Needs verification. |
| **Timezone** | UTC | Source timestamps include `.000Z` suffix (UTC). No conversion needed. |
| **Identifier** | Mint address directly | No mapping needed — Moralis queries by token address, which is the warehouse's mint_address identifier. |

### Differences from FL-001 Pipeline

| Property | FL-001 (DexPaprika) | FL-002 (Moralis) |
|---|---|---|
| **Auth** | None | API key + CU cost |
| **Query key** | Pool address (needs mapping) | Mint address (direct) |
| **Pagination** | `start`/`end` range | Cursor-based |
| **Gap behavior** | No data for inactive intervals | Data for every interval (zeros when inactive) |
| **Sort order** | Ascending | Descending |
| **Nested fields** | Flat (open, high, low, close, volume) | Nested objects (holdersIn.whales, newHoldersByAcquisition.swap) |

---

## Spec Gaps / Updates Needed

| Gap | Impact | Recommended Action |
|---|---|---|
| **FL-002 gap handling — RESOLVED** | Blocker removed. Moralis returns every interval. | Update FL-002 gap handling from "BLOCKED" to "Every interval has a snapshot. No gaps from source." |
| **FL-002 timestamp convention — NOT VERIFIED** | Moralis docs do not explicitly state whether timestamp is interval start or end. Assumed interval-start to match FL-001. | Verify with Moralis support or by cross-referencing holder changes against known on-chain events. |
| **FL-002 warehouse field names — PRELIMINARY** | Data spec says "holders_in/out by size tier" without exact column names. Mapping uses preliminary names like `holders_in_whales`. | Finalize field names during model implementation. |
| **CU budget for FL-002 pipeline** | 50 CU per call. Must factor into pipeline cost planning. | Document CU costs and budget in FL-002 pipeline record when written. |

---

## Raw Response Files Saved

| File | Contents |
|---|---|
| `moralis_holders_5min_raw.json` | 5-min holder snapshots, TRUMP token, 1-hour window (13 records) |
