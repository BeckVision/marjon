# Phase 4: Data Service

## Context Header

**Project:** marjon — crypto quantitative research platform
**Prerequisite:** Phase 2 or Phase 3 (needs real data in the database to test against). Curriculum Lesson 2 (expected value + NumPy) recommended — the data service output is what you'll analyze.
**Architecture:** Data service lives in `data_service/` Django app. Three read-only operations form the narrow interface. All consumers read data through these functions — never directly from warehouse models. The data service enforces PIT semantics and cross-layer alignment.
**Key reference docs (all in `docs/`):**
- `warehouse_implementation_guide.md` — Part 5 (QuerySet layer, `.as_of()` design), Part 6 (data service operations, 5-step query pipeline, service contract)
- `u001_dataset_implementation_record.md` — WDP9 (interval-start timestamp convention), WDP10 (alignment in application code), WDP11 (derived features on-the-fly), WDP12 (trust constraints), WDP13 (error handling)
- `u001_data_specification.md` — JK-001 (inner join, forward-fill, staleness field), PIT-001 (end-of-interval availability), DQ constraints

**Naming convention:** Data service is universe-agnostic — it operates on any models that inherit from the paradigm abstract bases.

---

## Goal

The three read-only operations that form the narrow interface. PIT enforcement, cross-layer alignment with forward-fill and staleness fields, and error handling per the service contract. Every consumer reads data through these functions — never directly from warehouse models.

**What you can DO after this phase:** Call `get_panel_slice(["asset_A", "asset_B"], ["FL-001", "FL-002"], simulation_time=datetime(...))` and receive a merged panel where each row has data from all requested layers for the same asset at the same timestamp, with PIT enforced (no future data leaking in). Call `get_universe_members(simulation_time)` and see only assets that were members by that time.

---

## Files/Code Produced

| Output | Description |
|---|---|
| `data_service/operations.py` | Three functions: `get_panel_slice()`, `get_universe_members()`, `get_reference_data()` |
| `data_service/alignment.py` | Forward-fill logic with staleness field computation |
| `data_service/tests/test_operations.py` | Tests for all three operations |
| `data_service/tests/test_alignment.py` | Tests for forward-fill and staleness edge cases |
| `data_service/tests/test_pit.py` | Tests specifically verifying PIT enforcement |

---

## Session 4.A — Brainstorm: Data Service Design (2-3 hours)

| # | Task | Reference |
|---|---|---|
| 4.A.1 | Design `get_universe_members(simulation_time)` interface. Input: simulation_time (datetime). Output: QuerySet of MigratedCoin rows where `anchor_event <= simulation_time AND (membership_end IS NULL OR membership_end > simulation_time)`. This is a thin wrapper around the `.as_of()` QuerySet written in Phase 1. For universes with permanent membership (membership_end always NULL), this simplifies to `anchor_event <= simulation_time`. | warehouse_implementation_guide.md Part 6 "Operation 2": two steps — time filter, return. |
| 4.A.2 | Design `get_panel_slice(coins, layers, simulation_time)` interface. Input: list of mint_addresses, list of layer IDs (e.g. `["FL-001", "FL-002"]`), simulation_time. Output: list of dicts (or DataFrame) in wide format — one row per coin per timestamp, with columns from all requested layers. Five-step query pipeline: (1) Scope — validate coins exist in MigratedCoin and time range is within observation window, raise error if not (WDP13). (2) Fetch — pull rows from each requested layer table independently. (3) Time filter — apply `.as_of(simulation_time)` to each layer's results. (4) Align — join layers on coin + timestamp, forward-fill if resolution mismatch, attach staleness fields, enforce inner join. (5) Return. **Critical:** time filter BEFORE alignment. | warehouse_implementation_guide.md Part 6 "Operation 1" — the 5-step pipeline. u001_dataset_implementation_record.md WDP10 (alignment in Python), WDP13 (error handling). |
| 4.A.3 | Design alignment logic. When all requested layers share the same temporal resolution, forward-fill is not needed. But the alignment function must: (a) match rows from all requested layers on asset + timestamp (inner join — JK-001), (b) handle the case where one layer has a row at timestamp T but another doesn't — inner join drops that row, (c) produce wide-format output with columns from all layers. When layers with different resolutions are added later, forward-fill activates: carry last known value from slower layer, attach `{layer_id}_{short_name}_staleness_minutes` field (0 for actual, increasing for filled). | u001_data_specification.md JK-001: inner join, row-level existence, forward-fill with staleness field naming convention `{layer_id}_{short_name}_staleness_minutes`. u001_dataset_implementation_record.md WDP10: application code (Python). |
| 4.A.4 | Design `get_reference_data(coin, start, end, simulation_time)` interface. Input: mint_address, time range, simulation_time. Output: QuerySet of RawTransaction rows where `timestamp >= start AND timestamp <= end AND timestamp <= simulation_time` (event-time PIT). Validates coin exists and time range is within observation window. Returns empty if valid request with no data. Raises error if coin not in universe or time range outside window. | warehouse_implementation_guide.md Part 6 "Operation 3". RD-001 is version 0.1 with TBD feature set — this is a stub for now. |
| 4.A.5 | Design service contract enforcement. Four guarantees the code must deliver: (1) Temporal safety — every row has knowledge time <= simulation_time (guaranteed by `.as_of()`). (2) Alignment completeness — no partial rows (guaranteed by inner join). (3) Staleness transparency — forward-filled values have staleness field (guaranteed by alignment function). (4) Data integrity — every row passed gate checks (guaranteed by trusting the warehouse, WDP12 Option C). Three exclusions: completeness, formula correctness, freshness — the service does NOT fill gaps, guarantee formulas are correct, or guarantee data is fresh. | warehouse_implementation_guide.md "Service Contract" — four guarantees, three exclusions |
| 4.A.6 | Design error handling. Two categories per WDP13: (1) Invalid request (asset not in universe, time range outside window) → raise `ValueError` with descriptive message. (2) Valid request, no data → return empty result (empty list/DataFrame). These are different outcomes — don't conflate them. | u001_dataset_implementation_record.md WDP13 |

---

## Session 4.B — Implementation: Universe Members + Reference Data (1-2 hours)

Start with the two simpler operations (single-table, no cross-table logic).

| # | Task | Notes |
|---|---|---|
| 4.B.1 | Write `get_universe_members(simulation_time)`. Call `MigratedCoin.objects.as_of(simulation_time)`. Return the QuerySet. | Thin wrapper around the Phase 1 QuerySet. One line of real logic. |
| 4.B.2 | Write `get_reference_data(coin, start, end, simulation_time)`. Step 1: validate `MigratedCoin.objects.filter(mint_address=coin).exists()` — raise if not. Validate time range against observation window — raise if outside. Step 2: `RawTransaction.objects.filter(coin=coin, timestamp__gte=start, timestamp__lte=end).as_of(simulation_time)`. Step 3: return QuerySet ordered by timestamp. | RD-001 is a stub — this operation will work but return empty until RD-001 has data |
| 4.B.3 | Write tests for `get_universe_members`. Setup: create 3 universe members with different anchor_events (e.g., 10:00, 11:00, 12:00). Assert: at simulation_time=10:30, only asset 1 is returned. At 11:30, assets 1 and 2. At 12:30, all three. | The core PIT correctness test for universes |
| 4.B.4 | Write tests for error handling. Call `get_reference_data` with an asset not in the universe — assert raises ValueError. Call with valid asset but no data — assert returns empty. | WDP13 error handling |

---

## Session 4.C — Implementation: Panel Slice + Alignment (2-3 hours)

The main operation. This is where cross-table logic lives.

| # | Task | Notes |
|---|---|---|
| 4.C.1 | Write the scope validation step. Given a list of mint_addresses: check each exists in MigratedCoin, raise if any don't. Given simulation_time: for each asset, check it falls within the asset's observation window (anchor_event + OBSERVATION_WINDOW_START to anchor_event + OBSERVATION_WINDOW_END), raise if outside. | WDP13: invalid request → raise error |
| 4.C.2 | Write the fetch step. For each requested layer (mapped from layer ID to model class, e.g., `"FL-001" → OHLCVCandle`), query `Model.objects.filter(coin__in=coins)`. Return a dict of `{layer_id: QuerySet}`. | Step 2 of the 5-step pipeline |
| 4.C.3 | Write the time filter step. For each layer's QuerySet, apply `.as_of(simulation_time)`. Return filtered QuerySets. **This must happen before alignment** — if alignment ran first, forward-fill could carry values from future rows before PIT removes them. | Step 3. warehouse_implementation_guide.md: "Critical ordering: Time filtering happens before alignment." |
| 4.C.4 | Write the alignment function in `data_service/alignment.py`. Input: dict of `{layer_id: list_of_rows}` (rows as dicts with `coin` and `timestamp` keys plus feature columns). Output: list of merged dicts in wide format. Logic: group rows by (coin, timestamp). For each (coin, timestamp) pair, check if ALL requested layers have a row (inner join). If yes, merge columns from all layers into one dict. If any layer is missing, drop the row. Prefix columns with layer short name to avoid collisions if needed (e.g., `fl001_open_price`, `fl002_total_holders`), or keep flat if column names are already unique. | JK-001: inner join, row-level existence. Current FL-001 and FL-002 have no column name collisions, so flat merge works. |
| 4.C.5 | Write the forward-fill stub. When all requested layers share the same temporal resolution, forward-fill is never triggered. But the alignment function should check: if layers have different `TEMPORAL_RESOLUTION`, activate forward-fill from the slower layer. Carry last known value, attach `{layer_id}_{short_name}_staleness_minutes` field (0 for actual observations, +5 for each filled interval). For now, this path logs a warning and returns without filling (no current use case). | JK-001 resolution mismatch rule. Not needed yet but the hook should exist. |
| 4.C.6 | Wire it together: `get_panel_slice(coins, layer_ids, simulation_time)` calls scope → fetch → time filter → align → return. | Step 5 |

---

## Session 4.D — Implementation: PIT Tests + Integration Tests (2-3 hours)

| # | Task | Notes |
|---|---|---|
| 4.D.1 | Write PIT enforcement test for feature layers. Setup: create a MigratedCoin with anchor_event at 10:00. Create OHLCVCandles at timestamps 10:00 (covers 10:00-10:05), 10:05 (covers 10:05-10:10), 10:10 (covers 10:10-10:15). Test: `get_panel_slice` at simulation_time=10:08 should return only the 10:00 candle (its interval 10:00-10:05 closed at 10:05, which is <= 10:08). The 10:05 candle (interval closes at 10:10) should NOT be visible. The 10:10 candle should NOT be visible. This is the end-of-interval PIT rule with interval-start timestamp convention (WDP9). | **The single most important test.** Interval end = timestamp + TEMPORAL_RESOLUTION (5 min). Candle at 10:05 has interval end = 10:10, which is > simulation_time 10:08, so it's filtered out. |
| 4.D.2 | Write alignment test. Setup: create an asset with both FL-001 and FL-002 data. FL-001 has candles at 10:00, 10:05, 10:10. FL-002 has snapshots at 10:00, 10:05 (but NOT 10:10). Test: `get_panel_slice` requesting both layers returns only 2 rows (10:00 and 10:05) — inner join drops 10:10 because FL-002 is missing. Each row has columns from both layers. | JK-001 inner join |
| 4.D.3 | Write empty-result test. Create an asset that joined the universe but has no feature layer data. `get_panel_slice` returns empty, not an error. | WDP13: valid request, no data → empty |
| 4.D.4 | Write scope validation test. Call `get_panel_slice` with an asset that doesn't exist in the universe. Assert raises ValueError. | WDP13: invalid request → error |
| 4.D.5 | Run full test suite: `python manage.py test data_service`. | |

---

## Verification Criteria

After all sessions are complete:

- [ ] `get_universe_members(simulation_time)` returns correct subset of coins based on anchor_event
- [ ] `get_panel_slice` at simulation_time=10:08 does NOT show the 10:05 candle (interval end 10:10 > 10:08)
- [ ] `get_panel_slice` at simulation_time=10:10 DOES show the 10:05 candle (interval end 10:10 <= 10:10)
- [ ] Requesting both FL-001 and FL-002 produces an inner join — rows missing from either layer are dropped
- [ ] `get_panel_slice` with nonexistent asset raises ValueError
- [ ] `get_panel_slice` with valid asset but no data returns empty (not an error)
- [ ] `get_reference_data` with nonexistent asset raises ValueError
- [ ] `get_reference_data` with valid asset but no RawTransaction data returns empty
- [ ] Forward-fill stub exists but logs a warning (no current use case)
- [ ] All tests pass: `python manage.py test data_service`

---

## Known Risks

| Risk | Impact | Mitigation |
|---|---|---|
| `FeatureLayerQuerySet.as_of()` interval arithmetic is wrong | PIT enforcement silently leaks future data | Test 4.D.1 is specifically designed to catch this. The test values (10:00, 10:05, 10:08, 10:10) are chosen to expose off-by-one errors. |
| Layer ID → model class mapping is hardcoded | Adding new layers requires code changes | Acceptable for now. A registry pattern can be added later. Keep the mapping in one place (e.g., a dict in `operations.py`). |
| Alignment in Python (WDP10) is slow for large datasets | Performance bottleneck at scale | Acceptable at initial scale (~1000 rows per asset). Profile if performance becomes an issue. WDP10 chose Python over SQL for flexibility. |
| Time filter before alignment ordering is easy to accidentally reverse | Forward-fill could carry future data | Code review: the 5-step pipeline must be implemented in exact order. Add a comment at the call site explaining why. |
| Forward-fill stub may rot if never tested | Breaks when first used for resolution mismatch | Write a minimal unit test with synthetic data of different resolutions, even though no current layer triggers it. |

---

## Estimated Effort

4 sessions: 1 brainstorm (2-3h) + 3 implementation (1-3h each).
