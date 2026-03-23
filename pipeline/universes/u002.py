"""
Pipeline configuration for U-002: Major Crypto Assets (BTCUSDT, ETHUSDT, SOLUSDT).

Calendar-driven universe. No discovery step — fixed entity list.
CSV-first backfill. Three feature layers (FL-001, FL-003, FL-004).
FL-002 (order book) deferred — requires real-time polling.
"""

from warehouse.models import (
    BinanceAsset, U002FundingRate, U002FuturesMetrics, U002OHLCVCandle,
)

UNIVERSE = {
    'id': BinanceAsset.UNIVERSE_ID,
    'name': BinanceAsset.NAME,
    'model': 'warehouse.models.BinanceAsset',
    'run_model': 'warehouse.models.U002PipelineRun',
    'status_model': 'warehouse.models.U002PipelineStatus',

    'discovery': None,  # Fixed entity list — no discovery pipeline

    'steps': [
        {
            'name': 'klines',
            'layer_id': U002OHLCVCandle.LAYER_ID,
            'handler': 'pipeline.orchestration.handlers.run_u002_klines',
            'per_coin': True,
            'source': 'csv',
            'rate_limit_sleep': 0,
            'workers': 1,
            'max_consecutive_failures': 3,
            'skip_if': 'window_complete',
        },
        {
            'name': 'futures_metrics',
            'layer_id': U002FuturesMetrics.LAYER_ID,
            'handler': 'pipeline.orchestration.handlers.run_u002_futures_metrics',
            'per_coin': True,
            'source': 'csv',
            'rate_limit_sleep': 0,
            'workers': 1,
            'max_consecutive_failures': 3,
            'skip_if': 'window_complete',
        },
        {
            'name': 'funding_rate',
            'layer_id': U002FundingRate.LAYER_ID,
            'handler': 'pipeline.orchestration.handlers.run_u002_funding_rate',
            'per_coin': True,
            'source': 'csv',
            'rate_limit_sleep': 0,
            'workers': 1,
            'max_consecutive_failures': 3,
            'skip_if': 'window_complete',
        },
    ],
}
