from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from pipeline.audits.rd001_chain_truth import (
    build_chain_observation,
    compare_window_to_chain,
    compare_row_to_chain,
)


class RD001ChainTruthTest(SimpleTestCase):
    def test_build_chain_observation_derives_buy_and_token_amount(self):
        tx = {
            'blockTime': int(datetime(2026, 4, 12, 1, 2, 3, tzinfo=timezone.utc).timestamp()),
            'meta': {
                'err': None,
                'fee': 5000,
                'preTokenBalances': [
                    {
                        'owner': 'POOL_A',
                        'mint': 'MINT_A',
                        'uiTokenAmount': {'amount': '1000'},
                    },
                ],
                'postTokenBalances': [
                    {
                        'owner': 'POOL_A',
                        'mint': 'MINT_A',
                        'uiTokenAmount': {'amount': '700'},
                    },
                ],
            },
            'transaction': {
                'message': {
                    'accountKeys': [
                        {'pubkey': 'WALLET_A', 'signer': True},
                    ],
                },
            },
        }

        observation = build_chain_observation(
            tx, mint_address='MINT_A', pool_address='POOL_A',
        )

        self.assertTrue(observation['exists'])
        self.assertTrue(observation['success'])
        self.assertEqual(observation['fee_payer'], 'WALLET_A')
        self.assertEqual(observation['trade_type'], 'BUY')
        self.assertEqual(observation['token_amount'], 300)
        self.assertEqual(observation['tx_fee'], Decimal('0.000005'))

    def test_compare_row_to_chain_reports_clean_match(self):
        row = SimpleNamespace(
            coin_id='MINT_A',
            tx_signature='SIG_A',
            timestamp=datetime(2026, 4, 12, 1, 2, 3, tzinfo=timezone.utc),
            tx_fee=Decimal('0.000005'),
            wallet_address='WALLET_A',
            trade_type='BUY',
            token_amount=300,
        )
        observation = {
            'exists': True,
            'success': True,
            'timestamp': row.timestamp,
            'tx_fee': row.tx_fee,
            'fee_payer': row.wallet_address,
            'trade_type': row.trade_type,
            'token_amount': row.token_amount,
            'derivation_complete': True,
        }

        result = compare_row_to_chain(row, observation)

        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['findings'], [])
        self.assertEqual(result['warnings'], [])

    def test_compare_row_to_chain_warns_when_trade_delta_cannot_be_derived(self):
        row = SimpleNamespace(
            coin_id='MINT_A',
            tx_signature='SIG_A',
            timestamp=datetime(2026, 4, 12, 1, 2, 3, tzinfo=timezone.utc),
            tx_fee=Decimal('0.000005'),
            wallet_address='WALLET_A',
            trade_type='BUY',
            token_amount=300,
        )
        observation = {
            'exists': True,
            'success': True,
            'timestamp': row.timestamp,
            'tx_fee': row.tx_fee,
            'fee_payer': row.wallet_address,
            'trade_type': None,
            'token_amount': None,
            'derivation_complete': False,
        }

        result = compare_row_to_chain(row, observation)

        self.assertEqual(result['status'], 'warning')
        self.assertEqual(result['findings'], [])
        self.assertIn('token_delta_unavailable', result['warnings'])

    def test_compare_window_to_chain_reports_missing_and_extra_signatures(self):
        result = compare_window_to_chain(
            coin_id='MINT_A',
            pool_address='POOL_A',
            start=datetime(2026, 4, 12, 1, 0, tzinfo=timezone.utc),
            end=datetime(2026, 4, 12, 2, 0, tzinfo=timezone.utc),
            warehouse_signatures={'sig-1', 'sig-extra'},
            chain_trade_signatures={'sig-1', 'sig-missing'},
            ambiguous_chain_signatures={'sig-ambiguous'},
            signature_scan_count=4,
        )

        self.assertEqual(result['status'], 'finding')
        self.assertIn('missing_trade_signatures', result['findings'])
        self.assertIn('extra_trade_signatures', result['findings'])
        self.assertEqual(result['missing_signatures'], ['sig-missing'])
        self.assertEqual(result['extra_signatures'], ['sig-extra'])

    def test_compare_window_to_chain_warns_on_ambiguous_only(self):
        result = compare_window_to_chain(
            coin_id='MINT_A',
            pool_address='POOL_A',
            start=datetime(2026, 4, 12, 1, 0, tzinfo=timezone.utc),
            end=datetime(2026, 4, 12, 2, 0, tzinfo=timezone.utc),
            warehouse_signatures={'sig-1'},
            chain_trade_signatures={'sig-1'},
            ambiguous_chain_signatures={'sig-ambiguous'},
            signature_scan_count=2,
        )

        self.assertEqual(result['status'], 'warning')
        self.assertEqual(result['findings'], [])
        self.assertIn('ambiguous_pool_signatures', result['warnings'])
