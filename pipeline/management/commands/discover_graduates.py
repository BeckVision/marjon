"""Management command to discover graduated pump.fun tokens."""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Max

from pipeline.conformance.u001_universe_moralis import conform_moralis_graduated
from pipeline.connectors.moralis import (
    CU_PER_CALL,
    DAILY_CU_LIMIT,
    get_daily_cu_used,
)
from pipeline.connectors.moralis_discovery import fetch_graduated_tokens
from pipeline.loaders.u001_universe import load_graduated_tokens
from warehouse.models import MigratedCoin, PipelineBatchRun, RunMode, RunStatus

logger = logging.getLogger(__name__)

BOOTSTRAP_STATE_PATH = Path(__file__).resolve().parent.parent.parent.parent / '.moralis_bootstrap_state.json'

DEFAULT_MAX_PAGES_STEADY = 50
DEFAULT_MAX_PAGES_BOOTSTRAP = 700


def run_discovery_steady_state(max_pages=None):
    """Core steady-state discovery logic.

    Fetch newest graduates, stop when we hit known tokens.

    Args:
        max_pages: Safety limit on pages. Default: 50.

    Returns:
        dict with 'created', 'updated', 'pages', 'cu_consumed'.

    Raises:
        ValueError: If no existing tokens (need bootstrap first).
    """
    if max_pages is None:
        max_pages = DEFAULT_MAX_PAGES_STEADY

    watermark = MigratedCoin.objects.aggregate(
        Max('anchor_event')
    )['anchor_event__max']

    if watermark is None:
        raise ValueError("No existing tokens. Run bootstrap first.")

    logger.info("Steady-state mode: watermark=%s", watermark)

    tokens_to_load = []
    cursor = None
    pages_fetched = 0
    cu_consumed = 0
    hit_watermark = False

    for page_num in range(1, max_pages + 1):
        daily_used = get_daily_cu_used()
        if daily_used + CU_PER_CALL > DAILY_CU_LIMIT:
            logger.warning(
                "CU budget exhausted after %d pages. Stopping.",
                pages_fetched,
            )
            break

        data = fetch_graduated_tokens(cursor=cursor)
        pages_fetched += 1
        cu_consumed += CU_PER_CALL

        result = data.get('result', [])
        if not result:
            break

        for token in result:
            graduated_at_str = token['graduatedAt']
            if graduated_at_str.endswith('Z'):
                graduated_at_str = graduated_at_str[:-1] + '+00:00'
            graduated_at = datetime.fromisoformat(graduated_at_str)
            graduated_at = graduated_at.replace(microsecond=0)

            if graduated_at < watermark:
                hit_watermark = True
                break

            tokens_to_load.append(token)

        if hit_watermark:
            break

        cursor = data.get('cursor')
        if not cursor:
            break

        time.sleep(0.5)

    created = 0
    updated = 0
    if tokens_to_load:
        canonical = conform_moralis_graduated(tokens_to_load)
        created, updated = load_graduated_tokens(canonical)

    logger.info(
        "Steady-state complete: %d new, %d updated, %d pages, %d CU",
        created, updated, pages_fetched, cu_consumed,
    )

    return {
        'created': created,
        'updated': updated,
        'pages': pages_fetched,
        'cu_consumed': cu_consumed,
    }


class Command(BaseCommand):
    help = "Discover graduated pump.fun tokens via Moralis and load into MigratedCoin"

    def add_arguments(self, parser):
        parser.add_argument(
            '--mode', required=True, choices=['bootstrap', 'steady-state'],
            help='bootstrap: paginate all tokens; steady-state: stop at watermark',
        )
        parser.add_argument(
            '--max-pages', type=int, default=None,
            help='Safety limit on pages (default: 50 steady-state, 700 bootstrap)',
        )
        parser.add_argument(
            '--restart-bootstrap', action='store_true',
            help='Delete saved bootstrap state and start from scratch',
        )

    def handle(self, *args, **options):
        mode = options['mode']
        max_pages = options['max_pages']
        restart_bootstrap = options['restart_bootstrap']

        if max_pages is None:
            max_pages = (
                DEFAULT_MAX_PAGES_BOOTSTRAP if mode == 'bootstrap'
                else DEFAULT_MAX_PAGES_STEADY
            )

        # CU budget check
        daily_used = get_daily_cu_used()
        if daily_used + CU_PER_CALL > DAILY_CU_LIMIT:
            raise CommandError(
                f"Insufficient CU budget. Used={daily_used}, "
                f"limit={DAILY_CU_LIMIT}, need at least {CU_PER_CALL} for one page."
            )

        # Create batch run
        batch = PipelineBatchRun.objects.create(
            pipeline_id='universe',
            mode=RunMode.BOOTSTRAP if mode == 'bootstrap' else RunMode.STEADY_STATE,
            status=RunStatus.STARTED,
            started_at=datetime.now(timezone.utc),
        )

        try:
            if mode == 'bootstrap':
                self._handle_bootstrap(batch, max_pages, restart_bootstrap)
            else:
                result = run_discovery_steady_state(max_pages)

                batch.status = RunStatus.COMPLETE
                batch.completed_at = datetime.now(timezone.utc)
                batch.coins_attempted = result['created'] + result['updated']
                batch.coins_succeeded = result['created'] + result['updated']
                batch.cu_consumed = result['cu_consumed']
                batch.api_calls = result['pages']
                batch.save()

                self.stdout.write(
                    f"Discovered {result['created']} new tokens, "
                    f"updated {result['updated']}, "
                    f"{result['pages']} pages, "
                    f"{result['cu_consumed']} CU consumed"
                )
        except CommandError:
            batch.status = RunStatus.ERROR
            batch.completed_at = datetime.now(timezone.utc)
            batch.save()
            raise
        except ValueError as e:
            batch.status = RunStatus.ERROR
            batch.completed_at = datetime.now(timezone.utc)
            batch.save()
            raise CommandError(str(e))
        except Exception as e:
            batch.status = RunStatus.ERROR
            batch.error_message = str(e)
            batch.completed_at = datetime.now(timezone.utc)
            batch.save()
            logger.error("Discovery failed", exc_info=True)
            raise CommandError(f"Discovery failed: {e}")

    def _handle_bootstrap(self, batch, max_pages, restart_bootstrap):
        """Paginate through all graduated tokens."""
        if restart_bootstrap and BOOTSTRAP_STATE_PATH.exists():
            BOOTSTRAP_STATE_PATH.unlink()
            logger.info("Deleted bootstrap state file, starting fresh")

        # Resume from saved state
        cursor = None
        pages_completed = 0
        if BOOTSTRAP_STATE_PATH.exists():
            state = json.loads(BOOTSTRAP_STATE_PATH.read_text())
            cursor = state.get('cursor')
            pages_completed = state.get('pages_completed', 0)
            logger.info(
                "Resuming bootstrap from page %d", pages_completed + 1,
            )
        else:
            if MigratedCoin.objects.exists() and not restart_bootstrap:
                raise CommandError(
                    "MigratedCoin table has data. Use --restart-bootstrap "
                    "to re-bootstrap or steady-state to update."
                )

        total_created = 0
        total_updated = 0
        cu_consumed = 0

        for page_num in range(1, max_pages + 1):
            daily_used = get_daily_cu_used()
            if daily_used + CU_PER_CALL > DAILY_CU_LIMIT:
                logger.warning(
                    "CU budget exhausted after %d pages. "
                    "Resume tomorrow.",
                    pages_completed,
                )
                break

            data = fetch_graduated_tokens(cursor=cursor)
            cu_consumed += CU_PER_CALL

            result = data.get('result', [])
            if not result:
                break

            canonical = conform_moralis_graduated(result)
            created, updated = load_graduated_tokens(canonical)
            total_created += created
            total_updated += updated

            pages_completed += 1
            cursor = data.get('cursor')

            BOOTSTRAP_STATE_PATH.write_text(json.dumps({
                'cursor': cursor,
                'pages_completed': pages_completed,
                'cu_used': cu_consumed,
                'last_updated': datetime.now(timezone.utc).isoformat(),
            }))

            if not cursor:
                logger.info(
                    "Bootstrap complete: %d tokens loaded (%d created, "
                    "%d updated) in %d pages",
                    total_created + total_updated, total_created,
                    total_updated, pages_completed,
                )
                BOOTSTRAP_STATE_PATH.unlink(missing_ok=True)
                break

            time.sleep(0.5)
        else:
            logger.info(
                "Bootstrap incomplete: %d pages done, resume tomorrow. "
                "Created=%d, updated=%d",
                pages_completed, total_created, total_updated,
            )

        batch.status = RunStatus.COMPLETE
        batch.completed_at = datetime.now(timezone.utc)
        batch.coins_attempted = total_created + total_updated
        batch.coins_succeeded = total_created + total_updated
        batch.cu_consumed = cu_consumed
        batch.api_calls = pages_completed
        batch.save()

        self.stdout.write(
            f"Bootstrap: {total_created} created, {total_updated} updated, "
            f"{pages_completed} pages, {cu_consumed} CU consumed"
        )
