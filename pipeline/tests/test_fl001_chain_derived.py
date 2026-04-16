from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from pipeline.audits.fl001_chain_derived import compare_candles, derive_candles


class FL001ChainDerivedTest(SimpleTestCase):
    def test_derive_candles_builds_single_bucket(self):
        trades = [
            SimpleNamespace(
                timestamp=datetime(2026, 4, 13, 0, 1, tzinfo=timezone.utc),
                token_amount=1_000_000,
                sol_amount=100_000_000,
            ),
            SimpleNamespace(
                timestamp=datetime(2026, 4, 13, 0, 3, tzinfo=timezone.utc),
                token_amount=500_000,
                sol_amount=75_000_000,
            ),
        ]
        sol_usd = {
            datetime(2026, 4, 13, 0, 1, tzinfo=timezone.utc): Decimal('120'),
            datetime(2026, 4, 13, 0, 3, tzinfo=timezone.utc): Decimal('125'),
        }

        rows, meta = derive_candles(trades, sol_usd, token_decimals=6)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['timestamp'], datetime(2026, 4, 13, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(rows[0]['open_price'], Decimal('12'))
        self.assertEqual(rows[0]['high_price'], Decimal('18.75'))
        self.assertEqual(rows[0]['low_price'], Decimal('12'))
        self.assertEqual(rows[0]['close_price'], Decimal('18.75'))
        self.assertEqual(rows[0]['volume'], Decimal('21.375'))
        self.assertEqual(rows[0]['trade_count'], 2)
        self.assertEqual(meta['skipped_missing_sol_price'], 0)

    def test_compare_candles_detects_drift_without_timestamp_mismatch(self):
        timestamp = datetime(2026, 4, 13, 0, 0, tzinfo=timezone.utc)
        result = compare_candles(
            coin_id='MINT_A',
            start=timestamp,
            end=timestamp + timedelta(minutes=5),
            stored_candles=[
                {
                    'timestamp': timestamp,
                    'open_price': Decimal('10'),
                    'high_price': Decimal('10'),
                    'low_price': Decimal('10'),
                    'close_price': Decimal('10'),
                    'volume': Decimal('10'),
                },
            ],
            derived_candles=[
                {
                    'timestamp': timestamp,
                    'open_price': Decimal('11'),
                    'high_price': Decimal('11'),
                    'low_price': Decimal('11'),
                    'close_price': Decimal('11'),
                    'volume': Decimal('13'),
                },
            ],
            price_tolerance_pct=Decimal('0.05'),
            volume_tolerance_pct=Decimal('0.10'),
        )

        self.assertEqual(result['status'], 'warning')
        self.assertEqual(result['findings'], [])
        self.assertIn('candle_value_drift', result['warnings'])
        self.assertGreaterEqual(result['field_drift_counts']['open_price'], 1)

    def test_compare_candles_detects_missing_timestamp(self):
        timestamp = datetime(2026, 4, 13, 0, 0, tzinfo=timezone.utc)
        result = compare_candles(
            coin_id='MINT_A',
            start=timestamp,
            end=timestamp + timedelta(minutes=5),
            stored_candles=[],
            derived_candles=[
                {
                    'timestamp': timestamp,
                    'open_price': Decimal('10'),
                    'high_price': Decimal('10'),
                    'low_price': Decimal('10'),
                    'close_price': Decimal('10'),
                    'volume': Decimal('10'),
                },
            ],
        )

        self.assertEqual(result['status'], 'finding')
        self.assertIn('missing_derived_candles', result['findings'])
