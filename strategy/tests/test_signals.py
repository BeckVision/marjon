"""Tests for signal registry and evaluation."""

from decimal import Decimal

from django.test import TestCase

from strategy.signals.registry import SIGNAL_REGISTRY

# Import triggers registration
import strategy.signals.u001  # noqa: F401

D = Decimal


class SignalRegistryTest(TestCase):

    def test_sg001_registered(self):
        self.assertIn('SG-001', SIGNAL_REGISTRY)

    def test_sg002_registered(self):
        self.assertIn('SG-002', SIGNAL_REGISTRY)

    def test_sg003_registered(self):
        self.assertIn('SG-003', SIGNAL_REGISTRY)

    def test_sg004_registered(self):
        self.assertIn('SG-004', SIGNAL_REGISTRY)

    def test_sg005_registered(self):
        self.assertIn('SG-005', SIGNAL_REGISTRY)

    def test_sg006_registered(self):
        self.assertIn('SG-006', SIGNAL_REGISTRY)

    def test_registry_completeness(self):
        """All U-001 signals are registered."""
        expected = {'SG-001', 'SG-002', 'SG-003', 'SG-004', 'SG-005', 'SG-006'}
        self.assertTrue(expected.issubset(set(SIGNAL_REGISTRY.keys())))


class SG001VolumeSpikeTest(TestCase):
    """SG-001: volume_ratio >= threshold."""

    def setUp(self):
        self.spec = SIGNAL_REGISTRY['SG-001']

    def test_fires_when_above_threshold(self):
        row = {'volume_ratio': D('5.0')}
        self.assertTrue(self.spec.evaluate(row, threshold=3.0))

    def test_does_not_fire_below_threshold(self):
        row = {'volume_ratio': D('2.0')}
        self.assertFalse(self.spec.evaluate(row, threshold=3.0))

    def test_fires_at_exact_threshold(self):
        row = {'volume_ratio': D('3.0')}
        self.assertTrue(self.spec.evaluate(row, threshold=3.0))

    def test_returns_false_on_null(self):
        row = {'volume_ratio': None}
        self.assertFalse(self.spec.evaluate(row, threshold=3.0))

    def test_param_override_changes_threshold(self):
        row = {'volume_ratio': D('2.0')}
        self.assertFalse(self.spec.evaluate(row, threshold=3.0))
        self.assertTrue(self.spec.evaluate(row, threshold=1.5))


class SG002PriceAboveVWAPTest(TestCase):
    """SG-002: close_price > vwap."""

    def setUp(self):
        self.spec = SIGNAL_REGISTRY['SG-002']

    def test_fires_when_above(self):
        row = {'close_price': D('100'), 'vwap': D('90')}
        self.assertTrue(self.spec.evaluate(row))

    def test_does_not_fire_when_below(self):
        row = {'close_price': D('80'), 'vwap': D('90')}
        self.assertFalse(self.spec.evaluate(row))

    def test_does_not_fire_at_equal(self):
        row = {'close_price': D('90'), 'vwap': D('90')}
        self.assertFalse(self.spec.evaluate(row))

    def test_returns_false_on_null_close(self):
        row = {'close_price': None, 'vwap': D('90')}
        self.assertFalse(self.spec.evaluate(row))

    def test_returns_false_on_null_vwap(self):
        row = {'close_price': D('100'), 'vwap': None}
        self.assertFalse(self.spec.evaluate(row))


class SG003PriceDropTest(TestCase):
    """SG-003: position ROI <= stop_loss_pct."""

    def setUp(self):
        self.spec = SIGNAL_REGISTRY['SG-003']

    def test_fires_when_roi_below_threshold(self):
        row = {'position_roi_pct': -60}
        self.assertTrue(self.spec.evaluate(row, stop_loss_pct=-50))

    def test_does_not_fire_when_above(self):
        row = {'position_roi_pct': -30}
        self.assertFalse(self.spec.evaluate(row, stop_loss_pct=-50))

    def test_fires_at_exact_threshold(self):
        row = {'position_roi_pct': -50}
        self.assertTrue(self.spec.evaluate(row, stop_loss_pct=-50))

    def test_returns_false_on_null_roi(self):
        row = {'position_roi_pct': None}
        self.assertFalse(self.spec.evaluate(row, stop_loss_pct=-50))


class SG004MomentumReturnTest(TestCase):
    def setUp(self):
        self.spec = SIGNAL_REGISTRY['SG-004']

    def test_fires_when_above_threshold(self):
        row = {'close_return_pct': D('25')}
        self.assertTrue(self.spec.evaluate(row, threshold_pct=20))

    def test_does_not_fire_when_below_threshold(self):
        row = {'close_return_pct': D('10')}
        self.assertFalse(self.spec.evaluate(row, threshold_pct=20))

    def test_returns_false_on_null(self):
        row = {'close_return_pct': None}
        self.assertFalse(self.spec.evaluate(row, threshold_pct=20))


class SG005BreakoutAbovePriorHighTest(TestCase):
    def setUp(self):
        self.spec = SIGNAL_REGISTRY['SG-005']

    def test_fires_at_threshold(self):
        row = {'breakout_ratio': D('1.01')}
        self.assertTrue(self.spec.evaluate(row, threshold_ratio=D('1.01')))

    def test_does_not_fire_below_threshold(self):
        row = {'breakout_ratio': D('0.99')}
        self.assertFalse(self.spec.evaluate(row, threshold_ratio=D('1.01')))

    def test_returns_false_on_null(self):
        row = {'breakout_ratio': None}
        self.assertFalse(self.spec.evaluate(row, threshold_ratio=D('1.01')))


class SG006StrongCloseTest(TestCase):
    def setUp(self):
        self.spec = SIGNAL_REGISTRY['SG-006']

    def test_fires_when_close_near_high(self):
        row = {'close_in_range': D('0.9')}
        self.assertTrue(self.spec.evaluate(row, threshold=D('0.8')))

    def test_does_not_fire_when_close_is_midrange(self):
        row = {'close_in_range': D('0.5')}
        self.assertFalse(self.spec.evaluate(row, threshold=D('0.8')))

    def test_returns_false_on_null(self):
        row = {'close_in_range': None}
        self.assertFalse(self.spec.evaluate(row, threshold=D('0.8')))
