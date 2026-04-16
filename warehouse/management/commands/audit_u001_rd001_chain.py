"""Sample RD-001 rows and compare them against direct Solana RPC truth."""

from datetime import datetime, timedelta
import json

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max
from django.utils import timezone

from pipeline.audits.rd001_chain_truth import (
    build_chain_observation,
    compare_window_to_chain,
    compare_row_to_chain,
    default_rpc_source,
    default_rpc_url,
    fetch_signatures_for_address,
    fetch_transaction,
    summarize_results,
    summarize_window_results,
)
from warehouse.models import (
    MigratedCoin,
    PoolMapping,
    RawTransaction,
    U001RD001ChainAuditRun,
)


class Command(BaseCommand):
    help = "Audit sampled RD-001 rows against direct Solana RPC transaction truth"

    def add_arguments(self, parser):
        parser.add_argument(
            '--sample-coins',
            type=int,
            default=1,
            help='How many coins to sample (default: 1)',
        )
        parser.add_argument(
            '--txs-per-coin',
            type=int,
            default=3,
            help='How many recent warehouse transactions to sample per coin (default: 3)',
        )
        parser.add_argument(
            '--hours',
            type=int,
            default=6,
            help='How many trailing hours of warehouse transactions to consider per coin (default: 6)',
        )
        parser.add_argument(
            '--rpc-url',
            default=None,
            help='Override the direct Solana RPC URL for this audit run',
        )
        parser.add_argument(
            '--fail-on-findings',
            action='store_true',
            help='Exit non-zero when any chain mismatch is detected',
        )

    def handle(self, *args, **options):
        started_at = timezone.now()
        effective_rpc_url = options['rpc_url'] or default_rpc_url()
        effective_rpc_source = 'command_override' if options['rpc_url'] else default_rpc_source()
        audit_options = {
            'sample_coins': options['sample_coins'],
            'txs_per_coin': options['txs_per_coin'],
            'hours': options['hours'],
            'rpc_url': effective_rpc_url,
            'rpc_source': effective_rpc_source,
        }
        results = []
        findings = []
        warnings = []
        summary = {}

        self.stdout.write("=" * 60)
        self.stdout.write("U-001 RD-001 DIRECT CHAIN AUDIT")
        self.stdout.write("=" * 60)
        self.stdout.write(
            "note: this command fetches sampled transactions from direct Solana RPC."
        )

        try:
            samples = self._sample_coin_windows(
                sample_coins=options['sample_coins'],
                txs_per_coin=options['txs_per_coin'],
                hours=options['hours'],
            )
            rows = [row for sample in samples for row in sample['rows']]
            window_results = []
            if not rows:
                warning = 'No RD-001 sample rows were available for direct-RPC audit.'
                warnings.append(warning)
                self.stdout.write(f"[warning] {warning}")
            else:
                tx_cache = {}
                for row in rows:
                    observation = build_chain_observation(
                        self._fetch_tx(
                            row.tx_signature,
                            rpc_url=effective_rpc_url,
                            tx_cache=tx_cache,
                        ),
                        mint_address=row.coin_id,
                        pool_address=row.pool_address,
                    )
                    result = compare_row_to_chain(row, observation)
                    results.append(result)
                    if result['status'] == 'finding':
                        findings.append(result['detail'])
                    elif result['status'] == 'warning':
                        warnings.append(result['detail'])
                    self.stdout.write(f"[{result['status']}] {result['detail']}")

                for sample in samples:
                    window_result = self._audit_window(
                        sample=sample,
                        rpc_url=effective_rpc_url,
                        tx_cache=tx_cache,
                    )
                    window_results.append(window_result)
                    if window_result['status'] == 'finding':
                        findings.append(window_result['detail'])
                    elif window_result['status'] == 'warning':
                        warnings.append(window_result['detail'])
                    self.stdout.write(f"[{window_result['status']}] {window_result['detail']}")

            summary = {
                'results': results,
                'aggregate': summarize_results(results),
                'window_results': window_results,
                'window_aggregate': summarize_window_results(window_results),
            }
            status = 'ok'
            if findings:
                status = 'finding'
            elif warnings:
                status = 'warning'

            self._persist_run(
                started_at=started_at,
                status=status,
                options=audit_options,
                rows=rows,
                findings=findings,
                warnings=warnings,
                summary=summary,
            )
            self.stdout.write("\n" + "=" * 60)

            if findings and options['fail_on_findings']:
                raise CommandError('U-001 RD-001 direct chain audit found mismatches')
        except Exception as exc:
            if not isinstance(exc, CommandError):
                self._persist_run(
                    started_at=started_at,
                    status='error',
                    options=audit_options,
                    rows=[],
                    findings=findings,
                    warnings=warnings,
                    summary=summary,
                    notes=str(exc),
                )
            raise

    def _sample_coin_windows(self, sample_coins, txs_per_coin, hours):
        if sample_coins <= 0 or txs_per_coin <= 0:
            return []

        candidates = list(
            MigratedCoin.objects.filter(
                pipeline_statuses__layer_id='RD-001',
                pipeline_statuses__status__in=('partial', 'window_complete'),
            )
            .filter(
                mint_address__in=PoolMapping.objects.values_list('coin_id', flat=True),
            )
            .filter(
                mint_address__in=RawTransaction.objects.values_list('coin_id', flat=True),
            )
            .annotate(latest_local=Max('raw_transactions__timestamp'))
            .distinct()
            .order_by('-latest_local', '-anchor_event')[: max(sample_coins * 8, 12)]
        )

        samples = []
        for coin in candidates:
            pool_address = PoolMapping.objects.filter(coin_id=coin.mint_address).values_list(
                'pool_address', flat=True,
            ).first()
            if not pool_address:
                continue
            latest_tx = RawTransaction.objects.filter(
                coin_id=coin.mint_address,
                pool_address=pool_address,
            ).order_by('-timestamp').first()
            if latest_tx is None:
                continue
            start = latest_tx.timestamp - timedelta(hours=hours)
            sampled_rows = list(
                RawTransaction.objects.filter(
                    coin_id=coin.mint_address,
                    pool_address=pool_address,
                    timestamp__gte=start,
                    timestamp__lte=latest_tx.timestamp,
                )
                .order_by('-timestamp', '-id')[:txs_per_coin]
            )
            if not sampled_rows:
                continue
            samples.append({
                'coin_id': coin.mint_address,
                'pool_address': pool_address,
                'start': start,
                'end': latest_tx.timestamp,
                'rows': sampled_rows,
                'window_signatures': list(
                    RawTransaction.objects.filter(
                        coin_id=coin.mint_address,
                        pool_address=pool_address,
                        timestamp__gte=start,
                        timestamp__lte=latest_tx.timestamp,
                    ).values_list('tx_signature', flat=True)
                ),
            })
            if len(samples) >= sample_coins:
                break
        return samples

    def _audit_window(self, *, sample, rpc_url, tx_cache):
        try:
            signature_rows = fetch_signatures_for_address(
                sample['pool_address'],
                start=sample['start'],
                end=sample['end'],
                rpc_url=rpc_url,
            )
            chain_trade_signatures = set()
            ambiguous_signatures = set()
            for signature_row in signature_rows:
                signature = signature_row['signature']
                if signature_row.get('err') is not None:
                    continue
                observation = build_chain_observation(
                    self._fetch_tx(signature, rpc_url=rpc_url, tx_cache=tx_cache),
                    mint_address=sample['coin_id'],
                    pool_address=sample['pool_address'],
                )
                if not observation.get('exists') or not observation.get('success'):
                    continue
                if observation.get('derivation_complete'):
                    chain_trade_signatures.add(signature)
                else:
                    ambiguous_signatures.add(signature)

            return compare_window_to_chain(
                coin_id=sample['coin_id'],
                pool_address=sample['pool_address'],
                start=sample['start'],
                end=sample['end'],
                warehouse_signatures=sample['window_signatures'],
                chain_trade_signatures=chain_trade_signatures,
                ambiguous_chain_signatures=ambiguous_signatures,
                signature_scan_count=len(signature_rows),
            )
        except Exception as exc:
            return {
                'status': 'warning',
                'detail': (
                    f"Direct-RPC window scan could not complete for {sample['coin_id']} "
                    f"over {sample['start']} -> {sample['end']}: {exc}"
                ),
                'coin': sample['coin_id'],
                'pool_address': sample['pool_address'],
                'start': sample['start'],
                'end': sample['end'],
                'findings': [],
                'warnings': ['window_scan_failed'],
                'missing_signatures': [],
                'extra_signatures': [],
                'ambiguous_signatures': [],
                'chain_trade_signature_count': 0,
                'warehouse_signature_count': len(sample['window_signatures']),
                'pool_signature_scan_count': 0,
            }

    def _fetch_tx(self, signature, *, rpc_url, tx_cache):
        if signature not in tx_cache:
            tx_cache[signature] = fetch_transaction(signature, rpc_url=rpc_url)
        return tx_cache[signature]

    def _persist_run(
        self,
        *,
        started_at,
        status,
        options,
        rows,
        findings,
        warnings,
        summary,
        notes=None,
    ):
        U001RD001ChainAuditRun.objects.create(
            started_at=started_at,
            completed_at=timezone.now(),
            status=status,
            options=self._json_ready(options),
            coin_count=len({row.coin_id for row in rows}),
            transaction_count=len(rows),
            finding_count=len(findings),
            warning_count=len(warnings),
            summary=self._json_ready(summary),
            notes=notes,
        )

    def _json_ready(self, value):
        return json.loads(json.dumps(value, default=self._json_default))

    def _json_default(self, value):
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)
