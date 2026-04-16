"""Sample live upstream sources and compare them against local U-001 warehouse state."""

from collections import Counter
from datetime import datetime, timedelta, timezone as dt_timezone
import json

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max
from django.utils import timezone

from pipeline.connectors.moralis_discovery import fetch_graduated_tokens
from pipeline.conformance.fl001_geckoterminal import conform as conform_fl001
from pipeline.conformance.fl002_moralis import conform as conform_fl002
from pipeline.conformance.rd001_shyft import conform as conform_rd001_shyft
from pipeline.connectors.geckoterminal import fetch_ohlcv
from pipeline.connectors.moralis import fetch_holders
from pipeline.connectors.shyft import fetch_transactions as fetch_shyft_transactions
from pipeline.pipelines.rd001 import SHYFT_RETENTION_DAYS
from warehouse.models import (
    HolderSnapshot,
    MigratedCoin,
    OHLCVCandle,
    PoolMapping,
    RawTransaction,
    U001SourceAuditRun,
    U001PipelineStatus,
)


class Command(BaseCommand):
    help = "Audit sampled live upstream source data against the local U-001 warehouse"

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-discovery-lag-hours',
            type=float,
            default=6.0,
            help='Flag discovery when upstream latest graduation is newer than local latest anchor by more than this many hours (default: 6)',
        )
        parser.add_argument(
            '--sample-fl001',
            type=int,
            default=1,
            help='How many FL-001 coins to sample from GeckoTerminal (default: 1)',
        )
        parser.add_argument(
            '--sample-fl002',
            type=int,
            default=1,
            help='How many FL-002 coins to sample from Moralis (default: 1)',
        )
        parser.add_argument(
            '--sample-rd001',
            type=int,
            default=1,
            help='How many recent RD-001 coins to sample from Shyft (default: 1)',
        )
        parser.add_argument(
            '--fl001-hours',
            type=int,
            default=24,
            help='Hours of FL-001 history to compare per sample coin (default: 24)',
        )
        parser.add_argument(
            '--fl002-hours',
            type=int,
            default=12,
            help='Hours of FL-002 history to compare per sample coin (default: 12)',
        )
        parser.add_argument(
            '--rd001-hours',
            type=int,
            default=3,
            help='Hours of recent RD-001 history to compare per sample coin (default: 3)',
        )
        parser.add_argument(
            '--fail-on-findings',
            action='store_true',
            help='Exit non-zero when any mismatch or lag finding is detected',
        )

    def handle(self, *args, **options):
        now = timezone.now()
        started_at = now
        findings = []
        warnings = []
        summary = {}
        audit_options = {
            'max_discovery_lag_hours': options['max_discovery_lag_hours'],
            'sample_fl001': options['sample_fl001'],
            'sample_fl002': options['sample_fl002'],
            'sample_rd001': options['sample_rd001'],
            'fl001_hours': options['fl001_hours'],
            'fl002_hours': options['fl002_hours'],
            'rd001_hours': options['rd001_hours'],
        }

        self.stdout.write("=" * 60)
        self.stdout.write("U-001 LIVE SOURCE AUDIT")
        self.stdout.write("=" * 60)
        self.stdout.write(
            "note: this command performs live upstream fetches and may consume provider budget."
        )
        try:
            discovery_result = self._audit_discovery(options['max_discovery_lag_hours'])
            self._print_discovery_result(discovery_result)
            if discovery_result['status'] == 'finding':
                findings.append(discovery_result['detail'])
            elif discovery_result['status'] == 'warning':
                warnings.append(discovery_result['detail'])

            fl001_results = self._audit_fl001_samples(
                sample_size=options['sample_fl001'],
                hours=options['fl001_hours'],
                now=now,
            )
            self._print_sample_results('FL-001', fl001_results)
            findings.extend(
                row['detail'] for row in fl001_results if row['status'] == 'finding'
            )
            warnings.extend(
                row['detail'] for row in fl001_results if row['status'] == 'warning'
            )

            fl002_results = self._audit_fl002_samples(
                sample_size=options['sample_fl002'],
                hours=options['fl002_hours'],
                now=now,
            )
            self._print_sample_results('FL-002', fl002_results)
            findings.extend(
                row['detail'] for row in fl002_results if row['status'] == 'finding'
            )
            warnings.extend(
                row['detail'] for row in fl002_results if row['status'] == 'warning'
            )

            rd001_results = self._audit_rd001_samples(
                sample_size=options['sample_rd001'],
                hours=options['rd001_hours'],
                now=now,
            )
            self._print_sample_results('RD-001', rd001_results)
            findings.extend(
                row['detail'] for row in rd001_results if row['status'] == 'finding'
            )
            warnings.extend(
                row['detail'] for row in rd001_results if row['status'] == 'warning'
            )

            summary = {
                'discovery': discovery_result,
                'layers': {
                    'fl001': fl001_results,
                    'fl002': fl002_results,
                    'rd001': rd001_results,
                },
                'findings': findings,
                'warnings': warnings,
            }

            if findings:
                self.stdout.write("\n--- Findings ---")
                for item in findings:
                    self.stdout.write(f"- {item}")
            else:
                self.stdout.write("\nNo findings detected.")

            if warnings:
                self.stdout.write("\n--- Warnings ---")
                for item in warnings:
                    self.stdout.write(f"- {item}")
            else:
                self.stdout.write("\nNo warnings detected.")

            self.stdout.write("\n" + "=" * 60)

            final_status = 'ok'
            if findings:
                final_status = 'finding'
            elif warnings:
                final_status = 'warning'
            self._persist_run(
                started_at=started_at,
                status=final_status,
                options=audit_options,
                findings=findings,
                warnings=warnings,
                summary=summary,
            )

            if findings and options['fail_on_findings']:
                raise CommandError("U-001 live source audit found mismatches or lag")
        except Exception as exc:
            if not isinstance(exc, CommandError):
                self._persist_run(
                    started_at=started_at,
                    status='error',
                    options=audit_options,
                    findings=findings,
                    warnings=warnings,
                    summary=summary,
                    notes=str(exc),
                )
            raise

    def _audit_discovery(self, max_lag_hours):
        latest_anchor = MigratedCoin.objects.order_by('-anchor_event').values_list(
            'anchor_event', flat=True,
        ).first()
        data = fetch_graduated_tokens(limit=100)
        result = data.get('result', [])
        latest_source = None
        if result:
            latest_source = self._parse_graduated_at(result[0]['graduatedAt'])

        if latest_source is None:
            return {
                'status': 'warning',
                'detail': 'Discovery source returned no results on the first page.',
                'latest_source': latest_source,
                'latest_anchor': latest_anchor,
                'lag_hours': None,
            }

        lag_hours = None
        if latest_anchor is not None:
            lag_hours = max((latest_source - latest_anchor).total_seconds() / 3600, 0.0)

        status = 'ok'
        detail = 'Local discovery is within the configured lag threshold.'
        if latest_anchor is None:
            status = 'finding'
            detail = 'Local discovery has no coins, but Moralis returned graduated tokens.'
        elif lag_hours > max_lag_hours:
            status = 'finding'
            detail = (
                f'Discovery lag is {lag_hours:.1f}h: source latest graduation '
                f'is {latest_source}, local latest anchor is {latest_anchor}.'
            )

        return {
            'status': status,
            'detail': detail,
            'latest_source': latest_source,
            'latest_anchor': latest_anchor,
            'lag_hours': lag_hours,
        }

    def _audit_fl001_samples(self, sample_size, hours, now):
        if sample_size <= 0:
            return []

        results = []
        informative_results = []
        skipped_empty = 0
        candidates = list(
            MigratedCoin.objects.filter(
                mint_address__in=PoolMapping.objects.values_list('coin_id', flat=True),
                pipeline_statuses__layer_id='FL-001',
                pipeline_statuses__status='window_complete',
                anchor_event__lte=now - MigratedCoin.OBSERVATION_WINDOW_END,
            )
            .annotate(latest_local=Max('ohlcv_candles__timestamp'))
            .distinct()
            .order_by('-latest_local', '-anchor_event')[:self._candidate_limit(sample_size)]
        )
        if not candidates:
            return [{
                'status': 'warning',
                'detail': 'No FL-001 window_complete sample candidates were available.',
            }]

        for coin in candidates:
            pool_address = PoolMapping.objects.filter(coin_id=coin.mint_address).values_list(
                'pool_address', flat=True,
            ).first()
            audit_start, audit_end = self._aligned_window(
                start=max(coin.window_start_time, coin.window_end_time - timedelta(hours=hours)),
                end=coin.window_end_time,
                resolution=OHLCVCandle.TEMPORAL_RESOLUTION,
            )
            warehouse_ts = Counter(
                OHLCVCandle.objects.filter(
                    coin_id=coin.mint_address,
                    timestamp__gte=audit_start,
                    timestamp__lte=audit_end,
                ).values_list('timestamp', flat=True)
            )
            raw, _meta = fetch_ohlcv(pool_address, audit_start, audit_end)
            canonical = conform_fl001(raw, coin.mint_address)
            source_ts = Counter(row['timestamp'] for row in canonical)
            result = self._compare_counter_result(
                layer='FL-001',
                coin=coin.mint_address,
                start=audit_start,
                end=audit_end,
                warehouse_counter=warehouse_ts,
                source_counter=source_ts,
            )
            if not result['informative']:
                skipped_empty += 1
                continue
            informative_results.append(result)
            if len(informative_results) >= sample_size:
                break

        results.extend(informative_results)
        results.extend(self._sample_selection_notes(
            layer='FL-001',
            skipped_empty=skipped_empty,
            sample_size=sample_size,
            actual_size=len(informative_results),
        ))
        return results

    def _audit_fl002_samples(self, sample_size, hours, now):
        if sample_size <= 0:
            return []

        results = []
        informative_results = []
        skipped_empty = 0
        candidates = list(
            MigratedCoin.objects.filter(
                pipeline_statuses__layer_id='FL-002',
                pipeline_statuses__status='window_complete',
                anchor_event__lte=now - MigratedCoin.OBSERVATION_WINDOW_END,
            )
            .annotate(latest_local=Max('holder_snapshots__timestamp'))
            .distinct()
            .order_by('-latest_local', '-anchor_event')[:self._candidate_limit(sample_size)]
        )
        if not candidates:
            return [{
                'status': 'warning',
                'detail': 'No FL-002 window_complete sample candidates were available.',
            }]

        for coin in candidates:
            audit_start, audit_end = self._aligned_window(
                start=max(coin.window_start_time, coin.window_end_time - timedelta(hours=hours)),
                end=coin.window_end_time,
                resolution=HolderSnapshot.TEMPORAL_RESOLUTION,
            )
            warehouse_ts = Counter(
                HolderSnapshot.objects.filter(
                    coin_id=coin.mint_address,
                    timestamp__gte=audit_start,
                    timestamp__lte=audit_end,
                ).values_list('timestamp', flat=True)
            )
            raw, _meta = fetch_holders(coin.mint_address, audit_start, audit_end)
            canonical = conform_fl002(raw, coin.mint_address)
            source_ts = Counter(row['timestamp'] for row in canonical)
            result = self._compare_counter_result(
                layer='FL-002',
                coin=coin.mint_address,
                start=audit_start,
                end=audit_end,
                warehouse_counter=warehouse_ts,
                source_counter=source_ts,
            )
            if not result['informative']:
                skipped_empty += 1
                continue
            informative_results.append(result)
            if len(informative_results) >= sample_size:
                break

        results.extend(informative_results)
        results.extend(self._sample_selection_notes(
            layer='FL-002',
            skipped_empty=skipped_empty,
            sample_size=sample_size,
            actual_size=len(informative_results),
        ))
        return results

    def _audit_rd001_samples(self, sample_size, hours, now):
        if sample_size <= 0:
            return []

        recent_cutoff = now - timedelta(days=SHYFT_RETENTION_DAYS)
        results = []
        informative_results = []
        skipped_empty = 0
        candidates = list(
            MigratedCoin.objects.filter(
                mint_address__in=PoolMapping.objects.values_list('coin_id', flat=True),
                pipeline_statuses__layer_id='RD-001',
                pipeline_statuses__status__in=('partial', 'window_complete'),
                anchor_event__gte=recent_cutoff,
            )
            .annotate(latest_local=Max('raw_transactions__timestamp'))
            .distinct()
            .order_by('-latest_local', '-anchor_event')[:self._candidate_limit(sample_size)]
        )
        if not candidates:
            diagnostics = self._rd001_recent_diagnostics(recent_cutoff)
            return [{
                'status': 'warning',
                'detail': (
                    'No recent RD-001 Shyft sample candidates were available '
                    f"(recent_discovered={diagnostics['recent_discovered']}, "
                    f"recent_mapped={diagnostics['recent_mapped']}, "
                    f"recent_with_raw={diagnostics['recent_with_raw']}, "
                    f"recent_with_rd001_status={diagnostics['recent_with_rd001_status']}, "
                    f"recent_partial_or_complete={diagnostics['recent_partial_or_complete']})."
                ),
            }]

        for coin in candidates:
            pool_address = PoolMapping.objects.filter(coin_id=coin.mint_address).values_list(
                'pool_address', flat=True,
            ).first()
            audit_start = max(coin.window_start_time, now - timedelta(hours=hours))
            audit_end = now
            warehouse_sigs = Counter(
                RawTransaction.objects.filter(
                    coin_id=coin.mint_address,
                    timestamp__gte=audit_start,
                    timestamp__lte=audit_end,
                ).values_list('tx_signature', flat=True)
            )
            raw, _meta = fetch_shyft_transactions(
                pool_address, audit_start, audit_end, max_workers=1,
            )
            canonical, _skipped = conform_rd001_shyft(raw, coin.mint_address, pool_address)
            source_sigs = Counter(row['tx_signature'] for row in canonical)
            result = self._compare_counter_result(
                layer='RD-001',
                coin=coin.mint_address,
                start=audit_start,
                end=audit_end,
                warehouse_counter=warehouse_sigs,
                source_counter=source_sigs,
                counter_label='signatures',
            )
            if not result['informative']:
                skipped_empty += 1
                continue
            informative_results.append(result)
            if len(informative_results) >= sample_size:
                break

        results.extend(informative_results)
        results.extend(self._sample_selection_notes(
            layer='RD-001',
            skipped_empty=skipped_empty,
            sample_size=sample_size,
            actual_size=len(informative_results),
        ))
        return results

    def _compare_counter_result(
        self,
        layer,
        coin,
        start,
        end,
        warehouse_counter,
        source_counter,
        counter_label='timestamps',
    ):
        missing = source_counter - warehouse_counter
        extra = warehouse_counter - source_counter
        source_total = sum(source_counter.values())
        warehouse_total = sum(warehouse_counter.values())
        if not source_total and not warehouse_total:
            return {
                'status': 'warning',
                'detail': (
                    f'{layer} sample window was empty on both source and warehouse for '
                    f'{coin} over {start} -> {end}.'
                ),
                'coin': coin,
                'informative': False,
            }
        if not missing and not extra:
            return {
                'status': 'ok',
                'detail': (
                    f'{layer} sample matched for {coin} over {start} -> {end} '
                    f'({source_total} {counter_label}).'
                ),
                'coin': coin,
                'informative': True,
            }

        return {
            'status': 'finding',
            'detail': (
                f'{layer} mismatch for {coin} over {start} -> {end}: '
                f'source={source_total}, warehouse={warehouse_total}, '
                f'missing_{counter_label}={sum(missing.values())}, extra_{counter_label}={sum(extra.values())}.'
            ),
            'coin': coin,
            'informative': True,
        }

    def _print_discovery_result(self, result):
        self.stdout.write("\n--- Discovery ---")
        self.stdout.write(f"latest_source_graduation: {result['latest_source']}")
        self.stdout.write(f"latest_local_anchor: {result['latest_anchor']}")
        self.stdout.write(f"lag_hours: {result['lag_hours']}")
        self.stdout.write(f"status: {result['status']}")
        self.stdout.write(f"detail: {result['detail']}")

    def _print_sample_results(self, layer, rows):
        self.stdout.write(f"\n--- {layer} Samples ---")
        if not rows:
            self.stdout.write("No samples requested.")
            return
        for row in rows:
            self.stdout.write(f"[{row['status']}] {row['detail']}")

    def _parse_graduated_at(self, value):
        if value.endswith('Z'):
            value = value[:-1] + '+00:00'
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_timezone.utc)
        return dt.replace(microsecond=0)

    def _aligned_window(self, start, end, resolution):
        if start is None or end is None:
            return start, end
        aligned_start = self._floor_timestamp(start, resolution)
        aligned_end = self._floor_timestamp(end, resolution)
        if aligned_end < aligned_start:
            aligned_end = aligned_start
        return aligned_start, aligned_end

    def _floor_timestamp(self, value, resolution):
        seconds = int(resolution.total_seconds())
        epoch = int(value.timestamp())
        floored = epoch - (epoch % seconds)
        return datetime.fromtimestamp(floored, tz=dt_timezone.utc)

    def _candidate_limit(self, sample_size):
        return max(sample_size * 8, 12)

    def _sample_selection_notes(self, layer, skipped_empty, sample_size, actual_size):
        notes = []
        if skipped_empty:
            notes.append({
                'status': 'info',
                'detail': (
                    f'{layer} skipped {skipped_empty} empty sample windows before selecting '
                    f'{actual_size} informative sample(s).'
                ),
            })
        if actual_size == 0:
            notes.append({
                'status': 'warning',
                'detail': (
                    f'{layer} found no informative sample windows. The candidate set was empty '
                    'on both source and warehouse for the sampled range.'
                ),
            })
        elif actual_size < sample_size:
            notes.append({
                'status': 'warning',
                'detail': (
                    f'{layer} only found {actual_size} informative sample(s) out of the '
                    f'{sample_size} requested.'
                ),
            })
        return notes

    def _rd001_recent_diagnostics(self, recent_cutoff):
        recent_coins = MigratedCoin.objects.filter(anchor_event__gte=recent_cutoff)
        recent_discovered = recent_coins.count()
        recent_mapped_qs = recent_coins.filter(
            mint_address__in=PoolMapping.objects.values_list('coin_id', flat=True),
        ).distinct()
        recent_mapped = recent_mapped_qs.count()
        recent_with_raw = recent_mapped_qs.filter(
            mint_address__in=RawTransaction.objects.values_list('coin_id', flat=True),
        ).distinct().count()
        recent_with_rd001_status = recent_mapped_qs.filter(
            pipeline_statuses__layer_id='RD-001',
        ).distinct().count()
        recent_partial_or_complete = recent_mapped_qs.filter(
            pipeline_statuses__layer_id='RD-001',
            pipeline_statuses__status__in=('partial', 'window_complete'),
        ).distinct().count()
        return {
            'recent_discovered': recent_discovered,
            'recent_mapped': recent_mapped,
            'recent_with_raw': recent_with_raw,
            'recent_with_rd001_status': recent_with_rd001_status,
            'recent_partial_or_complete': recent_partial_or_complete,
        }

    def _persist_run(self, started_at, status, options, findings, warnings, summary, notes=None):
        U001SourceAuditRun.objects.create(
            started_at=started_at,
            completed_at=timezone.now(),
            status=status,
            options=self._json_ready(options),
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
        raise TypeError(f'Object of type {type(value).__name__} is not JSON serializable')
