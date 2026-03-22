"""RD-001 pipeline spec: Raw transactions from Shyft / Helius."""

import logging
from datetime import datetime, timedelta, timezone

from pipeline.spec import PipelineSpec

logger = logging.getLogger(__name__)

# Source selection: safe margin below Shyft's documented 3-4 day retention
SHYFT_RETENTION_DAYS = 3


def _select_source(coin):
    """Auto-select source based on coin age.

    Coins within Shyft's retention window use Shyft (full 13 fields).
    Older coins use Helius (11 fields, pool reserves = NULL).
    """
    age = (datetime.now(timezone.utc) - coin.anchor_event).total_seconds()
    if age < SHYFT_RETENTION_DAYS * 86400:
        return 'shyft'
    return 'helius'


def _resolve_source(kw):
    """Resolve 'auto' to a concrete source using coin age."""
    source = kw.get('source', 'auto')
    if source == 'auto':
        return _select_source(kw['coin'])
    return source


def _fetch(mint, pool, start, end, **kw):
    source = _resolve_source(kw)
    if source == 'shyft':
        from pipeline.connectors.shyft import fetch_transactions
    else:
        from pipeline.connectors.helius import fetch_transactions
    logger.info("Source: %s for %s", source, mint)
    return fetch_transactions(
        pool, start, end, max_workers=kw.get('parse_workers', 1),
    )


def _conform(raw, mint, pool, **kw):
    source = _resolve_source(kw)
    if source == 'shyft':
        from pipeline.conformance.rd001_shyft import conform
    else:
        from pipeline.conformance.rd001_helius import conform
    return conform(raw, mint, pool)


def _load(mint, start, end, canonical, skipped):
    from pipeline.loaders.rd001 import load
    load(mint, start, end, canonical, skipped)


def _reconcile(canonical, skipped, start, end, meta, mint, **kw):
    source = _resolve_source(kw)
    logger.info(
        "Reconciliation for %s [%s]: parsed=%d, skipped=%d, api_calls=%d",
        mint, source, len(canonical), len(skipped), meta.get('api_calls', 0),
    )
    return {}


def _build_spec():
    from warehouse.models import RawTransaction
    return PipelineSpec(
        layer_id='RD-001',
        model=RawTransaction,
        overlap=timedelta(minutes=5),
        fetch=_fetch,
        conform=_conform,
        load=_load,
        requires_pool=True,
        conform_returns_tuple=True,
        reconcile=_reconcile,
    )


RD001 = _build_spec()
