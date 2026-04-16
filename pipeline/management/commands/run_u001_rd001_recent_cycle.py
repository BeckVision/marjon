"""Run one dedicated recent-window RD-001 maintenance cycle."""

from django.core.management import call_command
from django.core.management.base import BaseCommand



class Command(BaseCommand):
    help = (
        "Run one dedicated recent-window RD-001 cycle: repair -> recent RD-001 fetch "
        "(mapped pools only)"
    )

    def add_arguments(self, parser):
        parser.add_argument('--skip-repair', action='store_true')
        parser.add_argument('--rd001-max-coins', type=int, default=25)
        parser.add_argument('--rd001-candidate-limit', type=int, default=150)
        parser.add_argument('--rd001-source', type=str, default='auto')
        parser.add_argument('--rd001-status-filter', type=str, default='incomplete')
        parser.add_argument('--rd001-workers', type=int, default=4)
        parser.add_argument('--rd001-parse-workers', type=int, default=4)

    def handle(self, *args, **options):
        self.run_cycle(**options)
        return

    def run_cycle(self, **options):
        summary = {
            'repair_executed': False,
            'steps': {},
        }

        if not options['skip_repair']:
            call_command('repair_u001_ingestion', stdout=self.stdout)
            summary['repair_executed'] = True

        self.stdout.write("\n[cycle] recent rd001")
        rd001_summary = call_command(
            'fetch_transactions_batch',
            source=options['rd001_source'],
            status_filter=options['rd001_status_filter'],
            candidate_limit=options['rd001_candidate_limit'],
            max_coins=options['rd001_max_coins'],
            workers=options['rd001_workers'],
            parse_workers=options['rd001_parse_workers'],
            stdout=self.stdout,
        )
        summary['steps']['rd001_recent'] = rd001_summary

        return summary
