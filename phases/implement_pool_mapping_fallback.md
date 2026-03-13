# Implementation Prompt: Pool Mapping Fallback Chain

## A. Context

Marjon is a crypto quantitative research platform (Django + PostgreSQL). The pipeline app fetches market data from external APIs and loads it into the warehouse.

**What exists today:** `populate_pool_mapping` is a management command that discovers Pumpswap pool addresses for graduated pump.fun tokens. It uses DexPaprika's per-token endpoint (1 API call per token, 55% coverage). The command, handler, and universe config are all wired for this per-coin DexPaprika approach.

**What we're building:** Replace DexPaprika with a two-stage fallback chain: Dexscreener batch (primary, ~75% coverage, 300 req/min) then GeckoTerminal batch (fallback, ~80% of remaining misses, ~10 req/min). Both support batches of 30 tokens per call. This is the first multi-source fallback pipeline in the project.

**Spec documents (already read — do NOT re-read these, just follow the specs below):**
- `docs/u001_pool_mapping_pipeline_implementation_record.md`
- `docs/u001_pool_mapping_api_exploration_findings.md`
- `docs/coding_principles.md`

---

## B. Files to Create

### B1. `pipeline/connectors/dexscreener.py` (NEW)

Dexscreener batch pool discovery connector.

```python
"""Dexscreener source connector for pool discovery."""

import logging

from pipeline.connectors.http import request_with_retry

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dexscreener.com"
BATCH_SIZE = 30


def fetch_token_pools_batch(mint_addresses):
    """Fetch pool pairs from Dexscreener for a batch of token addresses.

    Args:
        mint_addresses: List of token mint address strings (max 30).

    Returns:
        Tuple of (records, metadata) where records is the raw JSON
        response (list of pair dicts) and metadata is a dict with
        'api_calls'.
    """
```

**Behavior:**
- Endpoint: `GET /tokens/v1/solana/{comma-separated-addresses}`
- Join addresses with `,` in the URL path
- No auth headers needed
- Response is a flat JSON array of pair objects (NOT wrapped — the JSON parses directly to a list)
- Return `(pair_list, {'api_calls': 1})`
- If `len(mint_addresses) > BATCH_SIZE`, raise `ValueError`
- Use `request_with_retry(url, params={})` — no query params needed

### B2. `pipeline/conformance/u001_pool_mapping_dexscreener.py` (NEW)

Pure conformance function: Dexscreener pair objects → PoolMapping-compatible dicts.

```python
"""Conformance: Dexscreener pairs -> canonical PoolMapping dicts.

Pure function — no side effects, no DB writes, no API calls.
"""

from datetime import datetime, timezone


def conform(raw_pairs):
    """Transform Dexscreener pair objects to PoolMapping-compatible dicts.

    Args:
        raw_pairs: List of pair dicts from the Dexscreener /tokens/v1/ response.

    Returns:
        List of dicts with keys: coin_id, pool_address, dex, source, created_at.
        Only pairs where dexId == "pumpswap" are included.
    """
```

**Field mapping (from spec):**

| Dexscreener field | Output key | Transformation |
|---|---|---|
| `baseToken.address` | `coin_id` | Direct — strict access (KeyError if missing = PDP6) |
| `pairAddress` | `pool_address` | Direct — strict access |
| `dexId` | `dex` | Direct — but only include pairs where `dexId == "pumpswap"` |
| (constant) | `source` | `"dexscreener"` |
| `pairCreatedAt` | `created_at` | `datetime.fromtimestamp(value / 1000, tz=timezone.utc)` |

**Rules:**
- Filter: skip pairs where `dexId != "pumpswap"` (silently — this is expected, not an error)
- Strict access on required fields within pumpswap pairs: `baseToken`, `baseToken.address`, `pairAddress`, `dexId`, `pairCreatedAt` — use `[]` not `.get()`. Missing fields = KeyError = crash (PDP6)
- Pure function — no imports from warehouse or pipeline.connectors

### B3. `pipeline/conformance/u001_pool_mapping_geckoterminal.py` (NEW)

Pure conformance function: GeckoTerminal JSON:API batch response → PoolMapping-compatible dicts.

```python
"""Conformance: GeckoTerminal batch pool response -> canonical PoolMapping dicts.

Pure function — no side effects, no DB writes, no API calls.
Handles JSON:API sideloading: pool details are in included[], referenced
by ID from token objects in data[].
"""

from datetime import datetime, timezone


def conform(raw_response):
    """Transform GeckoTerminal /tokens/multi/ response to PoolMapping-compatible dicts.

    Args:
        raw_response: Full JSON:API response dict with 'data' and 'included' keys.

    Returns:
        List of dicts with keys: coin_id, pool_address, dex, source, created_at.
        Only pools where dex.data.id == "pumpswap" are included.
    """
```

**Parsing logic:**
1. Build a lookup dict from `raw_response['included']` (list of pool objects), keyed by pool `id` (e.g. `"solana_36TATJ..."`)
2. If `'included'` is missing or empty, return `[]` (no pools sideloaded = no results, not an error)
3. For each token in `raw_response['data']`:
   - Get `relationships.top_pools.data` (list of pool refs)
   - For each pool ref, look up the full pool object in the lookup dict
   - Filter: only include pools where `relationships.dex.data.id == "pumpswap"`

**Field mapping (from spec):**

| GeckoTerminal field (on pool object in `included[]`) | Output key | Transformation |
|---|---|---|
| `relationships.base_token.data.id` | `coin_id` | Strip `"solana_"` prefix: `value.replace("solana_", "", 1)` |
| `attributes.address` | `pool_address` | Direct |
| `relationships.dex.data.id` | `dex` | Direct — only include if `== "pumpswap"` |
| (constant) | `source` | `"geckoterminal"` |
| `attributes.pool_created_at` | `created_at` | Parse ISO 8601: handle `Z` suffix → `datetime.fromisoformat(value.replace("Z", "+00:00"))` |

**Rules:**
- Strict access on required fields within pumpswap pools — use `[]` not `.get()` (PDP6)
- Token with empty `top_pools.data` list = no pool found, skip silently (expected)
- Pool ref in `top_pools.data` not found in `included[]` lookup = this shouldn't happen if `include=top_pools` was used; raise `KeyError` (PDP6)
- Pure function — no imports from warehouse or pipeline.connectors

### B4. `pipeline/loaders/u001_pool_mapping.py` (NEW)

Loader for PoolMapping table. Uses upsert pattern (like `u001_universe.py`).

```python
"""Loader for U-001: upsert into PoolMapping table."""

import logging

from django.db import transaction

from warehouse.models import PoolMapping

logger = logging.getLogger(__name__)


def load_pool_mappings(canonical_mappings):
    """Upsert canonical pool mappings into PoolMapping.

    Uses update_or_create on (coin_id, pool_address).

    Args:
        canonical_mappings: List of dicts from conformance. Each dict has:
            coin_id, pool_address, dex, source, created_at.

    Returns:
        Tuple of (created_count, updated_count).
    """
```

**Behavior:**
- Wrap in `transaction.atomic()`
- For each mapping: `PoolMapping.objects.update_or_create(coin_id=m['coin_id'], pool_address=m['pool_address'], defaults={'dex': m['dex'], 'source': m['source'], 'created_at': m['created_at']})`
- Return `(created_count, updated_count)`
- Log totals at the end
- Match the pattern in `pipeline/loaders/u001_universe.py` exactly

---

## C. Files to Modify

### C1. `pipeline/management/commands/populate_pool_mapping.py` (REWRITE)

**Current state:** Takes `--coin` argument, calls DexPaprika `fetch_token_pools` per-coin, filters for pumpswap, does `update_or_create` inline. Has a `populate_pool_mapping_for_coin()` function used by the orchestrator handler.

**New behavior:** Batch fallback chain. No more per-coin operation.

```python
"""Management command to populate pool mappings via Dexscreener/GeckoTerminal fallback chain."""

import logging
import time

from django.core.management.base import BaseCommand

from warehouse.models import MigratedCoin, PoolMapping

logger = logging.getLogger(__name__)

BATCH_SIZE = 30


def get_unmapped_tokens():
    """Return list of mint addresses that have no PoolMapping row."""
    mapped = set(
        PoolMapping.objects.values_list('coin_id', flat=True).distinct()
    )
    all_mints = list(
        MigratedCoin.objects.values_list('mint_address', flat=True)
    )
    return [m for m in all_mints if m not in mapped]


def run_fallback_chain(mint_addresses=None):
    """Execute the Dexscreener → GeckoTerminal fallback chain.

    Args:
        mint_addresses: List of mint addresses to process.
            If None, queries all unmapped tokens.

    Returns:
        dict with 'dexscreener_mapped', 'geckoterminal_mapped',
        'unmapped', 'total_processed', 'api_calls'.
    """
```

**Fallback chain logic (inside `run_fallback_chain`):**

```
1. If mint_addresses is None, call get_unmapped_tokens()
2. If no unmapped tokens, return early with zeros

Stage 1 — Dexscreener:
3. Split mint_addresses into batches of 30
4. For each batch:
   a. Call dexscreener.fetch_token_pools_batch(batch)
   b. Call dexscreener_conform.conform(raw_pairs)
   c. Call loader.load_pool_mappings(canonical)
   d. Track which mint addresses got mapped (from canonical results)
   e. No sleep needed between batches (300 req/min is generous)
5. Compute still_unmapped = original set minus mapped set

Stage 2 — GeckoTerminal (only tokens from Stage 1 misses):
6. Split still_unmapped into batches of 30
7. For each batch:
   a. Call geckoterminal.fetch_token_pools_batch(batch)
   b. Call geckoterminal_conform.conform(raw_response)
   c. Call loader.load_pool_mappings(canonical)
   d. Track which mint addresses got mapped
   e. time.sleep(6) between batches (~10 req/min rate limit)
8. Compute final_unmapped = Stage 1 misses minus Stage 2 mapped

Logging:
9. Log: "Stage 1 (Dexscreener): mapped N of M tokens (K API calls)"
10. Log: "Stage 2 (GeckoTerminal): mapped N of M tokens (K API calls)"
11. Log: "Unmapped after both stages: N tokens"
12. If final_unmapped and len(final_unmapped) <= 20, log the addresses

Return dict with counts.
```

**Imports needed:**
```python
from pipeline.connectors.dexscreener import fetch_token_pools_batch as dex_fetch
from pipeline.connectors.geckoterminal import fetch_token_pools_batch as gt_fetch
from pipeline.conformance.u001_pool_mapping_dexscreener import conform as dex_conform
from pipeline.conformance.u001_pool_mapping_geckoterminal import conform as gt_conform
from pipeline.loaders.u001_pool_mapping import load_pool_mappings
```

**Command class:**
```python
class Command(BaseCommand):
    help = "Populate pool mappings using Dexscreener/GeckoTerminal fallback chain"

    def add_arguments(self, parser):
        parser.add_argument(
            '--coin', type=str, default=None,
            help='Single mint address (bypasses batch — for debugging)',
        )

    def handle(self, *args, **options):
        if options['coin']:
            result = run_fallback_chain([options['coin']])
        else:
            result = run_fallback_chain()

        self.stdout.write(
            f"Dexscreener: {result['dexscreener_mapped']} mapped, "
            f"GeckoTerminal: {result['geckoterminal_mapped']} mapped, "
            f"Unmapped: {result['unmapped']}"
        )
```

### C2. `pipeline/connectors/geckoterminal.py` (ADD function)

**Current state:** Has `fetch_ohlcv(pool_address, start, end)` for OHLCV data. Uses gateway pool rotation, `HEADERS`, `DIRECT_URL`, `_next_base_url()`.

**Add:** A new `fetch_token_pools_batch` function. Do NOT modify any existing code.

```python
def fetch_token_pools_batch(mint_addresses):
    """Fetch pool info from GeckoTerminal for a batch of token addresses.

    Uses the /tokens/multi/ endpoint with include=top_pools to get
    sideloaded pool data in a single call.

    Args:
        mint_addresses: List of token mint address strings (max 30).

    Returns:
        Tuple of (response_dict, metadata) where response_dict is the
        full JSON:API response (with 'data' and 'included' keys) and
        metadata is a dict with 'api_calls'.
    """
```

**Behavior:**
- Endpoint: `GET /api/v2/networks/solana/tokens/multi/{comma-separated-addresses}`
- Query params: `{'include': 'top_pools'}`
- Use `_next_base_url()` for gateway rotation (same as `fetch_ohlcv`)
- Use `request_with_retry(url, params={'include': 'top_pools'}, headers=HEADERS)`
- If `len(mint_addresses) > 30`, raise `ValueError`
- Return `(response_dict, {'api_calls': 1})`
- The response is a dict with `data` (list of token objects) and `included` (list of pool objects). Return the whole dict — conformance handles parsing.

### C3. `pipeline/orchestration/handlers.py` (MODIFY `run_pool_mapping`)

**Current state:**
```python
def run_pool_mapping(coin, config):
    """Populate pool mapping for one coin."""
    from pipeline.management.commands.populate_pool_mapping import (
        populate_pool_mapping_for_coin,
    )
    return populate_pool_mapping_for_coin(coin.mint_address)
```

**New signature and behavior:**
```python
def run_pool_mapping(coins, config):
    """Populate pool mappings for a list of coins using the fallback chain.

    Not per-coin — runs batch discovery for all unmapped coins at once.

    Args:
        coins: List of MigratedCoin instances (unmapped ones).
        config: Universe config dict (unused but kept for handler contract).

    Returns:
        dict with 'dexscreener_mapped', 'geckoterminal_mapped',
        'unmapped', 'total_processed', 'api_calls'.
    """
    from pipeline.management.commands.populate_pool_mapping import (
        run_fallback_chain,
    )
    mint_addresses = [c.mint_address for c in coins]
    return run_fallback_chain(mint_addresses)
```

### C4. `pipeline/universes/u001.py` (MODIFY pool_mapping step)

**Current state:**
```python
{
    'name': 'pool_mapping',
    'handler': 'pipeline.orchestration.handlers.run_pool_mapping',
    'depends_on': 'discovery',
    'per_coin': True,
    'source': 'dexpaprika',
    'rate_limit_sleep': 0.5,
    'skip_if': 'pool_mapping_exists',
},
```

**New state:**
```python
{
    'name': 'pool_mapping',
    'handler': 'pipeline.orchestration.handlers.run_pool_mapping',
    'depends_on': 'discovery',
    'per_coin': False,
    'sources': ['dexscreener', 'geckoterminal'],
    'skip_if': 'pool_mapping_exists',
},
```

Changes:
- `per_coin: True` → `per_coin: False` (batch operation)
- `source: 'dexpaprika'` → `sources: ['dexscreener', 'geckoterminal']`
- Remove `rate_limit_sleep` (handled internally per-source in the command)

### C5. `pipeline/management/commands/orchestrate.py` (MODIFY step execution loop)

**Current code (lines 138-191):**
```python
            # 7. Run each step in dependency order
            for step in steps:
                if not step.get('per_coin', False):
                    continue                          # <-- line 141: skips batch steps

                step_name = step['name']              # <-- line 143: per-coin logic starts
                # ... per-coin loop (lines 143-191, unchanged) ...
```

**Change:** Replace the guard at lines 140-141 (`if not per_coin: continue`) with a branch that handles batch steps, then `continue` past the per-coin logic. The existing per-coin code (lines 143-191) stays exactly as-is.

**After modification, lines 139+ should read:**
```python
            # 7. Run each step in dependency order
            for step in steps:
                step_name = step['name']

                if not step.get('per_coin', False):
                    # --- NEW: batch step handling ---
                    logger.info("Step '%s': batch mode", step_name)
                    self.stdout.write(f"\nStep '{step_name}': batch mode")

                    # Filter to coins that shouldn't be skipped
                    batch_coins = [c for c in coins if not should_skip(c, step)]
                    if not batch_coins:
                        self.stdout.write(f"Step '{step_name}': all coins skipped")
                        continue

                    try:
                        result = call_handler(step['handler'], batch_coins, config)
                        mapped = result.get('dexscreener_mapped', 0) + result.get('geckoterminal_mapped', 0)
                        unmapped = result.get('unmapped', 0)
                        total_succeeded += mapped
                        logger.info(
                            "%s: %d mapped, %d unmapped",
                            step_name, mapped, unmapped,
                        )
                        self.stdout.write(
                            f"Step '{step_name}': {mapped} mapped, {unmapped} unmapped"
                        )
                    except Exception as e:
                        logger.error("%s failed: %s", step_name, e, exc_info=True)
                        self.stderr.write(f"Step '{step_name}' failed: {e}")
                        total_failed += len(batch_coins)
                    continue
                    # --- END batch step handling ---

                # EXISTING per-coin logic below (lines 143-191) — unchanged
                logger.info("Step '%s': %d coins to process", step_name, len(coins))
                # ... rest of per-coin loop stays exactly as-is ...
```

**Key structural point:** `step_name = step['name']` moves up before the branch (it was at line 143 inside the per-coin path). The `if not per_coin` block ends with `continue`, so per-coin code below never runs for batch steps.

---

## D. Conformance Specs (Summary)

### Dexscreener conformance

Input: list of pair dicts (flat JSON array from API).

```python
# For each pair in raw_pairs:
#   if pair['dexId'] != 'pumpswap': skip
#   else: emit {
#       'coin_id': pair['baseToken']['address'],
#       'pool_address': pair['pairAddress'],
#       'dex': pair['dexId'],
#       'source': 'dexscreener',
#       'created_at': datetime.fromtimestamp(pair['pairCreatedAt'] / 1000, tz=timezone.utc),
#   }
```

### GeckoTerminal conformance

Input: full JSON:API response dict.

```python
# 1. Build pool_lookup = {pool['id']: pool for pool in raw_response.get('included', [])}
# 2. For each token in raw_response['data']:
#      For each pool_ref in token['relationships']['top_pools']['data']:
#        pool = pool_lookup[pool_ref['id']]  # KeyError if missing = crash
#        if pool['relationships']['dex']['data']['id'] != 'pumpswap': skip
#        emit {
#            'coin_id': pool['relationships']['base_token']['data']['id'].replace('solana_', '', 1),
#            'pool_address': pool['attributes']['address'],
#            'dex': pool['relationships']['dex']['data']['id'],
#            'source': 'geckoterminal',
#            'created_at': parse_iso(pool['attributes']['pool_created_at']),
#        }
```

---

## E. Tests

### Existing fixtures (use these — do NOT create new fixture files)

| Fixture | Path | Contents |
|---|---|---|
| Dexscreener batch | `pipeline/tests/fixtures/u001/dexscreener_token_pools_sample.json` | 3 pumpswap pairs (MESSI, SMG, ONESHOTTED). All `dexId: "pumpswap"`. |
| GeckoTerminal batch | `pipeline/tests/fixtures/u001/geckoterminal_token_pools_sample.json` | 3 tokens (USIS, HBAD, BACKPACK). USIS has empty `top_pools.data`. HBAD and BACKPACK have pumpswap pools in `included[]`. |

### Test file: `pipeline/tests/test_conformance_pool_mapping.py` (NEW)

Follow the pattern in `pipeline/tests/test_conformance_u001_universe.py` and `pipeline/tests/test_conformance_fl001_gt.py`.

#### Dexscreener conformance tests

```python
class DexscreenerPoolMappingConformanceTest(TestCase):
```

| Test | What it checks |
|---|---|
| `test_record_count` | 3 pairs in fixture → 3 canonical records (all are pumpswap) |
| `test_output_keys` | Each record has exactly: `coin_id`, `pool_address`, `dex`, `source`, `created_at` |
| `test_coin_id_is_base_token_address` | `result[0]['coin_id']` == `"12qKJmoJj9hKs12S8kPhRMrWhsyfqaiEsDh9Z38xpump"` |
| `test_pool_address` | `result[0]['pool_address']` == `"DDfjJU1XXLM84G32y9u4oHjXm6EDRBGaUXucfvALgWyu"` |
| `test_dex_is_pumpswap` | All records have `dex == "pumpswap"` |
| `test_source_is_dexscreener` | All records have `source == "dexscreener"` |
| `test_created_at_is_utc_datetime` | `created_at` is a UTC-aware datetime |
| `test_created_at_millis_conversion` | First pair: `pairCreatedAt: 1772790925000` → `datetime(2026, 3, 4, ...)` (verify exact value) |
| `test_non_pumpswap_pair_excluded` | Feed a pair with `dexId: "meteora"` → empty output |
| `test_mixed_dex_ids_filtered` | Feed 2 pumpswap + 1 meteora → 2 records |
| `test_missing_required_field_crashes` | Pair missing `baseToken` → `KeyError` |
| `test_missing_pair_address_crashes` | Pair missing `pairAddress` → `KeyError` |

#### GeckoTerminal conformance tests

```python
class GeckoTerminalPoolMappingConformanceTest(TestCase):
```

| Test | What it checks |
|---|---|
| `test_record_count` | Fixture has 3 tokens, 2 with pools → 2 canonical records |
| `test_output_keys` | Each record has exactly: `coin_id`, `pool_address`, `dex`, `source`, `created_at` |
| `test_solana_prefix_stripped` | HBAD: `coin_id` == `"12iBz3EMnPb53wUFzYyX7M6b4LpdjBGsDwCzq3Kfpump"` (no `solana_` prefix) |
| `test_pool_address` | HBAD: `pool_address` == `"36TATJSRxW7bzhJP6zcmgDMSSYZUwniY8ya8rvhsyhLp"` |
| `test_dex_is_pumpswap` | All records have `dex == "pumpswap"` |
| `test_source_is_geckoterminal` | All records have `source == "geckoterminal"` |
| `test_created_at_is_utc_datetime` | `created_at` is a UTC-aware datetime |
| `test_created_at_iso_parsing` | HBAD: `pool_created_at: "2026-03-03T02:45:20Z"` → correct datetime |
| `test_token_with_no_pools_skipped` | USIS has `top_pools.data: []` → not in output (not an error) |
| `test_empty_included_returns_empty` | Response with `data` but no `included` → `[]` |
| `test_non_pumpswap_pool_excluded` | Pool with `dex.data.id: "meteora"` → excluded |
| `test_missing_pool_in_included_crashes` | Pool ref in `top_pools.data` not found in `included` → `KeyError` |

### Test file: `pipeline/tests/test_loader_pool_mapping.py` (NEW)

Follow the pattern in `pipeline/tests/test_loader_u001_universe.py`.

```python
class PoolMappingLoaderTest(TestCase):
```

| Test | What it checks |
|---|---|
| `test_create_new_mapping` | Load 1 mapping → `created=1, updated=0`. Verify all fields in DB. |
| `test_upsert_updates_existing` | Create mapping, then load same `(coin_id, pool_address)` with different source → `updated=1`. |
| `test_source_field_set_correctly` | Load with `source="dexscreener"` → DB row has `source="dexscreener"` |
| `test_batch_return_counts` | Load 2 new + 1 existing → `created=2, updated=1` |

**setUp:** Create `MigratedCoin` instances for FK constraint (same pattern as `FL001LoaderTest`).

### Test file: `pipeline/tests/test_pool_mapping_fallback.py` (NEW)

Integration test for the full fallback chain. Mock both connectors.

```python
class FallbackChainTest(TestCase):
```

**setUp:** Create 5 `MigratedCoin` rows with distinct mint addresses.

| Test | What it checks |
|---|---|
| `test_stage1_maps_some_tokens` | Mock `dexscreener.fetch_token_pools_batch` to return 3 pumpswap pairs. Mock `geckoterminal.fetch_token_pools_batch` to return 1 pool. Call `run_fallback_chain`. Verify: `dexscreener_mapped=3`, `geckoterminal_mapped=1`, `unmapped=1`. |
| `test_stage2_only_receives_misses` | Mock both connectors. Verify GeckoTerminal connector is called with ONLY the mint addresses that Dexscreener missed (not the full list). |
| `test_all_mapped_by_stage1_skips_stage2` | Mock Dexscreener to return all 5. Verify GeckoTerminal connector is NOT called. |
| `test_no_unmapped_tokens_returns_early` | All 5 coins already have PoolMapping rows. Call `run_fallback_chain()` (no args). Verify neither connector is called. |
| `test_db_rows_created` | After full chain, verify `PoolMapping.objects.count()` matches expected. Verify source field is correct per row. |

**Mock pattern** (follow existing tests in `test_orchestrate.py`):
```python
@patch('pipeline.management.commands.populate_pool_mapping.dex_fetch')
@patch('pipeline.management.commands.populate_pool_mapping.gt_fetch')
def test_...(self, mock_gt, mock_dex):
```

Mock return values should match the `(records, metadata)` tuple contract. Use inline dicts matching fixture shapes — don't load fixture files for mock data.

---

## F. Rules

1. **Read `docs/coding_principles.md` patterns.** Every file follows them.
2. **Conformance functions are pure.** No DB, no API, no side effects. Only stdlib imports.
3. **Connectors return `(records, metadata)` tuple.** This is the contract (see `coding_principles.md` section 5). `metadata` always has at least `'api_calls'`.
4. **Fail fast on malformed data (PDP6).** Use `[]` access, not `.get()`, for required fields. Missing field → `KeyError` → crash. No silent skipping.
5. **Filter non-pumpswap pairs silently.** This is expected behavior, not an error. Multi-DEX tokens are common.
6. **Don't break existing tests.** The existing `RunPoolMappingHandlerTest` in `test_orchestrate.py` will need updating because `run_pool_mapping`'s signature changes from `(coin, config)` to `(coins, config)`. Update it to pass a list.
7. **Run all tests after implementation:** `python manage.py test pipeline`
8. **PoolMapping model fields** (from `warehouse/models.py:329-346`):
   - `coin` — FK to MigratedCoin via `mint_address` (use `coin_id` for the string key)
   - `pool_address` — CharField(max_length=50)
   - `dex` — CharField(max_length=50)
   - `source` — CharField(max_length=50)
   - `created_at` — DateTimeField(null=True)
   - `discovered_at` — DateTimeField(auto_now_add=True)
   - `unique_together = [('coin', 'pool_address')]`
9. **Import paths are real.** Every import path in this doc exists in the codebase or is marked as NEW.
10. **No changes to `warehouse/models.py`.** The PoolMapping model already has all needed fields.
11. **No changes to `pipeline/connectors/http.py`.** The shared `request_with_retry` handles retries, 429s, and backoff.
12. **Gateway rotation** — the GeckoTerminal connector already has `_next_base_url()` for IP rotation. Use it for the new batch function. Dexscreener has no rate limit concerns (300 req/min), so direct URL is fine.

---

## G. Implementation Order

1. `pipeline/connectors/dexscreener.py` (new file)
2. `pipeline/conformance/u001_pool_mapping_dexscreener.py` (new file)
3. `pipeline/conformance/u001_pool_mapping_geckoterminal.py` (new file)
4. `pipeline/loaders/u001_pool_mapping.py` (new file)
5. `pipeline/connectors/geckoterminal.py` (add `fetch_token_pools_batch`)
6. `pipeline/management/commands/populate_pool_mapping.py` (rewrite)
7. `pipeline/orchestration/handlers.py` (modify `run_pool_mapping`)
8. `pipeline/universes/u001.py` (modify pool_mapping step config)
9. `pipeline/management/commands/orchestrate.py` (add batch step handling)
10. `pipeline/tests/test_conformance_pool_mapping.py` (new file)
11. `pipeline/tests/test_loader_pool_mapping.py` (new file)
12. `pipeline/tests/test_pool_mapping_fallback.py` (new file)
13. Update `RunPoolMappingHandlerTest` in `pipeline/tests/test_orchestrate.py`
14. Run `python manage.py test pipeline` — all tests must pass
