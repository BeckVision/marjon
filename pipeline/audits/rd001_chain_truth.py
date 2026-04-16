"""Direct-RPC helpers for sampled RD-001 chain-truth auditing.

This module intentionally does not write warehouse rows. It fetches
transactions from a Solana RPC endpoint and compares either sampled RD-001
rows or sampled pool windows against chain-native transaction data.
"""

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

from django.conf import settings

from pipeline.connectors.http import request_with_retry, validate_jsonrpc_response

WSOL_MINT = "So11111111111111111111111111111111111111112"


def default_rpc_url():
    """Return the configured direct RPC URL for Phase 0 chain audits."""
    return settings.U001_CHAIN_AUDIT_RPC_URL


def default_rpc_source():
    """Return the configured source label for the chain-audit RPC URL."""
    return settings.U001_CHAIN_AUDIT_RPC_SOURCE


def fetch_transaction(signature, rpc_url=None):
    """Fetch a parsed Solana transaction for one signature via direct RPC."""
    url = rpc_url or default_rpc_url()
    payload = {
        'jsonrpc': '2.0',
        'id': signature,
        'method': 'getTransaction',
        'params': [
            signature,
            {
                'encoding': 'jsonParsed',
                'commitment': 'confirmed',
                'maxSupportedTransactionVersion': 0,
            },
        ],
    }
    data = request_with_retry(
        url,
        method='POST',
        json_body=payload,
        validate_response=validate_jsonrpc_response,
    )
    return data.get('result')


def fetch_signatures_for_address(address, start=None, end=None, rpc_url=None, limit=1000):
    """Fetch direct-RPC signatures for one address over a bounded time window."""
    url = rpc_url or default_rpc_url()
    all_sigs = []
    cursor = None

    while True:
        params = [address, {'limit': limit}]
        if cursor:
            params[1]['before'] = cursor
        payload = {
            'jsonrpc': '2.0',
            'id': address,
            'method': 'getSignaturesForAddress',
            'params': params,
        }
        data = request_with_retry(
            url,
            method='POST',
            json_body=payload,
            validate_response=validate_jsonrpc_response,
        )
        result = data.get('result', [])
        if not result:
            break

        all_sigs.extend(result)

        if start is not None:
            oldest_time = result[-1].get('blockTime')
            if oldest_time is not None:
                oldest_time = datetime.fromtimestamp(oldest_time, tz=timezone.utc)
                if oldest_time < start:
                    break

        if len(result) < limit:
            break

        cursor = result[-1]['signature']

    filtered = []
    start_ts = int(start.timestamp()) if start else None
    end_ts = int(end.timestamp()) if end else None
    for row in all_sigs:
        block_time = row.get('blockTime')
        if block_time is None:
            continue
        if start_ts and block_time < start_ts:
            continue
        if end_ts and block_time > end_ts:
            continue
        filtered.append(row)
    return filtered


def build_chain_observation(tx, mint_address, pool_address):
    """Extract comparable fields from a direct-RPC transaction response."""
    if tx is None:
        return {
            'exists': False,
            'success': False,
            'detail': 'Signature not found on the direct RPC endpoint.',
        }

    meta = tx.get('meta') or {}
    transaction = tx.get('transaction') or {}
    message = transaction.get('message') or {}
    account_keys = message.get('accountKeys') or []

    block_time = tx.get('blockTime')
    timestamp = None
    if block_time is not None:
        timestamp = datetime.fromtimestamp(block_time, tz=timezone.utc)

    fee_payer = _fee_payer(account_keys)
    token_delta = _owner_mint_delta(
        pre_balances=meta.get('preTokenBalances') or [],
        post_balances=meta.get('postTokenBalances') or [],
        owner=pool_address,
        mint=mint_address,
    )

    trade_type = None
    token_amount = None
    if token_delta is not None and token_delta != 0:
        trade_type = 'BUY' if token_delta < 0 else 'SELL'
        token_amount = abs(token_delta)

    return {
        'exists': True,
        'success': meta.get('err') is None,
        'timestamp': timestamp,
        'tx_fee': Decimal(meta.get('fee', 0)) / Decimal(10 ** 9),
        'fee_payer': fee_payer,
        'trade_type': trade_type,
        'token_amount': token_amount,
        'derivation_complete': trade_type is not None and token_amount is not None,
    }


def compare_row_to_chain(row, observation):
    """Compare one RawTransaction row to its direct-RPC chain observation."""
    findings = []
    warnings = []

    if not observation['exists']:
        findings.append('missing_on_chain')
    elif not observation['success']:
        findings.append('failed_on_chain')

    observed_timestamp = observation.get('timestamp')
    if observed_timestamp is None:
        warnings.append('missing_block_time')
    elif observed_timestamp != row.timestamp:
        findings.append('timestamp_mismatch')

    observed_fee = observation.get('tx_fee')
    if observed_fee is not None and observed_fee != row.tx_fee:
        findings.append('tx_fee_mismatch')

    observed_fee_payer = observation.get('fee_payer')
    if observed_fee_payer and observed_fee_payer != row.wallet_address:
        warnings.append('fee_payer_mismatch')

    if observation.get('derivation_complete'):
        if observation['trade_type'] != row.trade_type:
            findings.append('trade_type_mismatch')
        if observation['token_amount'] != row.token_amount:
            findings.append('token_amount_mismatch')
    else:
        warnings.append('token_delta_unavailable')

    status = 'ok'
    if findings:
        status = 'finding'
    elif warnings:
        status = 'warning'

    detail = _detail_for_result(row, observation, findings, warnings)
    return {
        'status': status,
        'detail': detail,
        'signature': row.tx_signature,
        'coin': row.coin_id,
        'findings': findings,
        'warnings': warnings,
        'chain': {
            'timestamp': observation.get('timestamp'),
            'tx_fee': observation.get('tx_fee'),
            'fee_payer': observation.get('fee_payer'),
            'trade_type': observation.get('trade_type'),
            'token_amount': observation.get('token_amount'),
        },
    }


def summarize_results(results):
    """Return aggregate counts for a set of row-level chain-audit results."""
    return {
        'statuses': dict(Counter(row['status'] for row in results)),
        'finding_buckets': dict(
            Counter(bucket for row in results for bucket in row['findings'])
        ),
        'warning_buckets': dict(
            Counter(bucket for row in results for bucket in row['warnings'])
        ),
    }


def summarize_window_results(results):
    """Return aggregate counts for per-window reconciliation results."""
    return {
        'statuses': dict(Counter(row['status'] for row in results)),
        'finding_buckets': dict(
            Counter(bucket for row in results for bucket in row['findings'])
        ),
        'warning_buckets': dict(
            Counter(bucket for row in results for bucket in row['warnings'])
        ),
    }


def compare_window_to_chain(
    *,
    coin_id,
    pool_address,
    start,
    end,
    warehouse_signatures,
    chain_trade_signatures,
    ambiguous_chain_signatures=None,
    signature_scan_count=0,
):
    """Compare warehouse RD-001 signatures to direct-RPC trade signatures for a window."""
    ambiguous_chain_signatures = ambiguous_chain_signatures or set()
    warehouse_set = set(warehouse_signatures)
    chain_set = set(chain_trade_signatures)

    missing = chain_set - warehouse_set
    extra = warehouse_set - chain_set - ambiguous_chain_signatures
    findings = []
    warnings = []

    if missing:
        findings.append('missing_trade_signatures')
    if extra:
        findings.append('extra_trade_signatures')
    if ambiguous_chain_signatures:
        warnings.append('ambiguous_pool_signatures')
    if not chain_set and not warehouse_set:
        warnings.append('empty_window')

    status = 'ok'
    if findings:
        status = 'finding'
    elif warnings:
        status = 'warning'

    if findings:
        detail = (
            f"Direct-RPC window mismatch for {coin_id} over {start} -> {end}: "
            f"chain_trade_sigs={len(chain_set)}, warehouse_sigs={len(warehouse_set)}, "
            f"missing={len(missing)}, extra={len(extra)}, ambiguous={len(ambiguous_chain_signatures)}, "
            f"pool_signatures_scanned={signature_scan_count}."
        )
    elif warnings:
        detail = (
            f"Direct-RPC window partial match for {coin_id} over {start} -> {end}: "
            f"chain_trade_sigs={len(chain_set)}, warehouse_sigs={len(warehouse_set)}, "
            f"ambiguous={len(ambiguous_chain_signatures)}, "
            f"pool_signatures_scanned={signature_scan_count}, warnings={', '.join(warnings)}."
        )
    else:
        detail = (
            f"Direct-RPC window matched for {coin_id} over {start} -> {end} "
            f"({len(chain_set)} trade signatures, scanned {signature_scan_count} pool signatures)."
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
        'ambiguous_signatures': sorted(ambiguous_chain_signatures),
        'chain_trade_signature_count': len(chain_set),
        'warehouse_signature_count': len(warehouse_set),
        'pool_signature_scan_count': signature_scan_count,
    }


def _detail_for_result(row, observation, findings, warnings):
    if findings:
        return (
            f"Direct-RPC mismatch for {row.coin_id} {row.tx_signature}: "
            f"findings={', '.join(findings)}."
        )
    if warnings:
        return (
            f"Direct-RPC partial match for {row.coin_id} {row.tx_signature}: "
            f"warnings={', '.join(warnings)}."
        )
    return (
        f"Direct-RPC match for {row.coin_id} {row.tx_signature} "
        f"(timestamp, tx fee, trade side, and token amount)."
    )


def _fee_payer(account_keys):
    for key in account_keys:
        if isinstance(key, dict) and key.get('signer'):
            return key.get('pubkey')
    if account_keys:
        first = account_keys[0]
        if isinstance(first, dict):
            return first.get('pubkey')
        return first
    return None


def _owner_mint_delta(pre_balances, post_balances, owner, mint):
    deltas = _aggregate_owner_mint(post_balances)
    deltas.subtract(_aggregate_owner_mint(pre_balances))
    return deltas.get((owner, mint))


def _aggregate_owner_mint(rows):
    totals = Counter()
    for row in rows:
        row_owner = row.get('owner')
        row_mint = row.get('mint')
        if not row_owner or not row_mint:
            continue
        raw_amount = ((row.get('uiTokenAmount') or {}).get('amount'))
        if raw_amount is None:
            continue
        totals[(row_owner, row_mint)] += int(raw_amount)
    return totals
