"""
Pipeline configuration for U-001: Graduated Pump.fun Tokens — Early Lifecycle.

Read by the orchestrator to determine what steps to run, in what order,
with what constraints. This is the single source of truth for U-001's
pipeline chain.
"""

from warehouse.models import (
    HolderSnapshot, MigratedCoin, OHLCVCandle, RawTransaction,
)

UNIVERSE = {
    'id': MigratedCoin.UNIVERSE_ID,
    'name': MigratedCoin.NAME,
    'model': 'warehouse.models.MigratedCoin',
    'status_model': 'warehouse.models.U001PipelineStatus',

    'discovery': {
        'handler': 'pipeline.orchestration.handlers.run_discovery_u001',
        'source': 'moralis',
        'cu_cost_per_call': 50,
        'tokens_per_page': 100,
    },

    'steps': [
        {
            'name': 'pool_mapping',
            'handler': 'pipeline.orchestration.handlers.run_pool_mapping',
            'depends_on': 'discovery',
            'per_coin': False,
            'sources': ['dexscreener', 'geckoterminal'],
            'skip_if': 'pool_mapping_exists',
        },
        {
            'name': 'ohlcv',
            'layer_id': OHLCVCandle.LAYER_ID,
            'handler': 'pipeline.orchestration.handlers.run_ohlcv',
            'depends_on': 'pool_mapping',
            'per_coin': True,
            'source': 'geckoterminal',
            'rate_limit_sleep': 0,
            'workers': 6,
            'max_consecutive_failures': 5,
            'skip_if': 'window_complete_or_immature',
        },
        {
            'name': 'raw_transactions',
            'layer_id': RawTransaction.REFERENCE_ID,
            'handler': 'pipeline.orchestration.handlers.run_raw_transactions',
            'depends_on': 'pool_mapping',
            'per_coin': True,
            'source': 'auto',
            'rate_limit_sleep': 0,      # per-key rate limiting in connector handles pacing
            'workers': 1,               # coin-level; parse_workers handles concurrency
            'parse_workers': 8,         # intra-coin Phase 2 threads (SSOT for this value)
            'max_consecutive_failures': 5,
            'skip_if': 'window_complete',
        },
        {
            'name': 'holders',
            'layer_id': HolderSnapshot.LAYER_ID,
            'handler': 'pipeline.orchestration.handlers.run_holders',
            'depends_on': 'discovery',
            'per_coin': True,
            'source': 'moralis',
            'rate_limit_sleep': 1,
            'workers': 1,
            'max_consecutive_failures': 5,
            'requires_layer_complete': OHLCVCandle.LAYER_ID,
            'skip_if': 'window_complete_or_immature',
        },
    ],
}
