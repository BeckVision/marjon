# Phase 1: Django Project + Concrete Models

## Context Header

**Project:** marjon — crypto quantitative research platform
**Architecture:** Two Django apps: `warehouse` (all models, QuerySets, constraints) + `data_service` (three read-only operations, no models)
**Key reference docs (all in `docs/`):**
- `u001_dataset_implementation_record.md` — all 13 WDP selections, per-definition constants, data types, models summary
- `u001_data_specification.md` — universe definition, feature layers FL-001/FL-002, join key JK-001, PIT-001, DQ constraints DQ-001 through DQ-006
- `warehouse_implementation_guide.md` — paradigm guide for warehouse architecture, 13 decision points, QuerySet design, data service design
- `u001_fl001_pipeline_implementation_record.md` — pool mapping dimension table schema (needed as a model in this phase)
- `u001_fl002_pipeline_implementation_record.md` — FL-002 field list (needed for HolderSnapshot model)
- `models.py` — abstract base models sketch (UniverseBase, FeatureLayerBase, ReferenceTableBase)

**Naming convention:** Files prefixed `u001_` are universe-scoped. Files prefixed `u001_fl001_` or `u001_fl002_` are feature-layer-scoped.

---

## Goal

A running Django project with `warehouse` and `data_service` apps. All concrete models for the first universe created with correct field types, constraints, and per-definition constants. Custom QuerySets with `.as_of()` PIT enforcement. Empty tables in PostgreSQL. Data service app exists as a placeholder.

**What you can DO after this phase:** `python manage.py migrate` and see the schema. Create model instances in the shell. Verify CHECK constraints reject bad data (`high_price < low_price` fails). Verify `unique_together` prevents duplicate candles.

---

## Files/Code Produced

| Output | Description |
|---|---|
| `marjon/` Django project | Project config, settings, wsgi |
| `warehouse/models.py` | Abstract bases + concrete models: `MigratedCoin`, `OHLCVCandle`, `HolderSnapshot`, `RawTransaction`, `PoolMapping` |
| `warehouse/managers.py` | Custom QuerySets with `.as_of()` for each abstract base category |
| `data_service/` app | Placeholder — three function signatures, no implementation |
| Database migrations | Initial migration creating all tables |

---

## Session 1.A — Brainstorm: Model Design Review (1-2 hours)

Walk through every concrete model before writing code. Confirm field types, constraints, and per-definition constants against the documentation.

| # | Task | Reference |
|---|---|---|
| 1.A.1 | Review MigratedCoin model design. Fields: `mint_address` (CharField, max_length=50, unique), `anchor_event` (inherited from UniverseBase), `membership_end` (inherited from UniverseBase), `ingested_at` (DateTimeField, auto_now_add). Per-definition constants from u001_dataset_implementation_record.md: `UNIVERSE_ID="U-001"`, `UNIVERSE_TYPE="event-driven"`, `OBSERVATION_WINDOW_START=timedelta(0)`, `OBSERVATION_WINDOW_END=timedelta(minutes=5000)`, etc. | u001_dataset_implementation_record.md "Per-Definition Constants: MigratedCoin" |
| 1.A.2 | Review OHLCVCandle model design. FK to MigratedCoin via `to_field="mint_address"` (WDP2). Feature columns: `open_price`, `high_price`, `low_price`, `close_price` (DecimalField, max_digits=38, decimal_places=18), `volume` (DecimalField, high precision). All nullable (WDP3). `timestamp` inherited from FeatureLayerBase. `unique_together = [("coin", "timestamp")]` (DQ-001). CHECK constraints: `high_price >= low_price` (DQ-002), `open_price` and `close_price` between low/high (DQ-003), `volume >= 0` (DQ-004). `clean()` for DQ-005 (timestamp within observation window, cross-table check against MigratedCoin.anchor_event). | u001_dataset_implementation_record.md (WDP1-WDP9), u001_data_specification.md (DQ constraints) |
| 1.A.3 | Review HolderSnapshot model design. FK to MigratedCoin via `to_field="mint_address"`. 20+ fields: `total_holders`, `net_holder_change` (BigIntegerField), `holder_percent_change` (DecimalField), 3 acquisition method fields, 14 size tier fields (7 `holders_in_*`, 7 `holders_out_*`). All BigIntegerField except `holder_percent_change`. `unique_together = [("coin", "timestamp")]`. | u001_fl002_pipeline_implementation_record.md "Field Mapping Table", u001_data_specification.md (FL-002) |
| 1.A.4 | Review PoolMapping dimension table design. Not inheriting from any paradigm base. Fields: `mint_address` (FK to MigratedCoin), `pool_address` (CharField, max_length=50), `dex` (CharField), `source` (CharField), `created_at` (DateTimeField), `discovered_at` (DateTimeField). | u001_fl001_pipeline_implementation_record.md "Pool Mapping Dimension Table" |
| 1.A.5 | Review `.as_of()` QuerySet design. Three implementations: (1) UniverseBase: `anchor_event <= T AND (membership_end IS NULL OR membership_end > T)`. (2) FeatureLayerBase (end-of-interval): `timestamp + TEMPORAL_RESOLUTION <= T` (since WDP9 chose interval-start convention, interval end = timestamp + resolution). (3) ReferenceTableBase (event-time): `timestamp <= T`. | warehouse_implementation_guide.md Part 5, u001_dataset_implementation_record.md (WDP9: interval start) |

---

## Session 1.B — Implementation: Project Scaffolding + Abstract Bases (1-2 hours)

| # | Task | Notes |
|---|---|---|
| 1.B.1 | `django-admin startproject marjon`. Configure PostgreSQL in settings. `USE_TZ = True`. | |
| 1.B.2 | `python manage.py startapp warehouse` and `python manage.py startapp data_service`. Add to `INSTALLED_APPS`. | Two-app structure (architecture decision A1) |
| 1.B.3 | Copy the three abstract bases from `models.py` sketch into `warehouse/models.py`. Verify: `UniverseBase` has `anchor_event` (DateTimeField, null=True) and `membership_end` (DateTimeField, null=True). `FeatureLayerBase` has `timestamp` (DateTimeField) and timestamp index. `ReferenceTableBase` has `timestamp` and timestamp index. All are `abstract = True`. | models.py (current sketch) — already written |

---

## Session 1.C — Implementation: Concrete Models (2-3 hours)

| # | Task | Notes |
|---|---|---|
| 1.C.1 | Write `MigratedCoin(UniverseBase)`. Set all 8 per-definition constants. Add `mint_address = CharField(max_length=50, unique=True)`. Add `ingested_at = DateTimeField(auto_now_add=True)`. | Constants: UNIVERSE_ID, NAME, INCLUSION_CRITERIA, UNIVERSE_TYPE, OBSERVATION_WINDOW_START, OBSERVATION_WINDOW_END, EXCLUSION_CRITERIA, VERSION |
| 1.C.2 | Write `OHLCVCandle(FeatureLayerBase)`. Set all 9 per-definition constants. Add FK: `coin = ForeignKey(MigratedCoin, to_field="mint_address", on_delete=CASCADE)`. Add 5 feature fields (4 price DecimalFields + 1 volume DecimalField), all `null=True`. Add `ingested_at`. Add `Meta.constraints` for DQ-002/003/004. Add `Meta.unique_together = [("coin", "timestamp")]`. Add `clean()` for DQ-005. | Constants: LAYER_ID, UNIVERSE_ID, NAME, TEMPORAL_RESOLUTION=timedelta(minutes=5), AVAILABILITY_RULE="end-of-interval", GAP_HANDLING, DATA_SOURCE, REFRESH_POLICY, VERSION |
| 1.C.3 | Write `HolderSnapshot(FeatureLayerBase)`. Set all 9 per-definition constants. Add FK to MigratedCoin. Add 20+ feature fields: `total_holders` (BigIntegerField), `net_holder_change` (BigIntegerField), `holder_percent_change` (DecimalField), `acquired_via_swap/transfer/airdrop` (BigIntegerField), `holders_in_whales/sharks/dolphins/fish/octopus/crabs/shrimps` (BigIntegerField), same 7 for `holders_out_*`. All `null=True`. Add `ingested_at`. Add `unique_together`. | Use u001_fl002_pipeline_implementation_record.md field mapping table as the column checklist |
| 1.C.4 | Write `PoolMapping` (plain `models.Model`). Add fields from 1.A.4. | Not a paradigm model — no abstract base |
| 1.C.5 | Write `RawTransaction(ReferenceTableBase)` stub. Set per-definition constants with TBD values. Minimal fields — feature set not defined yet. | Placeholder only |

---

## Session 1.D — Implementation: QuerySets + Migration (1-2 hours)

| # | Task | Notes |
|---|---|---|
| 1.D.1 | Write `UniverseQuerySet` with `.as_of(simulation_time)`. Returns `self.filter(anchor_event__lte=simulation_time).exclude(membership_end__lte=simulation_time)`. Attach to `UniverseBase` via `objects = UniverseQuerySet.as_manager()`. | Event-driven filter with two-sided membership check |
| 1.D.2 | Write `FeatureLayerQuerySet` with `.as_of(simulation_time)`. End-of-interval logic: interval end = `timestamp + TEMPORAL_RESOLUTION`. Filter: `timestamp + TEMPORAL_RESOLUTION <= simulation_time`. Use Django's `F()` expression or compute in Python. Must read `TEMPORAL_RESOLUTION` from the concrete model's class constant. | This is the PIT enforcement mechanism. WDP9 (interval-start) means you add resolution to get interval end. |
| 1.D.3 | Write `ReferenceTableQuerySet` with `.as_of(simulation_time)`. Event-time logic: `timestamp <= simulation_time`. | Simpler than feature layer — no interval arithmetic |
| 1.D.4 | `python manage.py makemigrations warehouse` then `python manage.py migrate`. Verify all tables created in PostgreSQL. | |
| 1.D.5 | Shell test: create a `MigratedCoin`, create an `OHLCVCandle` with valid data (should succeed), create one with `high_price < low_price` (should fail on CHECK constraint), create a duplicate coin+timestamp (should fail on unique_together). | Your "it works" test |

---

## Verification Criteria

After all sessions are complete, the following must be true:

- [ ] `python manage.py migrate` runs without errors
- [ ] `MigratedCoin` has all 8 per-definition constants set (not `None`)
- [ ] `OHLCVCandle` has all 9 per-definition constants set (not `None`)
- [ ] `HolderSnapshot` has all 9 per-definition constants set (not `None`)
- [ ] `OHLCVCandle` with `high_price < low_price` is rejected by CHECK constraint
- [ ] Duplicate `(coin, timestamp)` pair is rejected by `unique_together`
- [ ] `MigratedCoin.objects.as_of(some_time)` returns the correct subset
- [ ] `OHLCVCandle.objects.as_of(some_time)` correctly applies end-of-interval PIT filtering
- [ ] `data_service/` app exists with placeholder function signatures
- [ ] `PoolMapping` model exists with all 6 fields

---

## Known Risks

| Risk | Impact | Mitigation |
|---|---|---|
| `FeatureLayerQuerySet.as_of()` needs per-model TEMPORAL_RESOLUTION but Django QuerySets don't easily access model class constants | Blocks PIT enforcement | Research Django's `QuerySet.model` attribute — it gives access to the concrete model class and its constants at query time |
| CHECK constraints via `Meta.constraints` may behave differently across PostgreSQL versions | Constraints silently ignored on wrong version | Test constraint rejection explicitly in 1.D.5 |
| `to_field="mint_address"` FK reference requires `mint_address` to be `unique=True` | Migration fails if not unique | Already specified in 1.C.1 — but verify during brainstorm |
| HolderSnapshot has 20+ fields — easy to miss one | Model incomplete | Use u001_fl002_pipeline_implementation_record.md field mapping table as a checklist, tick off each field |

---

## Estimated Effort

4 sessions: 1 brainstorm (1-2h) + 3 implementation (1-3h each).
