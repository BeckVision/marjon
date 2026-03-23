"""Tests for leaderboard — pure function + management command."""

from datetime import datetime, timezone
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from strategy.analysis.leaderboard import build_leaderboard
from strategy.models import BacktestStatus, U001BacktestResult, U001BacktestRun

D = Decimal
T0 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)


def _create_run_and_result(strategy_id='test_strat', sharpe=None,
                           total_pnl=D('0'), total_trades=0,
                           win_rate=None, label='', sweep_id=''):
    """Helper: create a completed run + result pair."""
    run = U001BacktestRun.objects.create(
        strategy_id=strategy_id,
        strategy_version=1,
        run_label=label,
        sweep_id=sweep_id,
        data_start=T0,
        data_end=T0,
        params_snapshot={},
        started_at=T0,
        completed_at=T0,
        status=BacktestStatus.COMPLETED,
    )
    result = U001BacktestResult.objects.create(
        run=run,
        total_trades=total_trades,
        total_pnl=total_pnl,
        sharpe_ratio=sharpe,
        win_rate=win_rate,
    )
    return run, result


class BuildLeaderboardTest(TestCase):

    def test_empty_queryset(self):
        qs = U001BacktestResult.objects.none()
        rows = build_leaderboard(qs)
        self.assertEqual(rows, [])

    def test_sort_by_sharpe_descending(self):
        _create_run_and_result(sharpe=D('1.5'))
        _create_run_and_result(sharpe=D('3.0'))
        _create_run_and_result(sharpe=D('0.5'))

        qs = U001BacktestResult.objects.all()
        rows = build_leaderboard(qs, sort_by='sharpe_ratio')

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]['sharpe_ratio'], D('3.0'))
        self.assertEqual(rows[1]['sharpe_ratio'], D('1.5'))
        self.assertEqual(rows[2]['sharpe_ratio'], D('0.5'))

    def test_sort_by_total_pnl(self):
        _create_run_and_result(total_pnl=D('100'))
        _create_run_and_result(total_pnl=D('500'))
        _create_run_and_result(total_pnl=D('-50'))

        rows = build_leaderboard(
            U001BacktestResult.objects.all(),
            sort_by='total_pnl',
        )
        self.assertEqual(rows[0]['total_pnl'], D('500'))
        self.assertEqual(rows[2]['total_pnl'], D('-50'))

    def test_top_n_limits(self):
        for i in range(5):
            _create_run_and_result(sharpe=D(str(i)))

        rows = build_leaderboard(
            U001BacktestResult.objects.all(), top_n=3,
        )
        self.assertEqual(len(rows), 3)

    def test_nulls_last(self):
        _create_run_and_result(sharpe=None)
        _create_run_and_result(sharpe=D('2.0'))
        _create_run_and_result(sharpe=D('1.0'))

        rows = build_leaderboard(U001BacktestResult.objects.all())
        # Non-null values come first
        self.assertEqual(rows[0]['sharpe_ratio'], D('2.0'))
        self.assertEqual(rows[1]['sharpe_ratio'], D('1.0'))
        self.assertIsNone(rows[2]['sharpe_ratio'])

    def test_rank_numbering(self):
        _create_run_and_result(sharpe=D('3.0'))
        _create_run_and_result(sharpe=D('1.0'))

        rows = build_leaderboard(U001BacktestResult.objects.all())
        self.assertEqual(rows[0]['rank'], 1)
        self.assertEqual(rows[1]['rank'], 2)

    def test_invalid_sort_field_raises(self):
        with self.assertRaises(ValueError):
            build_leaderboard(U001BacktestResult.objects.all(), sort_by='nonexistent')

    def test_run_metadata_included(self):
        run, _ = _create_run_and_result(
            strategy_id='my_strat', sharpe=D('1.0'), label='test label',
        )
        rows = build_leaderboard(U001BacktestResult.objects.all())
        self.assertEqual(rows[0]['strategy_id'], 'my_strat')
        self.assertEqual(rows[0]['run_label'], 'test label')
        self.assertEqual(rows[0]['run_id'], run.pk)


class LeaderboardCommandTest(TestCase):

    def test_command_runs_no_data(self):
        """Command should not error with no data."""
        call_command('leaderboard', stdout=__import__('io').StringIO())

    def test_command_with_data(self):
        _create_run_and_result(sharpe=D('2.5'), total_pnl=D('100'))
        out = __import__('io').StringIO()
        call_command('leaderboard', stdout=out)
        self.assertIn('Leaderboard', out.getvalue())
