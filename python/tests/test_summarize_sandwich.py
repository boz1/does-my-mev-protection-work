import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import summarize_sandwich as s


FIXTURE = Path(__file__).parent / 'fixtures' / 'sandwiches' / 'solana_tight.raw.json'


def tx_hash(n):
    return '0x' + f'{n:064x}'


def evm_bundle(positions, groups=None, chain='base'):
    """Synthetic canonical blocks for independent ordering/boundary assertions."""
    hashes = [tx_hash(i+1) for i in range(len(positions))]
    if groups is None:
        groups = [[0], [len(hashes)-1], list(range(1,len(hashes)-1))]
    b = {'format': s.FORMAT, 'chain': chain,
         'row': [[hashes[i] for i in group] for group in groups],
         'transactions': {}, 'blocks': {}, 'errors': {'transactions': {}, 'blocks': {}}}
    for i, (number, index) in enumerate(positions):
        block_hash = tx_hash(number+10000)
        block = b['blocks'].setdefault(str(number), {'number': hex(number), 'hash': block_hash,
            'timestamp': hex(1725494400 + 12*(number-100)), 'transactions': []})
        while len(block['transactions']) <= index:
            block['transactions'].append(tx_hash(number*100000+len(block['transactions'])))
        block['transactions'][index] = hashes[i]
        b['transactions'][hashes[i]] = {
            'chain_id': s.evm.CHAIN_IDS[chain],
            'block': {k:v for k,v in block.items() if k!='transactions'},
            'transaction': {'hash': hashes[i], 'blockHash': block_hash, 'blockNumber': hex(number),
                'from': '0x'+'12'*20, 'to': '0x'+'34'*20},
            'receipt': {'transactionHash':hashes[i], 'blockHash':block_hash,'blockNumber':hex(number),
                'transactionIndex':hex(index),'status':'0x1','logs':[]},
            'metadata': {'calls':{},'logs':{}}}
    return b


class InputTests(unittest.TestCase):
    def test_front_back_strings_and_victim_list(self):
        self.assertEqual(s.normalize_row([tx_hash(1),tx_hash(3),[tx_hash(2)]],'base'),
                         [[tx_hash(1)],[tx_hash(3)],[tx_hash(2)]])

    def test_multiple_front_back_groups_preserved(self):
        row=[[tx_hash(1),tx_hash(2)],[tx_hash(5),tx_hash(6)],[tx_hash(3),tx_hash(4)]]
        self.assertEqual(s.normalize_row(row,'ethereum'),row)

    def test_bad_input_and_duplicate_roles_rejected_before_rpc(self):
        good=[tx_hash(1),tx_hash(3),[tx_hash(2)]]
        bad=[[],[tx_hash(1),tx_hash(2)],[[tx_hash(1)],[],[tx_hash(2)]],
             [tx_hash(1),tx_hash(3),[tx_hash(1)]],['invalid',tx_hash(3),[tx_hash(2)]],
             [1,tx_hash(3),[tx_hash(2)]]]
        for row in bad:
            with self.subTest(row=row),self.assertRaises(s.SummaryError):s.normalize_row(row,'base')
        with self.assertRaises(s.SummaryError):s.normalize_row(good,'arbitrum')
        with self.assertRaises(s.SummaryError):s.normalize_row(good,'solana')

    def test_evm_case_normalized_before_duplicate_check(self):
        h='0x'+'aB'*32
        with self.assertRaises(s.SummaryError):s.normalize_row([h,h.lower(),[tx_hash(2)]],'ethereum')

    def test_jsonl_stream_one_based_line_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'data.jsonl';p.write_text('[1,2,3]\n[4,5,6]\nnot json\n')
            self.assertEqual(s.read_row(p,2),[4,5,6])
            for n in (0,3,4):
                with self.assertRaises(s.SummaryError):s.read_row(p,n)


class ClassificationTests(unittest.TestCase):
    def test_tight_single_victim_and_exact_span_counts(self):
        o=s.summarize_bundle(evm_bundle([(100,10),(100,11),(100,12)]))
        self.assertEqual(o['classification'],'tight');self.assertTrue(o['is_tight'])
        self.assertEqual(o['block_relation'],'within_block');self.assertTrue(o['strict_role_order'])
        self.assertEqual(o['span']['transaction_index_distance'],2)
        self.assertEqual(o['span']['transactions_between'],1)
        self.assertEqual(o['span']['transactions_inclusive'],3)
        self.assertEqual(o['span']['unlisted_transactions_between'],0)

    def test_tight_multiple_victims(self):
        o=s.summarize_bundle(evm_bundle([(100,4),(100,5),(100,6),(100,7)]))
        self.assertEqual(o['counts']['victim'],2)
        self.assertTrue(o['is_tight'])
        self.assertEqual([x['role'] for x in o['execution_order']],['front','victim','victim','back'])

    def test_multiple_fronts_backs_and_unsorted_input(self):
        b=evm_bundle([(100,i) for i in range(8,14)],groups=[[1,0],[5,4],[3,2]])
        o=s.summarize_bundle(b)
        self.assertEqual([o['counts'][r] for r in ('front','back','victim')],[2,2,2])
        self.assertTrue(o['is_tight'])
        self.assertEqual([x['role'] for x in o['execution_order']],['front','front','victim','victim','back','back'])
        self.assertEqual(o['front_txs'][0]['input_index'],0)
        self.assertEqual(o['execution_order'][0]['tx_hash'],tx_hash(1))

    def test_gap_anywhere_between_victims_is_wide(self):
        o=s.summarize_bundle(evm_bundle([(100,10),(100,11),(100,14),(100,15)]))
        self.assertEqual(o['classification'],'within_block');self.assertFalse(o['is_tight'])
        self.assertTrue(o['strict_role_order'])
        self.assertEqual(o['span']['unlisted_transactions_between'],2)

    def test_front_back_index_distance_is_not_exclusive_count(self):
        o=s.summarize_bundle(evm_bundle([(100,3),(100,5),(100,175)]))
        self.assertEqual(o['span']['transaction_index_distance'],172)
        self.assertEqual(o['span']['transactions_between'],171)
        self.assertEqual(o['classification_label'],'within-block wide')

    def test_cross_block_cannot_be_tight_even_if_indices_are_consecutive(self):
        o=s.summarize_bundle(evm_bundle([(100,3),(100,4),(101,5)]))
        self.assertEqual(o['classification'],'cross_block');self.assertFalse(o['is_tight'])
        self.assertTrue(o['strict_role_order'])
        self.assertIsNone(o['span']['transaction_index_distance'])

    def test_victim_in_different_block_is_cross_even_when_front_back_match(self):
        o=s.summarize_bundle(evm_bundle([(100,3),(101,0),(100,8)]))
        self.assertEqual(o['classification'],'cross_block')
        self.assertFalse(o['strict_role_order']);self.assertTrue(o['warnings'])

    def test_back_before_victim_never_tight(self):
        o=s.summarize_bundle(evm_bundle([(100,2),(100,4),(100,3)]))
        self.assertFalse(o['strict_role_order']);self.assertFalse(o['is_tight'])

    def test_interleaved_roles_preserved_without_claiming_invalid_attack(self):
        b=evm_bundle([(100,2),(100,3),(100,4),(100,5)],groups=[[0,2],[3],[1]])
        o=s.summarize_bundle(b)
        self.assertFalse(o['strict_role_order']);self.assertTrue(o['victims_enclosed'])
        self.assertFalse(o['is_tight']);self.assertEqual(o['span']['transaction_index_distance'],3)
        self.assertIn('interleaved',o['classification_label'])
        self.assertNotIn('invalid',o['classification_label'])
        self.assertEqual([x['role'] for x in o['execution_order']],['front','victim','front','back'])

    def test_missing_leg_is_unknown_not_invented_wide(self):
        b=evm_bundle([(100,10),(100,11),(100,12)])
        del b['transactions'][tx_hash(2)]
        o=s.summarize_bundle(b)
        self.assertEqual(o['classification'],'unknown');self.assertIsNone(o['is_tight'])
        self.assertIsNone(o['execution_order'])
        self.assertEqual(o['coverage']['missing_transactions'],1)
        self.assertIsNone(o['all_legs_succeeded'])

    def test_two_known_blocks_prove_cross_even_with_missing_third_leg(self):
        b=evm_bundle([(100,10),(101,0),(102,1)]);del b['transactions'][tx_hash(2)]
        o=s.summarize_bundle(b)
        self.assertEqual(o['classification'],'cross_block');self.assertFalse(o['is_tight'])
        self.assertIsNone(o['strict_role_order'])

    def test_date_is_front_utc_and_range_can_cross_midnight(self):
        b=evm_bundle([(100,1),(101,0),(102,0)],chain='ethereum')
        times={100:1725494399,101:1725494400,102:1725494412}
        for number,timestamp in times.items():
            b['blocks'][str(number)]['timestamp']=hex(timestamp)
        o=s.summarize_bundle(b)
        self.assertEqual(o['date'],'2024-09-04')
        self.assertEqual(o['timestamp_utc'],'2024-09-04T23:59:59Z')
        self.assertEqual(o['date_range_utc'],{'start':'2024-09-04','end':'2024-09-05'})

    def test_failed_leg_retains_ordering_but_reports_execution_failure(self):
        b=evm_bundle([(100,0),(100,1),(100,2)])
        b['transactions'][tx_hash(2)]['receipt']['status']='0x0'
        o=s.summarize_bundle(b)
        self.assertTrue(o['is_tight']);self.assertFalse(o['all_legs_succeeded'])
        self.assertEqual(o['victim_txs'][0]['swaps'],[])
        self.assertTrue(any('failed' in w for w in o['warnings']))

    def test_reorganized_or_misindexed_receipt_is_rejected(self):
        for mutation in ('hash','index','absent','chain'):
            b=evm_bundle([(100,0),(100,1),(100,2)])
            if mutation=='hash':b['blocks']['100']['hash']=tx_hash(999)
            elif mutation=='index':b['transactions'][tx_hash(2)]['receipt']['transactionIndex']='0x0'
            elif mutation=='absent':b['blocks']['100']['transactions'].remove(tx_hash(2))
            else:b['transactions'][tx_hash(2)]['chain_id']=1
            with self.subTest(mutation=mutation),self.assertRaises(s.SummaryError):s.summarize_bundle(b)

    def test_same_height_different_forks_rejected_even_without_block_list(self):
        b=evm_bundle([(100,0),(100,1),(100,2)]);b['blocks']={}
        raw=b['transactions'][tx_hash(2)]
        raw['receipt']['blockHash']=tx_hash(999)
        raw['transaction']['blockHash']=tx_hash(999)
        raw['block']['hash']=tx_hash(999)
        with self.assertRaisesRegex(s.SummaryError,'different block hashes'):s.summarize_bundle(b)


class SolanaAndIntegrationTests(unittest.TestCase):
    def test_real_solana_tight_row_and_decoded_swaps_offline(self):
        raw=json.loads(FIXTURE.read_text())
        with patch.object(s.solana,'rpc_call',side_effect=AssertionError('offline')):
            o=s.summarize_bundle(raw)
        self.assertEqual(o['date'],'2023-10-10')
        self.assertEqual(o['classification'],'tight')
        self.assertEqual([x['transaction_index'] for x in o['execution_order']],[455,456,457])
        self.assertEqual(o['blocks'][0]['number'],222669934)
        self.assertEqual(o['counts']['swaps'],3)
        self.assertEqual(o['front_txs'][0]['swaps'][0]['amount_in_raw'],'158673523')
        self.assertEqual(o['back_txs'][0]['swaps'][0]['amount_out_raw'],'163749394')
        self.assertFalse(o['coverage']['all_swaps_guaranteed'])
        self.assertIsNone(o['private_submission'])
        self.assertEqual(len(o['bot_candidates']),1)
        rendered=s.render_text(o)
        self.assertIn('VICTIM',rendered);self.assertIn('Raydium AMM v4',rendered)
        self.assertIn('0.7 WSOL',rendered)

    def test_missing_solana_block_keeps_swaps_but_tightness_unknown(self):
        raw=json.loads(FIXTURE.read_text());raw['blocks']={}
        o=s.summarize_bundle(raw)
        self.assertEqual(o['classification'],'within_block')
        self.assertIsNone(o['is_tight']);self.assertIsNone(o['strict_role_order'])
        self.assertEqual(o['coverage']['missing_transaction_indices'],3)
        self.assertEqual(o['counts']['swaps'],3)

    def test_missing_solana_time_does_not_invent_current_date(self):
        raw=json.loads(FIXTURE.read_text())
        for t in raw['transactions'].values():t['blockTime']=None
        for b in raw['blocks'].values():b['blockTime']=None
        o=s.summarize_bundle(raw)
        self.assertIsNone(o['date']);self.assertIsNone(o['timestamp_utc'])
        self.assertTrue(o['is_tight'])

    def test_front_back_sender_mismatch_is_not_labeled_same_bot(self):
        raw=evm_bundle([(100,0),(100,1),(100,2)])
        raw['transactions'][tx_hash(3)]['transaction']['from']='0x'+'99'*20
        self.assertEqual(s.summarize_bundle(raw)['bot_candidates'],[])

    def test_fetch_calls_one_block_lookup_per_distinct_slot(self):
        raw=json.loads(FIXTURE.read_text());slot=next(iter(raw['blocks']))
        with patch.object(s,'_prepare_transaction',side_effect=lambda chain,h,url,timeout:(copy.deepcopy(raw['transactions'][h]),[])), \
             patch.object(s.solana,'rpc_call',return_value=raw['blocks'][slot]) as rpc:
            fetched=s.fetch_sandwich(*raw['row'],rpc_url='https://example.invalid/secret')
        self.assertEqual(rpc.call_count,1)
        args=rpc.call_args.args
        self.assertEqual(args[0],'getBlock')
        self.assertEqual(args[1][1]['transactionDetails'],'signatures')
        self.assertNotIn('secret',json.dumps(fetched))
        self.assertEqual(s.summarize_bundle(fetched)['classification'],'tight')

    def test_fetch_failure_retained_as_explicit_missing_leg(self):
        row=[tx_hash(1),tx_hash(3),[tx_hash(2)]]
        with patch.object(s,'_prepare_transaction',side_effect=s.evm.RpcError('eth_getTransactionReceipt: RPC error -32000')):
            raw=s.fetch_sandwich(*row,chain='base',rpc_url='https://example.invalid/secret')
        o=s.summarize_bundle(raw)
        self.assertEqual(o['classification'],'unknown')
        self.assertEqual(o['coverage']['missing_transactions'],3)
        self.assertNotIn('secret',json.dumps(raw))

    def test_offline_cli_json_and_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'summary.json'
            self.assertEqual(s.main(['--from-json',str(FIXTURE),'--output',str(p)]),0)
            self.assertEqual(json.loads(p.read_text())['classification'],'tight')
            with patch('sys.stdout',new_callable=io.StringIO) as out:
                self.assertEqual(s.main(['--from-json',str(FIXTURE),'--format','text']),0)
                self.assertIn('index=455',out.getvalue())
            with patch('sys.stderr',new_callable=io.StringIO):
                self.assertEqual(s.main(['--from-json',str(FIXTURE),'--chain','base']),1)

    def test_function_accepts_parsed_row_without_losing_groups(self):
        row=[[tx_hash(1),tx_hash(2)],[tx_hash(5)],[tx_hash(3),tx_hash(4)]]
        with patch.object(s,'summarize_sandwich',return_value={'ok':True}) as summarize:
            self.assertEqual(s.summarize_row(row,chain='base'),{'ok':True})
        summarize.assert_called_once_with(*row,chain='base')

    def test_cli_rejects_conflicting_inputs(self):
        with patch('sys.stderr',new_callable=io.StringIO):
            self.assertEqual(s.main(['--from-json',str(FIXTURE),'--row-json','[]']),1)


if __name__=='__main__':
    unittest.main()
