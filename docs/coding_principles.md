# marjon — Coding Principles

Reference for all implementation work. These principles are non-negotiable — every prompt, every module, every function follows them.

---

## 1. Separation of Concerns

Each app, module, and layer has one responsibility. Nothing crosses boundaries.

| Layer | Responsibility | Does NOT do |
|---|---|---|
| `warehouse/` | Store data (models, constraints, managers) | Read for consumers, fetch from APIs |
| `data_service/` | Read data for consumers (PIT queries, panel slices) | Write data, call APIs |
| `pipeline/` | Fetch and load data (connectors, conformance, loaders) | Serve data to consumers |
| `pipeline/connectors/` | Talk to external APIs | Transform data, write to DB |
| `pipeline/conformance/` | Transform raw → canonical format | Talk to APIs, write to DB |
| `pipeline/loaders/` | Write canonical records to warehouse | Talk to APIs, transform data |
| `pipeline/orchestration/` | Chain steps, track progress | Implement pipeline logic |

## 2. Single Responsibility Principle (SRP)

One module, one job. One function, one purpose. One reason to change.

- Each connector talks to ONE API
- Each conformance function transforms ONE source's data format
- Each loader writes to ONE table
- Each management command runs ONE pipeline step
- Each handler wraps ONE command's logic

If a function does two things, split it.

## 3. Don't Repeat Yourself (DRY)

Shared behavior lives in abstract bases. Dataset-specific choices live in concrete models.

| Abstract base | Defines | Concrete models add |
|---|---|---|
| `UniverseBase` | anchor_event, ingested_at, membership_end | FK to specific entity, metadata fields |
| `FeatureLayerBase` | timestamp, ingested_at, clean() | Feature columns (prices, holders, etc.) |
| `ReferenceTableBase` | timestamp, ingested_at | Reference-specific fields |
| `PipelineRunBase` | status, timestamps, records, CU tracking | FK to specific universe model |
| `PipelineStatusBase` | status, watermark, last_error | FK to specific universe + run models |

Never duplicate field definitions across concrete models. If two models share a field, it belongs in the base.

## 4. Open/Closed Principle

Open for extension, closed for modification. Adding a new universe or feature layer requires ZERO changes to existing code.

- Adding U-002: create new concrete models, new config file in `pipeline/universes/`, new connectors. Don't touch U-001 code.
- Adding FL-003: create new model inheriting FeatureLayerBase, new conformance function, new loader. Don't touch FL-001 or FL-002.
- Adding a new API source: create new connector. Don't touch existing connectors.

If adding something requires modifying existing working code, the abstraction is wrong.

## 5. Adapter Pattern

Each API connector is an adapter — it translates external API conventions into a standard internal interface. Nothing downstream knows which API the data came from.

```
GeckoTerminal API → geckoterminal.py → (records, metadata) → conformance → canonical dicts → loader
Moralis API       → moralis.py       → (records, metadata) → conformance → canonical dicts → loader
```

Switching sources = change the import. Loader doesn't change. Orchestrator doesn't change. Models don't change.

All connectors return the same tuple: `(records, metadata_dict)`. This is the contract.

## 6. Pure Functions (Conformance)

Conformance functions are pure: no side effects, no DB access, no API calls, no logging that changes state.

```python
# YES — pure function
def conform_geckoterminal_fl001(raw_candles, mint_address):
    return [transform(candle) for candle in raw_candles]

# NO — side effects
def conform(raw_candles, mint_address):
    results = [transform(candle) for candle in raw_candles]
    logger.info(f"Transformed {len(results)}")  # logging is OK
    save_to_db(results)  # NOT OK — side effect
    return results
```

Why: pure functions are independently testable. Feed saved fixtures in, verify output. No mocking required.

## 7. Fail Fast (PDP6)

Malformed data crashes immediately. Silent skipping is more dangerous than blocked runs.

```python
# YES — crash on unexpected data
'mint_address': raw['tokenAddress'],  # KeyError if missing = crash = good

# NO — silently skip
'mint_address': raw.get('tokenAddress', None),  # None propagates silently = bad
```

Exceptions:
- Fields documented as nullable in the API schema → handle None gracefully
- Network errors → retry with backoff, THEN crash
- Per-coin errors in batch orchestration → log, mark ERROR, continue to next coin

## 8. Idempotency

Every pipeline step is safe to re-run with the same inputs. No duplicate rows, no corrupted data.

| Table type | Mechanism | Why |
|---|---|---|
| Universe (MigratedCoin) | Upsert on mint_address | Master data allows updates |
| Feature layer (OHLCVCandle) | Delete-write per coin per time range | Append-only, clean replacement |
| Dimension (PoolMapping) | Upsert on mint_address | Lookup data allows updates |
| Pipeline tracking | Append (new row per attempt) | Full history, never overwrite |

## 9. Configuration over Code

Pipeline behavior is driven by config, not hardcoded logic.

```python
# pipeline/universes/u001.py — the truth about U-001's pipeline
UNIVERSE = {
    'id': 'U-001',
    'steps': [
        {'name': 'pool_mapping', 'depends_on': 'discovery', 'skip_if': 'pool_mapping_exists'},
        {'name': 'ohlcv', 'depends_on': 'pool_mapping', 'skip_if': 'window_complete_or_immature'},
    ]
}
```

The orchestrator reads config and executes. Adding a step = add a dict. Removing a step = delete a dict. No code changes.

## 10. Defense in Depth

Three layers of validation. Each catches different problems.

| Layer | What it catches | Mechanism |
|---|---|---|
| Conformance (Python) | API contract changes, type mismatches, missing fields | Pure function crashes on unexpected input |
| Loader (Python) | Empty canonical records, business logic violations | ValueError on empty input, Django model validation |
| Database (PostgreSQL) | Data integrity violations | CHECK constraints (high>=low, volume>=0), unique_together, FK constraints |

Never rely on one layer alone. If conformance misses something, the database catches it.

## 11. Paradigm vs Dataset Separation

The most important architectural principle in marjon. NEVER embed dataset-specific assumptions into paradigm-level infrastructure.

| | Paradigm-level | Dataset-specific |
|---|---|---|
| **Language** | "entity", "universe", "feature layer", "observation" | "coin", "mint_address", "pump.fun", "Pumpswap" |
| **Files** | `data_specification_guide.md`, `warehouse_implementation_guide.md`, `pipeline_implementation_guide.md` | `u001_*.md`, `pipeline/universes/u001.py` |
| **Code** | Abstract bases, orchestrator, data service operations | Concrete models, connectors, conformance functions |
| **Test** | Does another universe (U-002) work without modifying this? | Is this specific to U-001's data sources? |

**The test:** If you see `coin`, `mint_address`, `pump`, `pumpswap`, `FL-001`, or `FL-002` in a paradigm-level file, it's a paradigm leak. Fix it.

## 12. Explore → Document → Spec → Implement

Never write code before understanding the data. The pattern for every new data source:

```
1. API Exploration     → u001_*_api_exploration_findings.md (raw responses, field analysis, quirks)
2. Pipeline Record     → u001_*_pipeline_implementation_record.md (11 PDPs decided, conformance mapping)
3. Implementation      → connector, conformance, loader, command, tests
4. End-to-end test     → real data through the full chain
```

Skipping step 1 caused the `inversed=true` bug ($86 prices instead of $0.0006).
Skipping step 2 caused the missing universe discovery pipeline (5 chats before anyone noticed).

---

## Quick Reference: When writing new code

Ask yourself:

1. **Does this module do ONE thing?** If not, split it.
2. **Could another universe use this?** If yes, it belongs in a paradigm-level abstraction. If no, it's dataset-specific.
3. **Is this function pure?** If it's conformance, it must be. No DB, no API, no side effects.
4. **Is this safe to re-run?** If not, the idempotency mechanism is wrong.
5. **What happens with bad input?** It should crash, not silently produce wrong data.
6. **Did I explore the API first?** If not, stop coding and explore.