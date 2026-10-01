"""Offline regression snapshots plus independent amount/ABI/trace checks."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

import evm_decode_swaps as d


A = '0x' + '11' * 20
B = '0x' + '22' * 20
POOL = '0x' + '33' * 20
USER = '0x' + '44' * 20
HASH = '0x' + 'ab' * 32
BLOCK = '0x' + 'cd' * 32
FIXTURES = Path(__file__).parent / 'fixtures' / 'evm'
CATEGORIES = ('swaps', 'conversions', 'settlements', 'route_summaries', 'non_swap_events')


def uint(n):
    return (n % (2**256)).to_bytes(32, 'big')


def static(t, value):
    if t.startswith('('):
        return b''.join(static(k, v) for k, v in zip(d._split_types(t[1:-1]), value))
    if t == 'address':
        return bytes(12) + bytes.fromhex(value[2:])
    if t.startswith('bytes'):
        return bytes.fromhex(value[2:]).ljust(32, b'\0')
    return uint(int(value))


def event_log(family, values, *, name=None, emitter=POOL, index=0):
    spec = next(e for e in d.EVENTS if e.family == family and
                (e.name == name if name else e.name not in ('Initialize', 'PoolInitialized')))
    plain = [(k, t) for k, t, indexed in spec.fields if not indexed]
    # The generated test events use elementary/static tuple fields and bytes.
    head_size = sum(d._size(t) for _, t in plain)
    head, tail = b'', b''
    for k, t in plain:
        if t in ('bytes', 'string'):
            raw = values.get(k, b'')
            head += uint(head_size + len(tail))
            tail += uint(len(raw)) + raw + bytes((-len(raw)) % 32)
        else:
            value = values.get(k, USER if t == 'address' else '0x' + '00'*32 if t == 'bytes32' else 0)
            head += static(t, value)
    topics = [spec.topic]
    for k, t, indexed in spec.fields:
        if indexed:
            topics.append('0x' + static(t, values.get(k, USER if t == 'address' else '0x'+'00'*32 if t == 'bytes32' else 0)).hex())
    return {'address': emitter, 'topics': topics, 'data': '0x' + (head+tail).hex(),
            'logIndex': hex(index), 'transactionHash': HASH, 'blockHash': BLOCK,
            'blockNumber': '0x123', 'removed': False}


def bundle(logs=(), chain=1):
    return {'chain_id': chain,
            'transaction': {'hash': HASH, 'blockHash': BLOCK, 'blockNumber': '0x123', 'from': USER, 'to': POOL},
            'receipt': {'transactionHash': HASH, 'blockHash': BLOCK, 'blockNumber': '0x123',
                        'transactionIndex': '0x1', 'status': '0x1', 'logs': list(logs)},
            'block': {'hash': BLOCK, 'number': '0x123', 'timestamp': '0x1'},
            'metadata': {'calls': {}, 'logs': {}}}


def cached(b, contract, signature, raw, args=()):
    data = d.selector(signature) + ''.join(d.word(x) for x in args)
    b['metadata']['calls'][contract + ':' + data] = {'result': '0x' + raw.hex()}


def pair(b, pool=POOL):
    cached(b, pool, 'token0()', static('address', A))
    cached(b, pool, 'token1()', static('address', B))


def amounts(s):
    return ([ (x['address'], x['amount_raw']) for x in s['inputs'] ],
            [ (x['address'], x['amount_raw']) for x in s['outputs'] ])


class AbiTests(unittest.TestCase):
    def test_ethereum_keccak_vectors(self):
        self.assertEqual(d.keccak256(b'').hex(), 'c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470')
        self.assertEqual(d.topic('Transfer(address,address,uint256)'), '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef')
        self.assertEqual(d.selector('transfer(address,uint256)'), '0xa9059cbb')

    def test_nested_tuple_array_and_bytes_offsets(self):
        raw = uint(2**160) + static('address', A) + uint(128) + uint(224)
        raw += uint(2) + uint(7) + uint(9) + uint(3) + b'abc' + bytes(29)
        self.assertEqual(d.abi_decode(['(uint256,address)', 'uint256[]', 'bytes'], raw),
                         [[2**160, A], [7, 9], '0x616263'])

    def test_malformed_abi_is_rejected(self):
        cases = [('bool', uint(2)), ('uint8', uint(256)), ('int8', uint(255)),
                 ('address', uint(2**160)), ('bytes2', b'abx'+bytes(29)),
                 ('bytes', uint(0)), ('bytes', uint(33)+uint(0)),
                 ('uint256[]', uint(32)+uint(4097)), ('uint256', bytes(31)),
                 ('bytes', uint(32)+uint(1)+b'a'+bytes(30)+b'x')]
        for typ, raw in cases:
            with self.subTest(typ=typ, raw=raw.hex()), self.assertRaises(d.DecodeError):
                d.abi_decode([typ], raw)
        self.assertEqual(d.abi_decode(['int8'], uint(-128)), [-128])

    def test_event_requires_exact_data_and_topic_layout(self):
        log = event_log('v2', {'amount0In': 100, 'amount1Out': 90})
        spec = d.EVENT_BY_TOPIC[log['topics'][0]][0]
        for mutate in (lambda x: x.update(data=x['data']+'00'*32),
                       lambda x: x['topics'].pop(),
                       lambda x: x['topics'].__setitem__(1, '0x01')):
            bad = copy.deepcopy(log); mutate(bad)
            with self.assertRaises(d.DecodeError): spec.decode(bad)


class DecoderTests(unittest.TestCase):
    def test_repeated_pool_calls_and_multihop_keep_log_order(self):
        logs = [event_log('v2', {'amount0In': 100, 'amount1Out': 90}, index=2),
                event_log('v2', {'amount1In': 80, 'amount0Out': 70}, index=4),
                event_log('v2', {'amount0In': 60, 'amount1Out': 50}, index=7, emitter=USER)]
        b = bundle(logs[::-1]); pair(b); pair(b, USER)
        s = d.decode_transaction(b)['swaps']
        self.assertEqual([x['log_index'] for x in s], [2, 4, 7])
        self.assertEqual([amounts(x) for x in s], [([(A,'100')],[(B,'90')]),
                         ([(B,'80')],[(A,'70')]), ([(A,'60')],[(B,'50')])])

    def test_v2_flash_repayment_is_not_fabricated_swap(self):
        b = bundle([event_log('v2', {'amount0In': 101, 'amount0Out': 100})]); pair(b)
        o = d.decode_transaction(b)
        self.assertEqual(o['swaps'], [])
        self.assertEqual(o['non_swap_events'][0]['venue_type'], 'flash_or_multi_asset_pool_flow')

    def test_signed_v3_amounts_remain_exact_above_javascript_precision(self):
        n = 2**180+7
        b = bundle([event_log('v3', {'amount0': n, 'amount1': -91})]); pair(b)
        cached(b, A, 'decimals()', uint(18))
        s = d.decode_transaction(b)['swaps'][0]
        self.assertEqual(amounts(s), ([(A,str(n))],[(B,'91')]))
        self.assertEqual(s['input']['amount'], str(n)[:-18]+'.'+str(n)[-18:])
        self.assertFalse(s['protocol_verified'])

    def test_maverick_uses_executed_amounts_not_requested_amount(self):
        b = bundle([event_log('maverick_v2', {'params': (999999, True, False, -10), 'amountIn': 17, 'amountOut': 23})])
        cached(b, POOL, 'tokenA()', static('address', A)); cached(b, POOL, 'tokenB()', static('address', B))
        self.assertEqual(amounts(d.decode_transaction(b)['swaps'][0]), ([(A,'17')],[(B,'23')]))

    def test_liquidity_book_packed_halves(self):
        b = bundle([event_log('liquidity_book', {'amountsIn': '0x'+uint(55).hex(), 'amountsOut': '0x'+uint(66<<128).hex()})])
        cached(b, POOL, 'getTokenX()', static('address', A)); cached(b, POOL, 'getTokenY()', static('address', B))
        self.assertEqual(amounts(d.decode_transaction(b)['swaps'][0]), ([(A,'55')],[(B,'66')]))

    def test_extended_rfq_uses_filled_amounts(self):
        b = bundle([event_log('rfq_extended', {'makerAsset': B, 'takerAsset': A,
            'makerAmount': 99999, 'takerAmount': 88888, 'filledMakerAmount': 21, 'filledTakerAmount': 13})])
        self.assertEqual(amounts(d.decode_transaction(b)['swaps'][0]), ([(A,'13')],[(B,'21')]))

    def test_schema_only_direct_adapters(self):
        cases = [
            ('wombat', None, {'fromToken': A, 'toToken': B, 'fromAmount': 11, 'toAmount': 22}),
            ('clipper', None, {'inAsset': A, 'outAsset': B, 'inAmount': 11, 'outAmount': 22, 'auxiliaryData': b'proof'}),
            ('dexalot', None, {'takerAsset': A, 'makerAsset': B, 'makerAmountReceived': 11, 'takerAmountReceived': 22}),
            ('rubicon', 'LogTake', {'buy_gem': A, 'pay_gem': B, 'give_amt': 11, 'take_amt': 22}),
            ('zeroex', 'RfqOrderFilled', {'takerToken': A, 'makerToken': B, 'takerTokenFilledAmount': 11, 'makerTokenFilledAmount': 22}),
            ('zeroex', 'LimitOrderFilled', {'takerToken': A, 'makerToken': B, 'takerTokenFilledAmount': 11, 'makerTokenFilledAmount': 22}),
        ]
        for fam, name, values in cases:
            with self.subTest(adapter=fam, event=name):
                o = d.decode_transaction(bundle([event_log(fam, values, name=name)]))
                self.assertEqual(o['undecoded_events'], [])
                self.assertEqual(amounts(o['swaps'][0]), ([(A,'11')],[(B,'22')]))

    def test_summaries_conversions_and_settlements_are_separate(self):
        logs = [event_log('dodo_summary', {'fromToken': A, 'toToken': B, 'fromAmount': 11, 'returnAmount': 22}, index=0),
                event_log('angle', {'tokenIn': A, 'tokenOut': B, 'amountIn': 11, 'amountOut': 22}, index=1),
                event_log('cow', {'sellToken': A, 'buyToken': A, 'sellAmount': 11, 'buyAmount': 11, 'orderUid': b'x'*56}, index=2)]
        o = d.decode_transaction(bundle(logs))
        self.assertEqual(o['swaps'], [])
        self.assertEqual(o['undecoded_events'], [])
        self.assertEqual([len(o[c]) for c in ('route_summaries','conversions','settlements')], [1,1,1])

    def test_only_canonical_weth_is_native_conversion(self):
        logs = [event_log('weth', {'wad': 5}, emitter=d.WETH[1]), event_log('weth', {'wad': 7}, emitter=POOL, index=1)]
        o = d.decode_transaction(bundle(logs))
        self.assertEqual(len(o['conversions']), 1)
        self.assertTrue(o['conversions'][0]['input']['is_native'])
        self.assertEqual(o['conversions'][0]['output']['address'], d.WETH[1])
        self.assertEqual(len(o['unclassified_logs']), 1)

    def test_v4_and_infinity_keys_and_opposite_sign_convention(self):
        for family in ('v4', 'infinity_cl', 'infinity_bin'):
            with self.subTest(family=family):
                key = [A, B, 3000, 60, d.ZERO] if family=='v4' else [A, B, d.ZERO, POOL, 3000, '0x'+'00'*32]
                kt = '(address,address,uint24,int24,address)' if family=='v4' else '(address,address,address,address,uint24,bytes32)'
                key_bytes = static(kt, key)
                pid = '0x'+d.keccak256(key_bytes).hex()
                types = [kt, 'bool', 'int128', 'bytes'] if family=='infinity_bin' else [kt, '(bool,int256,uint160)', 'bytes']
                args = key_bytes+uint(1)+uint(-50)
                if family!='infinity_bin': args += uint(2**96)
                args += uint(len(args)+32)+uint(0)
                calldata = d.selector('swap('+','.join(types)+')')+args.hex()
                log = event_log(family, {'id': pid, 'amount0': -50, 'amount1': 60})
                b = bundle([log]); b['trace']={'type':'CALL','to':POOL,'input':calldata,'logs':[log]}
                self.assertEqual(amounts(d.decode_transaction(b)['swaps'][0]), ([(A,'50')],[(B,'60')]))
                b['receipt']['logs'][0]['data'] = '0x'+uint(0).hex()*2+log['data'][130:]
                zero = d.decode_transaction(b)
                self.assertEqual(zero['swaps'], [])
                self.assertEqual(zero['non_swap_events'][0]['venue_type'], 'zero_pool_delta')
                b['trace']['input'] = calldata[:10] + uint(123).hex() + calldata[74:]
                bad = d.decode_transaction(b)
                self.assertTrue(bad['undecoded_events'])

    def test_factory_membership_controls_verified_identity(self):
        factory = next(a for a,x in d.FACTORIES[1].items() if x['name']=='UniswapV2')
        b = bundle([event_log('v2', {'amount0In': 1, 'amount1Out': 2})]); pair(b)
        cached(b, POOL, 'factory()', static('address', factory))
        for expected, verified, invalid in ((POOL,True,False),(d.ZERO,False,False),(USER,False,True)):
            cached(b, factory, 'getPair(address,address)', static('address', expected), (A,B))
            o = d.decode_transaction(b)
            if invalid:self.assertTrue(o['undecoded_events'])
            else:self.assertEqual(o['swaps'][0]['protocol_verified'], verified)

    def test_missing_metadata_is_visible_and_offline_never_uses_network(self):
        b = bundle([event_log('v3', {'amount0': 5, 'amount1': -6})])
        with patch.object(d, 'urlopen', side_effect=AssertionError('network forbidden')):
            o = d.decode_transaction(b)
        self.assertEqual(o['swaps'], [])
        self.assertIn('Historical metadata', o['undecoded_events'][0]['reasons'][0])
        self.assertFalse(o['coverage']['all_swaps_guaranteed'])

    def test_failed_transaction_has_no_swaps(self):
        b = bundle([event_log('v2', {'amount0In': 1, 'amount1Out': 2})]); b['receipt']['status']='0x0'
        o = d.decode_transaction(b)
        self.assertEqual(o['status'], 'failed'); self.assertEqual(o['swaps'], [])
        self.assertEqual(o['coverage']['decoded_swap_events'], 0)

    def test_foreign_removed_duplicate_logs_and_block_mismatch_rejected(self):
        original = bundle([event_log('v2', {'amount0In': 1, 'amount1Out': 2})]); pair(original)
        mutations = [lambda b:b.update(chain_id=10),
            lambda b:b['transaction'].update(hash='0x'+'00'*32),
            lambda b:b['transaction'].update(blockHash=HASH),
            lambda b:b['block'].update(hash=HASH),
            lambda b:b['receipt']['logs'][0].update(removed=True),
            lambda b:b['receipt']['logs'][0].update(transactionHash=BLOCK),
            lambda b:b['receipt']['logs'].append(copy.deepcopy(b['receipt']['logs'][0]))]
        for f in mutations:
            b=copy.deepcopy(original);f(b)
            with self.assertRaises(d.DecodeError):d.decode_transaction(b)

    def test_unknown_logs_retained(self):
        l=event_log('v2',{});l['topics']=['0x'+'fe'*32]
        o=d.decode_transaction(bundle([l]))
        self.assertEqual(o['unclassified_logs'][0]['topics'], l['topics'])
        self.assertFalse(o['coverage']['all_swaps_guaranteed'])

    def test_same_topic_different_indexing_is_dispatched_correctly(self):
        log=event_log('propamm', {'tokenIn':A,'tokenOut':B,'amountIn':15,'amountOut':25})
        candidates=d.EVENT_BY_TOPIC[log['topics'][0]]
        self.assertEqual({e.family for e in candidates}, {'mstable','propamm'})
        self.assertEqual(amounts(d.decode_transaction(bundle([log]))['swaps'][0]), ([(A,'15')],[(B,'25')]))


class TraceTests(unittest.TestCase):
    def test_reverted_parent_excludes_successful_children(self):
        trace={'type':'CALL','calls':[{'type':'CALL','error':'execution reverted','calls':[{'type':'CALL','to':POOL}]},
                                    {'type':'CALL','to':A}]}
        self.assertEqual([p for p,c in d.successful_calls(trace)], [(),(1,)])

    def test_proxy_delegate_logs_belong_to_parent_context(self):
        log=event_log('v2',{})
        proxy={'type':'CALL','to':POOL,'calls':[{'type':'DELEGATECALL','to':A,'logs':[log]}]}
        self.assertTrue(d.own_log_matches(proxy,log))
        proxy['calls'][0]['error']='reverted'
        self.assertFalse(d.own_log_matches(proxy,log))

    def test_mstable_uses_one_successful_scoped_input_transfer(self):
        log=event_log('mstable', {'swapper':USER,'input':A,'output':B,'outputAmount':75})
        transfer={'address':A,'topics':[d.topic('Transfer(address,address,uint256)'), '0x'+static('address',USER).hex(), '0x'+static('address',POOL).hex()], 'data':'0x'+uint(99).hex()}
        call={'type':'CALL','to':POOL,'logs':[log],'calls':[{'type':'CALL','to':A,'logs':[transfer]}]}
        b=bundle([log]);b['trace']=call
        self.assertEqual(amounts(d.decode_transaction(b)['swaps'][0]), ([(A,'99')],[(B,'75')]))
        call['calls'][0]['logs'].append(copy.deepcopy(transfer))
        self.assertTrue(d.decode_transaction(b)['undecoded_events'])
        call['calls'][0]['logs'].pop();call['error']='execution reverted'
        self.assertTrue(d.decode_transaction(b)['undecoded_events'])

    def test_oneinch_partial_fill_uses_return_values_and_hash(self):
        emitter='0x111111125421ca6dc452d289314280a0f8842a65'
        log=event_log('oneinch_limit',{'orderHash':HASH,'remainingAmount':900},emitter=emitter)
        order=[42,int(USER,16),0,int(B,16),int(A,16),1000,2000,0]
        signature='fillOrder(('+','.join(['uint256']*8)+'),bytes32,bytes32,uint256,uint256)'
        calldata=d.selector(signature)+(b''.join(uint(n) for n in order+[0,0,1000,0])).hex()
        call={'type':'CALL','to':emitter,'from':USER,'input':calldata,
              'output':'0x'+(uint(100)+uint(175)+bytes.fromhex(HASH[2:])).hex(),'logs':[log]}
        b=bundle([log]);b['trace']=call
        self.assertEqual(amounts(d.decode_transaction(b)['swaps'][0]), ([(A,'175')],[(B,'100')]))
        call['output']='0x'+(uint(100)+uint(175)+bytes(32)).hex()
        self.assertTrue(d.decode_transaction(b)['undecoded_events'])
        del b['trace']
        self.assertTrue(d.decode_transaction(b)['undecoded_events'])


class TransportAndCliTests(unittest.TestCase):
    def test_rpc_is_read_only_and_redacts_transport_secrets(self):
        rpc=d.Rpc('https://rpc.invalid/private-secret',retries=0)
        with self.assertRaisesRegex(d.RpcError,'read-only'):rpc.call('eth_sendRawTransaction',[])
        with patch.object(d,'urlopen',side_effect=URLError('https://rpc.invalid/private-secret')):
            with self.assertRaises(d.RpcError) as caught:rpc.call('eth_chainId',[])
        self.assertNotIn('private-secret',str(caught.exception))

    def test_metadata_queries_transaction_block_and_never_latest(self):
        class FakeRpc:
            def __init__(self):self.calls=[]
            def call(self,method,params):self.calls.append((method,params));return '0x'+static('address',A).hex()
        rpc=FakeRpc();meta=d.Metadata(bundle(),rpc)
        self.assertEqual(meta.call(POOL,'token0()'),A)
        self.assertEqual(rpc.calls[0][1][1],'0x123')
        meta.call(POOL,'token0()');self.assertEqual(len(rpc.calls),1)

    def test_fetch_rejects_wrong_chain_before_fetching_transaction(self):
        with patch.object(d.Rpc,'call',return_value='0x1') as rpc:
            with self.assertRaisesRegex(d.DecodeError,'chain ID'):d.fetch_transaction(HASH,'https://rpc.invalid','base')
        self.assertEqual(rpc.call_count,1)

    def test_reorganization_during_lookup_is_rejected(self):
        class FakeRpc:
            def call(self,method,params):return {'hash':HASH}
        with self.assertRaisesRegex(d.DecodeError,'reorganized'):d.decode_transaction(bundle(),FakeRpc())

    def test_cli_offline_chain_binding_and_bad_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bundle.json';path.write_text(json.dumps(bundle()))
            with patch('sys.stderr',new_callable=io.StringIO):
                self.assertEqual(d.main(['--from-json',str(path)],default_chain='base'),1)
            out=Path(tmp)/'decoded.json'
            self.assertEqual(d.main(['--from-json',str(path),'--output',str(out)],default_chain='ethereum'),0)
            self.assertEqual(json.loads(out.read_text())['chain'],'ethereum')
            path.write_text('{broken')
            with patch('sys.stderr',new_callable=io.StringIO) as stderr:
                self.assertEqual(d.main(['--from-json',str(path)]),1)
                self.assertIn('JSONDecodeError',stderr.getvalue())

    def test_catalog_includes_anonymous_formats(self):
        cat=d.protocol_catalog()
        self.assertEqual({x['adapter'] for x in cat['anonymous_schemas']},{'ekubo_v1','ekubo_v3'})
        self.assertFalse(cat['all_swaps_guaranteed'])


class RealTransactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest=json.loads((FIXTURES/'manifest.json').read_text())

    def test_frozen_real_transaction_regressions_without_rpc(self):
        with patch.object(d,'urlopen',side_effect=AssertionError('fixture replay must stay offline')):
            for item in self.manifest:
                with self.subTest(file=item['file']):
                    path=FIXTURES/item['file']
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),item['sha256'])
                    result=d.decode_transaction(json.loads(path.read_text()))
                    actual={cat:[{k:s[k] for k in ('log_index','adapter','emitter','pool_id','inputs','outputs')} for s in result[cat]] for cat in CATEGORIES}
                    self.assertEqual(actual,item['expected'])
                    self.assertEqual(result['undecoded_events'],[])
                    self.assertEqual([s['log_index'] for s in result['swaps']],sorted(s['log_index'] for s in result['swaps']))

    def test_ekubo_event_deltas_and_trace_returns_agree(self):
        checked=set()
        for item in self.manifest:
            b=json.loads((FIXTURES/item['file']).read_text());o=d.decode_transaction(b)
            logs={d.quantity(x['logIndex']):x for x in b['receipt']['logs']}
            for s in o['swaps']:
                if not s['adapter'].startswith('ekubo'):continue
                checked.add(s['adapter']);raw=bytes.fromhex(logs[s['log_index']]['data'][2:])
                ds=[int.from_bytes(raw[52:68],'big',signed=True),int.from_bytes(raw[68:84],'big',signed=True)]
                self.assertEqual(int(s['input']['amount_raw']),max(ds))
                self.assertEqual(int(s['output']['amount_raw']),-min(ds))
                b['trace']={'type':'CALL','error':'execution reverted'}
                broken=d.decode_transaction(b)
                self.assertTrue(any('ekubo' in e['possible_adapters'] for e in broken['undecoded_events']))
        self.assertEqual(checked,{'ekubo_v1','ekubo_v3'})


if __name__ == '__main__':
    unittest.main()
