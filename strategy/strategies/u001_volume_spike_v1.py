"""U-001 Volume Spike + VWAP Confirmation — v1.

Hypothesis: Tokens with >3x volume spike while price is above VWAP
will continue upward.

Entry: SG-001 (volume spike ≥ 3x) AND SG-002 (price > VWAP), both required.
Exit: +100% take profit, -50% stop loss, 1440 min timeout.
"""

STRATEGY = {
    'id': 'u001_volume_spike_v1',
    'name': 'Volume Spike + VWAP Confirmation',
    'version': 1,
    'universe_id': 'U-001',
    'hypothesis': (
        'Tokens with >3x volume spike while price is above VWAP '
        'will continue upward.'
    ),

    'signals': [
        {
            'signal_id': 'SG-001',
            'role': 'entry',
            'required': True,
            'param_overrides': {'threshold': 3.0},
            'evaluation': {'mode': 'point_in_time'},
        },
        {
            'signal_id': 'SG-002',
            'role': 'entry',
            'required': True,
            'param_overrides': {},
            'evaluation': {'mode': 'point_in_time'},
        },
    ],

    'entry_rules': {
        'require_all': True,
    },

    'exit_rules': {
        'take_profit_pct': 100,
        'stop_loss_pct': -50,
        'max_hold_minutes': 1440,
    },

    'position_sizing': {
        'amount_per_trade': 1.0,
        'max_open_positions': 3,
    },

    'data_requirements': {
        'layer_ids': ['FL-001'],
        'derived_ids': ['DF-001', 'DF-002'],
        'derived_params': {
            'DF-001': {'window_size': 20},
            'DF-002': {'lookback': 20},
        },
    },

    'price_field': 'close_price',
}
