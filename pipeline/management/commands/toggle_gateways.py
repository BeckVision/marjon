"""Management command to enable/disable API Gateway proxy endpoints."""

import os
import re

from django.conf import settings
from django.core.management.base import BaseCommand

from pipeline.connectors.geckoterminal import configure_gateway_urls

ENV_PATH = os.path.join(settings.BASE_DIR, '.env')

GATEWAY_LINE_RE = re.compile(r'^(#\s*)?(GATEWAY_URL_\d+=.+)$')


def toggle_lines(content, enable):
    """Toggle GATEWAY_URL_* lines in .env content.

    Args:
        content: Full .env file content as a string.
        enable: True to uncomment, False to comment out.

    Returns:
        Modified content string.
    """
    lines = content.splitlines(keepends=True)
    result = []
    for line in lines:
        stripped = line.rstrip('\n').rstrip('\r')
        match = GATEWAY_LINE_RE.match(stripped)
        if match:
            # Extract the actual KEY=VALUE part (group 2)
            key_value = match.group(2)
            if enable:
                # Uncomment: use the key=value without the # prefix
                result.append(key_value + '\n')
            else:
                # Comment out: add # prefix
                result.append('# ' + key_value + '\n')
        else:
            result.append(line)
    return ''.join(result)


def _read_env():
    """Read .env file content."""
    with open(ENV_PATH) as f:
        return f.read()


def _write_env(content):
    """Write .env file content."""
    with open(ENV_PATH, 'w') as f:
        f.write(content)


def _get_gateway_status(content):
    """Parse .env content and return (active, inactive) gateway counts."""
    active = 0
    inactive = 0
    for line in content.splitlines():
        match = GATEWAY_LINE_RE.match(line.strip())
        if match:
            if match.group(1):  # has # prefix
                inactive += 1
            else:
                active += 1
    return active, inactive


def _parse_active_urls(content):
    """Extract active (uncommented) GATEWAY_URL values from .env content."""
    urls = []
    for line in content.splitlines():
        match = GATEWAY_LINE_RE.match(line.strip())
        if match and not match.group(1):
            # Active line — extract URL from KEY=VALUE
            key_value = match.group(2)
            url = key_value.split('=', 1)[1]
            if url:
                urls.append(url)
    return urls


class Command(BaseCommand):
    help = "Enable/disable API Gateway proxy endpoints"

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            '--status', action='store_true',
            help='Show active/inactive gateway count',
        )
        group.add_argument(
            '--enable', action='store_true',
            help='Uncomment all GATEWAY_URL_* lines and hot-reload',
        )
        group.add_argument(
            '--disable', action='store_true',
            help='Comment out all GATEWAY_URL_* lines and hot-reload',
        )

    def handle(self, *args, **options):
        content = _read_env()

        if options['status']:
            active, inactive = _get_gateway_status(content)
            self.stdout.write(f"Gateways: {active} enabled, {inactive} disabled")
            return

        if options['disable']:
            new_content = toggle_lines(content, enable=False)
            _write_env(new_content)
            configure_gateway_urls([])
            self.stdout.write("Gateways disabled — using direct URL")
            return

        if options['enable']:
            new_content = toggle_lines(content, enable=True)
            _write_env(new_content)
            urls = _parse_active_urls(new_content)
            configure_gateway_urls(urls)
            self.stdout.write(f"Gateways enabled — {len(urls)} active")
