"""Loader for FL-001: delete-write into OHLCVCandle table."""

import logging

from django.db import transaction
from django.db.models import Max

from warehouse.models import OHLCVCandle

logger = logging.getLogger(__name__)


def load(mint_address, start, end, canonical_records):
    """Delete-write canonical OHLCV records into the warehouse.

    Within a single transaction: delete existing rows for this mint
    in the time range, then bulk_create the new records.

    Args:
        mint_address: String mint address.
        start: datetime — start of time range.
        end: datetime — end of time range.
        canonical_records: List of dicts matching OHLCVCandle fields.
    """
    if not canonical_records:
        raise ValueError(
            f"canonical_records is empty for {mint_address} in [{start}, {end}]. "
            "Refusing to delete-write with no data."
        )

    with transaction.atomic():
        deleted, _ = OHLCVCandle.objects.filter(
            coin_id=mint_address,
            timestamp__gte=start,
            timestamp__lte=end,
        ).delete()

        if deleted:
            logger.info(
                "Deleted %d existing candles for %s in [%s, %s]",
                deleted, mint_address, start, end,
            )

        objs = [OHLCVCandle(**record) for record in canonical_records]
        OHLCVCandle.objects.bulk_create(objs)

    logger.info(
        "Loaded %d candles for %s", len(canonical_records), mint_address
    )


def get_watermark(mint_address):
    """Return the latest timestamp for a mint, or None if no data."""
    result = OHLCVCandle.objects.filter(
        coin_id=mint_address,
    ).aggregate(Max('timestamp'))
    return result['timestamp__max']
