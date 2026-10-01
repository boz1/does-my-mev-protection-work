#!/usr/bin/env python3
"""Summarize one [front transactions, back transactions, victim transactions] row.

Python API: summarize_sandwich(front, back, victims, chain="solana"). Front
and back may each be a string or a list. CLI accepts JSONL, website JSON shards,
Solana binary parts and --row N, an inline --row-json value, or explicit legs. RPCs come from
SOLANA_RPC_URL, BASE_RPC_URL, or ETHEREUM_RPC_URL. See SANDWICH_SUMMARIES.md.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Sequence

import evm_decode_swaps as evm
import solana_decode_swaps as solana
import sandwich_dataset as dataset


CHAINS = ('solana', 'base', 'ethereum')
ROLES = ('front', 'back', 'victim')  # The input file's order, NOT execution order.
FORMAT = 'sandwich-raw-v1'
TxGroup = str | Sequence[str]
EXPOSURE_SOURCES = {'s': 'MEV-Share', 'm': 'MEVBlocker', 'k': 'Blink', 'r': 'Merkle'}
EXPOSURE_NOTE = ('Exposure labels are observations from the input dataset, not proof of which route caused '
                 'the attack. Missing labels do not rule out a route.')


class SummaryError(ValueError):
    pass


def normalize_row(row: Sequence | dict, chain: str = 'solana') -> list[list[str]]:
    """Validate all legs and preserve multiple fronts, backs and victims."""
    if chain not in CHAINS:
        raise SummaryError('Chain must be solana, base, or ethereum')
    try:
        row = dataset.row_from_record(row)
    except dataset.DatasetError as exc:
        raise SummaryError(str(exc)) from None
    if not isinstance(row, (list, tuple)) or len(row) != 3:
        raise SummaryError('A row must be [front_txs, back_txs, victim_txs]')
    result, seen = [], set()
    for role, group in zip(ROLES, row):
        if isinstance(group, str):
            group = [group]
        if not isinstance(group, (list, tuple)) or not group:
            raise SummaryError(f'{role} must contain at least one transaction')
        normalized = []
        for tx_hash in group:
            if not isinstance(tx_hash, str):
                raise SummaryError('Transaction identifiers must be strings')
            if chain == 'solana':
                try:
                    solana.validate_signature(tx_hash)
                except (ValueError, TypeError):
                    raise SummaryError('Invalid Solana transaction signature') from None
            else:
                if not re.fullmatch(r'0x[0-9a-fA-F]{64}', tx_hash):
                    raise SummaryError('EVM hashes must be 0x followed by 64 hexadecimal digits')
                tx_hash = tx_hash.lower()
            if tx_hash in seen:
                raise SummaryError('A transaction appears more than once in the row')
            seen.add(tx_hash)
            normalized.append(tx_hash)
        result.append(normalized)
    return result


def read_row(path: str | Path, line_number: int = 1, *, input_format: str = 'auto') -> list:
    """Read one record; for JSONL the number remains a physical line number."""
    try:
        return dataset.read_record(path, line_number, format=input_format).row
    except dataset.DatasetError as exc:
        raise SummaryError(str(exc)) from None


def safe_error(exc: Exception) -> str:
    # Existing decoder/RPC exceptions already suppress endpoint credentials.
    if isinstance(exc, (SummaryError, dataset.DatasetError, solana.DecodeError, evm.DecodeError, evm.RpcError)):
        return str(exc)
    return type(exc).__name__


def _validate_exposure_labels(value: dict, victims: Sequence[str]) -> dict:
    """Validate dataset evidence and bind it to victim hashes, including on replay."""
    if not isinstance(value, dict):
        raise SummaryError('Exposure labels must map victim transaction hashes to label objects')
    result = {}
    for tx_hash, labels in value.items():
        if not isinstance(tx_hash, str) or tx_hash.lower() not in victims:
            raise SummaryError('Exposure labels refer to a transaction outside the victim group')
        tx_hash = tx_hash.lower()
        if tx_hash in result:
            raise SummaryError('Duplicate victim hash in exposure labels')
        if not isinstance(labels, dict):
            raise SummaryError('Victim exposure labels (L) must be an object')
        for key in ('s', 'm'):
            if key in labels and not isinstance(labels[key], dict):
                raise SummaryError('MEV-Share/MEVBlocker exposure evidence must be an object')
        for key in ('k', 'r'):
            if key in labels and (not isinstance(labels[key], (bool, int)) or labels[key] not in (0, 1)):
                raise SummaryError('Blink/Merkle exposure labels must be 0 or 1')
        result[tx_hash] = deepcopy(labels)
    return result


def _record_exposure(source_record: dict | None, row: list, chain: str) -> dict | None:
    if source_record is None:
        return None
    if not isinstance(source_record, dict):
        raise SummaryError('source_record must be a website dataset record')
    source_row = normalize_row(source_record, chain)
    if any(set(a) != set(b) for a, b in zip(source_row, row)):
        raise SummaryError('Source record does not match the supplied front/back/victim groups')
    if chain != 'ethereum':
        return None
    labels = {v['h']: v['L'] for v in source_record['V'] if 'L' in v}
    return _validate_exposure_labels(labels, row[2])


def _exposure(labels: dict | None) -> dict:
    sources = [name for key, name in EXPOSURE_SOURCES.items()
               if labels is not None and key in labels and
               (key in ('s', 'm') or labels[key] == 1)]
    status = ('not_provided' if labels is None else 'observed' if sources else
              'unrecognized_labels' if any(k not in EXPOSURE_SOURCES for k in labels) else 'no_labels')
    return {'status': status, 'sources': sources,
            'evidence_origin': 'input_dataset' if labels is not None else None,
            'raw_labels': deepcopy(labels)}


def _prepare_transaction(chain: str, tx_hash: str, rpc_url: str, timeout: float) -> tuple[dict, list[str]]:
    """Fetch and populate decoder metadata so later summarization is offline."""
    warnings = []
    if chain != 'solana':
        raw, rpc = evm.fetch_transaction(tx_hash, rpc_url, chain, timeout)
        evm.decode_transaction(raw, rpc)  # Populates historical calls and traces.
        return raw, warnings
    raw = solana.fetch_transaction(tx_hash, rpc_url, timeout)
    decoded = solana.decode_transaction(raw)
    needed = sorted({s['required_market_account'] for s in decoded['undecoded_swaps']
                     if 'required_market_account' in s})
    for start in range(0, len(needed), 100):
        batch = needed[start:start+100]
        try:
            reply = solana.rpc_call('getMultipleAccounts', [batch, {
                'encoding': 'base64', 'commitment': 'finalized',
                'dataSlice': {'offset': 0, 'length': 800}}], rpc_url, timeout)
            accounts = dict(zip(batch, reply['value'], strict=True))
            raw.setdefault('_swap_decoder_market_accounts', {}).update(accounts)
        except (solana.DecodeError, KeyError, ValueError, TypeError) as exc:
            warnings.append('Order-book metadata unavailable: ' + safe_error(exc))
    return raw, warnings


def fetch_sandwich(front: TxGroup, back: TxGroup, victims: TxGroup, *,
                   chain: str = 'solana', rpc_url: str | None = None,
                   timeout: float = 30, workers: int = 4,
                   source_record: dict | None = None) -> dict:
    """Return a credential-free raw bundle, including complete block ordering."""
    row = normalize_row([front, back, victims], chain)
    exposure_labels = _record_exposure(source_record, row, chain)
    if timeout <= 0 or not 1 <= workers <= 16:
        raise SummaryError('Timeout must be positive and workers must be between 1 and 16')
    url = rpc_url or os.environ.get(chain.upper() + '_RPC_URL')
    if not url and chain != 'solana':
        url = os.environ.get('EVM_RPC_URL')
    if not url:
        raise SummaryError('Set ' + chain.upper() + '_RPC_URL or pass rpc_url')
    result = {'format': FORMAT, 'chain': chain, 'row': row,
              'transactions': {}, 'blocks': {}, 'errors': {'transactions': {}, 'blocks': {}},
              'warnings': {}}
    if exposure_labels is not None:
        result['exposure_labels'] = exposure_labels
    hashes = [h for group in row for h in group]

    def fetch_one(h):
        try:
            raw, warnings = _prepare_transaction(chain, h, url, timeout)
            return h, raw, warnings, None
        except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
            return h, None, [], safe_error(exc)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for h, raw, warnings, error in executor.map(fetch_one, hashes):
            if error:
                result['errors']['transactions'][h] = error
            else:
                result['transactions'][h] = raw
                if warnings:
                    result['warnings'][h] = warnings
    numbers = sorted({int(raw['slot']) if chain == 'solana' else evm.quantity(raw['receipt']['blockNumber'])
                      for raw in result['transactions'].values()})

    def fetch_block(number):
        try:
            if chain == 'solana':
                block = solana.rpc_call('getBlock', [number, {
                    'commitment': 'finalized', 'transactionDetails': 'signatures',
                    'rewards': False, 'maxSupportedTransactionVersion': 0}], url, timeout)
            else:
                block = evm.Rpc(url, timeout).call('eth_getBlockByNumber', [hex(number), False])
            if block is None:
                raise SummaryError('Block unavailable from RPC')
            return str(number), block, None
        except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
            return str(number), None, safe_error(exc)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        for number, block, error in executor.map(fetch_block, numbers):
            if error:
                result['errors']['blocks'][number] = error
            else:
                result['blocks'][number] = block
    return result


def iso_time(timestamp: int | None) -> str | None:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace('+00:00', 'Z') if timestamp is not None else None


def _block_order(block: dict, chain: str) -> list[str]:
    values = block.get('signatures' if chain == 'solana' else 'transactions')
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        raise SummaryError('Block lacks its ordered transaction signature/hash list')
    values = values if chain == 'solana' else [v.lower() for v in values]
    if len(values) != len(set(values)):
        raise SummaryError('Block contains duplicate transaction identifiers')
    return values


def _leg(raw_bundle: dict, tx_hash: str, role: str, input_index: int) -> dict:
    chain = raw_bundle['chain']
    raw = raw_bundle['transactions'].get(tx_hash)
    errors = raw_bundle.get('errors', {})
    leg = {'role': role, 'input_index': input_index, 'tx_hash': tx_hash,
           'block_number': None, 'slot': None, 'block_height': None, 'block_hash': None,
           'transaction_index': None, 'transaction_index_source': None,
           'timestamp_utc': None, 'date': None, 'sender': None, 'sender_kind': None,
           'success': None, 'swaps': [], 'decoding': None, 'error': None,
           'warnings': list(raw_bundle.get('warnings', {}).get(tx_hash, []))}
    if raw is None:
        leg['error'] = errors.get('transactions', {}).get(tx_hash, 'Transaction is not saved')
        return leg
    if chain == 'solana':
        signatures = raw.get('transaction', {}).get('signatures')
        if not isinstance(signatures, list) or not signatures or signatures[0] != tx_hash:
            raise SummaryError('Saved Solana transaction does not match its requested signature')
        number = int(raw['slot'])
        timestamp = raw.get('blockTime')
        leg.update(block_number=number, slot=number)
    else:
        if evm.quantity(raw['chain_id']) != evm.CHAIN_IDS[chain]:
            raise SummaryError('Saved transaction is on the wrong chain')
        if raw['transaction']['hash'].lower() != tx_hash:
            raise SummaryError('Saved EVM transaction does not match its requested hash')
        number = evm.quantity(raw['receipt']['blockNumber'])
        timestamp = evm.quantity(raw['block']['timestamp']) if raw.get('block', {}).get('timestamp') is not None else None
        leg.update(block_number=number, block_height=number, block_hash=raw['receipt']['blockHash'],
                   transaction_index=evm.quantity(raw['receipt']['transactionIndex']),
                   transaction_index_source='receipt')
    block = raw_bundle.get('blocks', {}).get(str(number))
    if block is not None:
        order = _block_order(block, chain)
        if tx_hash not in order:
            raise SummaryError('Requested transaction is absent from its claimed block')
        index = order.index(tx_hash)
        if chain == 'solana':
            block_time = block.get('blockTime')
            leg.update(block_hash=block['blockhash'], block_height=block.get('blockHeight'),
                       transaction_index=index, transaction_index_source='getBlock.signatures')
        else:
            if evm.quantity(block['number']) != number or block['hash'] != leg['block_hash']:
                raise SummaryError('Block hash/number differs from the transaction receipt; possible reorganization')
            if index != leg['transaction_index']:
                raise SummaryError('Receipt transaction index differs from block ordering')
            block_time = evm.quantity(block['timestamp'])
        if block_time is not None:
            if timestamp is not None and timestamp != block_time:
                leg['warnings'].append('Transaction and block timestamps differ; using the block timestamp')
            timestamp = block_time
    else:
        reason = errors.get('blocks', {}).get(str(number), 'Block ordering is not saved')
        leg['warnings'].append(reason)
    leg.update(timestamp_utc=iso_time(timestamp), date=iso_time(timestamp)[:10] if timestamp is not None else None)
    try:
        decoded = solana.decode_transaction(raw) if chain == 'solana' else evm.decode_transaction(raw)
        leg.update(success=decoded['success'] if chain == 'solana' else decoded['status'] == 'success',
                   sender=decoded['fee_payer'] if chain == 'solana' else decoded['transaction_sender'],
                   sender_kind='fee_payer' if chain == 'solana' else 'transaction_sender',
                   swaps=decoded['swaps'], decoding={k: v for k, v in decoded.items() if k != 'swaps'})
    except (ValueError, RuntimeError, KeyError, TypeError) as exc:
        leg['error'] = 'Swap decoder failed: ' + safe_error(exc)
    return leg


def _position(leg: dict) -> tuple[int, int] | None:
    if leg['block_number'] is None or leg['transaction_index'] is None:
        return None
    return leg['block_number'], leg['transaction_index']


def classify_legs(front: list[dict], back: list[dict], victims: list[dict]) -> dict:
    """Tight means a same-block consecutive F...F,V...V,B...B sequence."""
    legs = front + victims + back
    known_blocks = {x['block_number'] for x in legs if x['block_number'] is not None}
    all_blocks = all(x['block_number'] is not None for x in legs)
    complete_order = all(_position(x) is not None for x in legs)
    relation = 'cross_block' if len(known_blocks) > 1 else 'within_block' if all_blocks else 'unknown'
    strict_order, enclosed, tight = None, None, False if relation == 'cross_block' else None
    ordered = sorted(legs, key=_position) if complete_order else None
    if ordered:
        positions = [_position(x) for x in ordered]
        if len(positions) != len(set(positions)):
            raise SummaryError('Different legs claim the same block transaction index')
        strict_order = (max(map(_position, front)) < min(map(_position, victims)) and
                        max(map(_position, victims)) < min(map(_position, back)))
        enclosed = (ordered[0]['role'] == 'front' and ordered[-1]['role'] == 'back' and
                    min(map(_position, front)) < min(map(_position, victims)) and
                    max(map(_position, victims)) < max(map(_position, back)))
        if relation == 'within_block':
            tight = strict_order and all(b['transaction_index'] == a['transaction_index'] + 1
                                        for a, b in zip(ordered, ordered[1:]))
    classification = 'tight' if tight else relation
    label = {'tight': 'within-block tight', 'within_block': 'within-block wide' if tight is False else 'within-block (tightness unknown)',
             'cross_block': 'cross-block', 'unknown': 'unknown'}[classification]
    if enclosed is False:
        label += ' (legs do not fit between outer front/back boundaries)'
    elif strict_order is False:
        label += ' (interleaved roles)'
    span = {'front_tx_hash': None, 'back_tx_hash': None, 'transaction_index_distance': None,
            'transactions_between': None, 'transactions_inclusive': None,
            'unlisted_transactions_between': None,
            'count_scope': 'all block transactions, including votes/system transactions; cross-block counts not inferred'}
    if complete_order and enclosed:
        first, last = min(front, key=_position), max(back, key=_position)
        span.update(front_tx_hash=first['tx_hash'], back_tx_hash=last['tx_hash'])
        if relation == 'within_block':
            distance = last['transaction_index'] - first['transaction_index']
            span.update(transaction_index_distance=distance, transactions_between=distance-1,
                        transactions_inclusive=distance+1,
                        unlisted_transactions_between=distance+1-len(legs))
    return {'classification': classification, 'classification_label': label, 'block_relation': relation,
            'is_tight': tight, 'strict_role_order': strict_order, 'victims_enclosed': enclosed, 'span': span,
            'execution_order': [{k: x[k] for k in ('role', 'tx_hash', 'block_number', 'transaction_index')}
                                for x in ordered] if ordered else None}


def summarize_bundle(raw_bundle: dict) -> dict:
    """Pure offline summary of a saved fetch_sandwich bundle."""
    if raw_bundle.get('format') != FORMAT:
        raise SummaryError('Not a sandwich-raw-v1 bundle; use --raw-output to create one')
    chain = raw_bundle['chain']
    row = normalize_row(raw_bundle['row'], chain)
    exposure_labels = (_validate_exposure_labels(raw_bundle.get('exposure_labels', {}), row[2])
                       if chain == 'ethereum' else {})
    groups = [[_leg(raw_bundle, h, role, i) for i, h in enumerate(hashes)]
              for role, hashes in zip(ROLES, row)]
    front, back, victims = groups
    if chain == 'ethereum':
        for victim in victims:
            victim['exposure'] = _exposure(exposure_labels.get(victim['tx_hash']))
    legs = front + victims + back
    classified = classify_legs(front, back, victims)
    # Date belongs to the earliest front in ledger order, not the first victim.
    dated_front = min(front, key=lambda x: (x['block_number'], x['transaction_index'] or 0)) if all(x['block_number'] is not None for x in front) else front[0]
    dates = sorted({x['date'] for x in legs if x['date']})
    candidates = sorted({x['sender'] for x in front if x['sender']} & {x['sender'] for x in back if x['sender']})
    warnings = []
    if classified['victims_enclosed'] is False:
        warnings.append('The supplied legs are not enclosed by a front transaction and a back transaction in ledger order')
    elif classified['strict_role_order'] is False:
        warnings.append('Front/victim/back roles interleave; see execution_order for the actual sequence')
    if any(x['success'] is False for x in legs):
        warnings.append('At least one leg failed; ordering alone does not establish a successful sandwich')
    block_numbers = sorted({x['block_number'] for x in legs if x['block_number'] is not None})
    blocks = []
    for number in block_numbers:
        block = raw_bundle.get('blocks', {}).get(str(number))
        hashes = {x['block_hash'] for x in legs if x['block_number'] == number and x['block_hash']}
        if len(hashes) > 1:
            raise SummaryError('Legs at the same block number have different block hashes; possible reorganization')
        representative = next(x for x in legs if x['block_number'] == number)
        blocks.append({'number': number, 'number_kind': 'slot' if chain == 'solana' else 'block_number',
                       'block_height': representative['block_height'], 'hash': representative['block_hash'],
                       'timestamp_utc': representative['timestamp_utc'],
                       'transaction_count': len(_block_order(block, chain)) if block is not None else None})
    unresolved = sum(len((x['decoding'] or {}).get('undecoded_swaps' if chain == 'solana' else 'undecoded_events', [])) for x in legs)
    return {'chain': chain, 'date': dated_front['date'], 'timestamp_utc': dated_front['timestamp_utc'],
            'date_basis': 'earliest front block/slot; first supplied front if a front location is unavailable; UTC',
            'date_range_utc': {'start': dates[0] if dates else None, 'end': dates[-1] if dates else None},
            **classified, 'blocks': blocks,
            'bot_candidates': candidates,
            'bot_candidate_basis': 'shared front/back fee payer; may be a relayer' if chain == 'solana' else 'shared front/back transaction sender',
            'private_submission': None,  # Cannot be established from ordinary mined transaction RPC data.
            **({'exposure_note': EXPOSURE_NOTE} if chain == 'ethereum' else {}),
            'front_txs': front, 'victim_txs': victims, 'back_txs': back,
            'counts': {'front': len(front), 'victim': len(victims), 'back': len(back),
                       'swaps': sum(len(x['swaps']) for x in legs)},
            'all_legs_succeeded': None if any(x['success'] is None for x in legs) else all(x['success'] for x in legs),
            'coverage': {'all_swaps_guaranteed': False,
                         'missing_transactions': sum(x['block_number'] is None for x in legs),
                         'missing_transaction_indices': sum(x['transaction_index'] is None for x in legs),
                         'decoder_or_fetch_errors': sum(x['error'] is not None for x in legs),
                         'undecoded_recognized_events': unresolved},
            'warnings': warnings}


def summarize_sandwich(front: TxGroup, back: TxGroup, victims: TxGroup, *,
                       chain: str = 'solana', rpc_url: str | None = None,
                       timeout: float = 30, workers: int = 4,
                       source_record: dict | None = None) -> dict:
    """Summarize one sandwich; group order is FRONT, BACK, VICTIMS."""
    return summarize_bundle(fetch_sandwich(front, back, victims, chain=chain,
                            rpc_url=rpc_url, timeout=timeout, workers=workers,
                            source_record=source_record))


def summarize_row(row: Sequence | dict, **kwargs) -> dict:
    """Accept a parsed row directly, including rows with several front/back legs."""
    normalized = normalize_row(row, kwargs.get('chain', 'solana'))
    if isinstance(row, dict):
        if kwargs.get('source_record') is not None:
            raise SummaryError('Pass a website row or source_record, not both')
        kwargs['source_record'] = row
    return summarize_sandwich(*normalized, **kwargs)


def _short(value: str | None) -> str:
    return value[:8] + '…' + value[-6:] if value and len(value) > 18 else value or '?'


def _asset_text(asset: dict, amount: str | None = None, raw: str | None = None) -> str:
    token = asset.get('symbol') or _short(asset.get('address') or asset.get('mint'))
    amount = asset.get('amount') if amount is None else amount
    raw = asset.get('amount_raw') if raw is None else raw
    return f'{amount} {token}' if amount is not None else f'{raw} {token} (raw)'


def render_text(summary: dict) -> str:
    """Compact screenshot-style text; the JSON retains full addresses/evidence."""
    header = f"{summary['date'] or 'date unknown'} · {summary['chain']} · {summary['classification_label']}"
    candidates = summary['bot_candidates']
    if candidates:
        header += ' · shared front/back ' + ('fee payer ' if summary['chain'] == 'solana' else 'sender ') + ', '.join(map(_short, candidates))
    numbers = ', '.join(f"{x['number']:,}" for x in summary['blocks']) or 'unknown'
    lines = [header, ('Slots ' if summary['chain'] == 'solana' else 'Blocks ') + numbers, '']
    lookup = {x['tx_hash']: x for field in ('front_txs', 'victim_txs', 'back_txs') for x in summary[field]}
    ordered = summary['execution_order'] or [x for field in ('front_txs', 'victim_txs', 'back_txs') for x in summary[field]]
    for item in ordered:
        leg = lookup[item['tx_hash']]
        lines.append(f"{leg['role'].upper():6} {_short(leg['tx_hash'])}  block/slot={leg['block_number']} index={leg['transaction_index']}  from {_short(leg['sender'])}")
        if leg['error']:
            lines.append('       ERROR: ' + leg['error'])
        elif leg['success'] is False:
            lines.append('       Transaction failed; no executed swaps')
        elif not leg['swaps']:
            lines.append('       No swaps decoded')
        for swap in leg['swaps']:
            if summary['chain'] == 'solana':
                inp = _asset_text(swap['token_in'], swap.get('amount_in'), swap.get('amount_in_raw'))
                out = _asset_text(swap['token_out'], swap.get('amount_out'), swap.get('amount_out_raw'))
                venue = swap.get('dex') or _short(swap.get('program_id'))
            else:
                inp = ' + '.join(_asset_text(x) for x in swap['inputs'])
                out = ' + '.join(_asset_text(x) for x in swap['outputs'])
                venue = swap['protocol']
            lines.append(f'       {inp} → {out}  [{venue}]')
        exposure = leg.get('exposure')
        if exposure:
            if exposure['status'] == 'observed':
                names = [name + (' (full transaction)' if name in ('Blink', 'Merkle') else '')
                         for name in exposure['sources']]
                detail = ', '.join(names)
                if any(k not in EXPOSURE_SOURCES for k in exposure['raw_labels']):
                    detail += '; additional unrecognized labels in JSON'
                lines.append('       Exposure (dataset): ' + detail)
            elif exposure['status'] == 'no_labels':
                lines.append('       Exposure (dataset): no labels supplied; source unknown')
            elif exposure['status'] == 'unrecognized_labels':
                lines.append('       Exposure (dataset): unrecognized labels; see JSON')
            else:
                lines.append('       Exposure: not provided in input')
        decoded = leg['decoding'] or {}
        unknown = decoded.get('undecoded_swaps', decoded.get('undecoded_events', []))
        if unknown:
            lines.append(f'       {len(unknown)} recognized swap events/instructions could not be decoded; see JSON')
        for warning in leg['warnings']:
            lines.append('       WARNING: ' + warning)
    span = summary['span']
    if span['transaction_index_distance'] is not None:
        lines += ['', f"Front→back index distance: {span['transaction_index_distance']}; {span['transactions_between']} transactions strictly between; {span['unlisted_transactions_between']} not listed in this row."]
    elif summary['block_relation'] == 'cross_block':
        lines += ['', 'Cross-block transaction distance not calculated.']
    lines += ['WARNING: '+w for w in summary['warnings']]
    if summary.get('exposure_note'):
        lines += ['', summary['exposure_note']]
    lines += ['', 'Swap coverage is limited to the installed decoders; unknown activity is retained in JSON.']
    return '\n'.join(lines) + '\n'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', nargs='?', type=Path, help='JSONL rows, website JSON shard or Solana binary part')
    parser.add_argument('--row', type=int, default=1, help='One-based record number; physical line for JSONL (default 1)')
    parser.add_argument('--input-format', choices=dataset.FORMATS, default='auto', help='Default: infer from file extension')
    parser.add_argument('--row-json', help='One inline [front, back, victims] row or website JSON record')
    parser.add_argument('--front', nargs='+', help='One or more front transaction hashes')
    parser.add_argument('--back', nargs='+', help='One or more back transaction hashes')
    parser.add_argument('--victim', nargs='+', help='One or more victim transaction hashes')
    parser.add_argument('--chain', choices=CHAINS, help='Default solana; saved bundle chain for --from-json')
    parser.add_argument('--rpc-url', help='Prefer the chain-specific RPC environment variable')
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--from-json', type=Path, help='Offline replay of a saved raw sandwich bundle')
    parser.add_argument('--raw-output', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--format', choices=('json', 'text'), default='json')
    args = parser.parse_args(argv)
    try:
        modes = sum(bool(x) for x in (args.file, args.row_json, args.from_json,
                                     args.front or args.back or args.victim))
        if modes != 1:
            raise SummaryError('Choose exactly one: dataset file, --row-json, --from-json, or --front/--back/--victim')
        if args.from_json:
            raw = json.loads(args.from_json.read_text())
            if args.chain and raw.get('chain') != args.chain:
                raise SummaryError('Saved bundle does not match --chain')
        else:
            chain = args.chain or 'solana'
            if args.file and dataset.input_format(args.file, args.input_format) == 'solana-binary' and chain != 'solana':
                raise SummaryError('Solana binary parts require --chain solana')
            if args.file:
                record = dataset.read_record(args.file, args.row, format=args.input_format)
                row = record.metadata if record.metadata is not None else record.row
            else:
                row = json.loads(args.row_json) if args.row_json else [args.front, args.back, args.victim]
            source_record = row if isinstance(row, dict) else None
            row = normalize_row(row, chain)
            raw = fetch_sandwich(*row, chain=chain, rpc_url=args.rpc_url, timeout=args.timeout, workers=args.workers,
                                 source_record=source_record)
        summary = summarize_bundle(raw)
        if args.raw_output:
            args.raw_output.parent.mkdir(parents=True, exist_ok=True)
            args.raw_output.write_text(json.dumps(raw, indent=2)+'\n')
        rendered = render_text(summary) if args.format == 'text' else json.dumps(summary, indent=2)+'\n'
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered)
        else:
            sys.stdout.write(rendered)
        return 0
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as exc:
        print('Summary failed: '+safe_error(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
