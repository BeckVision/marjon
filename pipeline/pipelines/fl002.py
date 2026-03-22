"""FL-002 pipeline spec: Holder snapshots from Moralis."""

import logging
from datetime import timedelta

from pipeline.spec import PipelineSpec

logger = logging.getLogger(__name__)


def _fetch(mint, pool, start, end, **kw):
    import pipeline.management.commands.fetch_holders as cmd
    return cmd.fetch_holders(mint, start, end)


def _conform(raw, mint, pool, **kw):
    from pipeline.conformance.fl002_moralis import conform
    return conform(raw, mint)


def _load(mint, start, end, canonical, skipped):
    from pipeline.loaders.common import delete_write
    from warehouse.models import HolderSnapshot
    delete_write(HolderSnapshot, mint, start, end, canonical)


def _pre_flight(coin, pool, start, end, **kw):
    """CU budget guard — abort if insufficient daily budget."""
    from pipeline.exceptions import BudgetExhausted
    import pipeline.management.commands.fetch_holders as cmd
    estimated_cu = cmd.estimate_cu_cost(start, end)
    daily_used = cmd.get_daily_cu_used()
    if daily_used + estimated_cu > cmd.DAILY_CU_LIMIT:
        logger.warning(
            "CU budget guard: estimated %d CU for this run, "
            "%d already used today (limit: %d). Stopping step.",
            estimated_cu, daily_used, cmd.DAILY_CU_LIMIT,
        )
        raise BudgetExhausted(
            f"Would exceed daily CU limit. "
            f"Estimated={estimated_cu}, used={daily_used}, "
            f"limit={cmd.DAILY_CU_LIMIT}"
        )


def _reconcile(canonical, skipped, start, end, meta, mint, **kw):
    from warehouse.models import HolderSnapshot
    resolution_secs = HolderSnapshot.TEMPORAL_RESOLUTION.total_seconds()
    # Moralis returns both boundaries inclusive: +1
    expected_count = (end - start).total_seconds() / resolution_secs + 1
    loaded_count = len(canonical)
    if loaded_count != int(expected_count):
        logger.warning(
            "Count mismatch for %s: loaded %d but expected %d "
            "(missing intervals)",
            mint, loaded_count, int(expected_count),
        )
    timestamps = [r['timestamp'] for r in canonical]
    first_ts = min(timestamps)
    last_ts = max(timestamps)
    logger.info(
        "Reconciliation for %s: loaded=%d, expected=%d, "
        "first=%s, last=%s",
        mint, loaded_count, int(expected_count), first_ts, last_ts,
    )
    return {'records_expected': int(expected_count)}


def _build_spec():
    from warehouse.models import HolderSnapshot
    return PipelineSpec(
        layer_id='FL-002',
        model=HolderSnapshot,
        overlap=timedelta(minutes=30),
        fetch=_fetch,
        conform=_conform,
        load=_load,
        requires_pool=False,
        pre_flight=_pre_flight,
        reconcile=_reconcile,
    )


FL002 = _build_spec()
