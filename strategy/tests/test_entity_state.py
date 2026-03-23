"""Tests for EntityState and EntityStateTracker — pure unit tests, no DB."""

from datetime import datetime, timedelta, timezone

from django.test import TestCase

from strategy.engine.entity_state import EntityState, EntityStateTracker

T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


class EntityStateTest(TestCase):

    def test_new_state_is_available(self):
        state = EntityState('COIN_A')
        self.assertTrue(state.is_available(T0))

    def test_permanent_disqualification(self):
        state = EntityState('COIN_A')
        state.disqualified = True
        self.assertFalse(state.is_available(T0))

    def test_cooldown_blocks_during_period(self):
        state = EntityState('COIN_A')
        state.cooldown_until = T0 + timedelta(minutes=30)
        self.assertFalse(state.is_available(T0))
        self.assertFalse(state.is_available(T0 + timedelta(minutes=29)))

    def test_cooldown_expires(self):
        state = EntityState('COIN_A')
        state.cooldown_until = T0 + timedelta(minutes=30)
        self.assertTrue(state.is_available(T0 + timedelta(minutes=30)))
        self.assertTrue(state.is_available(T0 + timedelta(minutes=60)))


class EntityStateTrackerTest(TestCase):

    def test_get_or_create(self):
        tracker = EntityStateTracker()
        state = tracker.get_or_create('COIN_A')
        self.assertEqual(state.asset_id, 'COIN_A')
        # Same instance on second call
        self.assertIs(tracker.get_or_create('COIN_A'), state)

    def test_is_available_no_state(self):
        tracker = EntityStateTracker()
        self.assertTrue(tracker.is_available('COIN_A', T0))

    def test_permanent_disqualification(self):
        tracker = EntityStateTracker()
        tracker.apply_disqualification('COIN_A', 'permanent')
        self.assertFalse(tracker.is_available('COIN_A', T0))

    def test_cooldown_disqualification(self):
        tracker = EntityStateTracker()
        tracker.apply_disqualification(
            'COIN_A', 'cooldown', cooldown_minutes=30, current_time=T0,
        )
        self.assertFalse(tracker.is_available('COIN_A', T0 + timedelta(minutes=15)))
        self.assertTrue(tracker.is_available('COIN_A', T0 + timedelta(minutes=30)))

    def test_none_disqualification_does_nothing(self):
        tracker = EntityStateTracker()
        tracker.apply_disqualification('COIN_A', 'none')
        self.assertTrue(tracker.is_available('COIN_A', T0))

    def test_record_and_check_direction_rose_into(self):
        tracker = EntityStateTracker()
        tracker.record_evaluation('COIN_A', 'SG-001', T0, False)
        tracker.record_evaluation('COIN_A', 'SG-001', T0 + timedelta(minutes=5), True)
        self.assertTrue(tracker.check_direction('COIN_A', 'SG-001', 'rose_into'))
        self.assertFalse(tracker.check_direction('COIN_A', 'SG-001', 'fell_into'))

    def test_check_direction_fell_into(self):
        tracker = EntityStateTracker()
        tracker.record_evaluation('COIN_A', 'SG-001', T0, True)
        tracker.record_evaluation('COIN_A', 'SG-001', T0 + timedelta(minutes=5), False)
        self.assertTrue(tracker.check_direction('COIN_A', 'SG-001', 'fell_into'))
        self.assertFalse(tracker.check_direction('COIN_A', 'SG-001', 'rose_into'))

    def test_check_direction_started_in(self):
        tracker = EntityStateTracker()
        tracker.record_evaluation('COIN_A', 'SG-001', T0, True)
        tracker.record_evaluation('COIN_A', 'SG-001', T0 + timedelta(minutes=5), True)
        self.assertTrue(tracker.check_direction('COIN_A', 'SG-001', 'started_in'))

    def test_check_direction_any(self):
        tracker = EntityStateTracker()
        self.assertTrue(tracker.check_direction('COIN_A', 'SG-001', 'any'))

    def test_continuous_confirmation(self):
        tracker = EntityStateTracker()
        # 3 consecutive True evaluations at 5-min intervals = 15 min
        for i in range(3):
            tracker.record_evaluation(
                'COIN_A', 'SG-001', T0 + timedelta(minutes=5 * i), True,
            )
        # Require 15 min continuous (3 steps × 5 min)
        self.assertTrue(
            tracker.check_confirmation('COIN_A', 'SG-001', 'continuous', 15, T0)
        )
        # Require 20 min continuous (4 steps) — not enough history
        self.assertFalse(
            tracker.check_confirmation('COIN_A', 'SG-001', 'continuous', 20, T0)
        )

    def test_confirmation_none_always_true(self):
        tracker = EntityStateTracker()
        self.assertTrue(
            tracker.check_confirmation('COIN_A', 'SG-001', 'none', 0, T0)
        )

    def test_rolling_window_lookback(self):
        tracker = EntityStateTracker()
        for i in range(10):
            tracker.record_evaluation(
                'COIN_A', 'SG-001', T0 + timedelta(minutes=5 * i), True,
            )
        current = T0 + timedelta(minutes=45)
        tracker.apply_lookback(
            'COIN_A', 'SG-001', 'rolling_window',
            lookback_minutes=20, current_time=current,
        )
        state = tracker.get_or_create('COIN_A')
        # Only entries from last 20 min should remain
        for ts, _, _ in state.value_history['SG-001']:
            self.assertGreaterEqual(ts, current - timedelta(minutes=20))
