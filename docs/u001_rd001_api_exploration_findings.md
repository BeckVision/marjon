# RD-001 API Exploration Findings: Shyft Transaction History

**Date:** 2026-03-14
**Endpoint:** `GET https://api.shyft.to/sol/v1/transaction/history`
**Auth:** `x-api-key` header (`SHYFT_API_KEY` env var)
**Test tokens:** ELON (active, graduated today), WI6900 (2 days old), Lili (oldest, Feb 27)

---

## 1. Endpoint & Parameters

| Parameter | Value | Notes |
|---|---|---|
| `network` | `mainnet-beta` | Required |
| `account` | Address string | Can be mint_address OR pool_address |
| `tx_num` | 1–100 | Max transactions per page |
| `before_tx_signature` | Signature string | Pagination cursor (optional) |
| `enable_events` | `true` / `false` | Include parsed on-chain events |
| `enable_raw` | `true` / `false` | Include raw transaction bytes (not needed) |

**No server-side time filtering.** Tested `from_date`, `to_date`, `start_time`, `end_time`, `until_date` — all ignored. Connector must paginate backward and stop when `tx.timestamp < start`.

---

## 2. Query Key: mint_address vs pool_address

| Query by | Results | Noise level |
|---|---|---|
| **mint_address** | All transactions involving the token across all pools and programs | High — includes TOKEN_BURN, TOKEN_MINT, non-trade transfers |
| **pool_address** | Only transactions on that specific liquidity pool | Low — almost exclusively swap activity |

**Decision: query by pool_address.** Cleaner data, less post-filtering. Downside: requires PoolMapping (depends_on pool_mapping step). Tokens without pool mapping cannot be fetched.

---

## 3. Response Structure

```json
{
  "success": true,
  "message": "Transaction history fetched successfully",
  "result": [
    {
      "signatures": ["<tx_signature>"],
      "timestamp": "2026-03-14T13:20:00.000Z",
      "type": "SWAP" | "TOKEN_TRANSFER" | "CREATE_TOKEN_ACCOUNT" | "GETACCOUNTDATASIZE",
      "status": "Success",
      "fee": 0.000445,
      "fee_payer": "<wallet_address>",
      "protocol": {"name": "PUMP_FUN_AMM", "address": "pAMMBay6oceH9..."},
      "actions": [...],
      "events": [...],
      "token_balance_changes": [...]
    }
  ]
}
```

**Empty response:** `result: []` (success=true, no error). Safe to handle as zero trades.

---

## 4. Top-level `type` Field is UNRELIABLE for Trade Detection

In a 20-transaction sample from an active token's pool:

| Top-level `type` | Count | Has trade events? |
|---|---|---|
| `TOKEN_TRANSFER` | 18 | Yes (BuyEvent/SellEvent in events) |
| `SWAP` | 1 | Yes |
| `GETACCOUNTDATASIZE` | 1 | Yes |

**90% of trades have `type=TOKEN_TRANSFER`**, not `type=SWAP`. The `type` field reflects the outermost instruction, not the semantic intent. Cannot filter by type alone.

Similarly, `TOKEN_TRANSFER` transactions have `UNKNOWN` action types (not `SWAP`) while only `type=SWAP` transactions have a parseable `SWAP` action with `tokens_swapped`. **Actions are also unreliable** for the majority of trades.

---

## 5. Events are the Reliable Signal

Every trade on a Pump.fun AMM pool emits either a **BuyEvent** or **SellEvent** in the `events` array, regardless of top-level `type`. This is the primary detection method.

### Event Types Observed

| Event | Source | Contains trade data? |
|---|---|---|
| **BuyEvent** | Pump.fun AMM | Yes — primary source for buy trades |
| **SellEvent** | Pump.fun AMM | Yes — primary source for sell trades |
| **SwapsEvent** | Jupiter aggregator | Minimal — just input/output mint+amount, no fees |
| **SwapEvent** | Individual swap hop | Minimal — amm, input/output, no fees |

Transactions often have both: e.g., `[BuyEvent, SwapsEvent]` when a buy is routed through Jupiter. The BuyEvent/SellEvent has the richest data.

### BuyEvent Data Fields

```
timestamp             Unix epoch (seconds)
base_amount_out       Tokens received (raw, needs /10^decimals)
quote_amount_in       SOL paid to pool (lamports)
max_quote_amount_in   Slippage protection limit
lp_fee                LP fee (lamports)
lp_fee_basis_points   LP fee rate (2 bps)
protocol_fee          Protocol fee (lamports)
protocol_fee_basis_points  Protocol fee rate (93 bps)
coin_creator_fee      Creator fee (lamports)
coin_creator_fee_basis_points  Creator fee rate (30 bps)
user                  Trader wallet address
pool                  Pool address
user_base_token_account       Trader's token ATA
user_quote_token_account      Trader's SOL ATA
pool_base_token_reserves      Pool's token reserves after trade
pool_quote_token_reserves     Pool's SOL reserves after trade
```

### SellEvent Data Fields

```
timestamp             Unix epoch (seconds)
base_amount_in        Tokens sold (raw)
quote_amount_out      SOL received from pool (gross, lamports)
user_quote_amount_out SOL user receives (net, after fees)
min_quote_amount_out  Slippage protection limit
lp_fee                (same fee fields as BuyEvent)
protocol_fee
coin_creator_fee
user, pool            (same as BuyEvent)
pool_base_token_reserves, pool_quote_token_reserves
```

### Price Derivation

- **BUY:** `price_sol = quote_amount_in / base_amount_out` (both in raw units, then adjust for decimals)
- **SELL:** `price_sol = quote_amount_out / base_amount_in`

---

## 6. Pagination

**Method:** Cursor-based via `before_tx_signature`.

- Set `before_tx_signature = result[-1]['signatures'][0]` for next page.
- Results returned newest-first (descending timestamp).
- Stop when: `len(result) < tx_num` (last page).
- For time-bounded queries: also stop when `result[-1]['timestamp'] < start`.

**Max per page:** 100 transactions.

---

## 7. Rate Limits

- **REST API:** 1 request/second (confirmed by Shyft docs; meme_analyzer uses 0.3s conservative sleep).
- **No daily CU/call limit observed** — unlike Moralis (40K CU/day). However, Shyft plans may have monthly limits.
- **Multiple API keys available:** SHYFT_API_KEY (primary), SHYFT_API_KEY_2, SHYFT_API_KEY_4 in .env.

---

## 8. Volume Estimates

| Token | Status | Trades scanned | Time span | Estimated rate |
|---|---|---|---|---|
| ELON (active) | 2000+ (hit cap) | ~2.5 hours | ~800 trades/hour |
| WI6900 (mid) | 2000+ (hit cap) | ~1.5 days | ~56 trades/hour |
| Lili (dead) | 905 | ~3.5 days | ~11 trades/hour |

### Bootstrap Volume Estimation

For a typical token with 3.47-day observation window:
- **Active token:** ~66,000 trades → 660 API calls → ~11 minutes at 1 req/sec
- **Average token:** ~5,000–10,000 trades → 50–100 calls → ~1–2 minutes
- **Dead token:** ~500–2,000 trades → 5–20 calls → ~5–20 seconds

**Full universe bootstrap (5,113 tokens):**
- Estimated: ~25,000–50,000 API calls total
- At 1 req/sec: ~7–14 hours (single key, single worker)
- With 3 keys + concurrent: ~2–5 hours

**Feasible** with multi-key rotation, unlike initial fear of 46 days.

---

## 9. Edge Cases

| Case | Behavior |
|---|---|
| Dead coin with no recent trades | Returns old transactions (pool still exists on-chain) |
| Nonexistent address | `success: true`, `result: []` (empty, not error) |
| Failed transactions | Not observed in samples — all `status: "Success"`. May need larger sample. |
| Multi-event transactions | Common: `[BuyEvent, SwapsEvent]` for Jupiter-routed buys. Take BuyEvent/SellEvent as primary. |
| CREATE_TOKEN_ACCOUNT + trade | First trade for a wallet: creates ATA and swaps in one tx. Still has BuyEvent. |

---

## 10. Transaction Type Distribution (pool_address query)

From samples across active and dead tokens:

| Top-level type | Frequency | Contains trade? | Notes |
|---|---|---|---|
| `TOKEN_TRANSFER` | ~60% | Yes (via events) | Most common for direct trades |
| `CREATE_TOKEN_ACCOUNT` | ~25% | Yes (via events) | First-time buyers |
| `SWAP` | ~10% | Yes (action + events) | Direct swaps with parseable action |
| `GETACCOUNTDATASIZE` | ~5% | Sometimes | Jupiter pre-flight, often has events |

**Conclusion:** Filter by presence of `BuyEvent`/`SellEvent` in events, NOT by top-level type.

---

## 11. Key Decisions for Session B

1. **Query by pool_address** — cleaner data, adds PoolMapping dependency.
2. **Use BuyEvent/SellEvent as primary data extraction source** — reliable across all tx types, richest data (amounts, fees, wallet, pool, reserves).
3. **Ignore SwapsEvent/SwapEvent** — redundant, less detail than BuyEvent/SellEvent.
4. **No server-side time filtering** — connector must paginate backward and check timestamps.
5. **Bootstrap is feasible** — ~7-14 hours single-key, faster with multi-key rotation.
6. **Multiple Shyft API keys available** — enables concurrent fetching with key rotation.

---

## Fixture

Saved to `pipeline/tests/fixtures/shyft_transactions_sample.json` with 4 representative transactions:
1. `SWAP` with `SellEvent` (parseable SWAP action)
2. `GETACCOUNTDATASIZE` with `BuyEvent` + `SwapsEvent` (Jupiter-routed)
3. `TOKEN_TRANSFER` with `BuyEvent` (most common pattern)
4. `CREATE_TOKEN_ACCOUNT` with `BuyEvent` + `SwapsEvent` (first-time buyer)
