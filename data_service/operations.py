"""Data service — three read-only operations.

All consumers read data through these functions — never directly from
warehouse models. Enforces PIT semantics and cross-layer alignment.
"""

import logging

from django.db import models as dj_models

from warehouse.models import (
    BinanceAsset,
    MigratedCoin,
    OHLCVCandle,
    HolderSnapshot,
    RawTransaction,
    U002FundingRate,
    U002FuturesMetrics,
    U002OHLCVCandle,
    U002OrderBookSnapshot,
)

from .alignment import align_layers
from .derived import DERIVED_REGISTRY, compute_derived

logger = logging.getLogger(__name__)

# Layer ID -> model class mapping (keys from model constants — Rule 1)
LAYER_REGISTRY = {
    OHLCVCandle.LAYER_ID: OHLCVCandle,
    HolderSnapshot.LAYER_ID: HolderSnapshot,
    U002OHLCVCandle.LAYER_ID: U002OHLCVCandle,
    U002FuturesMetrics.LAYER_ID: U002FuturesMetrics,
    U002FundingRate.LAYER_ID: U002FundingRate,
    U002OrderBookSnapshot.LAYER_ID: U002OrderBookSnapshot,
}

# Universe ID -> model class mapping
UNIVERSE_REGISTRY = {
    MigratedCoin.UNIVERSE_ID: MigratedCoin,
    BinanceAsset.UNIVERSE_ID: BinanceAsset,
}

# Reference ID -> model class mapping
REFERENCE_REGISTRY = {
    RawTransaction.REFERENCE_ID: RawTransaction,
}


def get_universe_members(simulation_time, universe_id=None):
    """Return assets that were universe members at simulation_time.

    Args:
        simulation_time: datetime (UTC).
        universe_id: Universe ID string. Defaults to first registered universe.

    Returns:
        QuerySet of universe model rows.
    """
    if universe_id is None:
        universe_model = MigratedCoin
    else:
        if universe_id not in UNIVERSE_REGISTRY:
            raise ValueError(f"Unknown universe ID: '{universe_id}'")
        universe_model = UNIVERSE_REGISTRY[universe_id]

    qs = universe_model.objects.as_of(simulation_time)
    logger.info(
        "get_universe_members(sim=%s, universe=%s): %d members",
        simulation_time, universe_model.UNIVERSE_ID, qs.count(),
    )
    return qs


def get_panel_slice(asset_ids, layer_ids, simulation_time,
                    derived_ids=None, derived_params=None):
    """Return a merged panel of feature layer data with PIT enforcement.

    Six-step query pipeline:
    1. Scope — validate assets, time range, and derived IDs
    2. Fetch — pull rows from each layer
    3. Time filter — apply .as_of() (PIT enforcement)
    4. Align — inner join on (asset, timestamp)
    5. Compute derived features (if requested)
    6. Return

    Args:
        asset_ids: List of asset identifier strings.
        layer_ids: List of layer ID strings (e.g. ['FL-001', 'FL-002']).
        simulation_time: datetime (UTC).
        derived_ids: Optional list of derived feature IDs (e.g. ['DF-001']).
        derived_params: Optional dict of {derived_id: {param: value}}
                        overrides. Merged on top of spec defaults.

    Returns:
        List of dicts in wide format — one dict per (asset, timestamp).

    Raises:
        ValueError: If any asset doesn't exist in universe, or if
            layer_id / derived_id is not registered.
    """
    # Step 1: Scope validation
    for asset_id in asset_ids:
        # Try each registered universe to find the asset
        asset = _lookup_asset(asset_id)
        _validate_simulation_time(asset, simulation_time)

    for layer_id in layer_ids:
        if layer_id not in LAYER_REGISTRY:
            raise ValueError(f"Unknown layer ID: '{layer_id}'")

    if derived_ids:
        for derived_id in derived_ids:
            if derived_id not in DERIVED_REGISTRY:
                raise ValueError(f"Unknown derived feature: '{derived_id}'")

    # Step 2+3: Fetch and time filter
    layer_data = {}
    for layer_id in layer_ids:
        model = LAYER_REGISTRY[layer_id]
        fk_attname = _find_asset_fk(model)
        qs = model.objects.filter(
            **{f'{fk_attname}__in': asset_ids},
        ).as_of(simulation_time)

        # Convert to list of dicts
        rows = []
        feature_fields = _get_feature_fields(model)
        for obj in qs:
            row = {
                'coin_id': getattr(obj, fk_attname),
                'timestamp': obj.timestamp,
            }
            for field in feature_fields:
                row[field] = getattr(obj, field)
            rows.append(row)

        layer_data[layer_id] = rows

    # Step 4: Align
    result = align_layers(layer_data)

    # Step 5: Compute derived features
    if derived_ids:
        result = compute_derived(result, derived_ids, derived_params)

    logger.info(
        "get_panel_slice(assets=%s, layers=%s, derived=%s, sim=%s): %d rows",
        asset_ids, layer_ids, derived_ids, simulation_time, len(result),
    )

    # Step 6: Return
    return result


def get_reference_data(asset_id, start, end, simulation_time):
    """Return reference data for an asset within a time range.

    Args:
        asset_id: Asset identifier string.
        start: datetime (UTC) — start of range.
        end: datetime (UTC) — end of range.
        simulation_time: datetime (UTC) — PIT cutoff.

    Returns:
        QuerySet of reference table rows, ordered by timestamp.

    Raises:
        ValueError: If asset doesn't exist in universe.
    """
    asset = _lookup_asset(asset_id)

    # Validate time range against observation window
    ws = asset.window_start_time
    we = asset.window_end_time
    if ws is not None and we is not None:
        if start < ws or end > we:
            raise ValueError(
                f"Time range [{start}, {end}] is outside "
                f"observation window [{ws}, {we}] "
                f"for asset '{asset_id}'"
            )
    elif ws is not None:
        if start < ws:
            raise ValueError(
                f"Time range start {start} is before "
                f"observation window start {ws} "
                f"for asset '{asset_id}'"
            )

    qs = RawTransaction.objects.filter(
        coin_id=asset_id,
        timestamp__gte=start,
        timestamp__lte=end,
    ).as_of(simulation_time).order_by('timestamp')

    logger.info(
        "get_reference_data(asset=%s, range=[%s, %s], sim=%s): %d rows",
        asset_id, start, end, simulation_time, qs.count(),
    )
    return qs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_asset_fk(model):
    """Find the FK attname on a feature/reference model that points to UniverseBase."""
    from warehouse.utils import find_universe_fk
    try:
        return find_universe_fk(model)
    except ValueError:
        return 'coin_id'


def _lookup_asset(asset_id):
    """Look up an asset across all registered universes.

    Tries each universe model's unique CharField (the natural key).
    """
    for universe_model in UNIVERSE_REGISTRY.values():
        # Find the unique CharField that serves as the natural key
        for field in universe_model._meta.get_fields():
            if (hasattr(field, 'unique') and field.unique
                    and isinstance(field, dj_models.CharField)
                    and field.name != 'id'):
                try:
                    return universe_model.objects.get(**{field.name: asset_id})
                except universe_model.DoesNotExist:
                    break
    raise ValueError(
        f"Asset '{asset_id}' does not exist in any registered universe"
    )


def _validate_simulation_time(asset, simulation_time):
    """Validate simulation_time is within the asset's observation window."""
    ws = asset.window_start_time
    we = asset.window_end_time
    if ws is not None and we is not None:
        if not (ws <= simulation_time <= we):
            raise ValueError(
                f"simulation_time {simulation_time} is outside "
                f"observation window [{ws}, {we}] "
                f"for asset '{asset.pk}'"
            )
    elif ws is not None:
        if simulation_time < ws:
            raise ValueError(
                f"simulation_time {simulation_time} is before "
                f"observation window start {ws} "
                f"for asset '{asset.pk}'"
            )


def _get_feature_fields(model):
    """Return list of feature field names for a model (exclude FK, timestamps, PK)."""
    skip = {'id', 'coin', 'coin_id', 'asset', 'asset_id', 'timestamp', 'ingested_at'}
    fields = []
    for f in model._meta.get_fields():
        if hasattr(f, 'column') and f.name not in skip:
            fields.append(f.name)
    return fields
