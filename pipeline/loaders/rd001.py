"""Loader for RD-001: delete-write into RawTransaction + SkippedTransaction tables."""

import logging

from django.db import transaction
from django.db.models import Max

from warehouse.models import RawTransaction, SkippedTransaction

logger = logging.getLogger(__name__)


def load(mint_address, start, end, parsed_records, skipped_records):
    """Delete-write parsed and skipped transaction records into the warehouse.

    Within a single transaction: delete existing rows for this mint
    in the time range for both tables, then bulk_create both.

    Args:
        mint_address: String mint address.
        start: datetime — start of time range.
        end: datetime — end of time range.
        parsed_records: List of dicts matching RawTransaction fields.
        skipped_records: List of dicts matching SkippedTransaction fields.
    """
    if not parsed_records and not skipped_records:
        logger.warning(
            "Both parsed and skipped records empty for %s in [%s, %s]. "
            "Nothing to load.",
            mint_address, start, end,
        )
        return

    with transaction.atomic():
        del_raw, _ = RawTransaction.objects.filter(
            coin_id=mint_address,
            timestamp__gte=start,
            timestamp__lte=end,
        ).delete()

        del_skipped, _ = SkippedTransaction.objects.filter(
            coin_id=mint_address,
            timestamp__gte=start,
            timestamp__lte=end,
        ).delete()

        if del_raw or del_skipped:
            logger.info(
                "Deleted %d raw + %d skipped for %s in [%s, %s]",
                del_raw, del_skipped, mint_address, start, end,
            )

        if parsed_records:
            objs = [RawTransaction(**r) for r in parsed_records]
            RawTransaction.objects.bulk_create(objs)

        if skipped_records:
            objs = [SkippedTransaction(**r) for r in skipped_records]
            SkippedTransaction.objects.bulk_create(objs)

    logger.info(
        "Loaded %d parsed + %d skipped transactions for %s",
        len(parsed_records), len(skipped_records), mint_address,
    )


def get_watermark(mint_address):
    """Return the latest timestamp for a mint, or None if no data."""
    result = RawTransaction.objects.filter(
        coin_id=mint_address,
    ).aggregate(Max('timestamp'))
    return result['timestamp__max']
