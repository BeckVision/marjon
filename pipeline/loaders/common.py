"""Generic loader utilities shared across pipelines."""

import logging

from django.db import transaction
from django.db.models import Max

logger = logging.getLogger(__name__)


def delete_write(model, asset_id, start, end, records, asset_fk='coin_id'):
    """Generic delete-write for any single-table feature layer.

    Args:
        model: Django model class.
        asset_id: Asset identifier value.
        start: Start datetime of range to replace.
        end: End datetime of range to replace.
        records: List of dicts matching model fields.
        asset_fk: Name of the FK column to the universe model (default: 'coin_id').
    """
    if not records:
        raise ValueError(
            f"records is empty for {asset_id} in [{start}, {end}]. "
            "Refusing to delete-write with no data."
        )

    with transaction.atomic():
        deleted, _ = model.objects.filter(
            **{asset_fk: asset_id},
            timestamp__gte=start,
            timestamp__lte=end,
        ).delete()

        if deleted:
            logger.info(
                "Deleted %d existing records for %s in [%s, %s]",
                deleted, asset_id, start, end,
            )

        objs = [model(**record) for record in records]
        model.objects.bulk_create(objs)

    logger.info("Loaded %d records for %s", len(records), asset_id)


def get_watermark(model, asset_id, asset_fk='coin_id'):
    """Generic MAX(timestamp) watermark for any table with asset FK + timestamp."""
    return model.objects.filter(
        **{asset_fk: asset_id},
    ).aggregate(Max('timestamp'))['timestamp__max']
