"""U-001 Breakout + Strong Close — v1.

Hypothesis: Tokens that spike in volume, carry short-term momentum,
break above the recent high, and close near the top of the candle
have a better chance of short-horizon continuation.
"""

STRATEGY = {
    'id': 'u001_breakout_close_v1',
    'name': 'Breakout + Strong Close',
    'version': 1,
    'universe_id': 'U-001',
    'hypothesis': (
        'Tokens with a real volume expansion, short-term momentum, '
        'a breakout above the prior high, and a strong close '
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
            'signal_id': 'SG-004',
            'role': 'entry',
            'required': True,
            'param_overrides': {'threshold_pct': 20},
            'evaluation': {'mode': 'point_in_time'},
        },
        {
            'signal_id': 'SG-005',
            'role': 'entry',
            'required': True,
            'param_overrides': {'threshold_ratio': 1.0},
            'evaluation': {'mode': 'point_in_time'},
        },
        {
            'signal_id': 'SG-006',
            'role': 'entry',
            'required': True,
            'param_overrides': {'threshold': 0.8},
            'evaluation': {'mode': 'point_in_time'},
        },
    ],

    'entry_rules': {
        'require_all': True,
    },

    'exit_rules': {
        'take_profit_pct': 120,
        'stop_loss_pct': -40,
        'max_hold_minutes': 720,
    },

    'position_sizing': {
        'amount_per_trade': 1.0,
        'max_open_positions': 3,
    },

    'data_requirements': {
        'layer_ids': ['FL-001'],
        'derived_ids': ['DF-002', 'DF-005', 'DF-006', 'DF-007'],
        'derived_params': {
            'DF-002': {'lookback': 20},
            'DF-005': {'lookback': 3},
            'DF-007': {'lookback': 12},
        },
    },

    'price_field': 'close_price',
}
