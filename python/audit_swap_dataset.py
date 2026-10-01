#!/usr/bin/env python3
"""Resume a read-only swap-decoder audit of JSONL, JSON shards or Solana binaries.

Uses a SQLite transaction cache and per-file record checkpoints. RPC URLs come
only from environment variables or --rpc-url and are never stored in the state.
The default limit is 100 new transaction checks; --max-transactions 0 removes it.
Unknown execution evidence remains visible; this tool never certifies all swaps.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

import sandwich_dataset as dataset
import summarize_sandwich as summary


def decoder_revision(chain: str | None = None) -> str:
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    files = ['audit_swap_dataset.py', 'sandwich_dataset.py', 'summarize_sandwich.py']
    if chain != 'solana':
        files.append('evm_decode_swaps.py')
    if chain in (None, 'solana'):
        files += ['solana_decode_swaps.py', 'solana_swap_protocols.py', 'solana_registry_protocols.py']
    for name in files:
        digest.update(name.encode() + b'\0' + (root / name).read_bytes())
    return digest.hexdigest()


def check_transaction(chain: str, tx_hash: str, rpc_url: str, timeout: float,
                      raw_dir: Path | None = None) -> dict:
    result = {'chain': chain, 'tx_hash': tx_hash, 'all_swaps_guaranteed': False}
    try:
        raw, warnings = summary._prepare_transaction(chain, tx_hash, rpc_url, timeout)
        decoded = (summary.solana.decode_transaction(raw) if chain == 'solana'
                   else summary.evm.decode_transaction(raw))
        if raw_dir is not None:
            target = raw_dir / chain / (tx_hash + '.json')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(raw, separators=(',', ':')) + '\n')
        success = decoded['success'] if chain == 'solana' else decoded['status'] == 'success'
        unresolved = decoded['undecoded_swaps' if chain == 'solana' else 'undecoded_events']
        unknown = decoded['unclassified_instructions' if chain == 'solana' else 'unclassified_logs']
        pools = {s[k] for s in decoded['swaps'] for k in ('pool', 'pool_id') if isinstance(s.get(k), str)}
        result.update(status='decoded' if success else 'reverted', swaps=len(decoded['swaps']),
                      unresolved=unresolved, unclassified_count=len(unknown),
                      pools=sorted(pools),
                      adapters=sorted({s.get('adapter') or s.get('dex') or 'unknown' for s in decoded['swaps']}),
                      warnings=warnings + decoded.get('warnings', []),
                      review_required=bool(unresolved or unknown or (success and not decoded['swaps'])))
    except Exception as exc:
        result.update(status='fetch_error', error=summary.safe_error(exc), swaps=0,
                      unresolved=[], unclassified_count=0, pools=[], adapters=[], review_required=True)
    return result


def open_state(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS transactions (
            chain TEXT NOT NULL, tx_hash TEXT NOT NULL, revision TEXT NOT NULL,
            checked_run TEXT NOT NULL, result TEXT NOT NULL,
            PRIMARY KEY(chain, tx_hash));
        CREATE TABLE IF NOT EXISTS sources (
            path TEXT NOT NULL, chain TEXT NOT NULL, input_format TEXT NOT NULL,
            size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, revision TEXT NOT NULL,
            next_record INTEGER NOT NULL DEFAULT 1, complete INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(path, chain));
        CREATE TABLE IF NOT EXISTS record_issues (
            path TEXT NOT NULL, chain TEXT NOT NULL, number INTEGER NOT NULL,
            issues TEXT NOT NULL, PRIMARY KEY(path, chain, number));
    ''')
    return connection


def _incomplete(result: dict) -> bool:
    return result['status'] == 'fetch_error' or bool(result.get('unresolved'))


def run_audit(path: str | Path, *, chain: str, state: str | Path, rpc_url: str | None = None,
              input_format: str = 'auto', max_transactions: int = 100, workers: int = 4,
              timeout: float = 30, raw_dir: str | Path | None = None,
              retry_incomplete: bool = False, checker=check_transaction) -> dict:
    if chain not in summary.CHAINS or max_transactions < 0 or not 1 <= workers <= 16 or timeout <= 0:
        raise dataset.DatasetError('Invalid chain, limit, worker count or timeout')
    paths = [p.resolve() for p in dataset.dataset_paths(path, chain)]
    if chain != 'solana' and any(dataset.input_format(p, input_format) == 'solana-binary' for p in paths):
        raise dataset.DatasetError('Solana binary parts require --chain solana')
    url = rpc_url or os.environ.get(chain.upper() + '_RPC_URL')
    if not url and chain != 'solana':
        url = os.environ.get('EVM_RPC_URL')
    if not url:
        raise dataset.DatasetError('Set ' + chain.upper() + '_RPC_URL or pass --rpc-url')
    revision, run_id = decoder_revision(chain), uuid.uuid4().hex
    db = open_state(Path(state))
    attempted = 0
    record_count = 0
    try:
        # Validate every existing source identity before advancing any input.
        for source in paths:
            stat = source.stat()
            fmt = dataset.input_format(source, input_format)
            old = db.execute('SELECT input_format,size,mtime_ns,revision FROM sources WHERE path=? AND chain=?',
                             (str(source), chain)).fetchone()
            if old and old[:3] != (fmt, stat.st_size, stat.st_mtime_ns):
                raise dataset.DatasetError('Dataset file changed; use a new state file: ' + source.name)
            if not old:
                db.execute('INSERT INTO sources(path,chain,input_format,size,mtime_ns,revision) VALUES(?,?,?,?,?,?)',
                           (str(source), chain, fmt, stat.st_size, stat.st_mtime_ns, revision))
            elif old[3] != revision or retry_incomplete:
                db.execute('UPDATE sources SET revision=?,next_record=1,complete=0 WHERE path=? AND chain=?',
                           (revision, str(source), chain))
                db.execute('DELETE FROM record_issues WHERE path=? AND chain=?', (str(source), chain))
        db.commit()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            stop = False
            for source in paths:
                start, done = db.execute('SELECT next_record,complete FROM sources WHERE path=? AND chain=?',
                                         (str(source), chain)).fetchone()
                if done:
                    continue
                records = dataset.iter_records(source, format=input_format, start=start)
                try:
                    while True:
                        batch = list(islice(records, 32))
                        if not batch:
                            db.execute('UPDATE sources SET complete=1 WHERE path=? AND chain=?', (str(source), chain))
                            db.commit()
                            break
                        rows = [(record, summary.normalize_row(record.row, chain)) for record in batch]
                        ordered_hashes = list(dict.fromkeys(h for _, row in rows for group in row for h in group))
                        cache, pending = {}, []
                        for tx_hash in ordered_hashes:
                            saved = db.execute('SELECT revision,checked_run,result FROM transactions WHERE chain=? AND tx_hash=?',
                                               (chain, tx_hash)).fetchone()
                            result = json.loads(saved[2]) if saved else None
                            if (saved and saved[0] == revision and
                                not (retry_incomplete and saved[1] != run_id and _incomplete(result))):
                                cache[tx_hash] = result
                            else:
                                pending.append(tx_hash)
                        if max_transactions:
                            pending = pending[:max(0, max_transactions - attempted)]
                        def check(tx_hash):
                            return checker(chain, tx_hash, url, timeout, Path(raw_dir) if raw_dir else None)
                        # Limit queued futures, including for very large victim
                        # groups. Persist results even before a row is finished.
                        for offset in range(0, len(pending), workers * 4):
                            chunk = pending[offset:offset + workers * 4]
                            for tx_hash, result in zip(chunk, pool.map(check, chunk)):
                                cache[tx_hash] = result
                                db.execute('INSERT OR REPLACE INTO transactions VALUES(?,?,?,?,?)',
                                           (chain, tx_hash, revision, run_id, json.dumps(result, separators=(',', ':'))))
                                attempted += 1
                            db.commit()
                        # Advance only past records with every leg durably checked.
                        for record, row in rows:
                            hashes = [h for group in row for h in group]
                            if any(h not in cache for h in hashes):
                                stop = True
                                break
                            issues = []
                            expected_pool = (record.metadata or {}).get('pool')
                            for tx_hash in hashes:
                                result = cache[tx_hash]
                                if _incomplete(result):
                                    issues.append({'hash': tx_hash, 'kind': result['status'] if result['status'] == 'fetch_error' else 'unresolved_swap'})
                                pool_key = expected_pool.lower() if expected_pool and chain != 'solana' else expected_pool
                                if pool_key and pool_key not in result['pools']:
                                    issues.append({'hash': tx_hash, 'kind': 'expected_pool_not_decoded', 'pool': expected_pool})
                            if issues:
                                db.execute('INSERT OR REPLACE INTO record_issues VALUES(?,?,?,?)',
                                           (str(source), chain, record.number, json.dumps(issues)))
                            db.execute('UPDATE sources SET next_record=? WHERE path=? AND chain=?',
                                       (record.number + 1, str(source), chain))
                            record_count += 1
                        db.commit()
                        if stop:
                            break
                finally:
                    records.close()
                if stop:
                    break
        totals = {'cached_unique_transactions': 0, 'decoded_transactions': 0,
                  'reverted_transactions': 0, 'fetch_errors': 0, 'swaps': 0,
                  'unresolved_recognized_swaps': 0, 'unclassified_items': 0,
                  'transactions_requiring_review': 0}
        for (serialized,) in db.execute('SELECT result FROM transactions WHERE chain=? AND revision=?', (chain, revision)):
            result = json.loads(serialized)
            totals['cached_unique_transactions'] += 1
            totals[{'decoded': 'decoded_transactions', 'reverted': 'reverted_transactions', 'fetch_error': 'fetch_errors'}[result['status']]] += 1
            totals['swaps'] += result['swaps']
            totals['unresolved_recognized_swaps'] += len(result['unresolved'])
            totals['unclassified_items'] += result['unclassified_count']
            totals['transactions_requiring_review'] += int(result['review_required'])
        sources = []
        for source in paths:
            next_record, done = db.execute('SELECT next_record,complete FROM sources WHERE path=? AND chain=?',
                                          (str(source), chain)).fetchone()
            issues = db.execute('SELECT count(*) FROM record_issues WHERE path=? AND chain=?', (str(source), chain)).fetchone()[0]
            sources.append({'path': str(source), 'records_checked': next_record - 1,
                            'next_record': next_record, 'complete': bool(done), 'records_with_issues': issues})
        return {'chain': chain, 'checked_at_utc': datetime.now(timezone.utc).isoformat(),
                'decoder_revision': revision, 'new_transaction_checks': attempted,
                'records_checked_this_run': record_count,
                'input_scan_complete': all(s['complete'] for s in sources),
                'all_swaps_guaranteed': False, 'sources': sources,
                'totals_scope': 'deduplicated current-decoder results for this chain across all inputs in this state file',
                'totals': totals,
                'note': 'Complete means every input record was attempted, not that every swap was decoded. Inspect unresolved swaps, unknown evidence and record_issues in the SQLite state.'}
    finally:
        db.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path, help='Dataset file, shard directory or repository root')
    parser.add_argument('--chain', choices=summary.CHAINS, required=True)
    parser.add_argument('--state', type=Path, required=True, help='Persistent SQLite state; reuse to resume')
    parser.add_argument('--input-format', choices=dataset.FORMATS, default='auto')
    parser.add_argument('--max-transactions', type=int, default=100, help='New checks this run; 0 means unlimited (default 100)')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--rpc-url', help='Prefer SOLANA_RPC_URL, BASE_RPC_URL or ETHEREUM_RPC_URL')
    parser.add_argument('--raw-dir', type=Path, help='Optionally save full transaction evidence; can use substantial disk space')
    parser.add_argument('--retry-incomplete', action='store_true', help='Rescan inputs and retry fetch failures and unresolved recognized swaps')
    parser.add_argument('--report', type=Path, help='Save the JSON report instead of printing it')
    args = parser.parse_args(argv)
    try:
        result = run_audit(args.path, chain=args.chain, state=args.state, rpc_url=args.rpc_url,
                           input_format=args.input_format, max_transactions=args.max_transactions,
                           workers=args.workers, timeout=args.timeout, raw_dir=args.raw_dir,
                           retry_incomplete=args.retry_incomplete)
        serialized = json.dumps(result, indent=2) + '\n'
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(serialized)
        else:
            sys.stdout.write(serialized)
        return 0
    except KeyboardInterrupt:
        print('Audit interrupted; rerun with the same --state to resume.', file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        print('Audit failed: ' + summary.safe_error(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
