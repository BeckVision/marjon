"""Loader for U-002 FL-001: OHLCV+ spot klines into U002OHLCVCandle."""

from warehouse.models import U002OHLCVCandle

from .common import delete_write, get_watermark


def load(symbol, start, end, canonical_records):
    delete_write(U002OHLCVCandle, symbol, start, end, canonical_records,
                 asset_fk='asset_id')


def watermark(symbol):
    return get_watermark(U002OHLCVCandle, symbol, asset_fk='asset_id')
