import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import audit_swap_dataset as a
import sandwich_dataset as d


def h(n):
    return '0x' + f'{n:064x}'


def result(chain, tx_hash, status='decoded', unresolved=None, pools=None):
    return {'chain': chain, 'tx_hash': tx_hash, 'status': status, 'swaps': int(status == 'decoded'),
            'unresolved': unresolved or [], 'unclassified_count': 0, 'pools': pools or [],
            'adapters': [], 'review_required': status != 'decoded' or bool(unresolved),
            'all_swaps_guaranteed': False}


class ResumeTests(unittest.TestCase):
    def test_rpc_must_be_explicit_before_opening_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, state = Path(tmp)/'rows.jsonl', Path(tmp)/'state.sqlite'
            path.write_text(json.dumps([[h(1)], [h(3)], [h(2)]])+'\n')
            with patch.dict('os.environ', {}, clear=True):
                with self.assertRaisesRegex(d.DatasetError, 'RPC_URL'):
                    a.run_audit(path, chain='base', state=state)
            self.assertFalse(state.exists())

    def test_decoder_change_rechecks_cached_transactions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, state = Path(tmp)/'rows.jsonl', Path(tmp)/'state.sqlite'
            path.write_text(json.dumps([[h(1)], [h(3)], [h(2)]])+'\n')
            common = dict(chain='base', state=state, rpc_url='configured-at-runtime',
                          checker=lambda chain, tx_hash, *_: result(chain, tx_hash))
            with patch.object(a, 'decoder_revision', return_value='before'):
                a.run_audit(path, **common)
            with patch.object(a, 'decoder_revision', return_value='after'):
                report = a.run_audit(path, **common)
            self.assertEqual(report['new_transaction_checks'], 3)
            self.assertTrue(report['input_scan_complete'])
            self.assertEqual(report['totals']['cached_unique_transactions'], 3)

    def test_resume_inside_row_deduplicates_shared_hashes_and_never_skips_legs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, state = Path(tmp)/'rows.jsonl', Path(tmp)/'state.sqlite'
            path.write_text(json.dumps([[h(1)], [h(3)], [h(2)]])+'\n'+
                            json.dumps([[h(1)], [h(5)], [h(4)]])+'\n')
            calls = []
            def check(chain, tx_hash, *_):
                calls.append(tx_hash)
                return result(chain, tx_hash)
            common = dict(chain='base', state=state, rpc_url='configured-at-runtime', checker=check)
            first = a.run_audit(path, max_transactions=2, **common)
            self.assertFalse(first['input_scan_complete'])
            self.assertEqual(first['sources'][0]['next_record'], 1)
            second = a.run_audit(path, max_transactions=0, **common)
            self.assertTrue(second['input_scan_complete'])
            self.assertEqual(second['sources'][0]['records_checked'], 2)
            self.assertEqual(second['totals']['cached_unique_transactions'], 5)
            self.assertEqual(len(calls), len(set(calls)))
            third = a.run_audit(path, max_transactions=0, **common)
            self.assertEqual(third['new_transaction_checks'], 0)

    def test_completed_scan_still_exposes_fetch_errors_and_missing_pool_and_can_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, state = Path(tmp)/'shard.json', Path(tmp)/'state.sqlite'
            pool = '0x'+'ab'*20
            path.write_text(json.dumps([{'f': [h(1)], 'b': [h(3)], 'V': [{'h': h(2)}], 'pool': pool}]))
            failure = True
            def check(chain, tx_hash, *_):
                return result(chain, tx_hash, 'fetch_error' if failure and tx_hash == h(2) else 'decoded', pools=[] if failure and tx_hash == h(2) else [pool])
            common = dict(chain='ethereum', state=state, rpc_url='configured-at-runtime', checker=check)
            first = a.run_audit(path, **common)
            self.assertTrue(first['input_scan_complete'])
            self.assertFalse(first['all_swaps_guaranteed'])
            self.assertEqual(first['totals']['fetch_errors'], 1)
            self.assertEqual(first['sources'][0]['records_with_issues'], 1)
            failure = False
            retry = a.run_audit(path, retry_incomplete=True, **common)
            self.assertEqual(retry['new_transaction_checks'], 1)
            self.assertEqual(retry['totals']['fetch_errors'], 0)
            self.assertEqual(retry['sources'][0]['records_with_issues'], 0)

    def test_modified_input_cannot_silently_reuse_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, state = Path(tmp)/'rows.jsonl', Path(tmp)/'state.sqlite'
            path.write_text(json.dumps([[h(1)], [h(3)], [h(2)]])+'\n')
            common = dict(chain='base', state=state, rpc_url='configured-at-runtime',
                          checker=lambda chain, tx_hash, *_: result(chain, tx_hash))
            a.run_audit(path, **common)
            path.write_text(path.read_text()+'\n')
            with self.assertRaisesRegex(d.DatasetError, 'changed'):
                a.run_audit(path, **common)


if __name__ == '__main__':
    unittest.main()
