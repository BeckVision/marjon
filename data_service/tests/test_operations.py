"""Tests for data service operations."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.test import TestCase

from data_service.operations import (
    get_panel_slice,
    get_reference_data,
    get_universe_members,
)
from warehouse.models import (
    HolderSnapshot,
    MigratedCoin,
    OHLCVCandle,
)

T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


class UniverseMembersTest(TestCase):
    def setUp(self):
        self.coin1 = MigratedCoin.objects.create(
            mint_address='COIN_A', anchor_event=T0,
        )
        self.coin2 = MigratedCoin.objects.create(
            mint_address='COIN_B',
            anchor_event=T0 + timedelta(hours=1),
        )
        self.coin3 = MigratedCoin.objects.create(
            mint_address='COIN_C',
            anchor_event=T0 + timedelta(hours=2),
        )

    def test_at_10_30_only_coin1(self):
        sim = T0 + timedelta(minutes=30)
        members = get_universe_members(sim)
        mints = list(members.values_list('mint_address', flat=True))
        self.assertEqual(mints, ['COIN_A'])

    def test_at_11_30_coins_1_and_2(self):
        sim = T0 + timedelta(hours=1, minutes=30)
        members = get_universe_members(sim)
        mints = sorted(
            members.values_list('mint_address', flat=True)
        )
        self.assertEqual(mints, ['COIN_A', 'COIN_B'])

    def test_at_12_30_all_three(self):
        sim = T0 + timedelta(hours=2, minutes=30)
        members = get_universe_members(sim)
        self.assertEqual(members.count(), 3)


class ReferenceDataTest(TestCase):
    def test_nonexistent_asset_raises(self):
        with self.assertRaises(ValueError):
            get_reference_data(
                'NONEXISTENT', T0, T0 + timedelta(hours=1), T0,
            )

    def test_valid_asset_no_data_returns_empty(self):
        MigratedCoin.objects.create(
            mint_address='COIN_REF', anchor_event=T0,
        )
        result = get_reference_data(
            'COIN_REF', T0, T0 + timedelta(hours=1), T0,
        )
        self.assertEqual(result.count(), 0)

    def test_time_range_outside_observation_window_raises(self):
        MigratedCoin.objects.create(
            mint_address='COIN_WINDOW', anchor_event=T0,
        )
        # Observation window is T0 to T0+5000min. Request beyond that.
        far_future = T0 + timedelta(minutes=6000)
        with self.assertRaises(ValueError):
            get_reference_data(
                'COIN_WINDOW', far_future, far_future + timedelta(hours=1),
                far_future,
            )


class PanelSliceValidationTest(TestCase):
    def test_nonexistent_asset_raises(self):
        with self.assertRaises(ValueError):
            get_panel_slice(
                ['NONEXISTENT'], ['FL-001'],
                T0 + timedelta(minutes=10),
            )

    def test_unknown_layer_raises(self):
        MigratedCoin.objects.create(
            mint_address='COIN_V', anchor_event=T0,
        )
        with self.assertRaises(ValueError):
            get_panel_slice(
                ['COIN_V'], ['FL-999'],
                T0 + timedelta(minutes=10),
            )

    def test_valid_asset_no_data_returns_empty(self):
        MigratedCoin.objects.create(
            mint_address='COIN_EMPTY', anchor_event=T0,
        )
        result = get_panel_slice(
            ['COIN_EMPTY'], ['FL-001'],
            T0 + timedelta(minutes=10),
        )
        self.assertEqual(result, [])
