# API Exploration Findings: RD-001 v2 Two-Phase Connector

**Date:** 2026-03-15
**Purpose:** Explore `getSignaturesForAddress` (Shyft RPC) and `POST /transaction/parse_selected` (Shyft REST) before rewriting the RD-001 connector from single-phase (`/transaction/history`) to two-phase.
**Test pools:** Recent coin (anchor 2026-03-14), plus coins with closed observation windows (5d, 7d, 10d, 12d ago).

---

## getSignaturesForAddress (Shyft RPC)

### Connection Details

| Property | Value |
|---|---|
| Endpoint | `POST https://rpc.shyft.to?api_key=<key>` |
| Protocol | JSON-RPC 2.0 |
| Auth | API key in query string |
| Rate limit | No rate limiting observed (latency-bound at ~3.3 req/sec) |
| Data retention | **3-4 days** ([Shyft docs](https://docs.shyft.to/solana-apis/transactions/transaction-apis): "Transaction history is only limited to past 3-4 days. From both RPCs and APIs.") |

### Request Parameters (from [Shyft docs](https://docs.shyft.to/solana/rpc-calls/http/getsignaturesforaddress))

| Parameter | Required | Type | Description |
|---|---|---|---|
| `address` | Yes | string (base-58) | Account address for which to fetch transaction signatures |
| `limit` | No | number (1–1000) | Maximum transaction signatures to return |
| `before` | No | string (signature) | Start searching backwards from this transaction signature. If not provided, starts from highest confirmed block |
| `until` | No | string (signature) | Search until this transaction signature, if found before limit reached |
| `commitment` | No | string | `confirmed` or `finalized` (default: `finalized`) |
| `minContextSlot` | No | number | Minimum slot that the request can be evaluated at |

### Raw Response

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": [
    {
      "blockTime": 1773567069,
      "confirmationStatus": "finalized",
      "err": null,
      "memo": null,
      "signature": "sioXfYFjJd7dxZvFJKbmqfuLxovnQiBsyE6vUykjGVmreFthq9kqwEjqfB9hVRagVHAG3zwGF4EFKPuV6fkcx67",
      "slot": 406547798
    }
  ]
}
```

### Response Schema (from Shyft docs)

| Field | Type | Description |
|---|---|---|
| `signature` | string | Full transaction signature (base58) |
| `slot` | number | Solana slot number |
| `err` | null \| object | `null` = success, error object = failure details |
| `memo` | null \| string | Transaction memo (null in all samples) |
| `blockTime` | null \| number | Unix timestamp in seconds |
| `confirmationStatus` | string | `finalized` for historical transactions |

### Key Observations

1. **Results are newest-first (descending blockTime).** Paginate with `before = result[-1].signature` for next page. Stop when `len(result) < limit`.

2. **`until` is EXCLUSIVE.** Does not include the boundary signature in results. Returns only sigs NEWER than the `until` sig. Verified: setting `until` to the 6th sig returned only the 5 newer sigs.

3. **Max limit is 1000.** `limit=1000` works, `limit=1001` returns error: `"Invalid limit; max 1000"`.

4. **Failed transactions appear** with `err` set. Example: `{"err": {"InstructionError": [4, {"Custom": 6004}]}}`. In 1000 sigs from one pool: 5 had `err != null` (0.5%).

5. **Batch RPC supported.** Multiple calls packed into one HTTP request as a JSON array. Tested up to 250 calls per batch — all succeeded.

6. **Data retention: ~3-4 days.** Tested against coins with closed observation windows:

    | Coin anchor age | Total sigs from RPC | In observation window |
    |---|---|---|
    | ~1 day | 1000 | 1000 (100%) |
    | ~9 days (window closed 5d ago) | 0 | 0 |
    | ~10 days (window closed 7d ago) | 25 | 0 |
    | ~14 days (window closed 10d ago) | 9 | 0 |
    | ~16 days (window closed 12d ago) | 939 | 0 (oldest sig: 3.8d) |

    For coins with closed windows, RPC returns only recent sigs (within ~4 days). All in-window sigs are gone.

### Batch RPC Performance

| Batch size | Latency |
|---|---|
| 2 | 0.32s |
| 5 | 0.47s |
| 10 | 0.36s |
| 50 | 0.32s |
| 100 | 0.40s |
| 250 | 0.61s |

10 pools × limit=1000 in one batch: 1.4s, 2194 total sigs.

---

## POST /transaction/parse_selected (Shyft REST)

### Connection Details

| Property | Value |
|---|---|
| Endpoint | `POST https://api.shyft.to/sol/v1/transaction/parse_selected` |
| Auth | `x-api-key` header |
| Rate limit | 1 req/sec per key |
| Max per call | 100 signatures (hard limit) |
| Data retention | **3-4 days** (same platform limit as RPC) |

### Request Parameters (from [Shyft docs](https://docs.shyft.to/solana-apis/transactions/transaction-apis))

| Parameter | Required | Type | Description |
|---|---|---|---|
| `network` | Yes | string | `testnet`, `devnet`, or `mainnet-beta` |
| `transaction_signatures` | Yes | array of strings | 1–100 transaction signatures |
| `enable_events` | No | boolean | Include parsed anchor events |
| `enable_raw` | No | boolean | Include raw transaction data |
| `commitment` | No | string | `confirmed` (default) or `finalized` |

### Raw Response

```json
{
  "success": true,
  "message": "Selected transactions fetched successfully",
  "result": [
    {
      "timestamp": "2026-03-15T09:31:09.000Z",
      "fee": 6.202e-06,
      "fee_payer": "...",
      "signers": ["..."],
      "signatures": ["sioXfYFjJd7dxZvFJK..."],
      "protocol": {"address": "pAMMBay6oceH9...", "name": "PUMP_FUN_AMM"},
      "type": "SWAP",
      "status": "Success",
      "actions": [...],
      "events": [
        {
          "name": "SellEvent",
          "data": {
            "timestamp": 1773567069,
            "base_amount_in": 1000000,
            "quote_amount_out": 50000000,
            "lp_fee": 100000,
            "protocol_fee": 465000,
            "coin_creator_fee": 150000,
            "user": "...",
            "pool": "...",
            "pool_base_token_reserves": 999000000,
            "pool_quote_token_reserves": 5000000000
          }
        }
      ]
    }
  ]
}
```

### Response Schema (from Shyft docs)

| Field | Type | Description |
|---|---|---|
| `success` | boolean | API call status |
| `message` | string | Status message |
| `result` | array | Parsed transaction objects |
| `result[].timestamp` | string | ISO 8601 (UTC) |
| `result[].fee` | number | Network fee (SOL) |
| `result[].fee_payer` | string | Fee payer address |
| `result[].signers` | array of strings | Transaction signers |
| `result[].signatures` | array of strings | Transaction signatures |
| `result[].protocol` | object | `{address, name}` |
| `result[].type` | string | Top-level type (SWAP, TOKEN_TRANSFER, etc.) |
| `result[].status` | string | `Success` or `Fail` |
| `result[].actions` | array | Parsed actions |
| `result[].events` | array | Parsed anchor events (when `enable_events=true`) |

### Key Observations

1. **Response format is identical to `/transaction/history`.** All fields match — same structure, same event format. Existing conformance function (`pipeline/conformance/rd001_shyft.py`) works unchanged.

2. **Hard limit: 100 signatures per call.** 101+ returns `"Validation failed!"`. Same max as `/transaction/history`.

3. **Failed transaction sigs** return `status: "Fail"`, `events: []`. Same as `/transaction/history`.

4. **Non-existent signature** returns `success: true`, `result: []` (silently dropped, no error).

5. **Mixed pools in one batch work.** Sigs from different pools can be parsed in one call.

6. **New event types observed** (not in Session A): `CloseUserVolumeAccumulatorEvent` (account cleanup), `TradeEvent` (redundant with SellEvent). Neither affects conformance — existing logic extracts first BuyEvent/SellEvent and ignores others.

### Latency

| Batch size | Latency |
|---|---|
| 5 sigs | 1.5s |
| 10 sigs | 0.5s |
| 50 sigs | 2.0s |
| 100 sigs | 2.8–3.9s |

~3x slower per call than `/transaction/history` (~1s).

---

## Observation Window Analysis

**Test coin** (graduated ~1 day ago, window still open):

| Metric | Value |
|---|---|
| Total pool signatures | 1000 |
| In observation window | 1000 (100%) |
| Before window | 0 |
| After window | 0 |
| Failed transactions | 5 (0.5%) |

When querying by pool_address, all signatures fall within the observation window. Pool activity starts at graduation (= anchor_event). Pre-filtering by blockTime saves minimal calls for fresh coins, but is useful for steady-state (skip already-processed sigs) and for filtering failed txs (err != null).

---

## Conformance Compatibility

parse_selected output was compared field-by-field against `/transaction/history` output for the same signatures:

| Field | parse_selected | /transaction/history | Match |
|---|---|---|---|
| `signatures` | Present | Present | Yes |
| `timestamp` | ISO 8601 | ISO 8601 | Yes |
| `type` | Present | Present | Yes |
| `status` | Present | Present | Yes |
| `fee` | Present | Present | Yes |
| `fee_payer` | Present | Present | Yes |
| `protocol` | Present | Present | Yes |
| `actions` | Present | Present | Yes |
| `events` | Same BuyEvent/SellEvent structure | Same | Yes |
| `token_balance_changes` | Present | Present | Yes |

**No changes needed to `pipeline/conformance/rd001_shyft.py`.**

---

## Key Decisions for Implementation

1. **Two-phase adopted** — `getSignaturesForAddress` for discovery, `parse_selected` for parsing. Decoupled architecture with batch RPC for signature discovery.
2. **Process recent coins only** — Shyft's 3-4 day retention limit means pipeline must catch coins within ~4 days of graduation.
3. **Batch RPC for Phase 1** — up to 250 pools per HTTP request, 1000 sigs per pool. Entire universe signature discovery in ~4 minutes.
4. **Pre-filter between phases** — drop sigs with `err != null` (failed, no events) and sigs outside observation window before calling parse_selected.
5. **`until` cursor for incremental** — efficient steady-state updates without re-paginating known history.
6. **Conformance unchanged** — parse_selected response identical to /transaction/history.
