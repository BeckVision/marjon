"""Sample recent RD-001 rows and compare them to Solscan."""

import random
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max
from django.utils import timezone

from pipeline.audits.rd001_solscan import (
    compare_row_to_solscan,
    compare_window_to_solscan,
    fetch_account_transactions,
    fetch_transaction_detail,
    solscan_base_url,
    solscan_enabled,
)
from pipeline.pipelines.rd001 import SHYFT_RETENTION_DAYS
from warehouse.models import MigratedCoin, PoolMapping, RawTransaction


class Command(BaseCommand):
    help = "Sample recent RD-001 rows and compare them against Solscan spot-check data"

    def add_arguments(self, parser):
        parser.add_argument('--sample-coins', type=int, default=1)
        parser.add_argument('--txs-per-coin', type=int, default=2)
        parser.add_argument('--hours', type=int, default=1)
        parser.add_argument(
            '--fail-on-findings',
            action='store_true',
            help='Exit non-zero when Solscan mismatches are detected.',
        )

    def handle(self, *args, **options):
        if not solscan_enabled():
            self.stdout.write(
                '[warning] Solscan spot check skipped: SOLSCAN_API_KEY is not configured.'
            )
            return {
                'status': 'skipped',
                'reason': 'missing_solscan_api_key',
            }

        started_at = timezone.now()
        self.stdout.write("=" * 60)
        self.stdout.write("U-001 RD-001 SOLSCAN SPOT CHECK")
        self.stdout.write("=" * 60)
        self.stdout.write(f"Solscan base URL: {solscan_base_url()}")

        samples = self._sample_coin_windows(
            sample_coins=options['sample_coins'],
            txs_per_coin=options['txs_per_coin'],
            hours=options['hours'],
        )
        if not samples:
            warning = 'No recent RD-001 sample rows were available for Solscan spot checking.'
            self.stdout.write(f'[warning] {warning}')
            return {
                'status': 'warning',
                'warning_count': 1,
                'finding_count': 0,
                'summary': {'warnings': [warning]},
            }

        findings = []
        warnings = []
        row_results = []
        window_results = []

        for sample in samples:
            for row in sample['rows']:
                result = compare_row_to_solscan(row, fetch_transaction_detail(row.tx_signature))
                row_results.append(result)
                if result['status'] == 'finding':
                    findings.append(result['detail'])
                elif result['status'] == 'warning':
                    warnings.append(result['detail'])
                self.stdout.write(f"[{result['status']}] {result['detail']}")

            window_payload = fetch_account_transactions(sample['pool_address'])
            window_result = compare_window_to_solscan(
                coin_id=sample['coin_id'],
                pool_address=sample['pool_address'],
                start=sample['start'],
                end=sample['end'],
                warehouse_signatures=sample['window_signatures'],
                payload=window_payload,
            )
            window_results.append(window_result)
            if window_result['status'] == 'finding':
                findings.append(window_result['detail'])
            elif window_result['status'] == 'warning':
                warnings.append(window_result['detail'])
            self.stdout.write(f"[{window_result['status']}] {window_result['detail']}")

        status = 'ok'
        if findings:
            status = 'finding'
        elif warnings:
            status = 'warning'

        summary = {
            'started_at': str(started_at),
            'status': status,
            'sample_coins': len(samples),
            'sample_rows': len(row_results),
            'row_results': row_results,
            'window_results': window_results,
            'finding_count': len(findings),
            'warning_count': len(warnings),
        }

        if findings and options['fail_on_findings']:
            raise CommandError('U-001 RD-001 Solscan spot check found mismatches')

        return summary

    def _sample_coin_windows(self, *, sample_coins, txs_per_coin, hours):
        recent_cutoff = timezone.now() - timedelta(days=SHYFT_RETENTION_DAYS)
        candidates = list(
            MigratedCoin.objects.filter(
                anchor_event__gte=recent_cutoff,
                mint_address__in=PoolMapping.objects.values_list('coin_id', flat=True),
                mint_address__in=RawTransaction.objects.values_list('coin_id', flat=True),
            )
            .annotate(latest_local=Max('raw_transactions__timestamp'))
            .order_by('-latest_local', '-anchor_event')[: max(sample_coins * 8, 24)]
        )
        if not candidates:
            return []

        chosen = random.sample(candidates, min(sample_coins, len(candidates)))
        samples = []
        for coin in chosen:
            pool_address = PoolMapping.objects.filter(
                coin_id=coin.mint_address,
            ).values_list('pool_address', flat=True).first()
            if not pool_address:
                continue
            latest_tx = RawTransaction.objects.filter(
                coin_id=coin.mint_address,
                pool_address=pool_address,
            ).order_by('-timestamp').first()
            if latest_tx is None:
                continue
            start = latest_tx.timestamp - timedelta(hours=hours)
            sample_rows = list(
                RawTransaction.objects.filter(
                    coin_id=coin.mint_address,
                    pool_address=pool_address,
                    timestamp__gte=start,
                    timestamp__lte=latest_tx.timestamp,
                )
                .order_by('-timestamp', '-id')[:txs_per_coin]
            )
            if not sample_rows:
                continue
            window_signatures = list(
                RawTransaction.objects.filter(
                    coin_id=coin.mint_address,
                    pool_address=pool_address,
                    timestamp__gte=start,
                    timestamp__lte=latest_tx.timestamp,
                ).values_list('tx_signature', flat=True)
            )
            samples.append(
                {
                    'coin_id': coin.mint_address,
                    'pool_address': pool_address,
                    'start': start,
                    'end': latest_tx.timestamp,
                    'rows': sample_rows,
                    'window_signatures': window_signatures,
                }
            )
        return samples
