"""Tests for Helius -> RawTransaction conformance (RD-001).

Cross-validates against known Shyft BuyEvent/SellEvent values from the
helius_shyft_comparison fixture. Verifies that the tokenTransfers-based
extraction produces the same results as Shyft's event-based parsing.
"""

import copy
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from django.test import TestCase

from pipeline.conformance.rd001_helius import conform

COMPARISON_FIXTURE = (
    Path(__file__).parent / 'fixtures' / 'helius_shyft_comparison.json'
)

MINT = '46HrKBBJSHaPUEAADfSNG989zrzvYm3aBVPTrQMKpump'
POOL = '5rUSFxPuMcGVCBFkfD6NbA39iMeRSrnZkNW2JXAZ8Vt6'


class RD001HeliusConformanceTest(TestCase):
    """Tests using the helius_shyft_comparison fixture."""

    @classmethod
    def setUpTestData(cls):
        with open(COMPARISON_FIXTURE) as f:
            cls.fixture = json.load(f)

        # Parse BUY
        cls.buy_parsed, cls.buy_skipped = conform(
            [cls.fixture['helius_buy']], MINT, POOL,
        )

        # Parse SELL
        cls.sell_parsed, cls.sell_skipped = conform(
            [cls.fixture['helius_sell']], MINT, POOL,
        )

        # Shyft reference values
        cls.shyft_buy_event = next(
            e['data'] for e in cls.fixture['shyft_buy']['events']
            if e['name'] == 'BuyEvent'
        )
        cls.shyft_sell_event = next(
            e['data'] for e in cls.fixture['shyft_sell']['events']
            if e['name'] == 'SellEvent'
        )

    # ------------------------------------------------------------------
    # 1. Record counts
    # ------------------------------------------------------------------

    def test_buy_produces_one_record(self):
        self.assertEqual(len(self.buy_parsed), 1)
        self.assertEqual(len(self.buy_skipped), 0)

    def test_sell_produces_one_record(self):
        self.assertEqual(len(self.sell_parsed), 1)
        self.assertEqual(len(self.sell_skipped), 0)

    # ------------------------------------------------------------------
    # 2. Parsed keys — same set as Shyft conformance output
    # ------------------------------------------------------------------

    def test_parsed_keys(self):
        expected_keys = {
            'tx_signature', 'timestamp', 'trade_type', 'wallet_address',
            'token_amount', 'sol_amount', 'pool_address', 'tx_fee',
            'lp_fee', 'protocol_fee', 'coin_creator_fee',
            'pool_token_reserves', 'pool_sol_reserves', 'coin_id',
        }
        self.assertEqual(set(self.buy_parsed[0].keys()), expected_keys)
        self.assertEqual(set(self.sell_parsed[0].keys()), expected_keys)

    # ------------------------------------------------------------------
    # 3. BUY extraction — cross-validated against Shyft BuyEvent
    # ------------------------------------------------------------------

    def test_buy_trade_type(self):
        self.assertEqual(self.buy_parsed[0]['trade_type'], 'BUY')

    def test_buy_token_amount_matches_shyft(self):
        self.assertEqual(
            self.buy_parsed[0]['token_amount'],
            self.shyft_buy_event['base_amount_out'],
        )

    def test_buy_sol_amount_matches_shyft(self):
        self.assertEqual(
            self.buy_parsed[0]['sol_amount'],
            self.shyft_buy_event['quote_amount_in'],
        )

    def test_buy_lp_fee_matches_shyft(self):
        self.assertEqual(
            self.buy_parsed[0]['lp_fee'],
            self.shyft_buy_event['lp_fee'],
        )

    def test_buy_protocol_fee_matches_shyft(self):
        self.assertEqual(
            self.buy_parsed[0]['protocol_fee'],
            self.shyft_buy_event['protocol_fee'],
        )

    def test_buy_creator_fee_matches_shyft(self):
        self.assertEqual(
            self.buy_parsed[0]['coin_creator_fee'],
            self.shyft_buy_event['coin_creator_fee'],
        )

    def test_buy_wallet_matches_shyft(self):
        self.assertEqual(
            self.buy_parsed[0]['wallet_address'],
            self.shyft_buy_event['user'],
        )

    # ------------------------------------------------------------------
    # 4. SELL extraction — cross-validated against Shyft SellEvent
    # ------------------------------------------------------------------

    def test_sell_trade_type(self):
        self.assertEqual(self.sell_parsed[0]['trade_type'], 'SELL')

    def test_sell_token_amount_matches_shyft(self):
        self.assertEqual(
            self.sell_parsed[0]['token_amount'],
            self.shyft_sell_event['base_amount_in'],
        )

    def test_sell_sol_amount_within_tolerance(self):
        """SELL sol_amount may differ by ±1 lamport due to AMM integer rounding."""
        diff = abs(
            self.sell_parsed[0]['sol_amount']
            - self.shyft_sell_event['quote_amount_out']
        )
        self.assertLessEqual(diff, 1, f"sol_amount off by {diff}")

    def test_sell_lp_fee_within_tolerance(self):
        """SELL lp_fee may differ by ±1 lamport due to AMM integer rounding."""
        diff = abs(
            self.sell_parsed[0]['lp_fee']
            - self.shyft_sell_event['lp_fee']
        )
        self.assertLessEqual(diff, 1, f"lp_fee off by {diff}")

    def test_sell_protocol_fee_matches_shyft(self):
        self.assertEqual(
            self.sell_parsed[0]['protocol_fee'],
            self.shyft_sell_event['protocol_fee'],
        )

    def test_sell_creator_fee_matches_shyft(self):
        self.assertEqual(
            self.sell_parsed[0]['coin_creator_fee'],
            self.shyft_sell_event['coin_creator_fee'],
        )

    def test_sell_wallet_matches_shyft(self):
        self.assertEqual(
            self.sell_parsed[0]['wallet_address'],
            self.shyft_sell_event['user'],
        )

    # ------------------------------------------------------------------
    # 5. Pool reserves are NULL (not available from Helius)
    # ------------------------------------------------------------------

    def test_pool_reserves_are_none(self):
        self.assertIsNone(self.buy_parsed[0]['pool_token_reserves'])
        self.assertIsNone(self.buy_parsed[0]['pool_sol_reserves'])
        self.assertIsNone(self.sell_parsed[0]['pool_token_reserves'])
        self.assertIsNone(self.sell_parsed[0]['pool_sol_reserves'])

    # ------------------------------------------------------------------
    # 6. Type correctness
    # ------------------------------------------------------------------

    def test_amounts_are_integers(self):
        int_fields = [
            'token_amount', 'sol_amount', 'lp_fee',
            'protocol_fee', 'coin_creator_fee',
        ]
        for record in [self.buy_parsed[0], self.sell_parsed[0]]:
            for field in int_fields:
                self.assertIsInstance(
                    record[field], int,
                    f"{field} should be int, got {type(record[field])}",
                )

    def test_tx_fee_is_decimal(self):
        for record in [self.buy_parsed[0], self.sell_parsed[0]]:
            self.assertIsInstance(record['tx_fee'], Decimal)

    def test_timestamp_is_utc_aware(self):
        for record in [self.buy_parsed[0], self.sell_parsed[0]]:
            self.assertIsNotNone(record['timestamp'].tzinfo)
            self.assertEqual(record['timestamp'].tzinfo, timezone.utc)

    def test_timestamp_value(self):
        """Helius timestamp is Unix int, should convert correctly."""
        expected = datetime(2026, 3, 15, 11, 37, 48, tzinfo=timezone.utc)
        self.assertEqual(self.buy_parsed[0]['timestamp'], expected)

    def test_coin_id_set(self):
        self.assertEqual(self.buy_parsed[0]['coin_id'], MINT)
        self.assertEqual(self.sell_parsed[0]['coin_id'], MINT)

    # ------------------------------------------------------------------
    # 7. Skip conditions
    # ------------------------------------------------------------------

    def test_failed_transaction_skipped(self):
        tx = copy.deepcopy(self.fixture['helius_buy'])
        tx['transactionError'] = {'InstructionError': [4, {'Custom': 6004}]}

        parsed, skipped = conform([tx], MINT, POOL)
        self.assertEqual(len(parsed), 0)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]['skip_reason'], 'failed')

    def test_no_trade_event_skipped(self):
        """Transaction with no non-wSOL tokenTransfer involving pool."""
        tx = copy.deepcopy(self.fixture['helius_buy'])
        # Remove all non-wSOL transfers
        tx['tokenTransfers'] = [
            tt for tt in tx['tokenTransfers']
            if tt.get('mint') == 'So11111111111111111111111111111111111111112'
        ]

        parsed, skipped = conform([tx], MINT, POOL)
        self.assertEqual(len(parsed), 0)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]['skip_reason'], 'no_trade_event')

    def test_empty_transfers_skipped_as_no_trade(self):
        """Transaction with no tokenTransfers → no_trade_event."""
        tx = copy.deepcopy(self.fixture['helius_buy'])
        tx['tokenTransfers'] = []
        tx['accountData'] = []

        parsed, skipped = conform([tx], MINT, POOL)
        self.assertEqual(len(parsed), 0)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]['skip_reason'], 'no_trade_event')

    def test_skipped_record_has_raw_json(self):
        tx = copy.deepcopy(self.fixture['helius_buy'])
        tx['transactionError'] = {'Custom': 1}

        _, skipped = conform([tx], MINT, POOL)
        self.assertIn('raw_json', skipped[0])
        self.assertEqual(skipped[0]['raw_json']['signature'], tx['signature'])

    # ------------------------------------------------------------------
    # 8. Multiple transactions
    # ------------------------------------------------------------------

    def test_mixed_buy_and_sell(self):
        parsed, skipped = conform(
            [self.fixture['helius_buy'], self.fixture['helius_sell']],
            MINT, POOL,
        )
        self.assertEqual(len(parsed), 2)
        self.assertEqual(len(skipped), 0)
        self.assertEqual(parsed[0]['trade_type'], 'BUY')
        self.assertEqual(parsed[1]['trade_type'], 'SELL')
