"""Walk-forward testing — anti-overfitting validation.

Sweep in-sample, pick best params, test out-of-sample. Repeat across
rolling folds using anchored expanding windows.
"""

import logging
from decimal import Decimal

from .runner import BacktestRunner
from .sweep import generate_param_grid, apply_param_combination, run_sweep

logger = logging.getLogger(__name__)


def generate_folds(data_start, data_end, n_folds):
    """Generate anchored expanding window fold boundaries.

    Fold 1: IS = [start, T1], OOS = [T1, T2]
    Fold 2: IS = [start, T2], OOS = [T2, T3]
    ...
    Each fold expands in-sample, slides out-of-sample forward.

    Args:
        data_start: Start of data window.
        data_end: End of data window.
        n_folds: Number of folds.

    Returns:
        List of (is_start, is_end, oos_start, oos_end) tuples.
    """
    total_duration = data_end - data_start
    # Split total duration into (n_folds + 1) equal segments
    # First segment is always in IS, last n_folds segments alternate
    segment_duration = total_duration / (n_folds + 1)

    folds = []
    for i in range(n_folds):
        is_start = data_start
        is_end = data_start + segment_duration * (i + 1)
        oos_start = is_end
        oos_end = data_start + segment_duration * (i + 2)
        folds.append((is_start, is_end, oos_start, oos_end))

    return folds


def select_best_params(sweep_results, metric='sharpe_ratio'):
    """Pick best param combination from sweep results.

    Args:
        sweep_results: List of (combination_dict, result_dict) tuples
            from run_sweep().
        metric: Metric to maximize.

    Returns:
        (best_combination, best_result) tuple, or (None, None) if no results.
    """
    if not sweep_results:
        return None, None

    best_combo = None
    best_result = None
    best_value = None

    for combo, result in sweep_results:
        value = result['metrics'].get(metric)
        if value is None:
            continue
        if best_value is None or value > best_value:
            best_value = value
            best_combo = combo
            best_result = result

    return best_combo, best_result


def run_walk_forward(strategy_config, asset_ids, data_start, data_end,
                     n_folds=5, metric='sharpe_ratio'):
    """Run walk-forward validation.

    For each fold:
    1. Sweep IS → pick best params by metric
    2. Run single backtest on OOS with those params
    3. Record IS vs OOS performance

    Args:
        strategy_config: Strategy config dict.
        asset_ids: List of asset IDs.
        data_start: Start of full data window.
        data_end: End of full data window.
        n_folds: Number of walk-forward folds.
        metric: Metric to optimize in IS sweep.

    Returns:
        List of fold result dicts, each containing:
        - fold_num, is_start, is_end, oos_start, oos_end
        - best_combination (from IS sweep)
        - is_metric_value, oos_metric_value
        - is_result, oos_result
    """
    folds = generate_folds(data_start, data_end, n_folds)
    grid = generate_param_grid(strategy_config)

    logger.info(
        "Walk-forward: %d folds, %d param combinations, metric=%s",
        n_folds, len(grid), metric,
    )

    fold_results = []

    for fold_num, (is_start, is_end, oos_start, oos_end) in enumerate(folds, 1):
        logger.info(
            "Fold %d/%d: IS=[%s, %s] OOS=[%s, %s]",
            fold_num, n_folds, is_start, is_end, oos_start, oos_end,
        )

        # 1. Sweep in-sample
        sweep_id, is_results = run_sweep(
            strategy_config, asset_ids, is_start, is_end,
            sweep_id=f"wf_fold{fold_num}_is",
        )

        # 2. Pick best params
        best_combo, best_is_result = select_best_params(is_results, metric)

        if best_combo is None:
            logger.warning("Fold %d: no valid IS results, skipping", fold_num)
            fold_results.append({
                'fold_num': fold_num,
                'is_start': is_start,
                'is_end': is_end,
                'oos_start': oos_start,
                'oos_end': oos_end,
                'best_combination': None,
                'is_metric_value': None,
                'oos_metric_value': None,
                'is_result': None,
                'oos_result': None,
            })
            continue

        is_metric_value = best_is_result['metrics'].get(metric)

        # 3. Run OOS with best params
        modified_config = apply_param_combination(strategy_config, best_combo)
        oos_runner = BacktestRunner(
            modified_config, asset_ids, oos_start, oos_end,
        )
        oos_result = oos_runner.run()
        oos_metric_value = oos_result['metrics'].get(metric)

        logger.info(
            "Fold %d: best IS %s=%s, OOS %s=%s, combo=%s",
            fold_num, metric, is_metric_value,
            metric, oos_metric_value, best_combo,
        )

        fold_results.append({
            'fold_num': fold_num,
            'is_start': is_start,
            'is_end': is_end,
            'oos_start': oos_start,
            'oos_end': oos_end,
            'best_combination': best_combo,
            'is_metric_value': is_metric_value,
            'oos_metric_value': oos_metric_value,
            'is_result': best_is_result,
            'oos_result': oos_result,
        })

    return fold_results
