"""Loader for FL-002: delete-write into HolderSnapshot table."""

from pipeline.loaders.common import delete_write, get_watermark as _get_watermark
from warehouse.models import HolderSnapshot


def load(mint_address, start, end, canonical_records):
    """Delete-write canonical holder snapshots into the warehouse."""
    delete_write(HolderSnapshot, mint_address, start, end, canonical_records)


def get_watermark(mint_address):
    """Return the latest timestamp for a mint, or None if no data."""
    return _get_watermark(HolderSnapshot, mint_address)
