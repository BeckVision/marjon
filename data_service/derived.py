"""Derived feature registry and on-the-fly computation (WDP11-B).

Derived features are computed from aligned panel data — never stored.
Each spec declares its source layers, formula, parameters, warm-up period,
and output fields. The computation engine applies them per-coin over
time-ordered rows.
"""

from collections import defaultdict
from decimal import Decimal


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

DERIVED_REGISTRY = {}


class DerivedFeatureSpec:
    """Specification for one derived feature."""

    def __init__(self, derived_id, name, source_layers, formula,
                 output_fields, parameters=None, warm_up=0):
        self.derived_id = derived_id
        self.name = name
        self.source_layers = source_layers
        self.formula = formula
        self.output_fields = output_fields
        self.parameters = parameters or {}
        self.warm_up = warm_up


def register(spec):
    """Register a DerivedFeatureSpec in the global registry."""
    DERIVED_REGISTRY[spec.derived_id] = spec
    return spec


# ---------------------------------------------------------------------------
# Computation engine
# ---------------------------------------------------------------------------

def compute_derived(panel_rows, derived_ids, derived_params=None):
    """Apply derived features to aligned panel rows.

    Args:
        panel_rows: List of dicts from align_layers (wide format,
                    sorted by (coin_id, timestamp)).
        derived_ids: List of derived feature IDs to compute.
        derived_params: Optional dict of {derived_id: {param: value}}
                        overrides. Merged on top of spec defaults.

    Returns:
        panel_rows with derived columns added in place.

    Raises:
        ValueError: If a derived_id is not registered.
    """
    derived_params = derived_params or {}

    for derived_id in derived_ids:
        if derived_id not in DERIVED_REGISTRY:
            raise ValueError(f"Unknown derived feature: '{derived_id}'")

    # Group rows by coin_id (preserving order)
    by_coin = defaultdict(list)
    for row in panel_rows:
        by_coin[row['coin_id']].append(row)

    for derived_id in derived_ids:
        spec = DERIVED_REGISTRY[derived_id]
        params = {**spec.parameters, **derived_params.get(derived_id, {})}
        for coin_id, rows in by_coin.items():
            # Rows are already sorted by timestamp from alignment
            spec.formula(rows, **params)

    return panel_rows


# ---------------------------------------------------------------------------
# DF-001: VWAP — Volume Weighted Average Price
# ---------------------------------------------------------------------------

def _compute_vwap(rows, window_size=20):
    """Rolling VWAP using typical price = (H + L + C) / 3.

    VWAP = Σ(typical_price × volume) / Σ(volume) over the window.

    Warm-up period: first (window_size - 1) rows get None.
    Rows with null price or zero volume in the window are excluded from
    the calculation. If the entire window has zero volume, VWAP is None.
    """
    THREE = Decimal('3')

    for i, row in enumerate(rows):
        if i < window_size - 1:
            row['vwap'] = None
            continue

        window = rows[max(0, i - window_size + 1):i + 1]

        pv_sum = Decimal('0')
        v_sum = Decimal('0')

        for w in window:
            h = w.get('high_price')
            l = w.get('low_price')
            c = w.get('close_price')
            v = w.get('volume')

            if h is None or l is None or c is None or v is None:
                continue
            if v == 0:
                continue

            typical = (h + l + c) / THREE
            pv_sum += typical * v
            v_sum += v

        if v_sum == 0:
            row['vwap'] = None
        else:
            row['vwap'] = pv_sum / v_sum


register(DerivedFeatureSpec(
    derived_id='DF-001',
    name='Volume Weighted Average Price (VWAP)',
    source_layers=['FL-001'],
    formula=_compute_vwap,
    output_fields=['vwap'],
    parameters={'window_size': 20},
    warm_up=19,
))


# ---------------------------------------------------------------------------
# DF-002: Volume Ratio — current volume vs rolling mean
# ---------------------------------------------------------------------------

def _compute_volume_ratio(rows, lookback=20):
    """Volume ratio: current candle volume / mean volume over last N candles.

    Values > 1 indicate above-average activity. A spike to 5x means
    the current candle has 5 times the recent average volume.

    Warm-up period: first `lookback` rows get None (need N candles
    to establish the baseline mean). The current candle is NOT included
    in the mean — it's compared against the prior N candles.

    Rows with null volume are excluded from the mean. If all prior
    candles have null/zero volume, ratio is None.
    """
    for i, row in enumerate(rows):
        current_vol = row.get('volume')

        if i < lookback or current_vol is None:
            row['volume_ratio'] = None
            continue

        # Mean of the previous `lookback` candles (not including current)
        window = rows[i - lookback:i]
        volumes = [w.get('volume') for w in window if w.get('volume') is not None]

        if not volumes:
            row['volume_ratio'] = None
            continue

        mean_vol = sum(volumes) / len(volumes)

        if mean_vol == 0:
            row['volume_ratio'] = None
        else:
            row['volume_ratio'] = current_vol / mean_vol


register(DerivedFeatureSpec(
    derived_id='DF-002',
    name='Volume Ratio (current vs rolling mean)',
    source_layers=['FL-001'],
    formula=_compute_volume_ratio,
    output_fields=['volume_ratio'],
    parameters={'lookback': 20},
    warm_up=20,
))
