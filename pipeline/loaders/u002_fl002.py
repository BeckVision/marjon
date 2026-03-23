"""Loader for U-002 FL-002: order book snapshots into U002OrderBookSnapshot.

Uses bulk_create (append-only) — not delete-write, because each snapshot
is a unique point-in-time observation. No overlap to replace.
"""

import logging

from django.db import transaction

from warehouse.models import U002OrderBookSnapshot

logger = logging.getLogger(__name__)


def load(symbol, canonical_records):
    """Bulk insert order book snapshot rows.

    Args:
        symbol: Asset symbol (for logging).
        canonical_records: List of dicts matching U002OrderBookSnapshot fields.
    """
    if not canonical_records:
        return

    with transaction.atomic():
        objs = [U002OrderBookSnapshot(**r) for r in canonical_records]
        U002OrderBookSnapshot.objects.bulk_create(objs, ignore_conflicts=True)

    logger.info("Loaded %d order book rows for %s", len(canonical_records), symbol)
