"""Loader for U-002 FL-004: funding rate into U002FundingRate."""

from warehouse.models import U002FundingRate

from .common import delete_write, get_watermark


def load(symbol, start, end, canonical_records):
    delete_write(U002FundingRate, symbol, start, end, canonical_records,
                 asset_fk='asset_id')


def watermark(symbol):
    return get_watermark(U002FundingRate, symbol, asset_fk='asset_id')
