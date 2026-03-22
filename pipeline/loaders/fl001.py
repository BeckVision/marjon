"""Loader for FL-001: delete-write into OHLCVCandle table."""

from pipeline.loaders.common import delete_write, get_watermark as _get_watermark
from warehouse.models import OHLCVCandle


def load(mint_address, start, end, canonical_records):
    """Delete-write canonical OHLCV records into the warehouse."""
    delete_write(OHLCVCandle, mint_address, start, end, canonical_records)


def get_watermark(mint_address):
    """Return the latest timestamp for a mint, or None if no data."""
    return _get_watermark(OHLCVCandle, mint_address)
