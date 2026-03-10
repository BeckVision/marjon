"""Cross-layer alignment with inner join and forward-fill stub."""

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


def align_layers(layer_data):
    """Align multiple feature layers by inner join on (coin_id, timestamp).

    Args:
        layer_data: Dict of {layer_id: list_of_row_dicts}.
            Each row dict must have 'coin_id' and 'timestamp' keys.

    Returns:
        List of merged dicts in wide format. Only (coin_id, timestamp)
        pairs that exist in ALL layers are included (inner join).
    """
    layer_ids = list(layer_data.keys())

    if not layer_ids:
        return []

    if len(layer_ids) == 1:
        return layer_data[layer_ids[0]]

    # Check for resolution mismatch (forward-fill stub)
    _check_resolution_mismatch(layer_ids)

    # Index each layer by (coin_id, timestamp)
    indexed = {}
    for layer_id in layer_ids:
        index = {}
        for row in layer_data[layer_id]:
            key = (row['coin_id'], row['timestamp'])
            index[key] = row
        indexed[layer_id] = index

    # Inner join: only keys present in ALL layers
    all_keys = None
    for layer_id in layer_ids:
        keys = set(indexed[layer_id].keys())
        if all_keys is None:
            all_keys = keys
        else:
            all_keys &= keys

    if not all_keys:
        return []

    # Merge columns from all layers
    result = []
    for key in sorted(all_keys):
        merged = {'coin_id': key[0], 'timestamp': key[1]}
        for layer_id in layer_ids:
            row = indexed[layer_id][key]
            for k, v in row.items():
                if k not in ('coin_id', 'timestamp'):
                    merged[k] = v
        result.append(merged)

    return result


def _check_resolution_mismatch(layer_ids):
    """Log warning if layers have different temporal resolutions.

    Forward-fill is not implemented yet — this is a stub.
    """
    from data_service.operations import LAYER_REGISTRY

    resolutions = set()
    for layer_id in layer_ids:
        model = LAYER_REGISTRY.get(layer_id)
        if model and model.TEMPORAL_RESOLUTION:
            resolutions.add(model.TEMPORAL_RESOLUTION)

    if len(resolutions) > 1:
        logger.warning(
            "Resolution mismatch detected across layers %s: %s. "
            "Forward-fill not yet implemented.",
            layer_ids, resolutions,
        )
