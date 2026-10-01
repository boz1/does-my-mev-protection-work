"""Offline TRON address, amount, metadata and sandwich regression checks."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import evm_decode_swaps as d
import tron_decode_swaps as tron
import summarize_sandwich as summary
import sandwich_dataset as dataset
import audit_swap_dataset as audit
from test_evm_decode_swaps import A, B, POOL, USER, HASH, BLOCK, bundle, cached, event_log, pair, static, uint
from test_summarize_sandwich import evm_bundle, tx_hash


CHAIN = d.TRON_CHAIN_ID
WTRX = d.WETH[CHAIN]


def tron_bundle(logs=()):
    return bundle(logs, chain=CHAIN)


def factory(b, family='v2', pool=POOL):
    address = next(a for a, spec in d.FACTORIES[CHAIN].items() if spec['family'] == family)
    cached(b, pool, 'factoryAddress()' if family == 'tron_v1' else 'factory()', static('address', address))
    if family == 'v2':
        cached(b, address, 'getPair(address,address)', static('address', pool), (A, B))
    elif family == 'v3':
        cached(b, pool, 'fee()', uint(500))
        cached(b, address, 'getPool(address,address,uint24)', static('address', pool), (A, B, 500))
    else:
        cached(b, address, 'getExchange(address)', static('address', pool), (A,))
    return address


class TronTests(unittest.TestCase):
    def test_public_chain_fixtures_decode_offline_with_exact_event_amounts(self):
        root = Path(__file__).parent / 'fixtures' / 'tron'
        with patch.object(d.Rpc, 'call', side_effect=AssertionError('offline')):
            for case in json.loads((root/'manifest.json').read_text())['cases']:
                with self.subTest(fixture=case['fixture']):
                    result = tron.decode_transaction(json.loads((root/case['fixture']).read_text()))
                    self.assertEqual(result['undecoded_events'], [])
                    self.assertEqual([s['protocol'] for s in result['swaps']], case['protocols'])
                    self.assertEqual([[s['input']['amount_raw'], s['output']['amount_raw']]
                                      for s in result['swaps']], case['amounts'])

    def test_known_address_formats_and_checksum(self):
        base58 = 'TNUC9Qb1rRpS5CbWLmNMxXBjyFoydXjWFR'
        for value in (base58, WTRX, '41' + WTRX[2:], '0x41' + WTRX[2:].upper()):
            self.assertEqual(d.tron_address_hex(value), WTRX)
            self.assertEqual(d.tron_address_base58(value), base58)
        self.assertEqual(d.tron_address_base58(d.ZERO), 'T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb')
        for value in (base58[:-1] + '1', '0x42' + WTRX[2:], 'T' * 34, 'not-an-address'):
            with self.subTest(value=value), self.assertRaises(d.DecodeError):
                d.tron_address_hex(value)

    def test_bare_txid_is_accepted_only_for_tron_and_duplicates_are_normalized(self):
        self.assertEqual(d.normalize_tx_hash(HASH[2:].upper(), 'tron'), HASH)
        with self.assertRaises(d.DecodeError):
            d.normalize_tx_hash(HASH[2:], 'ethereum')
        with self.assertRaises(summary.SummaryError):
            summary.normalize_row([HASH, HASH[2:], [tx_hash(1)]], 'tron')

    def test_v2_exact_integer_amounts_and_verified_factory(self):
        amount = 2**90 + 123
        b = tron_bundle([event_log('v2', {'amount1In': 1_234_567, 'amount0Out': amount})])
        pair(b); factory(b)
        cached(b, B, 'decimals()', uint(6))
        cached(b, A, 'decimals()', uint(18))
        result = tron.decode_transaction(b)
        swap = result['swaps'][0]
        self.assertEqual(swap['protocol'], 'SunSwapV2')
        self.assertTrue(swap['protocol_verified'])
        self.assertEqual(swap['verification'], 'registered_factory_membership_latest_state')
        self.assertEqual(swap['input']['amount'], '1.234567')
        self.assertEqual(swap['output']['amount_raw'], str(amount))
        self.assertEqual(swap['pool_base58'], d.tron_address_base58(POOL))
        self.assertEqual(result['metadata_scope'], 'latest_state')
        self.assertIn('not historical', result['warnings'][0])

    def test_all_hops_remain_separate_and_calldata_limits_are_not_amounts(self):
        logs = [event_log('v2', {'amount0In': 100, 'amount1Out': 90}, index=4),
                event_log('v2', {'amount1In': 80, 'amount0Out': 70}, index=8)]
        b = tron_bundle(logs[::-1]); pair(b)
        b['transaction']['input'] = '0x' + d.word(2**255)
        swaps = tron.decode_transaction(b)['swaps']
        self.assertEqual([s['log_index'] for s in swaps], [4, 8])
        self.assertEqual([s['input']['amount_raw'] for s in swaps], ['100', '80'])

    def test_v3_signed_amounts_and_factory_fee_membership(self):
        b = tron_bundle([event_log('v3', {'amount0': -123, 'amount1': 456, 'tick': -1})])
        pair(b); factory(b, 'v3')
        swap = tron.decode_transaction(b)['swaps'][0]
        self.assertEqual(swap['protocol'], 'SunSwapV3')
        self.assertEqual(swap['input']['address'], B)
        self.assertEqual(swap['output']['amount_raw'], '123')

    def test_v1_native_trx_both_directions_use_six_decimals(self):
        for name, values, native_side in (
            ('TokenPurchase', {'trx_sold': 1_234_567, 'tokens_bought': 999}, 'input'),
            ('TrxPurchase', {'tokens_sold': 999, 'trx_bought': 1_234_567}, 'output'),
        ):
            with self.subTest(name=name):
                b = tron_bundle([event_log('tron_v1', values, name=name)])
                cached(b, POOL, 'tokenAddress()', static('address', A)); factory(b, 'tron_v1')
                swap = tron.decode_transaction(b)['swaps'][0]
                self.assertEqual(swap['protocol'], 'SunSwapV1')
                self.assertEqual(swap['adapter'], 'tron_v1')
                self.assertEqual(swap[native_side]['symbol'], 'TRX')
                self.assertEqual(swap[native_side]['amount'], '1.234567')
                self.assertEqual(swap[native_side]['decimals'], 6)
                self.assertTrue(swap[native_side]['is_native'])

    def test_trx_purchase_topic_matches_official_sun_abi(self):
        self.assertEqual(d.topic('TrxPurchase(address,uint256,uint256)'),
                         '0xdad9ec5c9b9c82bf6927bf0b64293dcdd1f82c92793aef3c5f26d7b93a4a5306')

    def test_curve_and_underlying_coin_resolution(self):
        pool = d.tron_address_hex('THJCvwFmu6uDqKY59JbULHDgfddy2Zojty')
        for name, getter in [('TokenExchange', 'coins(uint256)'),
                             ('TokenExchangeUnderlying', 'underlying_coins(uint256)')]:
            b = tron_bundle([event_log('curve', {'sold_id': 0, 'bought_id': 1,
                                'tokens_sold': 123, 'tokens_bought': 122}, name=name, emitter=pool)])
            cached(b, pool, getter, static('address', A), (0,))
            cached(b, pool, getter, static('address', B), (1,))
            swap = tron.decode_transaction(b)['swaps'][0]
            self.assertEqual(swap['protocol'], 'SunCurve')
            self.assertEqual(swap['input']['address'], A)
            self.assertEqual(swap['output']['amount_raw'], '122')

    def test_wrap_and_unwrap_are_conversions_with_trx_units(self):
        for name, native_side in [('Deposit', 'input'), ('Withdrawal', 'output')]:
            b = tron_bundle([event_log('weth', {'wad': 2_000_001}, name=name, emitter=WTRX)])
            cached(b, WTRX, 'decimals()', uint(6))
            result = tron.decode_transaction(b)
            self.assertFalse(result['swaps'])
            converted = result['conversions'][0]
            self.assertEqual(converted['protocol'], 'WTRX')
            self.assertEqual(converted[native_side]['symbol'], 'TRX')
            self.assertEqual(converted[native_side]['amount'], '2.000001')

    def test_unrelated_deposit_topic_is_not_assumed_wtrx(self):
        result = tron.decode_transaction(tron_bundle([event_log('weth', {'wad': 1})]))
        self.assertEqual(len(result['unclassified_logs']), 1)
        self.assertFalse(result['conversions'])

    def test_ethereum_only_event_is_retained_as_unclassified(self):
        result = tron.decode_transaction(tron_bundle([event_log('fluid_lite', {})]))
        self.assertEqual(len(result['unclassified_logs']), 1)
        self.assertFalse(result['undecoded_events'])

    def test_missing_metadata_never_invents_token_identity(self):
        result = tron.decode_transaction(tron_bundle([event_log('v2', {'amount0In': 10, 'amount1Out': 9})]))
        self.assertFalse(result['swaps'])
        self.assertEqual(len(result['undecoded_events']), 1)
        self.assertIn('Latest-state', result['undecoded_events'][0]['reasons'][0])

    def test_false_factory_membership_is_rejected(self):
        b = tron_bundle([event_log('v2', {'amount0In': 10, 'amount1Out': 9})]); pair(b)
        f = factory(b)
        cached(b, f, 'getPair(address,address)', static('address', USER), (A, B))
        result = tron.decode_transaction(b)
        self.assertFalse(result['swaps'])
        self.assertIn('not the pool registered', result['undecoded_events'][0]['reasons'][0])

    def test_failed_transaction_cannot_report_executed_swaps(self):
        b = tron_bundle([event_log('v2', {'amount0In': 10, 'amount1Out': 9})]); pair(b)
        b['receipt']['status'] = '0x0'
        self.assertEqual(tron.decode_transaction(b)['swaps'], [])

    def test_flash_repayment_is_not_counted_as_token_swap(self):
        b = tron_bundle([event_log('v2', {'amount0In': 11, 'amount0Out': 10})]); pair(b)
        result = tron.decode_transaction(b)
        self.assertFalse(result['swaps'])
        self.assertEqual(len(result['non_swap_events']), 1)

    def test_wrapper_rejects_saved_bundle_from_another_chain(self):
        with self.assertRaisesRegex(d.DecodeError, 'TRON'):
            tron.decode_transaction(bundle())

    def test_metadata_calls_latest_on_tron_and_original_block_on_evm(self):
        for chain, tag in [(CHAIN, 'latest'), (1, '0x123'), (8453, '0x123')]:
            rpc = Mock(); rpc.call.return_value = '0x' + d.word(A)
            b = bundle(chain=chain)
            self.assertEqual(d.Metadata(b, rpc).call(POOL, 'token0()'), A)
            self.assertEqual(rpc.call.call_args.args[1][1], tag)
            if chain == CHAIN:
                self.assertEqual(b['metadata']['state_scope'], 'latest')
                self.assertEqual(next(iter(b['metadata']['calls'].values()))['block_tag'], 'latest')

    def test_transient_tron_constant_call_error_is_retried(self):
        replies = [io.StringIO(json.dumps({'id': 1, 'error': {'code': -32000}})),
                   io.StringIO(json.dumps({'id': 1, 'result': '0x01'}))]
        rpc = d.Rpc('https://rpc.invalid'); rpc.retry_rpc_codes = {-32000}
        with patch.object(d, 'urlopen', side_effect=replies) as open_url, patch.object(d.time, 'sleep'):
            self.assertEqual(rpc.call('eth_call', [{}, 'latest']), '0x01')
            self.assertEqual(open_url.call_count, 2)

    def test_fetch_rejects_wrong_rpc_chain_before_loading_receipt(self):
        with patch.object(d, 'Rpc') as rpc:
            rpc.return_value.call.return_value = '0x1'
            with self.assertRaisesRegex(d.DecodeError, 'chain ID'):
                tron.fetch_transaction(HASH[2:], 'https://rpc.invalid')
            self.assertEqual(rpc.return_value.call.call_count, 1)

    def test_cli_requires_tron_specific_rpc_and_has_offline_replay(self):
        with patch.dict(os.environ, {'EVM_RPC_URL': 'https://wrong-chain.invalid'}, clear=True), \
             patch('sys.stderr', new_callable=io.StringIO), patch.object(d, 'fetch_transaction') as fetch:
            with self.assertRaises(SystemExit):
                tron.main([HASH])
            fetch.assert_not_called()
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True), \
             patch.object(d.Rpc, 'call', side_effect=AssertionError('offline')), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            path = Path(tmp) / 'raw.json'; path.write_text(json.dumps(tron_bundle()))
            self.assertEqual(tron.main([HASH[2:], '--from-json', str(path)]), 0)
            self.assertEqual(json.loads(output.getvalue())['chain'], 'tron')

    def test_catalog_identifies_tron_deployments_and_limits(self):
        catalog = d.protocol_catalog('tron')
        self.assertEqual(catalog['chains'], {'tron': CHAIN})
        self.assertEqual(catalog['anonymous_schemas'], [])
        self.assertFalse(catalog['all_swaps_guaranteed'])
        self.assertEqual(catalog['metadata_scope'], 'latest_state')
        self.assertEqual({v['name'] for v in catalog['factories'][CHAIN].values()},
                         {'SunSwapV1', 'SunSwapV2', 'SunSwapV3'})

    def test_tron_multiple_victims_and_all_ordering_classifications(self):
        for positions, label in [([(100, 0), (100, 1), (100, 2), (100, 3)], 'tight'),
                                 ([(100, 0), (100, 3), (100, 4), (100, 9)], 'within_block'),
                                 ([(100, 0), (100, 1), (101, 1), (101, 2)], 'cross_block')]:
            raw = evm_bundle(positions, chain='tron')
            raw['row'] = [[h[2:] for h in group] for group in raw['row']]
            out = summary.summarize_bundle(raw)
            self.assertEqual(out['classification'], label)
            self.assertEqual(out['counts']['victim'], 2)
            self.assertEqual(out['date'], '2024-09-05')
            self.assertIn('latest state', summary.render_text(out))

    def test_auditor_chooses_tron_shards_and_normalizes_base58_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'tron/s').mkdir(parents=True); (root/'s').mkdir()
            (root/'s/0.json').write_text('[]')
            row = {'f': [tx_hash(1)[2:]], 'b': [tx_hash(3)], 'V': [{'h': tx_hash(2)}],
                   'pool': d.tron_address_base58(POOL)}
            shard = root/'tron/s/0.json'; shard.write_text(json.dumps([row]))
            self.assertEqual(dataset.dataset_paths(root, 'tron'), [shard])
            def checker(chain, txid, *_):
                return {'status': 'decoded', 'swaps': 1, 'unresolved': [], 'unclassified_count': 0,
                        'pools': [POOL], 'adapters': ['v2'], 'review_required': False}
            result = audit.run_audit(root, chain='tron', state=root/'audit.sqlite',
                                     rpc_url='configured-at-runtime', checker=checker)
            self.assertTrue(result['input_scan_complete'])
            self.assertEqual(result['sources'][0]['records_with_issues'], 0)
            self.assertEqual(result['totals']['cached_unique_transactions'], 3)

    def test_repository_layout_keeps_ethereum_and_tron_inputs_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for prefix in ('ethereum', 'tron', 'base'):
                (root/prefix/'s').mkdir(parents=True)
                (root/prefix/'s/0.json').write_text('[]')
            for chain in ('ethereum', 'tron', 'base'):
                self.assertEqual(dataset.dataset_paths(root, chain), [root/chain/'s/0.json'])


if __name__ == '__main__':
    unittest.main()
