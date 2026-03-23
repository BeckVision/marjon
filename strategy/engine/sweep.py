"""Parameter sweep engine — grid search over signal and strategy params.

Each combination runs as its own BacktestRun, grouped by a shared sweep_id.
"""

import copy
import logging
import uuid
from itertools import product

from data_service.operations import get_panel_slice
from strategy.signals.registry import SIGNAL_REGISTRY

from .runner import BacktestRunner

logger = logging.getLogger(__name__)


def generate_param_grid(strategy_config):
    """Cartesian product of all sweepable params.

    Sources:
    - SignalSpec.param_ranges for each signal in the strategy
    - Keys ending in '_range' in exit_rules / position_sizing

    Returns list of dicts, each a flat combination:
    {'SG-001.threshold': 3.0, 'exit.take_profit_pct': 100, ...}
    """
    axes = []  # list of (param_key, values) tuples

    # Signal param ranges
    for sig_cfg in strategy_config['signals']:
        signal_id = sig_cfg['signal_id']
        spec = SIGNAL_REGISTRY.get(signal_id)
        if spec and spec.param_ranges:
            for param_name, values in spec.param_ranges.items():
                key = f"{signal_id}.{param_name}"
                axes.append((key, values))

    # Exit rule ranges
    exit_rules = strategy_config.get('exit_rules', {})
    for k, v in list(exit_rules.items()):
        if k.endswith('_range'):
            base_key = k[:-6]  # strip '_range'
            axes.append((f"exit.{base_key}", v))

    # Position sizing ranges
    sizing = strategy_config.get('position_sizing', {})
    for k, v in list(sizing.items()):
        if k.endswith('_range'):
            base_key = k[:-6]
            axes.append((f"sizing.{base_key}", v))

    if not axes:
        return [{}]

    keys = [a[0] for a in axes]
    value_lists = [a[1] for a in axes]

    grid = []
    for combo in product(*value_lists):
        grid.append(dict(zip(keys, combo)))

    return grid


def apply_param_combination(strategy_config, combination):
    """Deep-copy config, apply one param combo. Returns modified config."""
    config = copy.deepcopy(strategy_config)

    for key, value in combination.items():
        if '.' not in key:
            continue
        prefix, param = key.split('.', 1)

        if prefix.startswith('SG-'):
            # Signal param override
            for sig_cfg in config['signals']:
                if sig_cfg['signal_id'] == prefix:
                    sig_cfg.setdefault('param_overrides', {})[param] = value
                    break

        elif prefix == 'exit':
            config['exit_rules'][param] = value

        elif prefix == 'sizing':
            config['position_sizing'][param] = value

    return config


def run_sweep(strategy_config, asset_ids, data_start, data_end,
              sweep_id=None, panel=None):
    """Execute full parameter sweep.

    1. Load data once via get_panel_slice() (or use provided panel)
    2. Generate param grid
    3. For each combination: apply params, run backtest with pre-loaded panel
    4. Return list of (combination_dict, result_dict) tuples

    Args:
        strategy_config: Strategy config dict.
        asset_ids: List of asset IDs.
        data_start: Start of data window.
        data_end: End of data window.
        sweep_id: Optional sweep_id. Generated if not provided.
        panel: Optional pre-loaded panel data.

    Returns:
        Tuple of (sweep_id, results) where results is a list of
        (combination_dict, runner_result_dict) tuples.
    """
    if sweep_id is None:
        sweep_id = f"sweep_{uuid.uuid4().hex[:12]}"

    # Load data once
    if panel is None:
        data_req = strategy_config['data_requirements']
        panel = get_panel_slice(
            asset_ids,
            data_req['layer_ids'],
            data_end,
            derived_ids=data_req.get('derived_ids'),
            derived_params=data_req.get('derived_params'),
        )

    grid = generate_param_grid(strategy_config)
    logger.info(
        "Sweep %s: %d combinations to test", sweep_id, len(grid),
    )

    results = []
    for i, combo in enumerate(grid, 1):
        modified_config = apply_param_combination(strategy_config, combo)
        runner = BacktestRunner(
            modified_config, asset_ids, data_start, data_end,
            panel=panel,
        )
        result = runner.run()
        result['sweep_id'] = sweep_id
        result['combination'] = combo
        results.append((combo, result))
        logger.info(
            "Sweep %s: %d/%d complete — trades=%d, sharpe=%s",
            sweep_id, i, len(grid),
            result['metrics']['total_trades'],
            result['metrics'].get('sharpe_ratio'),
        )

    return sweep_id, results
