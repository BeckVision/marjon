# Codebase Rules

Rules that govern how code is written in this project. These are not guidelines — they are enforced invariants. Every rule exists because a violation was caught and corrected.

---

## Rule 1: Single Source of Truth for Paradigm Constants

**Constants defined on warehouse models are the single source of truth.** Any code that needs these values must import and reference them from the warehouse model. Never duplicate them as local constants.

### Paradigm constants (defined on warehouse abstract bases and concrete models):

| Constant | Source of Truth | Meaning |
|---|---|---|
| `TEMPORAL_RESOLUTION` | `FeatureLayerBase` subclass (e.g. `OHLCVCandle.TEMPORAL_RESOLUTION`) | Interval between observations |
| `OBSERVATION_WINDOW_START` | `UniverseBase` subclass (e.g. `MigratedCoin.OBSERVATION_WINDOW_START`) | Start offset from anchor event |
| `OBSERVATION_WINDOW_END` | `UniverseBase` subclass (e.g. `MigratedCoin.OBSERVATION_WINDOW_END`) | End offset from anchor event |
| `UNIVERSE_ID` | `UniverseBase` subclass (e.g. `MigratedCoin.UNIVERSE_ID`) | Universe identifier |
| `LAYER_ID` | `FeatureLayerBase` subclass (e.g. `OHLCVCandle.LAYER_ID`) | Feature layer identifier |
| `REFERENCE_ID` | `ReferenceTableBase` subclass (e.g. `RawTransaction.REFERENCE_ID`) | Reference dataset identifier |
| `AVAILABILITY_RULE` | Any paradigm model | When data becomes visible to a strategy |
| `GAP_HANDLING` | `FeatureLayerBase` subclass | Behavior when no data exists for an interval |

### Why this matters

These constants are **data specification concepts** — they define the contract. They flow through the entire system: PIT enforcement (`.as_of()` uses `TEMPORAL_RESOLUTION`), alignment (resolution mismatch checks), pipeline commands (watermark comparisons, reconciliation), and the data service (layer registry).

If a value is duplicated and one copy changes, the system silently drifts. A connector using a local `timedelta(minutes=5)` instead of `OHLCVCandle.TEMPORAL_RESOLUTION` will break if the resolution ever changes.

### Correct pattern

```python
# In a connector — reference the model constant
from warehouse.models import OHLCVCandle

current_start = last_dt + OHLCVCandle.TEMPORAL_RESOLUTION
```

### Violation pattern

```python
# WRONG — duplicates the constant
CANDLE_INTERVAL = timedelta(minutes=5)
current_start = last_dt + CANDLE_INTERVAL
```

### Where this rule applies

- Pipeline connectors, conformance functions, loaders, management commands
- Data service operations and alignment
- Orchestration configuration

### Where this rule does NOT apply

- Tests — hardcoded values in test assertions and fixtures are acceptable
- Comments and docstrings — descriptive references like "FL-001" in text are fine
- Documentation files

---

## Rule 2: Connectors Import Warehouse Models for Constants Only

The pipeline implementation guide says the source connector is the **anti-corruption layer** — it should not transform data or know the warehouse schema. However, connectors **may** import warehouse models to reference paradigm constants (Rule 1).

### Allowed

```python
from warehouse.models import OHLCVCandle

# Using a paradigm constant for pagination
current_start = last_dt + OHLCVCandle.TEMPORAL_RESOLUTION
```

### Not allowed

```python
from warehouse.models import OHLCVCandle

# Using the model for data transformation — this belongs in conformance
OHLCVCandle.objects.create(...)  # connector should never write
```

The connector's job is to return raw API responses. It uses paradigm constants only for operational decisions (pagination advancement, request parameter construction) — not for data interpretation.

---

## Rule 3: Check Paradigm Docs Before Changing Architecture

Before making any architectural change (moving imports, extracting constants, refactoring layers), read:

1. `docs/data_specification_guide.md` — defines the 7 data specification concepts
2. `docs/pipeline_implementation_guide.md` — defines pipeline layers and their contracts
3. `docs/warehouse_implementation_guide.md` — defines warehouse architecture and decision points

Every architectural decision in this codebase traces back to one of these guides. If a proposed change contradicts a guide, the guide wins unless the user explicitly decides to update the guide.
