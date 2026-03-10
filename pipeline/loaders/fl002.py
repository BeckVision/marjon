"""Loader for FL-002: delete-write into HolderSnapshot table."""

import logging

from django.db import transaction
from django.db.models import Max

from warehouse.models import HolderSnapshot

logger = logging.getLogger(__name__)


def load(mint_address, start, end, canonical_records):
    """Delete-write canonical holder snapshots into the warehouse."""
    if not canonical_records:
        raise ValueError(
            f"canonical_records is empty for {mint_address} in [{start}, {end}]. "
            "Refusing to delete-write with no data."
        )

    with transaction.atomic():
        deleted, _ = HolderSnapshot.objects.filter(
            coin_id=mint_address,
            timestamp__gte=start,
            timestamp__lte=end,
        ).delete()

        if deleted:
            logger.info(
                "Deleted %d existing snapshots for %s in [%s, %s]",
                deleted, mint_address, start, end,
            )

        objs = [HolderSnapshot(**record) for record in canonical_records]
        HolderSnapshot.objects.bulk_create(objs)

    logger.info(
        "Loaded %d snapshots for %s", len(canonical_records), mint_address
    )


def get_watermark(mint_address):
    """Return the latest timestamp for a mint, or None if no data."""
    result = HolderSnapshot.objects.filter(
        coin_id=mint_address,
    ).aggregate(Max('timestamp'))
    return result['timestamp__max']
