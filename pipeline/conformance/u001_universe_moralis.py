"""Conformance function: Moralis graduated tokens -> canonical MigratedCoin dicts.

Pure function — no side effects, no DB writes, no API calls.
"""

from pipeline.conformance.utils import parse_iso_timestamp


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

        anchor_event = parse_iso_timestamp(graduated_at_str)
        anchor_event = anchor_event.replace(microsecond=0)

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
