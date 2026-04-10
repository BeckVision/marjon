from django.test import SimpleTestCase

from strategy.strategies import load_strategy_config


class StrategyConfigTest(SimpleTestCase):
    def test_load_breakout_close_config(self):
        config = load_strategy_config('u001_breakout_close_v1')

        self.assertEqual(config['id'], 'u001_breakout_close_v1')
        self.assertEqual(config['data_requirements']['layer_ids'], ['FL-001'])
        self.assertIn('DF-007', config['data_requirements']['derived_ids'])
        self.assertEqual(
            [sig['signal_id'] for sig in config['signals']],
            ['SG-001', 'SG-004', 'SG-005', 'SG-006'],
        )
