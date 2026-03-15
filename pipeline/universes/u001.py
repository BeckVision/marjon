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
            'skip_if': 'window_complete_or_immature',
        },
        {
            'name': 'raw_transactions',
            'layer_id': RawTransaction.REFERENCE_ID,
            'handler': 'pipeline.orchestration.handlers.run_raw_transactions',
            'depends_on': 'pool_mapping',
            'per_coin': True,
            'source': 'auto',
            'rate_limit_sleep': 1,
            'workers': 4,
            'skip_if': 'window_complete',
        },
        # Uncomment when ready:
        # {
        #     'name': 'holders',
        #     'layer_id': HolderSnapshot.LAYER_ID,
        #     'handler': 'pipeline.orchestration.handlers.run_holders',
        #     'depends_on': 'discovery',
        #     'per_coin': True,
        #     'source': 'moralis',
        #     'rate_limit_sleep': 1,
        #     'api_keys': ['MORALIS_API_KEY_1', 'MORALIS_API_KEY_2', 'MORALIS_API_KEY_3', 'MORALIS_API_KEY_4'],
        #     'skip_if': 'window_complete_or_immature',
        # },
    ],
}
