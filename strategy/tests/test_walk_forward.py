"""Tests for walk-forward validation."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.test import TestCase

from strategy.engine.walk_forward import (
    generate_folds,
    run_walk_forward,
    select_best_params,
)
from warehouse.models import MigratedCoin, OHLCVCandle

D = Decimal
T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# generate_folds
# ---------------------------------------------------------------------------

class GenerateFoldsTest(TestCase):

    def test_fold_count(self):
        folds = generate_folds(T0, T0 + timedelta(hours=6), n_folds=5)
        self.assertEqual(len(folds), 5)

    def test_anchored_expanding_window(self):
        """IS always starts at data_start; IS grows, OOS slides."""
        folds = generate_folds(T0, T0 + timedelta(hours=6), n_folds=5)
        for is_start, is_end, oos_start, oos_end in folds:
            self.assertEqual(is_start, T0)
            self.assertEqual(is_end, oos_start)  # IS ends where OOS begins

        # IS window should expand
        self.assertLess(folds[0][1], folds[1][1])
        self.assertLess(folds[1][1], folds[2][1])

    def test_last_fold_ends_at_data_end(self):
        end = T0 + timedelta(hours=6)
        folds = generate_folds(T0, end, n_folds=5)
        self.assertEqual(folds[-1][3], end)

    def test_single_fold(self):
        folds = generate_folds(T0, T0 + timedelta(hours=2), n_folds=1)
        self.assertEqual(len(folds), 1)
        is_start, is_end, oos_start, oos_end = folds[0]
        self.assertEqual(is_start, T0)
        self.assertEqual(oos_end, T0 + timedelta(hours=2))


# ---------------------------------------------------------------------------
# select_best_params
# ---------------------------------------------------------------------------

class SelectBestParamsTest(TestCase):

    def test_empty_results(self):
        combo, result = select_best_params([])
        self.assertIsNone(combo)
        self.assertIsNone(result)

    def test_selects_highest_metric(self):
        results = [
            ({'threshold': 1.0}, {'metrics': {'sharpe_ratio': D('1.5'), 'total_trades': 5}, 'trades': []}),
            ({'threshold': 3.0}, {'metrics': {'sharpe_ratio': D('3.0'), 'total_trades': 5}, 'trades': []}),
            ({'threshold': 5.0}, {'metrics': {'sharpe_ratio': D('0.5'), 'total_trades': 5}, 'trades': []}),
        ]
        combo, result = select_best_params(results, 'sharpe_ratio')
        self.assertEqual(combo, {'threshold': 3.0})
        self.assertEqual(result['metrics']['sharpe_ratio'], D('3.0'))

    def test_skips_none_metric_values(self):
        results = [
            ({'a': 1}, {'metrics': {'sharpe_ratio': None, 'total_trades': 0}, 'trades': []}),
            ({'a': 2}, {'metrics': {'sharpe_ratio': D('1.0'), 'total_trades': 3}, 'trades': []}),
        ]
        combo, result = select_best_params(results, 'sharpe_ratio')
        self.assertEqual(combo, {'a': 2})


# ---------------------------------------------------------------------------
# run_walk_forward integration
# ---------------------------------------------------------------------------

class RunWalkForwardTest(TestCase):

    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='WF_COIN', anchor_event=T0,
        )
        # Create enough data for walk-forward: 60 candles = 5 hours at 5-min
        for i in range(60):
            OHLCVCandle.objects.create(
                coin_id='WF_COIN',
                timestamp=T0 + timedelta(minutes=5 * (i + 1)),
                open_price=D('10'), high_price=D('10'),
                low_price=D('10'), close_price=D('10'),
                volume=D('100'),
            )

    def test_walk_forward_returns_fold_results(self):
        config = {
            'id': 'test_wf',
            'name': 'Test WF',
            'version': 1,
            'universe_id': 'U-001',
            'hypothesis': 'Test',
            'signals': [
                {
                    'signal_id': 'SG-002',
                    'role': 'entry',
                    'required': True,
                    'param_overrides': {},
                    'evaluation': {'mode': 'point_in_time'},
                },
            ],
            'entry_rules': {'require_all': True},
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
                    'DF-001': {'window_size': 3},
                    'DF-002': {'lookback': 3},
                },
            },
            'price_field': 'close_price',
        }
        fold_results = run_walk_forward(
            config, ['WF_COIN'],
            T0, T0 + timedelta(hours=5),
            n_folds=3,
        )
        self.assertEqual(len(fold_results), 3)
        for fr in fold_results:
            self.assertIn('fold_num', fr)
            self.assertIn('is_start', fr)
            self.assertIn('oos_start', fr)
            self.assertIn('oos_result', fr)

    def test_fold_results_have_expected_keys(self):
        config = {
            'id': 'test_wf2',
            'name': 'Test WF 2',
            'version': 1,
            'universe_id': 'U-001',
            'hypothesis': 'Test',
            'signals': [
                {
                    'signal_id': 'SG-002',
                    'role': 'entry',
                    'required': True,
                    'param_overrides': {},
                    'evaluation': {'mode': 'point_in_time'},
                },
            ],
            'entry_rules': {'require_all': True},
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
                    'DF-001': {'window_size': 3},
                    'DF-002': {'lookback': 3},
                },
            },
            'price_field': 'close_price',
        }
        fold_results = run_walk_forward(
            config, ['WF_COIN'],
            T0, T0 + timedelta(hours=5),
            n_folds=2,
        )
        expected_keys = {
            'fold_num', 'is_start', 'is_end', 'oos_start', 'oos_end',
            'best_combination', 'is_metric_value', 'oos_metric_value',
            'is_result', 'oos_result',
        }
        for fr in fold_results:
            self.assertEqual(set(fr.keys()), expected_keys)
