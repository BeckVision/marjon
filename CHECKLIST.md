# Checklist

## U-001

### Current Ingestion State

- [x] Discovery current through 2026-04-08.
- [x] FL-001 current through 2026-04-08.
- [x] FL-002 holders ingestion working again in capped mature-only slices.
- [ ] FL-002 coverage is still low relative to the mature universe and needs repeated catch-up passes.
- [x] RD-001 recent-coin batch path works with conservative free-tier defaults.
- [ ] RD-001 still has too many `partial` and `error` statuses for research-wide use.

### Operational Loop

- [ ] Keep running `make u001-holders` in capped slices until FL-002 coverage is materially higher.
- [ ] Re-check the 30 historical FL-002 error rows after more holders passes and separate stale residue from active auth issues.
- [ ] Keep running `./scripts/run_batch.sh --max-coins N` for guarded RD-001 catch-up on recent coins.
- [ ] Decide when to start capped Helius backfill for older RD-001 windows instead of only recent Shyft coverage.

### RD-001 Hardening

- [x] Conservative free-tier defaults are in place for batch workers, parse workers, RPC batch size, and signature caps.
- [x] Oversized recent coins are skipped before they can blow up a free-tier batch.
- [ ] Reduce Shyft transport instability and watch whether `Server disconnected` errors fall after the HTTP client change.
- [ ] Add a targeted RD-001 recovery path for historical `error` rows instead of relying only on the generic batch queue.
- [ ] Review noisy conformance warnings such as `Multiple trade events found` and decide whether they indicate lost signal or harmless redundancy.

### Research Readiness Gate

- [ ] Raise FL-002 coverage enough that holder-derived features are not based on a tiny subset.
- [ ] Raise RD-001 coverage enough that trade-flow features are not biased toward a narrow recent sample.
- [ ] Only start major U-001 feature/signal expansion after FL-002 and RD-001 coverage are good enough to trust.
