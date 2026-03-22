# Data Specification: U-001

**Dataset:** Graduated Pump.fun Tokens — Early Lifecycle
**Reference:** data_specification_guide.md (concept definitions, attribute templates, glossary)
**Related:** warehouse_implementation_guide.md (warehouse architecture and decision points)

---

## Defined Specs

### U-001: Graduated Pump.fun Tokens — Early Lifecycle

**Version:** 1.0

| Attribute | Value |
|---|---|
| **Universe ID** | U-001 |
| **Name** | Graduated Pump.fun Tokens — Early Lifecycle |
| **Universe** | All tokens launched on pump.fun and migrated to Pumpswap |
| **Universe type** | Event-driven |
| **Anchor event** | Graduation time (migration from pump.fun to Pumpswap) |
| **Observation window start** | T0 (t₁ = 0) |
| **Observation window end** | T0 + 5000 minutes (~3.47 days), candle-aligned inclusive |
| **Exclusion criteria** | None (intentional — avoids survivorship bias) |
| **Version** | 1.0 |

**Design decisions:**

- **No exclusion criteria:** In live trading, you cannot know whether a token will rug, go to zero, or pump. Excluding any tokens would introduce survivorship bias.
- **Candle-aligned inclusive:** Any candle whose 5-min bucket overlaps the observation window is included, even if only partially. This preserves the volatile first moments after graduation.

---

### FL-001: OHLCV Price Data

**Version:** 1.0

| Attribute | Value |
|---|---|
| **Layer ID** | FL-001 |
| **Universe ID** | U-001 |
| **Name** | OHLCV Price Data |
| **Feature set** | open_price, high_price, low_price, close_price, volume (all in USD) |
| **Temporal resolution** | 5-minute candles |
| **Availability rule** | End-of-interval — a candle covering T to T+5min becomes available at T+5min |
| **Gap handling** | No candle created if no trades occurred in the interval |
| **Data source** | GeckoTerminal |
| **Refresh policy** | Daily |
| **Version** | 1.0 |

---

### FL-002: Holder Snapshots

**Version:** 1.0

| Attribute | Value |
|---|---|
| **Layer ID** | FL-002 |
| **Universe ID** | U-001 |
| **Name** | Holder Snapshots |
| **Feature set** | total_holders, net_holder_change, holder_percent_change, acquired_via_swap, acquired_via_transfer, acquired_via_airdrop, holders_in/out by size tier (whales, sharks, dolphins, fish, octopus, crabs, shrimps) |
| **Temporal resolution** | 5-minute snapshots |
| **Availability rule** | End-of-interval — a snapshot covering T to T+5min becomes available at T+5min |
| **Gap handling** | Every interval has a snapshot — Moralis returns data for every interval even when no holder change occurred. Dead coins show `netHolderChange=0` with stable `totalHolders`. No gaps from source. |
| **Data source** | Moralis API |
| **Refresh policy** | Daily |
| **Version** | 1.0 |

---

### JK-001: Standard Join Key

**Version:** 1.0

| Attribute | Value |
|---|---|
| **Join Key ID** | JK-001 |
| **Universe ID** | U-001 |
| **Key fields** | coin + timestamp |
| **Resolution mismatch rule** | Forward-fill from lower-resolution layer to higher-resolution grid. Attach staleness field using naming convention `{layer_id}_{short_name}_staleness_minutes` (e.g. `fl_001_ohlcv_staleness_minutes`). |
| **Null handling** | Inner join — row only exists when all requested layers have data at that timestamp. Row-level existence: a row counts if it exists in the table, regardless of whether individual fields are null. |

**Design decisions:**

- **Inner join:** Strategy only sees rows where all requested data exists. No partial rows.
- **Row-level existence:** A candle with a null feature field still counts as existing. Null fields within a row are the strategy's responsibility.
- **Staleness field naming:** `{layer_id}_{short_name}_staleness_minutes` — both unique and readable.
- **Sparsity risk:** Acknowledged but not applicable yet. All current layers share the same 5-min resolution.

---

### PIT-001: Standard Point-in-Time Rules

**Version:** 1.0

| Attribute | Value |
|---|---|
| **PIT ID** | PIT-001 |
| **Layer ID** | FL-001, FL-002, RD-001 |
| **Lag** | None (not meaningful at 5-minute resolution) |
| **Look-ahead protection** | Strategy can only access observations whose availability rule has been satisfied at the current simulated time |
| **Knowledge time assumption** | Knowledge time equals as-of time — data is not revised or delayed after the interval closes |

**Note on availability rules:** FL-001 and FL-002 both declare end-of-interval as their availability rule. The PIT spec does not repeat this — it defines the shared assumptions (lag, knowledge time, look-ahead protection) that apply on top of each layer's declared availability rule.

**Why this matters:** In backtesting, all data exists in the database already. Nothing physically prevents your code from reading a 1:00–1:05 candle at simulated time 1:03. PIT rules prevent this look-ahead bias by making the backtest behave like real time. The knowledge time assumption holds for real-time market data feeds. If a future feature layer uses a source that publishes with delay or revises data retroactively, it will need its own PIT rule with an explicit knowledge time offset.

---

### Data Quality Constraints

| Constraint ID | Scope | Rule | Severity | Validation method |
|---|---|---|---|---|
| **DQ-001** | FL-001 | No duplicate rows per coin per timestamp | Hard reject | unique_together DB index |
| **DQ-002** | FL-001 | `high_price >= low_price` | Hard reject | Row-level check |
| **DQ-003** | FL-001 | `open_price` and `close_price` between `low_price` and `high_price` | Hard reject | Row-level check |
| **DQ-004** | FL-001 | `volume >= 0` | Hard reject | Row-level check |
| **DQ-005** | FL-001 | Candle timestamp must fall within observation window (T0 to T0+5000min) | Hard reject | Range check against MigratedCoin.anchor_event |
| **DQ-006** | All layers | First observation must be at or near T0 | Hard reject | Compare first row timestamp to anchor event |
| **DQ-007** | RD-001 | No duplicate rows per coin per tx_signature | Hard reject | unique_together DB constraint |
| **DQ-008** | RD-001 | `token_amount > 0` | Hard reject | DB CHECK constraint |
| **DQ-009** | RD-001 | `sol_amount >= 0` | Hard reject | DB CHECK constraint |
| **DQ-010** | RD-001 | `trade_type IN ('BUY', 'SELL')` | Hard reject | DB CHECK constraint |
| **DQ-011** | RD-001 | `pool_token_reserves >= 0` (when present) | Hard reject | DB CHECK constraint |
| **DQ-012** | RD-001 | `pool_sol_reserves >= 0` (when present) | Hard reject | DB CHECK constraint |
| **DQ-013** | RD-001 | `lp_fee >= 0`, `protocol_fee >= 0`, `coin_creator_fee >= 0` | Hard reject | DB CHECK constraints |

**Important:** Sparse data is NOT a quality violation. A coin with 12 candles out of 1000 possible is not corrupt — the coin just died. This is expected behavior for memecoins.

---

### RD-001: Raw Transaction Data

**Version:** 1.0

| Attribute | Value |
|---|---|
| **Reference ID** | RD-001 |
| **Universe ID** | U-001 |
| **Name** | Raw Transaction Data |
| **Record type** | Single trade (buy or sell) |
| **Feature set** | tx_signature, trade_type (BUY/SELL), wallet_address, token_amount, sol_amount, pool_address, tx_fee, lp_fee, protocol_fee, coin_creator_fee, pool_token_reserves (nullable), pool_sol_reserves (nullable) |
| **Timestamp field** | Exact transaction timestamp |
| **Availability rule** | Event-time — a transaction becomes visible at the exact moment it occurs |
| **Access pattern** | "Get all trades for coin X between T1 and T2" |
| **Data source** | Shyft (primary, recent coins within 3-4 day retention), Helius (secondary, full historical backfill) |
| **Refresh policy** | Daily |
| **Version** | 1.0 |

Aggregated summaries (tx_count, buy_volume, sell_volume per 5-min) may be created as FL-003 in a future version.

---

### Derived Features — None defined yet

Will emerge as strategy needs develop. The spec structure is ready.

---

## Blocked Items

None — all blockers resolved.