# marjon Roadmap

Generated 2026-03-10. Covers the full path from current documentation state to a working quant research platform.

---

## Status Snapshot

| File | What it is | State |
|---|---|---|
| `data_specification_guide.md` | Paradigm guide — defines the 7 data spec concepts | **Complete** |
| `pipeline_implementation_guide.md` | Paradigm guide — defines pipeline layers, 11 decision points, conformance design, failure modes | **Complete** |
| `warehouse_implementation_guide.md` | Paradigm guide — defines warehouse architecture, 13 decision points, data service, query pipelines | **Complete** |
| `u001_data_specification.md` | U-001 dataset contract — universe, feature layers, join key, PIT, quality constraints | **Complete** (v1.0) |
| `u001_dataset_implementation_record.md` | U-001 warehouse decisions — all 13 WDPs selected with reasoning | **Complete** |
| `u001_fl001_pipeline_implementation_record.md` | FL-001 pipeline decisions — all 11 PDPs, DexPaprika conformance mapping, pool mapping table | **Complete** |
| `u001_fl002_pipeline_implementation_record.md` | FL-002 pipeline decisions — all 11 PDPs, Moralis conformance mapping | **Complete** |
| `u001_fl001_api_exploration_findings.md` | DexPaprika + GeckoTerminal API exploration for OHLCV data | **Complete** |
| `u001_fl002_api_exploration_findings.md` | Moralis API exploration for holder snapshots | **Complete** |
| `models.py` | Abstract base models (UniverseBase, FeatureLayerBase, ReferenceTableBase) | **Sketch** — no concrete models, no constraints, no QuerySets |
| `audit_report.md` | Paradigm audit — all 29 issues identified, all resolved. Final audit passed 7/7 checks with zero paradigm leaks. | **Complete** |
| `pipeline_specification_roadmap.md` | Pipeline spec work tracker | **Complete** — all items done |
| `pipeline_concepts_reference.md` | Early learning reference | **Superseded** by pipeline_implementation_guide.md |
| `warehouse_implementation_decisions.md` | Early decisions doc | **Superseded** by u001_dataset_implementation_record.md |
| `archive_dataset_specifications_v3.md` | Old dataset spec | **Superseded** by u001_data_specification.md + data_specification_guide.md |
| `data_spec_explorer.jsx` | React visualization of the 7 data spec concepts | **Complete** (informational) |

**Curriculum status:** Lesson 1 (base rate vs conditional probability) done. Lesson 2 (expected value + NumPy) is next.

**Code status:** Zero. No Django project, no pipeline code, no data in any database.

---

## Phase Overview

| Phase | Delivers | Complexity | Prerequisites | Phase Doc |
|---|---|---|---|---|
| **1** | Running Django project with empty database tables | Medium | Basic Django knowledge | `phase_1.md` |
| **2** | FL-001 pipeline: real OHLCV data flowing into the warehouse | Large | Phase 1 | `phase_2.md` |
| **3** | FL-002 pipeline: real holder data flowing into the warehouse | Medium | Phase 1 (can run parallel with Phase 2) | `phase_3.md` |
| **4** | Data service: three read operations with PIT enforcement and alignment | Medium | Phase 2 or 3 (needs data to test against) | `phase_4.md` |
| **5** | First analysis: derived features, data exploration, basic signals | Medium | Phase 4, Curriculum Lessons 2-4 | Master roadmap only |
| **6** | Strategy specification + backtesting framework | Large | Phase 5, Curriculum Lessons 5+ | Master roadmap only |
| **7** | Execution specification | Large | Phase 6 | Master roadmap only |

```
Phase 1: Django + models ──> Phase 2: FL-001 pipeline ──┐
              │                                          │
              └──> Phase 3: FL-002 pipeline ────────────>├──> Phase 4: Data service
                                                         │
                                           Curriculum ───┘
                                         L2 (EV+NumPy)

Phase 4 ──> Phase 5: Analysis ──> Phase 6: Strategy ──> Phase 7: Execution
              L3-4 (stats)          L5-6 (linalg)         L7 (stochastic)
```

---

## Phase 5: First Analysis

**What it delivers:** Derived features computed on real data. Your first exploration of what U-001 data actually looks like. Basic signal discovery.

**What you can DO:** Compute a 20-candle SMA, look at volume patterns after graduation, compare holder growth to price, visualize coin lifecycles. Ask questions like "do coins with fast holder growth in the first hour tend to pump?"

**Prerequisites:** Phase 4 (data service working). Curriculum through Lesson 4 (statistics basics — mean, variance, correlation).

**Curriculum connection:** This is where the quant curriculum and the system-building converge. You can't interpret signals without statistics. You can't compute signals without the data service.

### Tasks (exploratory — not prescriptive)

| # | Task | Curriculum lesson needed |
|---|---|---|
| 5.1 | Define DF-001 (e.g., 20-candle SMA of close_price). Compute on-the-fly through the data service (WDP11: on-the-fly). | Lesson 2 (expected value — a moving average IS an expected value estimate) |
| 5.2 | Define DF-002 (e.g., volume ratio: current interval volume vs mean volume over last N intervals). | Lesson 2 (expected value), Lesson 3 (variance) |
| 5.3 | Explore the data visually. Plot coin lifecycles. Look at distributions. | Lesson 3-4 (descriptive statistics, distributions) |
| 5.4 | Write the first audit management commands (Shelf 4 logic). What does "unusual but valid" look like in real data? | Phase 4 complete |
| 5.5 | Cross-layer analysis: holder growth vs price movement. Does FL-002 predict FL-001? | Lesson 4 (correlation, conditional probability) |

**Estimated effort:** Ongoing — transitions into strategy work.

---

## Phase 6: Strategy Specification (Future — Layer 3)

**Status:** Zero work done. Exists as a concept in the four-layer documentation structure.

**Prerequisites:** Phase 5 (data exploration experience). Curriculum through Lesson 5+ (linear algebra for portfolio optimization; basic strategies need only statistics).

**What needs to happen (high-level):**

- Define what a "strategy" is in the paradigm (entry rules, exit rules, position sizing, risk constraints)
- Build a backtesting engine that respects PIT semantics (the data service already enforces this)
- Define strategy evaluation metrics (Sharpe ratio, max drawdown, etc.)
- Write the first strategy specification for U-001

---

## Phase 7: Execution Specification (Future — Layer 4)

**Status:** Zero work done. Furthest out on the roadmap.

**Prerequisites:** Phase 6 (a strategy that produces signals).

**What needs to happen (high-level):**

- Define order types and execution rules
- Build a paper trading system (simulated execution against real-time data)
- Define risk management rules (position limits, loss limits)
- Eventually: real execution via exchange APIs

---

## Curriculum Integration

| Lesson | Topic | Connects to Phase | Why |
|---|---|---|---|
| **L1** (done) | Base rate vs conditional probability | Phase 5 | "80% of memecoins rug" is a base rate; a strategy needs conditional probability |
| **L2** (next) | Expected value + NumPy | Phase 4-5 | Moving averages are expected value estimates. NumPy is the tool for data manipulation |
| **L3** | Variance, standard deviation | Phase 5 | Volatility measurement. Is a signal noise or real? |
| **L4** | Distributions, hypothesis testing | Phase 5 | "Is this pattern statistically significant or did I get lucky on 10 coins?" |
| **L5** | Linear algebra | Phase 6 | Portfolio construction, multi-factor models |
| **L6** | Calculus | Phase 6 | Optimization, gradient-based parameter tuning |
| **L7+** | Stochastic calculus | Phase 6-7 | Continuous-time pricing models. Far out. |

**Practical rule:** Build Phases 1-3 now — they need programming skills, not math. The curriculum catches up by Phase 5 when you need the math to interpret results.

---

## What to Do Next

**If implementation session:** Start Phase 1. Create the Django project. Write the first concrete model (MigratedCoin). Everything you need is documented — abstract bases are sketched, per-definition constants are recorded, decisions are made.

**If brainstorm session:** Do Session 1.A — walk through every model design against the documentation before writing code.

**If curriculum session:** Do Lesson 2 (expected value + NumPy). Directly feeds Phase 5, and NumPy familiarity helps with Phase 4 output.

---

## What's Deliberately Not on This Roadmap

- **U-002 through U-005.** The audit identified missing paradigm concepts (MC-1 through MC-7) needed for future universes. Fix those when you start a second universe, not before.

- **GeckoTerminal as a second source.** Architecture supports it (additive change). PDP9 chose single-source. Add when DexPaprika reliability becomes a measured problem.

- **Real-time streaming.** Everything here is batch/scheduled. Real-time feeds are a different architecture on top of this one. Not needed until Phase 7.

- **Infrastructure scaling.** Single-machine dev setup (Django + PostgreSQL + Redis/Celery). Scaling is an operational concern that doesn't change the architecture.
