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


# ---------------------------------------------------------------------------
# DF-003: CVD — Cumulative Volume Delta
# ---------------------------------------------------------------------------

def _compute_cvd(rows):
    """Cumulative Volume Delta: running sum of (buy_volume - sell_volume).

    For each candle:
        delta = taker_buy_volume - (volume - taker_buy_volume)
              = 2 * taker_buy_volume - volume

    CVD is the running sum of delta from the start of the series
    (or from the last reset point if reset_period is set).

    Positive CVD = net buying pressure. Negative = net selling pressure.
    Divergence between price and CVD is a classic signal.

    Works with U-002 klines (which have taker_buy_volume).
    For U-001 klines (no taker data), all rows get None.
    """
    cumulative = Decimal('0')

    for row in rows:
        vol = row.get('volume')
        tbv = row.get('taker_buy_volume')

        if vol is None or tbv is None:
            row['cvd'] = None
            row['volume_delta'] = None
            continue

        delta = tbv + tbv - vol  # 2 * taker_buy_volume - volume
        cumulative += delta
        row['volume_delta'] = delta
        row['cvd'] = cumulative


register(DerivedFeatureSpec(
    derived_id='DF-003',
    name='Cumulative Volume Delta (CVD)',
    source_layers=['U002-FL-001'],
    formula=_compute_cvd,
    output_fields=['cvd', 'volume_delta'],
    parameters={},
    warm_up=0,
))


# ---------------------------------------------------------------------------
# DF-004: Liquidity — bid/ask depth within X% of mid price
# ---------------------------------------------------------------------------

def _compute_liquidity(rows, pct_range=Decimal('0.001')):
    """Order book liquidity: sum of quantity within pct_range of mid price.

    For each row (which is one level of the book), this is a pass-through.
    The real computation aggregates across levels — so this operates on
    panel data that includes order book rows grouped by (coin_id, timestamp).

    Since order book is normalized (40 rows per snapshot), this formula
    needs to aggregate across rows sharing the same (coin_id, timestamp)
    to produce per-snapshot metrics:
    - bid_depth: total bid quantity within pct_range of mid
    - ask_depth: total ask quantity within pct_range of mid
    - spread_bps: (best_ask - best_bid) / mid_price * 10000

    The formula modifies only the first row of each snapshot group and
    marks the rest for the caller.
    """
    from itertools import groupby

    # Group rows by (coin_id, timestamp) — each group = one snapshot
    keyfunc = lambda r: (r['coin_id'], r['timestamp'])

    # Collect all rows, process per snapshot
    # Since rows are sorted by (coin_id, timestamp), groupby works
    for key, group_rows in groupby(rows, key=keyfunc):
        snapshot_rows = list(group_rows)

        bids = [(r.get('price'), r.get('quantity'), r.get('level'))
                for r in snapshot_rows if r.get('side') == 'bid']
        asks = [(r.get('price'), r.get('quantity'), r.get('level'))
                for r in snapshot_rows if r.get('side') == 'ask']

        # Calculate mid price
        best_bid = max((p for p, q, l in bids if p), default=None)
        best_ask = min((p for p, q, l in asks if p), default=None)

        if best_bid is None or best_ask is None:
            for r in snapshot_rows:
                r['bid_depth'] = None
                r['ask_depth'] = None
                r['spread_bps'] = None
            continue

        mid = (best_bid + best_ask) / 2
        lower_bound = mid * (1 - pct_range)
        upper_bound = mid * (1 + pct_range)

        bid_depth = sum(q for p, q, l in bids
                        if p and q and p >= lower_bound)
        ask_depth = sum(q for p, q, l in asks
                        if p and q and p <= upper_bound)
        spread_bps = (best_ask - best_bid) / mid * 10000

        for r in snapshot_rows:
            r['bid_depth'] = bid_depth
            r['ask_depth'] = ask_depth
            r['spread_bps'] = spread_bps


register(DerivedFeatureSpec(
    derived_id='DF-004',
    name='Order Book Liquidity',
    source_layers=['U002-FL-002'],
    formula=_compute_liquidity,
    output_fields=['bid_depth', 'ask_depth', 'spread_bps'],
    parameters={'pct_range': Decimal('0.001')},  # 0.1% from mid
    warm_up=0,
))


# ---------------------------------------------------------------------------
# DF-005: Close Return % — current close vs close N candles ago
# ---------------------------------------------------------------------------

def _compute_close_return_pct(rows, lookback=3):
    """Close-to-close return in percent over `lookback` candles."""
    HUNDRED = Decimal('100')

    for i, row in enumerate(rows):
        current_close = row.get('close_price')

        if i < lookback or current_close is None:
            row['close_return_pct'] = None
            continue

        prior_close = rows[i - lookback].get('close_price')
        if prior_close in (None, 0):
            row['close_return_pct'] = None
            continue

        row['close_return_pct'] = (
            (current_close - prior_close) / prior_close
        ) * HUNDRED


register(DerivedFeatureSpec(
    derived_id='DF-005',
    name='Close Return %',
    source_layers=['FL-001'],
    formula=_compute_close_return_pct,
    output_fields=['close_return_pct'],
    parameters={'lookback': 3},
    warm_up=3,
))


# ---------------------------------------------------------------------------
# DF-006: Candle Structure — body, wicks, and close location
# ---------------------------------------------------------------------------

def _compute_candle_structure(rows):
    """Per-candle structure ratios derived from OHLC values."""
    for row in rows:
        o = row.get('open_price')
        h = row.get('high_price')
        l = row.get('low_price')
        c = row.get('close_price')

        if None in (o, h, l, c):
            row['candle_body_ratio'] = None
            row['upper_wick_ratio'] = None
            row['lower_wick_ratio'] = None
            row['close_in_range'] = None
            continue

        candle_range = h - l
        if candle_range <= 0:
            row['candle_body_ratio'] = None
            row['upper_wick_ratio'] = None
            row['lower_wick_ratio'] = None
            row['close_in_range'] = None
            continue

        body = abs(c - o)
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l

        row['candle_body_ratio'] = body / candle_range
        row['upper_wick_ratio'] = upper_wick / candle_range
        row['lower_wick_ratio'] = lower_wick / candle_range
        row['close_in_range'] = (c - l) / candle_range


register(DerivedFeatureSpec(
    derived_id='DF-006',
    name='Candle Structure Ratios',
    source_layers=['FL-001'],
    formula=_compute_candle_structure,
    output_fields=[
        'candle_body_ratio', 'upper_wick_ratio',
        'lower_wick_ratio', 'close_in_range',
    ],
    parameters={},
    warm_up=0,
))


# ---------------------------------------------------------------------------
# DF-007: Breakout Ratio — current close vs prior rolling high
# ---------------------------------------------------------------------------

def _compute_breakout_ratio(rows, lookback=12):
    """Current close divided by the highest high in the prior window."""
    HUNDRED = Decimal('100')

    for i, row in enumerate(rows):
        current_close = row.get('close_price')

        if i < lookback or current_close is None:
            row['breakout_ratio'] = None
            row['breakout_margin_pct'] = None
            continue

        window = rows[i - lookback:i]
        highs = [w.get('high_price') for w in window if w.get('high_price') is not None]
        if not highs:
            row['breakout_ratio'] = None
            row['breakout_margin_pct'] = None
            continue

        prior_high = max(highs)
        if prior_high == 0:
            row['breakout_ratio'] = None
            row['breakout_margin_pct'] = None
            continue

        ratio = current_close / prior_high
        row['breakout_ratio'] = ratio
        row['breakout_margin_pct'] = ((current_close - prior_high) / prior_high) * HUNDRED


register(DerivedFeatureSpec(
    derived_id='DF-007',
    name='Breakout Ratio vs Prior High',
    source_layers=['FL-001'],
    formula=_compute_breakout_ratio,
    output_fields=['breakout_ratio', 'breakout_margin_pct'],
    parameters={'lookback': 12},
    warm_up=12,
))
