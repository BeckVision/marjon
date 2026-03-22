"""Generic loader utilities shared across pipelines."""

import logging

from django.db import transaction
from django.db.models import Max

logger = logging.getLogger(__name__)


def delete_write(model, mint_address, start, end, records):
    """Generic delete-write for any single-table feature layer."""
    if not records:
        raise ValueError(
            f"records is empty for {mint_address} in [{start}, {end}]. "
            "Refusing to delete-write with no data."
        )

    with transaction.atomic():
        deleted, _ = model.objects.filter(
            coin_id=mint_address,
            timestamp__gte=start,
            timestamp__lte=end,
        ).delete()

        if deleted:
            logger.info(
                "Deleted %d existing records for %s in [%s, %s]",
                deleted, mint_address, start, end,
            )

        objs = [model(**record) for record in records]
        model.objects.bulk_create(objs)

    logger.info("Loaded %d records for %s", len(records), mint_address)


def get_watermark(model, mint_address):
    """Generic MAX(timestamp) watermark for any table with coin_id + timestamp."""
    return model.objects.filter(
        coin_id=mint_address,
    ).aggregate(Max('timestamp'))['timestamp__max']
