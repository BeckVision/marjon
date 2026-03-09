# Pipeline Specification Roadmap

What we need to do to complete the pipeline specification for the quantitative trading paradigm.

---

## Where We Are

| Step | Status | Output |
|---|---|---|
| 1. Learn paradigm concepts | ✅ Done | `pipeline_concepts_reference.md` |
| 2. Explore APIs (FL-001 sources) | ✅ Done | `u001_fl001_api_exploration_findings.md` |
| 2b. Explore APIs (FL-002 sources) | ✅ Done | `u001_fl002_api_exploration_findings.md` |
| 3. Write general pipeline specification | ✅ Done | `pipeline_implementation_guide.md` |
| 4. Write U-001 pipeline record | ✅ Done (FL-001 only) | `u001_fl001_pipeline_implementation_record.md` |

---

## Step 3 Breakdown: General Pipeline Specification

The pipeline spec mirrors the warehouse guide — paradigm-level concepts, decision points, tradeoffs. No dataset-specific choices.

### 3a. Teach & discuss the established pipeline layers
- [x] Source connector
- [x] Staging area / bronze layer
- [x] Canonicalization layer (semantic conformance)
- [x] Orchestration (DAG, scheduling)
- [x] Extract strategy (incremental vs full load, high-water mark)
- [x] Idempotent write (upsert, delete-write, merge)
- [x] Reconciliation / completeness check
- [x] Data lineage / provenance metadata
- [x] Discuss each layer — what decisions live at each layer, what are the tradeoffs
- [x] Discuss how layers connect to each other

### 3b. Identify and discuss decision points
- [x] Extract strategy (full load vs incremental)
- [x] ETL vs ELT (where semantic transformations happen)
- [x] Idempotency mechanism (upsert vs delete-write vs skip-existing)
- [x] Watermark / high-water mark strategy
- [x] Rate limit handling
- [x] Error handling (fail-fast vs skip-and-continue vs retry)
- [x] Reconciliation strategy
- [x] Provenance / lineage tracking
- [x] Multi-source handling
- [x] Scheduling / orchestration
- [x] Dimension table location (emerged from discussion)

### 3c. Discuss pipeline architecture patterns
- [x] How source connectors relate to the conformance layer
- [x] Where dimension tables (like pool mapping) fit
- [x] How the pipeline interacts with the warehouse models
- [x] How backfill uses the same code path as daily runs

### 3d. Write the document
- [x] Write pipeline_implementation_guide.md from the discussed and agreed concepts
- [x] Review together

---

## Step 4 Breakdown: U-001 Pipeline Implementation Record

Dataset-specific choices for FL-001 (OHLCV) pipeline. Same role as u001_dataset_implementation_record.md but for pipelines.

### 4a. Record decision selections
- [x] Select option for each decision point from step 3b
- [x] Document reasoning for each

### 4b. Define source-specific conformance mappings
- [x] DexPaprika field mapping table (source field → warehouse field, with transformations)
- [ ] GeckoTerminal field mapping table (deferred — single source chosen)
- [x] API parameter settings (inversed=true verified with TRUMP)

### 4c. Define pipeline-specific details
- [x] Rate limit budget (10,000/day DexPaprika)
- [x] Pagination strategy (366 per call DexPaprika, 3 calls for full window)
- [x] Pool mapping dimension table design (preliminary schema)
- [x] Observation window handling (T0 to T0+5000min per coin)

### 4d. Incorporate spec changes from API exploration
- [x] Document: remove market_cap from FL-001 feature set
- [x] Document: add explicit USD denomination for prices and volume
- [x] Document: add pool mapping dimension table
- [x] Document: add ingested_at field to warehouse models

### 4e. Write the document
- [x] Write u001_fl001_pipeline_implementation_record.md
- [x] Review together

---

## Open Items

| Item | Status | Notes |
|---|---|---|
| Explore Moralis API for FL-002 (holder snapshots) | ✅ Done | `u001_fl002_api_exploration_findings.md`. FL-002 gap handling unblocked. |
| DexPaprika volume denomination | ✅ Verified | Cross-referenced against GeckoTerminal. Both APIs return USD volume. |
| FL-002 pipeline scope | ✅ Done | `u001_fl002_pipeline_implementation_record.md` |
| Pool mapping population process | ✅ Resolved | Separate pipeline using GeckoTerminal API. OHLCV pipeline reads results. |
| PDP11 (Dimension table location) | ✅ Resolved | Option A: Warehouse app owns all tables, pipeline app owns only code |
| Windowed incremental overlap size | ⚠️ Preliminary | 30 minutes chosen as starting point. May need tuning. |
| Apply spec changes to data specification | ✅ Done | `u001_data_specification.md` and `u001_dataset_implementation_record.md` produced |
