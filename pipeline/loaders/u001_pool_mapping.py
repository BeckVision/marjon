"""Loader for U-001: upsert into PoolMapping table."""

import logging

from django.db import transaction

from warehouse.models import PoolMapping

logger = logging.getLogger(__name__)


def load_pool_mappings(canonical_mappings):
    """Upsert canonical pool mappings into PoolMapping.

    Uses update_or_create on (coin_id, pool_address).

    Args:
        canonical_mappings: List of dicts from conformance. Each dict has:
            coin_id, pool_address, dex, source, created_at.

    Returns:
        Tuple of (created_count, updated_count).
    """
    created_count = 0
    updated_count = 0

    with transaction.atomic():
        for m in canonical_mappings:
            _, created = PoolMapping.objects.update_or_create(
                coin_id=m['coin_id'],
                pool_address=m['pool_address'],
                defaults={
                    'dex': m['dex'],
                    'source': m['source'],
                    'created_at': m['created_at'],
                },
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

    logger.info(
        "Loaded %d pool mappings (created=%d, updated=%d)",
        len(canonical_mappings), created_count, updated_count,
    )
    return created_count, updated_count
