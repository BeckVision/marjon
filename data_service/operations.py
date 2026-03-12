"""Data service — three read-only operations.

All consumers read data through these functions — never directly from
warehouse models. Enforces PIT semantics and cross-layer alignment.
"""

import logging

from warehouse.models import (
    MigratedCoin,
    OHLCVCandle,
    HolderSnapshot,
    RawTransaction,
)

from .alignment import align_layers

logger = logging.getLogger(__name__)

# Layer ID -> model class mapping (keys from model constants — Rule 1)
LAYER_REGISTRY = {
    OHLCVCandle.LAYER_ID: OHLCVCandle,
    HolderSnapshot.LAYER_ID: HolderSnapshot,
}


def get_universe_members(simulation_time):
    """Return assets that were universe members at simulation_time.

    Args:
        simulation_time: datetime (UTC).

    Returns:
        QuerySet of MigratedCoin rows.
    """
    qs = MigratedCoin.objects.as_of(simulation_time)
    logger.info(
        "get_universe_members(sim=%s): %d members", simulation_time, qs.count(),
    )
    return qs


def get_panel_slice(asset_ids, layer_ids, simulation_time):
    """Return a merged panel of feature layer data with PIT enforcement.

    Five-step query pipeline:
    1. Scope — validate assets and time range
    2. Fetch — pull rows from each layer
    3. Time filter — apply .as_of() (PIT enforcement)
    4. Align — inner join on (asset, timestamp)
    5. Return

    Args:
        asset_ids: List of mint_address strings.
        layer_ids: List of layer ID strings (e.g. ['FL-001', 'FL-002']).
        simulation_time: datetime (UTC).

    Returns:
        List of dicts in wide format — one dict per (asset, timestamp).

    Raises:
        ValueError: If any asset doesn't exist in universe, or if
            layer_id is not registered.
    """
    # Step 1: Scope validation
    for asset_id in asset_ids:
        try:
            coin = MigratedCoin.objects.get(mint_address=asset_id)
        except MigratedCoin.DoesNotExist:
            raise ValueError(
                f"Asset '{asset_id}' does not exist in the universe"
            )

        if coin.anchor_event:
            window_start = (
                coin.anchor_event + MigratedCoin.OBSERVATION_WINDOW_START
            )
            window_end = (
                coin.anchor_event + MigratedCoin.OBSERVATION_WINDOW_END
            )
            if not (window_start <= simulation_time <= window_end):
                raise ValueError(
                    f"simulation_time {simulation_time} is outside "
                    f"observation window [{window_start}, {window_end}] "
                    f"for asset '{asset_id}'"
                )

    for layer_id in layer_ids:
        if layer_id not in LAYER_REGISTRY:
            raise ValueError(f"Unknown layer ID: '{layer_id}'")

    # Step 2+3: Fetch and time filter
    layer_data = {}
    for layer_id in layer_ids:
        model = LAYER_REGISTRY[layer_id]
        qs = model.objects.filter(
            coin_id__in=asset_ids,
        ).as_of(simulation_time)

        # Convert to list of dicts
        rows = []
        feature_fields = _get_feature_fields(model)
        for obj in qs:
            row = {
                'coin_id': obj.coin_id,
                'timestamp': obj.timestamp,
            }
            for field in feature_fields:
                row[field] = getattr(obj, field)
            rows.append(row)

        layer_data[layer_id] = rows

    # Step 4: Align
    result = align_layers(layer_data)

    logger.info(
        "get_panel_slice(assets=%s, layers=%s, sim=%s): %d rows",
        asset_ids, layer_ids, simulation_time, len(result),
    )

    # Step 5: Return
    return result


def get_reference_data(asset_id, start, end, simulation_time):
    """Return reference data for an asset within a time range.

    Args:
        asset_id: Mint address string.
        start: datetime (UTC) — start of range.
        end: datetime (UTC) — end of range.
        simulation_time: datetime (UTC) — PIT cutoff.

    Returns:
        QuerySet of RawTransaction rows, ordered by timestamp.

    Raises:
        ValueError: If asset doesn't exist in universe.
    """
    try:
        coin = MigratedCoin.objects.get(mint_address=asset_id)
    except MigratedCoin.DoesNotExist:
        raise ValueError(
            f"Asset '{asset_id}' does not exist in the universe"
        )

    # Validate time range against observation window
    if coin.anchor_event:
        window_start = (
            coin.anchor_event + MigratedCoin.OBSERVATION_WINDOW_START
        )
        window_end = (
            coin.anchor_event + MigratedCoin.OBSERVATION_WINDOW_END
        )
        if start < window_start or end > window_end:
            raise ValueError(
                f"Time range [{start}, {end}] is outside "
                f"observation window [{window_start}, {window_end}] "
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


def _get_feature_fields(model):
    """Return list of feature field names for a model (exclude FK, timestamps, PK)."""
    skip = {'id', 'coin', 'coin_id', 'timestamp', 'ingested_at'}
    fields = []
    for f in model._meta.get_fields():
        if hasattr(f, 'column') and f.name not in skip:
            fields.append(f.name)
    return fields
