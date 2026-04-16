"""Audit sampled U-001 FL-001 candles derived from warehouse RD-001 trades."""

from datetime import datetime, timedelta
from decimal import Decimal
import json

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max, Min
from django.utils import timezone

from pipeline.audits.fl001_chain_derived import (
    ONE_MINUTE,
    compare_candles,
    derive_candles,
    floor_timestamp,
    summarize_results,
)
from warehouse.models import (
    MigratedCoin,
    OHLCVCandle,
    PoolMapping,
    RawTransaction,
    U001FL001DerivedAuditRun,
    U001PipelineStatus,
    U002OHLCVCandle,
)


class Command(BaseCommand):
    help = "Audit sampled U-001 FL-001 candles derived from canonical RD-001 trades"

    def add_arguments(self, parser):
        parser.add_argument(
            '--sample-coins',
            type=int,
            default=1,
            help='How many coins to sample (default: 1)',
        )
        parser.add_argument(
            '--hours',
            type=int,
            default=1,
            help='How many trailing hours of candle history to compare per coin (default: 1)',
        )
        parser.add_argument(
            '--price-tolerance-pct',
            type=float,
            default=0.05,
            help='Warn when derived price fields drift by more than this fraction (default: 0.05)',
        )
        parser.add_argument(
            '--volume-tolerance-pct',
            type=float,
            default=0.10,
            help='Warn when derived volume drifts by more than this fraction (default: 0.10)',
        )
        parser.add_argument(
            '--fail-on-findings',
            action='store_true',
            help='Exit non-zero when any candle mismatch is detected',
        )

    def handle(self, *args, **options):
        started_at = timezone.now()
        audit_options = {
            'sample_coins': options['sample_coins'],
            'hours': options['hours'],
            'price_tolerance_pct': options['price_tolerance_pct'],
            'volume_tolerance_pct': options['volume_tolerance_pct'],
            'sol_symbol': 'SOLUSDT',
        }
        results = []
        findings = []
        warnings = []
        summary = {}

        self.stdout.write("=" * 60)
        self.stdout.write("U-001 FL-001 DERIVED AUDIT")
        self.stdout.write("=" * 60)
        self.stdout.write(
            "note: this command derives U-001 FL-001 candles from warehouse RD-001 plus local SOLUSDT candles."
        )

        try:
            samples = self._sample_coin_windows(
                sample_coins=options['sample_coins'],
                hours=options['hours'],
            )
            if not samples:
                warning = 'No FL-001 / RD-001 sample windows were available for derived-candle audit.'
                warnings.append(warning)
                self.stdout.write(f"[warning] {warning}")
            else:
                for sample in samples:
                    result = self._audit_sample(
                        sample=sample,
                        price_tolerance_pct=Decimal(str(options['price_tolerance_pct'])),
                        volume_tolerance_pct=Decimal(str(options['volume_tolerance_pct'])),
                    )
                    results.append(result)
                    if result['status'] == 'finding':
                        findings.append(result['detail'])
                    elif result['status'] == 'warning':
                        warnings.append(result['detail'])
                    self.stdout.write(f"[{result['status']}] {result['detail']}")

            summary = {
                'results': results,
                'aggregate': summarize_results(results),
                'findings': findings,
                'warnings': warnings,
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
                results=results,
                findings=findings,
                warnings=warnings,
                summary=summary,
            )
            self.stdout.write("\n" + "=" * 60)

            if findings and options['fail_on_findings']:
                raise CommandError('U-001 FL-001 derived audit found mismatches')
        except Exception as exc:
            if not isinstance(exc, CommandError):
                self._persist_run(
                    started_at=started_at,
                    status='error',
                    options=audit_options,
                    results=[],
                    findings=findings,
                    warnings=warnings,
                    summary=summary,
                    notes=str(exc),
                )
            raise

    def _sample_coin_windows(self, sample_coins, hours):
        if sample_coins <= 0:
            return []

        sol_bounds = U002OHLCVCandle.objects.filter(asset_id='SOLUSDT').aggregate(
            min_ts=Min('timestamp'),
            max_ts=Max('timestamp'),
        )
        sol_min = sol_bounds['min_ts']
        sol_max = sol_bounds['max_ts']
        if sol_min is None or sol_max is None:
            return []

        fl_complete_ids = set(
            U001PipelineStatus.objects.filter(
                layer_id='FL-001',
                status='window_complete',
            ).values_list('coin_id', flat=True)
        )
        raw_ids = set(RawTransaction.objects.values_list('coin_id', flat=True).distinct())
        ohlcv_ids = set(OHLCVCandle.objects.values_list('coin_id', flat=True).distinct())
        mapped_ids = set(PoolMapping.objects.values_list('coin_id', flat=True).distinct())
        eligible_ids = fl_complete_ids & raw_ids & ohlcv_ids & mapped_ids
        if not eligible_ids:
            return []

        latest_candle_rows = list(
            OHLCVCandle.objects.filter(coin_id__in=eligible_ids)
            .values('coin_id')
            .annotate(latest_candle=Max('timestamp'))
            .order_by('-latest_candle')
        )
        latest_raw_map = {
            row['coin_id']: row['latest_raw']
            for row in RawTransaction.objects.filter(
                coin_id__in=[row['coin_id'] for row in latest_candle_rows],
            )
            .values('coin_id')
            .annotate(latest_raw=Max('timestamp'))
        }
        coin_map = MigratedCoin.objects.in_bulk(
            [row['coin_id'] for row in latest_candle_rows],
            field_name='mint_address',
        )
        candidates = []
        for row in latest_candle_rows:
            latest_raw = latest_raw_map.get(row['coin_id'])
            latest_candle = row['latest_candle']
            if latest_raw is None or latest_candle is None:
                continue
            overlap_end = min(
                latest_candle,
                floor_timestamp(latest_raw, timedelta(minutes=5)),
            )
            if overlap_end > sol_max:
                continue
            candidates.append({
                'coin_id': row['coin_id'],
                'latest_candle': latest_candle,
                'latest_raw': latest_raw,
                'overlap_end': overlap_end,
            })
        candidates.sort(key=lambda row: row['overlap_end'], reverse=True)
        candidates = candidates[: max(sample_coins * 8, 12)]

        samples = []
        for row in candidates:
            coin = coin_map.get(row['coin_id'])
            if coin is None:
                continue
            overlap_end = row['overlap_end']
            start = max(coin.window_start_time, overlap_end - timedelta(hours=hours))
            if overlap_end < start:
                continue
            if start < sol_min:
                start = sol_min
            trades = list(
                RawTransaction.objects.filter(
                    coin_id=coin.mint_address,
                    timestamp__gte=start,
                    timestamp__lte=overlap_end + timedelta(minutes=5),
                ).order_by('timestamp', 'id')
            )
            stored_candles = list(
                OHLCVCandle.objects.filter(
                    coin_id=coin.mint_address,
                    timestamp__gte=floor_timestamp(start, timedelta(minutes=5)),
                    timestamp__lte=overlap_end,
                ).order_by('timestamp')
            )
            if not trades or not stored_candles:
                continue

            minute_start = floor_timestamp(start, ONE_MINUTE)
            minute_end = floor_timestamp(overlap_end + timedelta(minutes=5), ONE_MINUTE)
            sol_candles = list(
                U002OHLCVCandle.objects.filter(
                    asset_id='SOLUSDT',
                    timestamp__gte=minute_start,
                    timestamp__lte=minute_end,
                ).values('timestamp', 'close_price')
            )
            if not sol_candles:
                continue

            samples.append({
                'coin': coin,
                'start': start,
                'end': overlap_end,
                'trades': trades,
                'stored_candles': stored_candles,
                'sol_candles': sol_candles,
            })
            if len(samples) >= sample_coins:
                break

        return samples

    def _audit_sample(self, *, sample, price_tolerance_pct, volume_tolerance_pct):
        sol_usd_by_minute = {
            row['timestamp']: Decimal(row['close_price'])
            for row in sample['sol_candles']
            if row['close_price'] is not None
        }
        derived_candles, meta = derive_candles(
            sample['trades'],
            sol_usd_by_minute,
            token_decimals=sample['coin'].decimals or 6,
        )
        stored = [
            {
                'timestamp': row.timestamp,
                'open_price': row.open_price,
                'high_price': row.high_price,
                'low_price': row.low_price,
                'close_price': row.close_price,
                'volume': row.volume,
            }
            for row in sample['stored_candles']
        ]
        return compare_candles(
            coin_id=sample['coin'].mint_address,
            start=sample['start'],
            end=sample['end'],
            stored_candles=stored,
            derived_candles=derived_candles,
            price_tolerance_pct=price_tolerance_pct,
            volume_tolerance_pct=volume_tolerance_pct,
            skipped_missing_sol_price=meta['skipped_missing_sol_price'],
        )

    def _persist_run(
        self,
        *,
        started_at,
        status,
        options,
        results,
        findings,
        warnings,
        summary,
        notes=None,
    ):
        U001FL001DerivedAuditRun.objects.create(
            started_at=started_at,
            completed_at=timezone.now(),
            status=status,
            options=self._json_ready(options),
            coin_count=len({row['coin'] for row in results}),
            candle_count=sum(row['derived_count'] for row in results),
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
