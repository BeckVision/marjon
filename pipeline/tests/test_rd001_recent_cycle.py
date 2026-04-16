from unittest.mock import patch

from django.test import SimpleTestCase

from pipeline.management.commands.run_u001_rd001_recent_cycle import Command


class RD001RecentCycleCommandTest(SimpleTestCase):
    @patch('pipeline.management.commands.run_u001_rd001_recent_cycle.call_command')
    def test_runs_repair_and_rd001(self, mock_call_command):
        mock_call_command.side_effect = [
            None,
            {'queued_coins': 25, 'records_loaded': 321},
        ]

        summary = Command().run_cycle(
            skip_repair=False,
            rd001_max_coins=25,
            rd001_candidate_limit=150,
            rd001_source='auto',
            rd001_status_filter='incomplete',
            rd001_workers=4,
            rd001_parse_workers=4,
        )

        self.assertTrue(summary['repair_executed'])
        self.assertEqual(summary['steps']['rd001_recent']['records_loaded'], 321)
        self.assertEqual(
            mock_call_command.call_args_list[1].kwargs['candidate_limit'],
            150,
        )

        expected = [call.args[0] for call in mock_call_command.call_args_list]
        self.assertEqual(
            expected,
            [
                'repair_u001_ingestion',
                'fetch_transactions_batch',
            ],
        )
        self.assertEqual(mock_call_command.call_args_list[1].kwargs['workers'], 4)
        self.assertEqual(mock_call_command.call_args_list[1].kwargs['parse_workers'], 4)

    @patch('pipeline.management.commands.run_u001_rd001_recent_cycle.call_command')
    def test_can_skip_repair_and_run_rd001_only(self, mock_call_command):
        mock_call_command.side_effect = [
            {'queued_coins': 25, 'records_loaded': 0},
        ]

        summary = Command().run_cycle(
            skip_repair=True,
            rd001_max_coins=25,
            rd001_candidate_limit=150,
            rd001_source='auto',
            rd001_status_filter='incomplete',
            rd001_workers=4,
            rd001_parse_workers=4,
        )

        self.assertFalse(summary['repair_executed'])
        expected = [call.args[0] for call in mock_call_command.call_args_list]
        self.assertEqual(expected, ['fetch_transactions_batch'])
