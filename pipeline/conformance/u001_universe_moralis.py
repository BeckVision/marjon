"""Conformance function: Moralis graduated tokens -> canonical MigratedCoin dicts.

Pure function — no side effects, no DB writes, no API calls.
"""

from datetime import datetime


def conform_moralis_graduated(raw_tokens):
    """Transform raw Moralis graduated token response to canonical form.

    Args:
        raw_tokens: List of token dicts (the 'result' array from one page).

    Returns:
        List of dicts ready for MigratedCoin upsert.
    """
    records = []

    for raw in raw_tokens:
        # PDP6: strict — crash if required fields are missing
        mint_address = raw['tokenAddress']
        graduated_at_str = raw['graduatedAt']

        # Parse ISO 8601 "2026-03-10T17:22:07.000Z" -> UTC datetime
        if graduated_at_str.endswith('.000Z'):
            graduated_at_str = graduated_at_str[:-5] + '+00:00'
        elif graduated_at_str.endswith('Z'):
            graduated_at_str = graduated_at_str[:-1] + '+00:00'
        anchor_event = datetime.fromisoformat(graduated_at_str)

        # decimals is a string in the API ("6"), convert to int
        raw_decimals = raw.get('decimals')
        decimals = int(raw_decimals) if raw_decimals is not None else None

        records.append({
            'mint_address': mint_address,
            'anchor_event': anchor_event,
            'name': raw.get('name'),
            'symbol': raw.get('symbol'),
            'decimals': decimals,
            'logo_url': raw.get('logo'),
        })

    return records
