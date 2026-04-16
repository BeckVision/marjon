"""Optional Solscan-backed spot checks for recent RD-001 parsing."""

from datetime import datetime, timezone
from decimal import Decimal

from django.conf import settings

from pipeline.connectors.http import request_with_retry


def solscan_enabled():
    """Return whether a Solscan API key is configured."""
    return bool(settings.SOLSCAN_API_KEY)


def solscan_base_url():
    """Return the configured Solscan API base URL."""
    return settings.SOLSCAN_API_BASE_URL.rstrip('/')


def _solscan_headers():
    if not settings.SOLSCAN_API_KEY:
        return {}
    return {
        'accept': 'application/json',
        'token': settings.SOLSCAN_API_KEY,
    }


def validate_solscan_response(data):
    """Raise when Solscan returns an unsuccessful payload."""
    if not isinstance(data, dict):
        raise ValueError('Solscan response was not a JSON object')
    if data.get('success') is True:
        return
    errors = data.get('errors')
    if isinstance(errors, dict):
        message = errors.get('message') or str(errors)
    else:
        message = str(errors or data)
    raise ValueError(f'Solscan error: {message}')


def fetch_transaction_detail(signature):
    """Fetch one Solscan transaction detail payload."""
    return request_with_retry(
        f"{solscan_base_url()}/transaction/detail",
        params={'tx': signature},
        headers=_solscan_headers(),
        validate_response=validate_solscan_response,
    )


def fetch_account_transactions(address, *, limit=40, before=None):
    """Fetch recent Solscan account transactions for one address."""
    params = {
        'address': address,
        'limit': max(10, min(int(limit), 40)),
    }
    if before:
        params['before'] = before
    return request_with_retry(
        f"{solscan_base_url()}/account/transactions",
        params=params,
        headers=_solscan_headers(),
        validate_response=validate_solscan_response,
    )


def compare_row_to_solscan(row, payload):
    """Compare one warehouse row against Solscan transaction detail."""
    data = payload.get('data') or {}
    findings = []
    warnings = []

    tx_hash = data.get('tx_hash')
    if tx_hash != row.tx_signature:
        findings.append('tx_hash_mismatch')

    status = str(data.get('status') or '').lower()
    if status and status not in {'success', 'successful'}:
        findings.append('failed_on_solscan')

    block_time = data.get('block_time')
    if block_time is None:
        warnings.append('missing_block_time')
    else:
        observed_timestamp = datetime.fromtimestamp(block_time, tz=timezone.utc)
        if observed_timestamp != row.timestamp:
            findings.append('timestamp_mismatch')

    fee = data.get('fee')
    observed_fee = None
    if fee is None:
        warnings.append('missing_fee')
    else:
        try:
            observed_fee = Decimal(str(fee)) / Decimal(10 ** 9)
            if observed_fee.normalize() != Decimal(row.tx_fee).normalize():
                findings.append('tx_fee_mismatch')
        except Exception:
            warnings.append('unparseable_fee')

    signer = data.get('signer')
    signer_value = None
    if isinstance(signer, list):
        signer_value = signer[0] if signer else None
    elif isinstance(signer, str):
        signer_value = signer
    if signer_value and signer_value != row.wallet_address:
        warnings.append('signer_mismatch')

    result_status = 'ok'
    if findings:
        result_status = 'finding'
    elif warnings:
        result_status = 'warning'

    if findings:
        detail = (
            f"Solscan mismatch for {row.coin_id} {row.tx_signature}: "
            f"findings={', '.join(findings)}."
        )
    elif warnings:
        detail = (
            f"Solscan partial match for {row.coin_id} {row.tx_signature}: "
            f"warnings={', '.join(warnings)}."
        )
    else:
        detail = f"Solscan matched {row.coin_id} {row.tx_signature}."

    return {
        'status': result_status,
        'detail': detail,
        'coin': row.coin_id,
        'signature': row.tx_signature,
        'findings': findings,
        'warnings': warnings,
        'solscan': {
            'tx_hash': tx_hash,
            'block_time': block_time,
            'fee': str(observed_fee) if observed_fee is not None else None,
            'signer': signer_value,
        },
    }


def compare_window_to_solscan(*, coin_id, pool_address, start, end, warehouse_signatures, payload):
    """Compare a short recent warehouse window to Solscan account transactions."""
    items = payload.get('data') or []
    scanned = []
    truncated = False
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())

    for item in items:
        block_time = item.get('block_time')
        tx_hash = item.get('tx_hash')
        if tx_hash is None:
            continue
        scanned.append(tx_hash)
        if block_time is None:
            continue
        if start_ts <= int(block_time) <= end_ts:
            continue
        if int(block_time) >= start_ts and len(items) == 40:
            truncated = True

    solscan_window = {
        item.get('tx_hash')
        for item in items
        if item.get('tx_hash') and item.get('block_time') is not None
        and start_ts <= int(item['block_time']) <= end_ts
    }
    warehouse_set = set(warehouse_signatures)
    missing = solscan_window - warehouse_set
    extra = warehouse_set - solscan_window
    findings = []
    warnings = []

    if missing:
        findings.append('missing_signatures')
    if extra:
        findings.append('extra_signatures')
    if truncated:
        warnings.append('window_may_be_truncated')
    if not solscan_window and not warehouse_set:
        warnings.append('empty_window')

    status = 'ok'
    if findings:
        status = 'finding'
    elif warnings:
        status = 'warning'

    if findings:
        detail = (
            f"Solscan window mismatch for {coin_id} over {start} -> {end}: "
            f"solscan_sigs={len(solscan_window)}, warehouse_sigs={len(warehouse_set)}, "
            f"missing={len(missing)}, extra={len(extra)}."
        )
    elif warnings:
        detail = (
            f"Solscan window partial match for {coin_id} over {start} -> {end}: "
            f"solscan_sigs={len(solscan_window)}, warehouse_sigs={len(warehouse_set)}, "
            f"warnings={', '.join(warnings)}."
        )
    else:
        detail = (
            f"Solscan window matched for {coin_id} over {start} -> {end} "
            f"({len(solscan_window)} signatures)."
        )

    return {
        'status': status,
        'detail': detail,
        'coin': coin_id,
        'pool_address': pool_address,
        'start': start,
        'end': end,
        'findings': findings,
        'warnings': warnings,
        'missing_signatures': sorted(missing),
        'extra_signatures': sorted(extra),
        'signature_count': len(solscan_window),
        'scanned_count': len(scanned),
    }
