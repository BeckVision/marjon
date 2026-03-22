"""Tests for derived features — registry, VWAP computation, and data service integration."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.test import TestCase

from data_service.derived import (
    DERIVED_REGISTRY,
    _compute_volume_ratio,
    _compute_vwap,
    compute_derived,
)
from data_service.operations import get_panel_slice
from warehouse.models import MigratedCoin, OHLCVCandle

T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
D = Decimal


# --- Registry tests ---------------------------------------------------------

class DerivedRegistryTest(TestCase):
    def test_df001_registered(self):
        self.assertIn('DF-001', DERIVED_REGISTRY)

    def test_df001_spec(self):
        spec = DERIVED_REGISTRY['DF-001']
        self.assertEqual(spec.name, 'Volume Weighted Average Price (VWAP)')
        self.assertEqual(spec.source_layers, ['FL-001'])
        self.assertEqual(spec.output_fields, ['vwap'])
        self.assertEqual(spec.parameters['window_size'], 20)
        self.assertEqual(spec.warm_up, 19)

    def test_unknown_derived_raises(self):
        with self.assertRaises(ValueError):
            compute_derived([], ['DF-999'])


# --- VWAP computation tests -------------------------------------------------

class VWAPComputationTest(TestCase):
    """Unit tests for _compute_vwap with small windows."""

    def _make_row(self, i, high, low, close, volume):
        return {
            'coin_id': 'TEST',
            'timestamp': T0 + timedelta(minutes=5 * i),
            'high_price': D(str(high)),
            'low_price': D(str(low)),
            'close_price': D(str(close)),
            'volume': D(str(volume)),
        }

    def test_basic_vwap_window_3(self):
        """3-candle window: hand-calculated VWAP."""
        rows = [
            self._make_row(0, 12, 8, 10, 100),   # tp = 10
            self._make_row(1, 15, 9, 12, 200),    # tp = 12
            self._make_row(2, 18, 12, 15, 300),   # tp = 15
        ]
        _compute_vwap(rows, window_size=3)

        # Warm-up: first 2 rows are None
        self.assertIsNone(rows[0]['vwap'])
        self.assertIsNone(rows[1]['vwap'])

        # Row 2: VWAP = (10*100 + 12*200 + 15*300) / (100+200+300)
        # = (1000 + 2400 + 4500) / 600 = 7900/600
        expected = D('7900') / D('600')
        self.assertEqual(rows[2]['vwap'], expected)

    def test_rolling_window_slides(self):
        """Window slides forward — old candles drop out."""
        rows = [
            self._make_row(0, 12, 8, 10, 100),    # tp = 10
            self._make_row(1, 15, 9, 12, 200),     # tp = 12
            self._make_row(2, 18, 12, 15, 300),    # tp = 15
            self._make_row(3, 21, 15, 18, 400),    # tp = 18
        ]
        _compute_vwap(rows, window_size=3)

        # Row 3: window is rows 1,2,3
        # VWAP = (12*200 + 15*300 + 18*400) / (200+300+400)
        # = (2400 + 4500 + 7200) / 900 = 14100/900
        expected = D('14100') / D('900')
        self.assertEqual(rows[3]['vwap'], expected)

    def test_null_prices_excluded(self):
        """Rows with null prices are excluded from the window."""
        rows = [
            self._make_row(0, 12, 8, 10, 100),
            {'coin_id': 'TEST', 'timestamp': T0 + timedelta(minutes=5),
             'high_price': None, 'low_price': None,
             'close_price': None, 'volume': D('200')},
            self._make_row(2, 18, 12, 15, 300),
        ]
        _compute_vwap(rows, window_size=3)

        # Only rows 0 and 2 contribute: (10*100 + 15*300) / (100+300)
        expected = D('5500') / D('400')
        self.assertEqual(rows[2]['vwap'], expected)

    def test_zero_volume_excluded(self):
        """Rows with zero volume are excluded from the window."""
        rows = [
            self._make_row(0, 12, 8, 10, 0),
            self._make_row(1, 15, 9, 12, 200),
            self._make_row(2, 18, 12, 15, 300),
        ]
        _compute_vwap(rows, window_size=3)

        # Row 0 excluded (zero volume): (12*200 + 15*300) / (200+300)
        expected = D('6900') / D('500')
        self.assertEqual(rows[2]['vwap'], expected)

    def test_all_zero_volume_returns_none(self):
        """Window entirely zero volume → VWAP is None."""
        rows = [
            self._make_row(0, 12, 8, 10, 0),
            self._make_row(1, 15, 9, 12, 0),
            self._make_row(2, 18, 12, 15, 0),
        ]
        _compute_vwap(rows, window_size=3)
        self.assertIsNone(rows[2]['vwap'])

    def test_window_size_1(self):
        """Window of 1 — VWAP equals typical price of that candle."""
        rows = [
            self._make_row(0, 12, 6, 9, 100),  # tp = (12+6+9)/3 = 9
        ]
        _compute_vwap(rows, window_size=1)
        self.assertEqual(rows[0]['vwap'], D('9'))


# --- Data service integration tests -----------------------------------------

class VWAPIntegrationTest(TestCase):
    """Test VWAP through get_panel_slice with derived_ids."""

    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='VWAP_COIN', anchor_event=T0,
        )
        # Create 5 candles
        for i in range(5):
            OHLCVCandle.objects.create(
                coin_id='VWAP_COIN',
                timestamp=T0 + timedelta(minutes=5 * (i + 1)),
                open_price=D('10'),
                high_price=D('12'),
                low_price=D('8'),
                close_price=D('10'),
                volume=D('100'),
            )

    def test_vwap_column_present(self):
        """get_panel_slice with DF-001 adds vwap column."""
        sim = T0 + timedelta(minutes=30)
        result = get_panel_slice(
            ['VWAP_COIN'], ['FL-001'], sim, derived_ids=['DF-001'],
        )
        self.assertTrue(len(result) > 0)
        self.assertIn('vwap', result[0])

    def test_vwap_without_derived_ids(self):
        """get_panel_slice without derived_ids has no vwap column."""
        sim = T0 + timedelta(minutes=30)
        result = get_panel_slice(
            ['VWAP_COIN'], ['FL-001'], sim,
        )
        self.assertTrue(len(result) > 0)
        self.assertNotIn('vwap', result[0])

    def test_unknown_derived_raises(self):
        """Unknown derived ID raises ValueError."""
        sim = T0 + timedelta(minutes=30)
        with self.assertRaises(ValueError):
            get_panel_slice(
                ['VWAP_COIN'], ['FL-001'], sim, derived_ids=['DF-999'],
            )

    def test_warm_up_produces_none(self):
        """With window_size=20 and only 5 candles, all are warm-up → None."""
        sim = T0 + timedelta(minutes=30)
        result = get_panel_slice(
            ['VWAP_COIN'], ['FL-001'], sim, derived_ids=['DF-001'],
        )
        # Default window is 20, we only have 5 candles → all None
        for row in result:
            self.assertIsNone(row['vwap'])

    def test_param_override_changes_window(self):
        """Override window_size=3 → last 3 candles produce a value."""
        sim = T0 + timedelta(minutes=30)
        result = get_panel_slice(
            ['VWAP_COIN'], ['FL-001'], sim,
            derived_ids=['DF-001'],
            derived_params={'DF-001': {'window_size': 3}},
        )
        # 5 candles, window=3: first 2 are None, last 3 have values
        none_count = sum(1 for r in result if r['vwap'] is None)
        value_count = sum(1 for r in result if r['vwap'] is not None)
        self.assertEqual(none_count, 2)
        self.assertEqual(value_count, 3)

    def test_param_override_does_not_mutate_spec(self):
        """Overriding params doesn't change the registered spec defaults."""
        from data_service.derived import DERIVED_REGISTRY
        sim = T0 + timedelta(minutes=30)
        get_panel_slice(
            ['VWAP_COIN'], ['FL-001'], sim,
            derived_ids=['DF-001'],
            derived_params={'DF-001': {'window_size': 3}},
        )
        # Spec default should still be 20
        self.assertEqual(DERIVED_REGISTRY['DF-001'].parameters['window_size'], 20)


# --- Volume ratio computation tests -----------------------------------------

class VolumeRatioRegistryTest(TestCase):
    def test_df002_registered(self):
        self.assertIn('DF-002', DERIVED_REGISTRY)

    def test_df002_spec(self):
        spec = DERIVED_REGISTRY['DF-002']
        self.assertEqual(spec.source_layers, ['FL-001'])
        self.assertEqual(spec.output_fields, ['volume_ratio'])
        self.assertEqual(spec.parameters['lookback'], 20)
        self.assertEqual(spec.warm_up, 20)


class VolumeRatioComputationTest(TestCase):
    """Unit tests for _compute_volume_ratio with small lookbacks."""

    def _make_row(self, i, volume):
        return {
            'coin_id': 'TEST',
            'timestamp': T0 + timedelta(minutes=5 * i),
            'volume': D(str(volume)),
        }

    def test_basic_ratio(self):
        """Lookback=3, current volume is 2x the mean."""
        rows = [
            self._make_row(0, 100),
            self._make_row(1, 100),
            self._make_row(2, 100),
            self._make_row(3, 200),  # mean of prior 3 = 100, ratio = 2
        ]
        _compute_volume_ratio(rows, lookback=3)

        # First 3 are warm-up
        for i in range(3):
            self.assertIsNone(rows[i]['volume_ratio'])

        self.assertEqual(rows[3]['volume_ratio'], D('2'))

    def test_ratio_below_one(self):
        """Current volume below average → ratio < 1."""
        rows = [
            self._make_row(0, 200),
            self._make_row(1, 200),
            self._make_row(2, 200),
            self._make_row(3, 50),  # mean = 200, ratio = 0.25
        ]
        _compute_volume_ratio(rows, lookback=3)
        self.assertEqual(rows[3]['volume_ratio'], D('0.25'))

    def test_rolling_window_excludes_current(self):
        """The current candle is NOT in the lookback mean."""
        rows = [
            self._make_row(0, 100),
            self._make_row(1, 100),
            self._make_row(2, 100),
            self._make_row(3, 300),  # ratio = 300/100 = 3
            self._make_row(4, 100),  # mean of [100,100,300] = 500/3
        ]
        _compute_volume_ratio(rows, lookback=3)

        self.assertEqual(rows[3]['volume_ratio'], D('3'))
        # Row 4: mean = (100+100+300)/3 = 500/3, ratio = 100/(500/3) = 300/500
        expected = D('100') / (D('500') / D('3'))
        self.assertEqual(rows[4]['volume_ratio'], expected)

    def test_null_volume_excluded_from_mean(self):
        """Null volumes in lookback are excluded from the mean."""
        rows = [
            self._make_row(0, 100),
            {'coin_id': 'TEST', 'timestamp': T0 + timedelta(minutes=5),
             'volume': None},
            self._make_row(2, 300),
            self._make_row(3, 400),  # mean of [100, 300] = 200, ratio = 2
        ]
        _compute_volume_ratio(rows, lookback=3)
        self.assertEqual(rows[3]['volume_ratio'], D('2'))

    def test_all_zero_mean_returns_none(self):
        """All prior candles have zero volume → ratio is None."""
        rows = [
            self._make_row(0, 0),
            self._make_row(1, 0),
            self._make_row(2, 0),
            self._make_row(3, 100),
        ]
        _compute_volume_ratio(rows, lookback=3)
        self.assertIsNone(rows[3]['volume_ratio'])

    def test_current_null_volume_returns_none(self):
        """Current candle has null volume → ratio is None."""
        rows = [
            self._make_row(0, 100),
            self._make_row(1, 100),
            self._make_row(2, 100),
            {'coin_id': 'TEST', 'timestamp': T0 + timedelta(minutes=15),
             'volume': None},
        ]
        _compute_volume_ratio(rows, lookback=3)
        self.assertIsNone(rows[3]['volume_ratio'])


class VolumeRatioIntegrationTest(TestCase):
    """Test volume ratio through get_panel_slice."""

    def setUp(self):
        self.coin = MigratedCoin.objects.create(
            mint_address='VRATIO_COIN', anchor_event=T0,
        )
        for i in range(5):
            OHLCVCandle.objects.create(
                coin_id='VRATIO_COIN',
                timestamp=T0 + timedelta(minutes=5 * (i + 1)),
                open_price=D('10'),
                high_price=D('12'),
                low_price=D('8'),
                close_price=D('10'),
                volume=D('100'),
            )

    def test_volume_ratio_with_override(self):
        """Override lookback=3, last 2 candles get values."""
        sim = T0 + timedelta(minutes=30)
        result = get_panel_slice(
            ['VRATIO_COIN'], ['FL-001'], sim,
            derived_ids=['DF-002'],
            derived_params={'DF-002': {'lookback': 3}},
        )
        # 5 candles, lookback=3: first 3 None, last 2 have values
        none_count = sum(1 for r in result if r['volume_ratio'] is None)
        value_count = sum(1 for r in result if r['volume_ratio'] is not None)
        self.assertEqual(none_count, 3)
        self.assertEqual(value_count, 2)
        # All volumes are 100, so ratio should be 1.0
        for r in result:
            if r['volume_ratio'] is not None:
                self.assertEqual(r['volume_ratio'], D('1'))

    def test_both_derived_features(self):
        """Request DF-001 and DF-002 together."""
        sim = T0 + timedelta(minutes=30)
        result = get_panel_slice(
            ['VRATIO_COIN'], ['FL-001'], sim,
            derived_ids=['DF-001', 'DF-002'],
            derived_params={
                'DF-001': {'window_size': 3},
                'DF-002': {'lookback': 3},
            },
        )
        self.assertTrue(len(result) > 0)
        self.assertIn('vwap', result[-1])
        self.assertIn('volume_ratio', result[-1])
