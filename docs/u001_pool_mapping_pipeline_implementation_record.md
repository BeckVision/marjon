# Pipeline Implementation Record: U-001 / Pool Mapping

**Scope:** Dimension table population — mint address → Pumpswap pool address mapping
**Dataset:** U-001 — Graduated Pump.fun Tokens — Early Lifecycle
**Primary Source:** Dexscreener API (batch endpoint)
**Fallback Source:** GeckoTerminal API (batch endpoint)
**Serves:** FL-001 (OHLCV pipeline — queries by pool address)
**Reference:** pipeline_implementation_guide.md
**Reference:** u001_pool_mapping_api_exploration_findings.md

---

## Decision Selections

Each row references a decision point (DP) from the Pipeline Implementation Guide.

| DP | Decision | Selected Option | Reasoning |
|---|---|---|---|
| **PDP1** | Extract Strategy | **A: Full load (batch discovery)** | This is not time-series extraction — tokens either have a pool or they don't. No watermark in the traditional sense. The "watermark" is "which tokens already have a PoolMapping row." Each run queries all tokens missing a mapping, in batches of ≤30. No overlap window needed — discovery is idempotent by nature. |
| **PDP2** | ETL vs ELT | **A: ETL (transform before load)** | Both sources are free to re-fetch (no CU cost, no API key). If a conformance bug is discovered, re-fetching the entire universe costs ~154 API calls — trivial. No staging tables needed. |
| **PDP3** | Idempotency Mechanism | **A: Upsert** | PoolMapping is a dimension table (master data), not a time series. `update_or_create()` on `coin_id`: insert if new, update metadata (`pool_address`, `dex`, `source`, `created_at`) if the token already has a mapping. A token's pool address is deterministic (same address across all sources), so re-runs are harmless. Not delete-write — there is no scope to delete within; the unit of work is the individual token mapping. |
| **PDP4** | Watermark Strategy | Not applicable | No watermark needed. The "what needs processing" set is derived by query: `MigratedCoin` rows that have no corresponding `PoolMapping` row. This set shrinks as mappings are discovered. No timestamp-based watermark. |
| **PDP5** | Rate Limit Handling | **A: Serial with sleep** | Dexscreener: 300 req/min (documented). No sleep needed between batches at current scale (~123 calls for full universe). GeckoTerminal fallback: ~10 req/min. `time.sleep(6)` between batch calls. Both sources are free — rate limit is the only constraint. |
| **PDP6** | Error Handling | **D: Retry with backoff, then fail** | Per source, not per token. If Dexscreener returns malformed data (invalid JSON, missing expected fields), crash — fail fast (PDP6). If Dexscreener returns no results for a token, that's not an error — the token moves to the fallback stage. Connector's `request_with_retry()` handles transient errors (network, 429, 5xx) with exponential backoff. After max retries, the exception propagates and the batch fails. |
| **PDP7** | Reconciliation Strategy | **A: Count-based** | Log how many tokens were mapped per source per run: Dexscreener mapped N, GeckoTerminal mapped M, K tokens remain unmapped. No boundary check — there is no time range to validate against. Count reconciliation reveals coverage trends over time. |
| **PDP8** | Provenance Tracking | **B: Row-level source field** | `source` field on PoolMapping records which API discovered the mapping (`"dexscreener"` or `"geckoterminal"`). Plus `discovered_at` timestamp (auto_now_add). Run-level logging: tokens processed, mapped per source, still unmapped. |
| **PDP9** | Multi-Source Handling | **D: Fallback chain** | First pipeline with a deliberate multi-source fallback. Dexscreener batch is primary (74.8% coverage, 300 req/min, 30 tokens/call). GeckoTerminal batch is fallback (~80% of Dexscreener misses, ~10 req/min, 30 tokens/call). Tokens unmapped by both are logged as unmappable. Sources are NOT merged — a token is mapped by exactly one source (whichever finds it first). |
| **PDP10** | Scheduling | **A: Manual (management commands)** | `python manage.py populate_pool_mapping` for standalone runs. `python manage.py orchestrate` for integration with the full pipeline chain. Pool mapping runs after discovery and before FL-001. |
| **PDP11** | Dimension Table Location | **A: Warehouse app owns all tables** | PoolMapping already lives in the warehouse app. Pipeline app owns only code (connectors, conformance functions, management command). Same pattern as FL-001/FL-002. |

---

## Fallback Chain

This is the first pipeline with a multi-source fallback pattern. Fallback logic lives in the management command (orchestration layer), not in connectors.

### Flow

```
Input: List of mint addresses needing pool mapping
       (MigratedCoin rows with no PoolMapping entry)

Stage 1: Dexscreener batch
    → Batch tokens into groups of ≤30
    → Call GET /tokens/v1/solana/{addresses} per batch
    → Filter response for dexId == "pumpswap"
    → Match response pairs to input tokens via baseToken.address
    → Tokens with pumpswap pair → conformance → loader (source="dexscreener")
    → Tokens without pumpswap pair → collect for Stage 2

Stage 2: GeckoTerminal batch (fallback)
    → Batch unfound tokens into groups of ≤30
    → Call GET /api/v2/networks/solana/tokens/multi/{addresses}?include=top_pools
    → Parse JSON:API sideloaded pools from included[]
    → Filter for dex.data.id == "pumpswap"
    → Tokens with pumpswap pool → conformance → loader (source="geckoterminal")
    → Tokens without pumpswap pool → log as unmappable

Result: PoolMapping rows created with source tracking
```

### Why this order?

1. **Dexscreener first** — higher coverage (74.8% vs sample), faster rate limit (300 vs ~10 req/min), simpler response format (flat JSON vs JSON:API sideloading).
2. **GeckoTerminal second** — catches ~80% of what Dexscreener misses (12/15 in sample). Slower rate limit but only processes the smaller "misses" set.
3. **DexPaprika not in chain** — only 13.8% recovery of Dexscreener misses (228/1,650) vs GeckoTerminal's 80%. Legacy source. No batch support.

---

## Source Configuration

### Primary: Dexscreener (batch)

| Property | Value |
|---|---|
| Base URL | `https://api.dexscreener.com` |
| Auth | None required |
| Rate limit | 300 req/min (documented in endpoint heading) |
| Endpoint | `GET /tokens/v1/solana/{comma-separated-addresses}` |
| Batch size | ≤30 token addresses per call |
| Response cap | 30 pairs per call (hard limit, no pagination) |
| Response format | Flat JSON array of pair objects |
| Cost | Free |

### Fallback: GeckoTerminal (batch)

| Property | Value |
|---|---|
| Base URL | `https://api.geckoterminal.com` |
| Auth | None required (free tier). Higher limits via CoinGecko API paid plans. |
| Rate limit | ~10 req/min (free tier). 429 observed after 5 rapid-fire calls. |
| Endpoint | `GET /api/v2/networks/solana/tokens/multi/{comma-separated-addresses}?include=top_pools` |
| Batch size | ≤30 token addresses per call |
| Response format | JSON:API with sideloading — token objects in `data[]`, pool details in `included[]` |
| Cost | Free |

**`top_pools` limitation:** The batch endpoint returns only the highest-liquidity pool per token. Tested 4 multi-DEX tokens — all returned only the Pumpswap pool (highest liquidity in every case). Risk: if a token's Pumpswap pool has lower liquidity than another DEX's pool, `top_pools` may exclude it. For pump.fun graduates Pumpswap is typically dominant. If `top_pools` misses Pumpswap for a token, the per-token endpoint (`GET /api/v2/networks/solana/tokens/{address}/pools`) returns all pools — usable as a per-token fallback within Stage 2 if needed.

---

## Conformance Mapping

Two conformance functions — one per source. Both produce the same PoolMapping-compatible output.

### Dexscreener → PoolMapping

| Dexscreener field | PoolMapping field | Transformation |
|---|---|---|
| `baseToken.address` | `coin_id` (FK) | Direct — mint address |
| `pairAddress` | `pool_address` | Direct — Solana pool address |
| `dexId` | `dex` | Direct — `"pumpswap"` matches warehouse canonical name |
| (constant) | `source` | `"dexscreener"` |
| `pairCreatedAt` | `created_at` | Unix millis ÷ 1000 → UTC datetime: `datetime.fromtimestamp(v/1000, tz=utc)` |

### GeckoTerminal → PoolMapping

| GeckoTerminal field | PoolMapping field | Transformation |
|---|---|---|
| `relationships.base_token.data.id` | `coin_id` (FK) | Strip `solana_` prefix |
| `attributes.address` | `pool_address` | Direct — Solana pool address |
| `relationships.dex.data.id` | `dex` | Direct — `"pumpswap"` matches warehouse canonical name |
| (constant) | `source` | `"geckoterminal"` |
| `attributes.pool_created_at` | `created_at` | ISO 8601 UTC string → `datetime.fromisoformat(v)` |

### Conformance contract

Both functions are pure — no side effects, no API calls, no database access. Input is raw API response data. Output is a list of dicts matching PoolMapping fields. Malformed input (missing required fields, unexpected types) raises an exception — fail fast (PDP6). No results for a token is not an error — returns empty list.

---

## DEX Identifier Mapping

All sources use consistent naming for Pumpswap:

| Source | Raw identifier | Canonical (warehouse) |
|---|---|---|
| Dexscreener | `pumpswap` | `pumpswap` |
| GeckoTerminal | `pumpswap` | `pumpswap` |
| DexPaprika (legacy) | `pumpswap` | `pumpswap` |

**Filter rule:** Only store pairs where DEX identifier is `"pumpswap"`. Other identifiers (`pumpfun`/`pump-fun` = bonding curve, `meteora`, `raydium`, `orca`) are excluded at conformance time.

---

## Pool Selection Strategy

When a token has multiple Pumpswap pools (rare but possible):

**Selection:** `PoolMapping.objects.filter(coin_id=mint_address).order_by('created_at').first()` — select the oldest pool by `created_at`.

**Reasoning:** The oldest Pumpswap pool is the graduation pool — created when the token migrated from pump.fun's bonding curve to Pumpswap. Later pools may represent re-listings or community-created pools and do not reflect the token's primary lifecycle.

**Applied at query time, not population time.** If a future source discovers additional Pumpswap pools for a token, they are stored with their own `source` and `created_at`. The query-time selection picks the oldest.

---

## Integration with Orchestrator

Pool mapping is a step in the U-001 pipeline DAG (`pipeline/universes/u001.py`):

| Property | Value |
|---|---|
| Step name | `pool_mapping` |
| Depends on | `discovery` (MigratedCoin rows must exist) |
| Depended on by | `ohlcv` (FL-001 needs pool addresses) |
| Per-coin | No — batch operation (unlike FL-001/FL-002 which are per-coin) |
| Skip condition | Token already has a PoolMapping row |

**Note:** The current `u001.py` config has `per_coin: True` and `source: 'dexpaprika'`. This needs updating to reflect the batch multi-source pattern. The orchestrator's `run_pool_mapping` handler should accept a list of unmapped tokens and execute the fallback chain, not call DexPaprika per-coin.

---

## API Call Budget

| Scenario | Dexscreener calls | GeckoTerminal calls | Total |
|---|---|---|---|
| Full universe (3,674 tokens) | ~123 | ~31 | ~154 |
| Daily new tokens (~272) | ~10 | ~3 | ~13 |
| Single re-run (927 unmapped) | ~31 | ~31 | ~62 |

Both sources are free. The constraint is GeckoTerminal's rate limit (~10 req/min). A full-universe GeckoTerminal fallback stage (~31 calls) takes ~3 minutes. Dexscreener's stage (~123 calls at 300 req/min) completes in under 30 seconds.

---

## Known Limitations

1. **Dexscreener coverage gap.** Covers ~74.8% of universe. Some tokens with valid Pumpswap pools (confirmed on GeckoTerminal) are simply not in Dexscreener's index.
2. **GeckoTerminal `top_pools` risk.** Batch endpoint returns only the highest-liquidity pool. If another DEX has higher liquidity than Pumpswap for a token, the Pumpswap pool may be excluded. Not observed in 4 multi-DEX tests, but theoretically possible.
3. **~5% unmappable tokens.** After both sources, ~5% of tokens may have no pool on any source. These likely never graduated to Pumpswap, graduated too recently to be indexed, or had their liquidity removed.
4. **Pre-Pumpswap tokens.** Tokens that graduated before the Pumpswap era (migrated to Raydium instead) will have no Pumpswap pool mapping and therefore no FL-001 OHLCV data. Intentional scope boundary for U-001.

---

## Open Items

| Item | Status | Impact |
|---|---|---|
| Dexscreener connector | Not started | `pipeline/connectors/dexscreener.py` — batch fetch, filter for pumpswap |
| GeckoTerminal pool discovery connector | Not started | `pipeline/connectors/geckoterminal.py` — add batch pool discovery (separate from existing OHLCV fetch) |
| Dexscreener conformance function | Not started | `pipeline/conformance/u001_pool_mapping_dexscreener.py` |
| GeckoTerminal pool conformance function | Not started | `pipeline/conformance/u001_pool_mapping_geckoterminal.py` |
| Management command rewrite | Not started | Update `populate_pool_mapping` to use fallback chain instead of per-coin DexPaprika |
| Orchestrator config update | Not started | Update `u001.py`: `per_coin: False`, source: multi-source, remove `dexpaprika` |
| Full GeckoTerminal coverage scan | Not done | Only 15-token sample tested. Full scan would verify 80% fill rate at scale. Low priority — will be verified organically during production runs. |
| `top_pools` exclusion handling | Deferred | If production runs reveal tokens where `top_pools` excludes Pumpswap, add per-token endpoint (`/tokens/{address}/pools`) as Stage 2b fallback within GeckoTerminal stage. |
