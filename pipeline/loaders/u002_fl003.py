"""Loader for U-002 FL-003: futures metrics into U002FuturesMetrics."""

from warehouse.models import U002FuturesMetrics

from .common import delete_write, get_watermark


def load(symbol, start, end, canonical_records):
    delete_write(U002FuturesMetrics, symbol, start, end, canonical_records,
                 asset_fk='asset_id')


def watermark(symbol):
    return get_watermark(U002FuturesMetrics, symbol, asset_fk='asset_id')
