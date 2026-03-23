"""Tests that enforce abstract base contracts across all concrete models.

These tests discover all concrete subclasses via Django's app registry and
validate that paradigm constants are set, universe types are valid, and
window type conventions are followed. They pass today with U-001 only and
serve as guardrails for any future universe.
"""

from datetime import datetime, timedelta, timezone

from django.apps import apps
from django.test import TestCase

from warehouse.models import (
    FeatureLayerBase,
    MigratedCoin,
    ReferenceTableBase,
    UniverseBase,
)

T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)

VALID_UNIVERSE_TYPES = {'event-driven', 'calendar-driven'}


def _concrete_subclasses(base):
    """Return all non-abstract registered models that inherit from base."""
    return [
        model for model in apps.get_models()
        if issubclass(model, base) and not model._meta.abstract
    ]


# ---------------------------------------------------------------------------
# UniverseBase contract
# ---------------------------------------------------------------------------

class UniverseBaseContractTest(TestCase):
    """Every concrete UniverseBase subclass must declare its identity."""

    def test_all_set_required_constants(self):
        required = ['UNIVERSE_ID', 'NAME', 'UNIVERSE_TYPE', 'VERSION']
        for model in _concrete_subclasses(UniverseBase):
            for attr in required:
                value = getattr(model, attr, None)
                self.assertIsNotNone(
                    value,
                    f"{model.__name__}.{attr} is None — "
                    f"concrete universe models must set all required constants",
                )

    def test_universe_type_is_valid(self):
        for model in _concrete_subclasses(UniverseBase):
            self.assertIn(
                model.UNIVERSE_TYPE, VALID_UNIVERSE_TYPES,
                f"{model.__name__}.UNIVERSE_TYPE = {model.UNIVERSE_TYPE!r} "
                f"— must be one of {VALID_UNIVERSE_TYPES}",
            )

    def test_event_driven_has_timedelta_windows(self):
        """Event-driven universes use timedelta offsets from anchor_event."""
        for model in _concrete_subclasses(UniverseBase):
            if model.UNIVERSE_TYPE != 'event-driven':
                continue
            self.assertIsInstance(
                model.OBSERVATION_WINDOW_START, timedelta,
                f"{model.__name__}.OBSERVATION_WINDOW_START must be timedelta "
                f"for event-driven universes",
            )
            self.assertIsInstance(
                model.OBSERVATION_WINDOW_END, timedelta,
                f"{model.__name__}.OBSERVATION_WINDOW_END must be timedelta "
                f"for event-driven universes",
            )

    def test_calendar_driven_windows_are_datetime_or_none(self):
        """Calendar-driven universes use absolute datetime or None (unbounded)."""
        for model in _concrete_subclasses(UniverseBase):
            if model.UNIVERSE_TYPE != 'calendar-driven':
                continue
            ws = model.OBSERVATION_WINDOW_START
            we = model.OBSERVATION_WINDOW_END
            self.assertTrue(
                ws is None or isinstance(ws, datetime),
                f"{model.__name__}.OBSERVATION_WINDOW_START must be "
                f"datetime or None for calendar-driven universes, got {type(ws)}",
            )
            self.assertTrue(
                we is None or isinstance(we, datetime),
                f"{model.__name__}.OBSERVATION_WINDOW_END must be "
                f"datetime or None for calendar-driven universes, got {type(we)}",
            )


# ---------------------------------------------------------------------------
# FeatureLayerBase contract
# ---------------------------------------------------------------------------

class FeatureLayerBaseContractTest(TestCase):
    """Every concrete FeatureLayerBase subclass must declare its identity."""

    def test_all_set_required_constants(self):
        required = [
            'LAYER_ID', 'UNIVERSE_ID', 'NAME',
            'TEMPORAL_RESOLUTION', 'AVAILABILITY_RULE', 'VERSION',
        ]
        for model in _concrete_subclasses(FeatureLayerBase):
            for attr in required:
                value = getattr(model, attr, None)
                self.assertIsNotNone(
                    value,
                    f"{model.__name__}.{attr} is None — "
                    f"concrete feature layer models must set all required constants",
                )

    def test_temporal_resolution_is_timedelta(self):
        for model in _concrete_subclasses(FeatureLayerBase):
            self.assertIsInstance(
                model.TEMPORAL_RESOLUTION, timedelta,
                f"{model.__name__}.TEMPORAL_RESOLUTION must be timedelta",
            )


# ---------------------------------------------------------------------------
# ReferenceTableBase contract
# ---------------------------------------------------------------------------

class ReferenceTableBaseContractTest(TestCase):
    """Every concrete ReferenceTableBase subclass must declare its identity."""

    def test_all_set_required_constants(self):
        required = [
            'REFERENCE_ID', 'UNIVERSE_ID', 'NAME',
            'RECORD_TYPE', 'AVAILABILITY_RULE', 'VERSION',
        ]
        for model in _concrete_subclasses(ReferenceTableBase):
            for attr in required:
                value = getattr(model, attr, None)
                self.assertIsNotNone(
                    value,
                    f"{model.__name__}.{attr} is None — "
                    f"concrete reference table models must set all required constants",
                )


# ---------------------------------------------------------------------------
# UniverseBase property tests (using existing U-001 MigratedCoin)
# ---------------------------------------------------------------------------

class UniverseBasePropertyTest(TestCase):
    """Test window_start_time, window_end_time, is_mature on UniverseBase."""

    def test_window_end_time_event_driven(self):
        coin = MigratedCoin(mint_address='WET_TEST', anchor_event=T0)
        expected = T0 + MigratedCoin.OBSERVATION_WINDOW_END
        self.assertEqual(coin.window_end_time, expected)

    def test_window_start_time_event_driven(self):
        coin = MigratedCoin(mint_address='WST_TEST', anchor_event=T0)
        expected = T0 + MigratedCoin.OBSERVATION_WINDOW_START
        self.assertEqual(coin.window_start_time, expected)

    def test_window_times_none_when_no_anchor_event_driven(self):
        coin = MigratedCoin(mint_address='NO_ANCHOR')
        self.assertIsNone(coin.window_start_time)
        self.assertIsNone(coin.window_end_time)

    def test_is_mature_event_driven_no_anchor_returns_false(self):
        coin = MigratedCoin(mint_address='NO_ANCHOR')
        self.assertFalse(coin.is_mature)

    def test_is_mature_event_driven_past_window_returns_true(self):
        coin = MigratedCoin(
            mint_address='OLD_COIN',
            anchor_event=T0 - timedelta(days=10),
        )
        self.assertTrue(coin.is_mature)

    def test_is_mature_event_driven_within_window_returns_false(self):
        from django.utils import timezone as dj_tz
        coin = MigratedCoin(
            mint_address='FRESH_COIN',
            anchor_event=dj_tz.now() - timedelta(hours=1),
        )
        self.assertFalse(coin.is_mature)
