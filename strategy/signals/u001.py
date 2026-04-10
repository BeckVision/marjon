"""U-001 signal definitions.

SG-001: Volume Spike — volume_ratio >= threshold
SG-002: Price Above VWAP — close_price > vwap
SG-003: Price Drop Exit — roi <= stop_loss_pct (evaluated on open positions)
SG-004: Momentum Return — close_return_pct >= threshold_pct
SG-005: Breakout Above Prior High — breakout_ratio >= threshold_ratio
SG-006: Strong Close — close_in_range >= threshold
"""

from .registry import SignalSpec, register_signal


# ---------------------------------------------------------------------------
# SG-001: Volume Spike
# ---------------------------------------------------------------------------

def _eval_volume_spike(row, threshold=3.0):
    """True when current volume is >= threshold × recent average."""
    volume_ratio = row.get('volume_ratio')
    if volume_ratio is None:
        return False
    return volume_ratio >= threshold


register_signal(SignalSpec(
    signal_id='SG-001',
    name='Volume Spike',
    category='volume',
    source_layers=['FL-001'],
    evaluate=_eval_volume_spike,
    default_params={'threshold': 3.0},
    derived_features=['DF-002'],
    description='Fires when volume_ratio >= threshold (default 3x recent average).',
    param_ranges={'threshold': [1.5, 2.0, 3.0, 5.0, 10.0]},
))


# ---------------------------------------------------------------------------
# SG-002: Price Above VWAP
# ---------------------------------------------------------------------------

def _eval_price_above_vwap(row):
    """True when close price is above VWAP."""
    close = row.get('close_price')
    vwap = row.get('vwap')
    if close is None or vwap is None:
        return False
    return close > vwap


register_signal(SignalSpec(
    signal_id='SG-002',
    name='Price Above VWAP',
    category='price',
    source_layers=['FL-001'],
    evaluate=_eval_price_above_vwap,
    default_params={},
    derived_features=['DF-001'],
    description='Fires when close_price > VWAP.',
))


# ---------------------------------------------------------------------------
# SG-003: Price Drop Exit
# ---------------------------------------------------------------------------

def _eval_price_drop(row, stop_loss_pct=-50):
    """True when current ROI on position has dropped below stop_loss_pct.

    This signal is evaluated differently — it checks position ROI, not raw data.
    The runner injects 'position_roi_pct' into the row before evaluation.
    """
    roi = row.get('position_roi_pct')
    if roi is None:
        return False
    return roi <= stop_loss_pct


register_signal(SignalSpec(
    signal_id='SG-003',
    name='Price Drop Exit',
    category='price',
    source_layers=[],
    evaluate=_eval_price_drop,
    default_params={'stop_loss_pct': -50},
    derived_features=[],
    description='Fires when position ROI drops below stop_loss_pct.',
))


# ---------------------------------------------------------------------------
# SG-004: Momentum Return
# ---------------------------------------------------------------------------

def _eval_momentum_return(row, threshold_pct=20):
    """True when close_return_pct exceeds the configured threshold."""
    ret = row.get('close_return_pct')
    if ret is None:
        return False
    return ret >= threshold_pct


register_signal(SignalSpec(
    signal_id='SG-004',
    name='Momentum Return',
    category='price',
    source_layers=['FL-001'],
    evaluate=_eval_momentum_return,
    default_params={'threshold_pct': 20},
    derived_features=['DF-005'],
    description='Fires when close_return_pct >= threshold_pct.',
    param_ranges={'threshold_pct': [5, 10, 20, 30, 50]},
))


# ---------------------------------------------------------------------------
# SG-005: Breakout Above Prior High
# ---------------------------------------------------------------------------

def _eval_breakout_above_prior_high(row, threshold_ratio=1):
    """True when current close exceeds the rolling prior high by threshold."""
    ratio = row.get('breakout_ratio')
    if ratio is None:
        return False
    return ratio >= threshold_ratio


register_signal(SignalSpec(
    signal_id='SG-005',
    name='Breakout Above Prior High',
    category='price',
    source_layers=['FL-001'],
    evaluate=_eval_breakout_above_prior_high,
    default_params={'threshold_ratio': 1},
    derived_features=['DF-007'],
    description='Fires when breakout_ratio >= threshold_ratio.',
    param_ranges={'threshold_ratio': [1, 1.01, 1.02, 1.05]},
))


# ---------------------------------------------------------------------------
# SG-006: Strong Close
# ---------------------------------------------------------------------------

def _eval_strong_close(row, threshold=0.8):
    """True when the candle closes in the top portion of its range."""
    close_in_range = row.get('close_in_range')
    if close_in_range is None:
        return False
    return close_in_range >= threshold


register_signal(SignalSpec(
    signal_id='SG-006',
    name='Strong Close',
    category='price',
    source_layers=['FL-001'],
    evaluate=_eval_strong_close,
    default_params={'threshold': 0.8},
    derived_features=['DF-006'],
    description='Fires when close_in_range >= threshold.',
    param_ranges={'threshold': [0.6, 0.7, 0.8, 0.9]},
))
