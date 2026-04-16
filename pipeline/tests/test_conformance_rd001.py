"""Tests for RD-001 conformance function (Shyft -> RawTransaction)."""

import copy
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from django.test import TestCase

from pipeline.conformance.rd001_shyft import conform

FIXTURE_PATH = (
    Path(__file__).parent / 'fixtures' / 'shyft_transactions_sample.json'
)

MINT = 'TEST_MINT_RD001'
POOL = 'FaX71QvybvdEW7mB7MbMrPocYEUVjvtKUmdP5GJXmm5y'


class RD001ShyftConformanceTest(TestCase):
    def setUp(self):
        with open(FIXTURE_PATH) as f:
            raw = json.load(f)
        self.raw_transactions = raw['result']
        self.parsed, self.skipped = conform(self.raw_transactions, MINT, POOL)

    # ------------------------------------------------------------------
    # 1. Record counts
    # ------------------------------------------------------------------
    def test_record_counts(self):
        """4 transactions all have BuyEvent/SellEvent -> 4 parsed, 0 skipped."""
        self.assertEqual(len(self.parsed), 4)
        self.assertEqual(len(self.skipped), 0)

    # ------------------------------------------------------------------
    # 2. Parsed keys
    # ------------------------------------------------------------------
    def test_parsed_keys(self):
        expected_keys = {
            'tx_signature', 'timestamp', 'trade_type', 'wallet_address',
            'token_amount', 'sol_amount', 'pool_address', 'tx_fee',
            'lp_fee', 'protocol_fee', 'coin_creator_fee',
            'pool_token_reserves', 'pool_sol_reserves', 'coin_id',
        }
        for record in self.parsed:
            self.assertEqual(set(record.keys()), expected_keys)

    # ------------------------------------------------------------------
    # 3. First record values (SWAP / SellEvent)
    # ------------------------------------------------------------------
    def test_first_record_values(self):
        r = self.parsed[0]
        self.assertEqual(
            r['tx_signature'],
            '2ENhwEw1U2rbsUwRzWZGJ5i4K8PkUJHSoqwSzaqzHJQJC1BJRRvfCdGpd2mTJFjAjvTKHTq29nZ97tXkNwvYGkRX',
        )
        self.assertEqual(
            r['timestamp'],
            datetime(2026, 3, 14, 15, 28, 44, tzinfo=timezone.utc),
        )
        self.assertEqual(r['trade_type'], 'SELL')
        self.assertEqual(
            r['wallet_address'],
            '7xWMV4CtWrqtLKwZKAh7Zr3vMqjPevcJfHamcuhT2Tg',
        )
        # SellEvent: token_amount = base_amount_in, sol_amount = quote_amount_out
        self.assertEqual(r['token_amount'], 1538262280)
        self.assertEqual(r['sol_amount'], 31636)
        self.assertEqual(
            r['pool_address'],
            'FaX71QvybvdEW7mB7MbMrPocYEUVjvtKUmdP5GJXmm5y',
        )
        self.assertEqual(r['tx_fee'], Decimal(str(0.001005001)))
        self.assertEqual(r['lp_fee'], 7)
        self.assertEqual(r['protocol_fee'], 295)
        self.assertEqual(r['coin_creator_fee'], 95)
        self.assertEqual(r['pool_token_reserves'], 938731456550836)
        self.assertEqual(r['pool_sol_reserves'], 19306580856)
        self.assertEqual(r['coin_id'], MINT)

    # ------------------------------------------------------------------
    # 4. Buy vs sell detection
    # ------------------------------------------------------------------
    def test_buy_vs_sell_detection(self):
        """TX 1 is SELL (SellEvent), TXs 2-4 are BUY (BuyEvent)."""
        self.assertEqual(self.parsed[0]['trade_type'], 'SELL')
        self.assertEqual(self.parsed[1]['trade_type'], 'BUY')
        self.assertEqual(self.parsed[2]['trade_type'], 'BUY')
        self.assertEqual(self.parsed[3]['trade_type'], 'BUY')

    # ------------------------------------------------------------------
    # 5. Timestamp is UTC-aware
    # ------------------------------------------------------------------
    def test_timestamp_is_utc_aware(self):
        for record in self.parsed:
            self.assertIsNotNone(record['timestamp'].tzinfo)
            self.assertEqual(record['timestamp'].tzinfo, timezone.utc)

    # ------------------------------------------------------------------
    # 6. Amounts are integers
    # ------------------------------------------------------------------
    def test_amounts_are_integers(self):
        int_fields = [
            'token_amount', 'sol_amount', 'lp_fee', 'protocol_fee',
            'coin_creator_fee', 'pool_token_reserves', 'pool_sol_reserves',
        ]
        for record in self.parsed:
            for field in int_fields:
                self.assertIsInstance(
                    record[field], int,
                    f'{field} should be int, got {type(record[field]).__name__}',
                )

    # ------------------------------------------------------------------
    # 7. tx_fee is Decimal
    # ------------------------------------------------------------------
    def test_tx_fee_is_decimal(self):
        for record in self.parsed:
            self.assertIsInstance(record['tx_fee'], Decimal)

    # ------------------------------------------------------------------
    # 8. coin_id set from parameter
    # ------------------------------------------------------------------
    def test_coin_id_set(self):
        for record in self.parsed:
            self.assertEqual(record['coin_id'], MINT)

    # ------------------------------------------------------------------
    # 9. Failed transaction -> skipped with reason 'failed'
    # ------------------------------------------------------------------
    def test_failed_transaction_skipped(self):
        txs = copy.deepcopy(self.raw_transactions)
        txs[0]['status'] = 'Failed'
        parsed, skipped = conform(txs, MINT, POOL)

        self.assertEqual(len(parsed), 3)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]['skip_reason'], 'failed')
        self.assertEqual(
            skipped[0]['tx_signature'],
            '2ENhwEw1U2rbsUwRzWZGJ5i4K8PkUJHSoqwSzaqzHJQJC1BJRRvfCdGpd2mTJFjAjvTKHTq29nZ97tXkNwvYGkRX',
        )
        self.assertEqual(skipped[0]['tx_status'], 'Failed')
        self.assertEqual(skipped[0]['pool_address'], POOL)
        self.assertEqual(skipped[0]['coin_id'], MINT)
        self.assertIn('raw_json', skipped[0])

    # ------------------------------------------------------------------
    # 10. No events -> skipped with reason 'no_trade_event'
    # ------------------------------------------------------------------
    def test_no_events_skipped(self):
        tx_no_events = {
            'signatures': ['SIG_NO_EVENTS'],
            'timestamp': '2026-03-14T12:00:00.000Z',
            'status': 'Success',
            'type': 'SOL_TRANSFER',
            'events': [],
        }
        parsed, skipped = conform([tx_no_events], MINT, POOL)

        self.assertEqual(len(parsed), 0)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]['skip_reason'], 'no_trade_event')
        self.assertEqual(skipped[0]['tx_signature'], 'SIG_NO_EVENTS')
        self.assertEqual(skipped[0]['tx_type'], 'SOL_TRANSFER')
        self.assertEqual(skipped[0]['pool_address'], POOL)

    def test_multiple_trade_events_prefers_requested_pool_without_warning(self):
        txs = copy.deepcopy(self.raw_transactions)
        tx = txs[0]
        foreign_event = copy.deepcopy(
            next(e for e in tx['events'] if e['name'] == 'SellEvent')
        )
        foreign_event['data']['pool'] = 'FOREIGN_POOL_RD001'
        tx['events'] = [foreign_event] + tx['events']

        with self.assertNoLogs('pipeline.conformance.rd001_shyft', level='WARNING'):
            parsed, skipped = conform([tx], MINT, POOL)

        self.assertEqual(len(skipped), 0)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]['pool_address'], POOL)
        self.assertEqual(parsed[0]['trade_type'], 'SELL')

    def test_duplicate_trade_events_are_deduplicated_without_warning(self):
        txs = copy.deepcopy(self.raw_transactions)
        tx = txs[1]
        buy_event = copy.deepcopy(
            next(e for e in tx['events'] if e['name'] == 'BuyEvent')
        )
        tx['events'] = [buy_event, buy_event]

        with self.assertNoLogs('pipeline.conformance.rd001_shyft', level='WARNING'):
            parsed, skipped = conform([tx], MINT, POOL)

        self.assertEqual(len(skipped), 0)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]['trade_type'], 'BUY')
        self.assertEqual(parsed[0]['pool_address'], POOL)

    def test_conflicting_trade_events_for_same_pool_warn(self):
        txs = copy.deepcopy(self.raw_transactions)
        tx = txs[1]
        buy_event = copy.deepcopy(
            next(e for e in tx['events'] if e['name'] == 'BuyEvent')
        )
        sell_event = copy.deepcopy(buy_event)
        sell_event['name'] = 'SellEvent'
        sell_event['data']['base_amount_in'] = buy_event['data']['base_amount_out']
        sell_event['data']['quote_amount_out'] = buy_event['data']['quote_amount_in']
        tx['events'] = [buy_event, sell_event]

        with self.assertLogs(
            'pipeline.conformance.rd001_shyft', level='WARNING',
        ) as captured:
            parsed, skipped = conform([tx], MINT, POOL)

        self.assertEqual(len(skipped), 0)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]['trade_type'], 'BUY')
        self.assertIn('Multiple trade events found for requested pool', captured.output[0])
