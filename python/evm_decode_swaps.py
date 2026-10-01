#!/usr/bin/env python3
"""Decode executed swap events on Ethereum, Base and TRON (Python 3.10+, stdlib only).

Amounts come from receipt events or successful executed calls, never router
quotes or calldata limits. Metadata (historical on EVM, latest on TRON) and
traces are saved with --raw-output for replay without RPC access.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class DecodeError(ValueError):
    pass


class RpcError(RuntimeError):
    pass


class MetadataError(DecodeError):
    pass


# Keccak-f[1600], with Ethereum's Keccak padding (not SHA3-256 padding).
_RC = (0x1, 0x8082, 0x800000000000808a, 0x8000000080008000,
       0x808b, 0x80000001, 0x8000000080008081, 0x8000000000008009,
       0x8a, 0x88, 0x80008009, 0x8000000a, 0x8000808b,
       0x800000000000008b, 0x8000000000008089, 0x8000000000008003,
       0x8000000000008002, 0x8000000000000080, 0x800a,
       0x800000008000000a, 0x8000000080008081, 0x8000000000008080,
       0x80000001, 0x8000000080008008)
_ROT = ((0, 36, 3, 41, 18), (1, 44, 10, 45, 2), (62, 6, 43, 15, 61),
        (28, 55, 25, 21, 56), (27, 20, 39, 8, 14))
_MASK = (1 << 64) - 1


def keccak256(data: bytes) -> bytes:
    def rot(x: int, n: int) -> int:
        return ((x << n) | (x >> (64 - n))) & _MASK if n else x
    padded = bytearray(data)
    padded.append(1)
    padded.extend(b'\0' * ((-len(padded)) % 136))
    padded[-1] |= 128
    a = [0] * 25
    for pos in range(0, len(padded), 136):
        for i in range(17):
            a[i] ^= int.from_bytes(padded[pos + 8*i:pos + 8*i + 8], 'little')
        for rc in _RC:
            c = [a[x] ^ a[x+5] ^ a[x+10] ^ a[x+15] ^ a[x+20] for x in range(5)]
            d = [c[(x-1) % 5] ^ rot(c[(x+1) % 5], 1) for x in range(5)]
            for x in range(5):
                for y in range(5):
                    a[x+5*y] ^= d[x]
            b = [0] * 25
            for x in range(5):
                for y in range(5):
                    b[y + 5*((2*x+3*y) % 5)] = rot(a[x+5*y], _ROT[x][y])
            for x in range(5):
                for y in range(5):
                    a[x+5*y] = b[x+5*y] ^ ((~b[(x+1) % 5+5*y]) & b[(x+2) % 5+5*y])
            a[0] ^= rc
    return b''.join(n.to_bytes(8, 'little') for n in a)[:32]


@lru_cache(maxsize=2048)
def topic(signature: str) -> str:
    return '0x' + keccak256(signature.encode()).hex()


def selector(signature: str) -> str:
    return topic(signature)[:10]


ZERO = '0x' + '0' * 40
NATIVE = '0x' + 'e' * 40
TRON_CHAIN_ID = 0x2b6653dc
CHAIN_IDS = {'ethereum': 1, 'base': 8453, 'tron': TRON_CHAIN_ID}
WETH = {1: '0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2',
        8453: '0x4200000000000000000000000000000000000006',
        TRON_CHAIN_ID: '0x891cdb91d149f23b1a45d9c5ca78a88d0cb44c18'}
TRON_FAMILIES = frozenset(('v2', 'v3', 'tron_v1', 'curve', 'saddle', 'weth'))
_BASE58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def normalize_tx_hash(value: str, chain: str | None = None) -> str:
    if chain == 'tron' and isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{64}', value):
        value = '0x' + value
    if not isinstance(value, str) or not re.fullmatch(r'0x[0-9a-fA-F]{64}', value):
        raise DecodeError('Transaction hash must be 0x followed by 64 hexadecimal digits (TRON also accepts bare txIDs)')
    return value.lower()


def tron_address_hex(value: str) -> str:
    """Accept JSON-RPC 20-byte hex, TRON 41-prefixed hex, or Base58Check."""
    if isinstance(value, str) and re.fullmatch(r'(?:0x)?41[0-9a-fA-F]{40}', value):
        return '0x' + value[-40:].lower()
    if isinstance(value, str) and len(value) == 34 and value.startswith('T'):
        try:
            number = 0
            for char in value:
                number = number * 58 + _BASE58.index(char)
            raw = number.to_bytes(25, 'big')
        except (ValueError, OverflowError):
            raise DecodeError('Invalid TRON Base58Check address') from None
        checksum = hashlib.sha256(hashlib.sha256(raw[:-4]).digest()).digest()[:4]
        if raw[0] != 0x41 or raw[-4:] != checksum:
            raise DecodeError('Invalid TRON address prefix or checksum')
        return '0x' + raw[1:21].hex()
    return address(value)


def tron_address_base58(value: str) -> str:
    raw = b'\x41' + bytes.fromhex(tron_address_hex(value)[2:])
    raw += hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
    number, encoded = int.from_bytes(raw, 'big'), ''
    while number:
        number, digit = divmod(number, 58)
        encoded = _BASE58[digit] + encoded
    return encoded


def _add_tron_addresses(value: Any) -> None:
    """Keep canonical hex keys for dataset matching; add display addresses."""
    if isinstance(value, list):
        for child in value:
            _add_tron_addresses(child)
    elif isinstance(value, dict):
        for key, child in list(value.items()):
            if key in ('address', 'pool', 'emitter', 'factory', 'sender', 'recipient',
                       'transaction_sender', 'transaction_to') and isinstance(child, str) and re.fullmatch(r'0x[0-9a-fA-F]{40}', child):
                value[key + '_base58'] = tron_address_base58(child)
            elif isinstance(child, (dict, list)):
                _add_tron_addresses(child)


def hex_bytes(value: str) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r'0x(?:[0-9a-fA-F]{2})*', value):
        raise DecodeError('Invalid hexadecimal byte string')
    return bytes.fromhex(value[2:])


def address(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}', value):
        raise DecodeError('Invalid EVM address')
    return value.lower()


def quantity(value: Any) -> int:
    if isinstance(value, int):
        return value
    return int(value, 16) if value.startswith('0x') else int(value)


def word(value: int | str) -> str:
    if isinstance(value, str):
        raw = hex_bytes(value)
        if len(raw) > 32:
            raise DecodeError('ABI word is too long')
        return raw.hex().rjust(64, '0')
    if not -(1 << 255) <= value < (1 << 256):
        raise DecodeError('ABI integer is out of range')
    return (value % (1 << 256)).to_bytes(32, 'big').hex()


def _split_types(value: str) -> list[str]:
    out, start, depth = [], 0, 0
    for i, char in enumerate(value):
        depth += (char == '(') - (char == ')')
        if char == ',' and depth == 0:
            out.append(value[start:i])
            start = i + 1
    if value[start:]:
        out.append(value[start:])
    return out


def _array_type(t: str) -> tuple[str, int | None] | None:
    m = re.fullmatch(r'(.*)\[([0-9]*)\]', t)
    return (m[1], int(m[2]) if m[2] else None) if m else None


def _dynamic(t: str) -> bool:
    ar = _array_type(t)
    if ar:
        return ar[1] is None or _dynamic(ar[0])
    if t.startswith('('):
        return any(map(_dynamic, _split_types(t[1:-1])))
    return t in ('bytes', 'string')


def _size(t: str) -> int:
    if _dynamic(t):
        return 32
    ar = _array_type(t)
    if ar:
        return ar[1] * _size(ar[0])
    if t.startswith('('):
        return sum(map(_size, _split_types(t[1:-1])))
    return 32


def abi_decode(types: list[str], data: bytes, base: int = 0) -> list[Any]:
    """Bounded ABI decoder, including dynamic arrays/bytes and nested tuples."""
    def chunk(pos: int, size: int = 32) -> bytes:
        if pos < 0 or size < 0 or pos + size > len(data):
            raise DecodeError('Truncated ABI data')
        return data[pos:pos+size]

    def uint(pos: int) -> int:
        return int.from_bytes(chunk(pos), 'big')

    def sequence(ts: list[str], at: int, depth: int) -> list[Any]:
        if depth > 24 or len(ts) > 4096:
            raise DecodeError('ABI nesting/array limit exceeded')
        head_size = sum(map(_size, ts))
        chunk(at, head_size)
        result, pos = [], at
        for t in ts:
            loc = pos
            if _dynamic(t):
                offset = uint(pos)
                if offset % 32 or offset < head_size:
                    raise DecodeError('Invalid ABI dynamic offset')
                loc = at + offset
            result.append(decode(t, loc, depth+1))
            pos += _size(t)
        return result

    def decode(t: str, pos: int, depth: int) -> Any:
        ar = _array_type(t)
        if ar:
            subtype, count = ar
            if count is None:
                count, pos = uint(pos), pos + 32
            if count > 4096:
                raise DecodeError('ABI array is too long')
            return sequence([subtype] * count, pos, depth)
        if t.startswith('('):
            return sequence(_split_types(t[1:-1]), pos, depth)
        if t in ('bytes', 'string'):
            size = uint(pos)
            raw = chunk(pos + 32, size)
            padding = chunk(pos + 32 + size, (-size) % 32)
            if any(padding):
                raise DecodeError('Nonzero ABI byte padding')
            return raw.decode('utf-8', 'replace') if t == 'string' else '0x' + raw.hex()
        raw, n = chunk(pos), uint(pos)
        if t == 'address':
            if n >= 1 << 160:
                raise DecodeError('Invalid ABI address padding')
            return '0x' + raw[-20:].hex()
        if t == 'bool':
            if n not in (0, 1):
                raise DecodeError('Invalid ABI boolean')
            return bool(n)
        if t.startswith(('uint', 'int')):
            signed = t.startswith('int')
            bits = int(t[3:] if signed else t[4:])
            if bits < 8 or bits > 256 or bits % 8:
                raise DecodeError('Invalid ABI integer type')
            if signed and n >= 1 << 255:
                n -= 1 << 256
            if not (-(1 << (bits-1)) <= n < 1 << (bits-1) if signed else 0 <= n < 1 << bits):
                raise DecodeError('Invalid ABI integer sign extension')
            return n
        if t.startswith('bytes'):
            size = int(t[5:])
            if not 1 <= size <= 32 or any(raw[size:]):
                raise DecodeError('Invalid ABI fixed bytes')
            return '0x' + raw[:size].hex()
        raise DecodeError('Unsupported ABI type: ' + t)

    return sequence(types, base, 0)


@dataclass(frozen=True)
class Event:
    family: str
    name: str
    # (field name, canonical ABI type, indexed)
    fields: tuple[tuple[str, str, bool], ...]
    source: str = ''

    @property
    def signature(self) -> str:
        return self.name + '(' + ','.join(f[1] for f in self.fields) + ')'

    @property
    def topic(self) -> str:
        return topic(self.signature)

    def decode(self, log: dict) -> dict:
        topics = log.get('topics', [])
        indexed = [f for f in self.fields if f[2]]
        plain = [f for f in self.fields if not f[2]]
        if len(topics) != len(indexed) + 1 or topics[0].lower() != self.topic:
            raise DecodeError('Event topic layout mismatch')
        raw = hex_bytes(log.get('data', '0x'))
        types = [f[1] for f in plain]
        if not any(map(_dynamic, types)) and len(raw) != sum(map(_size, types)):
            raise DecodeError('Event data length mismatch')
        result = dict(zip([f[0] for f in plain], abi_decode(types, raw)))
        for field, value in zip(indexed, topics[1:]):
            if len(hex_bytes(value)) != 32:
                raise DecodeError('Event indexed word length mismatch')
            result[field[0]] = value.lower() if _dynamic(field[1]) or field[1].startswith('(') or '[' in field[1] else abi_decode([field[1]], hex_bytes(value))[0]
        return result


class Rpc:
    """Read-only JSON-RPC transport. Errors never include the credentialed URL."""
    ALLOWED = {'eth_chainId', 'eth_getTransactionByHash', 'eth_getTransactionReceipt',
               'eth_getBlockByNumber', 'eth_getBlockByHash', 'eth_call', 'eth_getLogs',
               'eth_getCode', 'eth_blockNumber', 'debug_traceTransaction'}

    def __init__(self, url: str, timeout: float = 30, retries: int = 2):
        if not url.startswith(('https://', 'http://')):
            raise RpcError('RPC URL must use HTTP or HTTPS')
        self.url, self.timeout, self.retries, self._id = url, timeout, retries, 0
        self.retry_rpc_codes: set[int] = set()

    def call(self, method: str, params: list) -> Any:
        if method not in self.ALLOWED:
            raise RpcError('Only read-only RPC methods are allowed')
        self._id += 1
        payload = json.dumps({'jsonrpc': '2.0', 'id': self._id,
                              'method': method, 'params': params}).encode()
        for attempt in range(self.retries + 1):
            try:
                req = Request(self.url, payload, {'Content-Type': 'application/json',
                                                  'User-Agent': 'evm-swap-decoder/1'})
                with urlopen(req, timeout=self.timeout) as response:
                    result = json.load(response)
                if not isinstance(result, dict) or result.get('id') != self._id:
                    raise RpcError(method + ': invalid RPC response')
                if result.get('error'):
                    code = result['error'].get('code', 'unknown')
                    if method == 'eth_call' and code in self.retry_rpc_codes and attempt < self.retries:
                        time.sleep(0.25 * (attempt + 1))
                        continue
                    # Revert details and server messages can contain URLs; retain the code only.
                    raise RpcError(f'{method}: RPC error {code}')
                if 'result' not in result:
                    raise RpcError(method + ': missing result')
                return result['result']
            except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
                if attempt == self.retries:
                    status = f' HTTP {exc.code}' if isinstance(exc, HTTPError) else ''
                    raise RpcError(f'{method}: transport failure{status} ({type(exc).__name__})') from None
                time.sleep(0.25 * (attempt + 1))
        raise AssertionError('unreachable')


class Metadata:
    def __init__(self, bundle: dict, rpc: Rpc | None = None):
        self.bundle, self.rpc = bundle, rpc
        self.block = bundle['receipt']['blockNumber']
        self.chain_id = quantity(bundle['chain_id'])
        # java-tron eth_call only supports latest state. Even object-form block
        # references execute at latest; never label these calls as historical.
        self.call_block = 'latest' if self.chain_id == TRON_CHAIN_ID else self.block
        self.state_label = 'Latest-state' if self.chain_id == TRON_CHAIN_ID else 'Historical'
        self.cache = bundle.setdefault('metadata', {'calls': {}, 'logs': {}})
        self.cache.setdefault('calls', {})
        self.cache.setdefault('logs', {})
        if self.chain_id == TRON_CHAIN_ID:
            self.cache['state_scope'] = 'latest'

    def call(self, contract: str, signature: str, args: tuple = (), returns: tuple = ('address',)) -> Any:
        contract = address(contract)
        data = selector(signature) + ''.join(map(word, args))
        key = contract + ':' + data
        if key not in self.cache['calls']:
            if self.rpc is None:
                raise MetadataError(self.state_label + ' metadata is not saved: ' + signature)
            try:
                value = self.rpc.call('eth_call', [{'to': contract, 'data': data}, self.call_block])
                self.cache['calls'][key] = {'result': value}
                if self.chain_id == TRON_CHAIN_ID:
                    self.cache['calls'][key]['block_tag'] = 'latest'
            except RpcError as exc:
                self.cache['calls'][key] = {'error': str(exc)}
        record = self.cache['calls'][key]
        if 'error' in record:
            raise MetadataError(self.state_label + ' call unavailable: ' + signature)
        raw = hex_bytes(record['result'])
        if not raw:
            raise MetadataError('Empty ' + self.state_label.lower() + ' response: ' + signature)
        values = abi_decode(list(returns), raw)
        return values[0] if len(values) == 1 else values

    def first_call(self, contract: str, signatures: list[str], args: tuple = (), returns: tuple = ('address',)) -> Any:
        for signature in signatures:
            try:
                return self.call(contract, signature, args, returns)
            except DecodeError:
                pass
        raise MetadataError(self.state_label + ' metadata unavailable: ' + ', '.join(signatures))

    def trace(self) -> dict:
        if 'trace' not in self.bundle and 'trace_error' not in self.bundle and self.rpc:
            try:
                self.bundle['trace'] = self.rpc.call('debug_traceTransaction', [
                    self.bundle['transaction']['hash'],
                    {'tracer': 'callTracer', 'tracerConfig': {'withLog': True}, 'timeout': '20s'}])
            except RpcError as exc:
                self.bundle['trace_error'] = str(exc)
        return self.bundle.get('trace') or {}

    def token(self, contract: str, amount: int) -> dict:
        contract = address(contract)
        if amount < 0:
            raise DecodeError('Negative token amount')
        native = contract in (NATIVE, ZERO)
        native_info = (6, 'TRX') if self.chain_id == TRON_CHAIN_ID else (18, 'ETH')
        decimals, symbol = native_info if native else (None, None)
        if not native:
            try:
                decimals = self.call(contract, 'decimals()', returns=('uint8',))
            except DecodeError:
                pass
            try:
                symbol = self.call(contract, 'symbol()', returns=('string',))
            except DecodeError:
                try:
                    raw = self.call(contract, 'symbol()', returns=('bytes32',))
                    symbol = hex_bytes(raw).rstrip(b'\0').decode('utf-8', 'replace')
                except DecodeError:
                    pass
        if symbol:
            symbol = ''.join(c for c in symbol if c.isprintable())[:80]
        formatted = None
        if decimals is not None:
            digits = str(amount).rjust(decimals + 1, '0')
            formatted = (digits[:-decimals] + '.' + digits[-decimals:]).rstrip('0').rstrip('.') if decimals else digits
        return {'address': None if native else contract, 'is_native': native,
                'symbol': symbol, 'decimals': decimals, 'amount_raw': str(amount),
                'amount': formatted}



# Public ABI/deployment facts; full provenance is in outputs/evm/registry_sources.json.
EVENTS = [
    Event('v2', 'Swap', (('sender', 'address', True), ('amount0In', 'uint256', False), ('amount1In', 'uint256', False), ('amount0Out', 'uint256', False), ('amount1Out', 'uint256', False), ('to', 'address', True)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/solidly/SolidlyPair.json'),
    Event('v3', 'Swap', (('sender', 'address', True), ('recipient', 'address', True), ('amount0', 'int256', False), ('amount1', 'int256', False), ('sqrtPriceX96', 'uint160', False), ('liquidity', 'uint128', False), ('tick', 'int24', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/uniswap-v3/UniswapV3Pool.abi.json'),
    Event('pancake_v3', 'Swap', (('sender', 'address', True), ('recipient', 'address', True), ('amount0', 'int256', False), ('amount1', 'int256', False), ('sqrtPriceX96', 'uint160', False), ('liquidity', 'uint128', False), ('tick', 'int24', False), ('protocolFeesToken0', 'uint128', False), ('protocolFeesToken1', 'uint128', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/pancakeswap-v3/PancakeswapV3Pool.abi.json'),
    Event('algebra_integral', 'Swap', (('sender', 'address', True), ('recipient', 'address', True), ('amount0', 'int256', False), ('amount1', 'int256', False), ('price', 'uint160', False), ('liquidity', 'uint128', False), ('tick', 'int24', False), ('overrideFee', 'uint24', False), ('pluginFee', 'uint24', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/algebra-integral/AlgebraIntegralPool.abi.json'),
    Event('v4', 'Initialize', (('id', 'bytes32', True), ('currency0', 'address', True), ('currency1', 'address', True), ('fee', 'uint24', False), ('tickSpacing', 'int24', False), ('hooks', 'address', False), ('sqrtPriceX96', 'uint160', False), ('tick', 'int24', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/uniswap-v4/pool-manager.abi.json'),
    Event('v4', 'Swap', (('id', 'bytes32', True), ('sender', 'address', True), ('amount0', 'int128', False), ('amount1', 'int128', False), ('sqrtPriceX96', 'uint160', False), ('liquidity', 'uint128', False), ('tick', 'int24', False), ('fee', 'uint24', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/uniswap-v4/pool-manager.abi.json'),
    Event('balancer_v1', 'LOG_SWAP', (('caller', 'address', True), ('tokenIn', 'address', True), ('tokenOut', 'address', True), ('tokenAmountIn', 'uint256', False), ('tokenAmountOut', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/BalancerV1Pool.json'),
    Event('balancer_v2', 'Swap', (('poolId', 'bytes32', True), ('tokenIn', 'address', True), ('tokenOut', 'address', True), ('amountIn', 'uint256', False), ('amountOut', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/balancer-v2/vault.json'),
    Event('balancer_v3', 'Swap', (('pool', 'address', True), ('tokenIn', 'address', True), ('tokenOut', 'address', True), ('amountIn', 'uint256', False), ('amountOut', 'uint256', False), ('swapFeePercentage', 'uint256', False), ('swapFeeAmount', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/balancer-v3/vault-extension.json'),
    Event('curve', 'TokenExchange', (('buyer', 'address', True), ('sold_id', 'int128', False), ('tokens_sold', 'uint256', False), ('bought_id', 'int128', False), ('tokens_bought', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/curve-v1/StableSwap3Pool.json'),
    Event('curve', 'TokenExchangeUnderlying', (('buyer', 'address', True), ('sold_id', 'int128', False), ('tokens_sold', 'uint256', False), ('bought_id', 'int128', False), ('tokens_bought', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/curve-v1/CurveV1StableNg.json'),
    Event('curve', 'TokenExchange', (('buyer', 'address', True), ('sold_id', 'uint256', False), ('tokens_sold', 'uint256', False), ('bought_id', 'uint256', False), ('tokens_bought', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/CurveV2.json'),
    Event('saddle', 'TokenSwap', (('buyer', 'address', True), ('tokensSold', 'uint256', False), ('tokensBought', 'uint256', False), ('soldId', 'uint128', False), ('boughtId', 'uint128', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/StablePool.json'),
    Event('saddle', 'TokenSwapUnderlying', (('buyer', 'address', True), ('tokensSold', 'uint256', False), ('tokensBought', 'uint256', False), ('soldId', 'uint128', False), ('boughtId', 'uint128', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/nerve/nerve-metapool.json'),
    Event('maverick_v1', 'Swap', (('sender', 'address', False), ('recipient', 'address', False), ('tokenAIn', 'bool', False), ('exactOutput', 'bool', False), ('amountIn', 'uint256', False), ('amountOut', 'uint256', False), ('activeTick', 'int32', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/maverick-v1/pool.json'),
    Event('maverick_v2', 'PoolSwap', (('sender', 'address', False), ('recipient', 'address', False), ('params', '(uint256,bool,bool,int32)', False), ('amountIn', 'uint256', False), ('amountOut', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/maverick-v2/MaverickV2Pool.json'),
    Event('fluid', 'Swap', (('swap0to1', 'bool', False), ('amountIn', 'uint256', False), ('amountOut', 'uint256', False), ('to', 'address', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/fluid-dex/fluid-dex.abi.json'),
    Event('woo', 'WooSwap', (('fromToken', 'address', True), ('toToken', 'address', True), ('fromAmount', 'uint256', False), ('toAmount', 'uint256', False), ('from', 'address', False), ('to', 'address', True), ('rebateTo', 'address', False), ('swapVol', 'uint256', False), ('swapFee', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/woo-fi-v2/WooPPV2.abi.json'),
    Event('paraswap_rfq', 'OrderFilled', (('orderHash', 'bytes32', True), ('maker', 'address', True), ('makerAsset', 'address', False), ('makerAmount', 'uint256', False), ('taker', 'address', True), ('takerAsset', 'address', False), ('takerAmount', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/paraswap-limit-orders/AugustusRFQ.abi.json'),
    Event('dexalot', 'SwapExecuted', (('nonceAndMeta', 'uint256', False), ('maker', 'address', False), ('taker', 'address', False), ('makerAsset', 'address', False), ('takerAsset', 'address', False), ('makerAmountReceived', 'uint256', False), ('takerAmountReceived', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/dexalot/DexalotMainnetRFQ.json'),
    Event('angle', 'Swap', (('tokenIn', 'address', True), ('tokenOut', 'address', True), ('amountIn', 'uint256', False), ('amountOut', 'uint256', False), ('from', 'address', True), ('to', 'address', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/angle-transmuter/Transmuter.json'),
    Event('psm3', 'Swap', (('assetIn', 'address', True), ('assetOut', 'address', True), ('sender', 'address', False), ('receiver', 'address', True), ('amountIn', 'uint256', False), ('amountOut', 'uint256', False), ('referralCode', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/sdai/PSM3.abi.json'),
    Event('paraswap_summary', 'Swapped', (('uuid', 'bytes16', False), ('initiator', 'address', False), ('beneficiary', 'address', True), ('srcToken', 'address', True), ('destToken', 'address', True), ('srcAmount', 'uint256', False), ('receivedAmount', 'uint256', False), ('expectedAmount', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/IParaswap.json'),
    Event('paraswap_summary', 'Swapped2', (('uuid', 'bytes16', False), ('partner', 'address', False), ('feePercent', 'uint256', False), ('initiator', 'address', False), ('beneficiary', 'address', True), ('srcToken', 'address', True), ('destToken', 'address', True), ('srcAmount', 'uint256', False), ('receivedAmount', 'uint256', False), ('expectedAmount', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/IParaswap.json'),
    Event('paraswap_summary', 'SwappedDirect', (('uuid', 'bytes16', False), ('partner', 'address', False), ('feePercent', 'uint256', False), ('initiator', 'address', False), ('kind', 'uint8', False), ('beneficiary', 'address', True), ('srcToken', 'address', True), ('destToken', 'address', True), ('srcAmount', 'uint256', False), ('receivedAmount', 'uint256', False), ('expectedAmount', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/DirectSwap.json'),
    Event('paraswap_summary', 'SwappedV3', (('uuid', 'bytes16', False), ('partner', 'address', False), ('feePercent', 'uint256', False), ('initiator', 'address', False), ('beneficiary', 'address', True), ('srcToken', 'address', True), ('destToken', 'address', True), ('srcAmount', 'uint256', False), ('receivedAmount', 'uint256', False), ('expectedAmount', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/DirectSwap.json'),
    Event('dodo_summary', 'OrderHistory', (('fromToken', 'address', False), ('toToken', 'address', False), ('sender', 'address', False), ('fromAmount', 'uint256', False), ('returnAmount', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/dodo-v2-proxy.json'),
    Event('curve_summary', 'Exchange', (('sender', 'address', True), ('receiver', 'address', True), ('route', 'address[11]', False), ('swap_params', 'uint256[5][5]', False), ('pools', 'address[5]', False), ('in_amount', 'uint256', False), ('out_amount', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/curve-v1-factory/CurveV1Router.abi.json'),
    Event('erc4626', 'Deposit', (('sender', 'address', True), ('owner', 'address', True), ('assets', 'uint256', False), ('shares', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/ERC4626.json'),
    Event('erc4626', 'Withdraw', (('sender', 'address', True), ('receiver', 'address', True), ('owner', 'address', True), ('assets', 'uint256', False), ('shares', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/ERC4626.json'),
    Event('erc4626', 'Deposit', (('dst', 'address', True), ('wad', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/ERC4626.json'),
    Event('ekubo', 'PoolInitialized', (('poolId', 'bytes32', False), ('poolKey', '(address,address,bytes32)', False), ('tick', 'int32', False), ('sqrtRatio', 'uint96', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/ekubo-v3/core.json'),
    Event('solidly', 'Swap', (('sender', 'address', True), ('to', 'address', True), ('amount0In', 'uint256', False), ('amount1In', 'uint256', False), ('amount0Out', 'uint256', False), ('amount1Out', 'uint256', False)), 'https://github.com/aerodrome-finance/contracts/blob/main/contracts/interfaces/IPool.sol'),
    Event('dodo_v2', 'DODOSwap', (('fromToken', 'address', False), ('toToken', 'address', False), ('fromAmount', 'uint256', False), ('toAmount', 'uint256', False), ('trader', 'address', False), ('receiver', 'address', False)), 'https://github.com/DODOEX/contractV2/blob/main/contracts/DODOVendingMachine/impl/DVMTrader.sol'),
    Event('dodo_v1', 'SellBaseToken', (('seller', 'address', True), ('payBase', 'uint256', False), ('receiveQuote', 'uint256', False)), 'https://github.com/DODOEX/dodo-smart-contract/blob/master/contracts/impl/Trader.sol'),
    Event('dodo_v1', 'BuyBaseToken', (('buyer', 'address', True), ('receiveBase', 'uint256', False), ('payQuote', 'uint256', False)), 'https://github.com/DODOEX/dodo-smart-contract/blob/master/contracts/impl/Trader.sol'),
    Event('cow', 'Trade', (('owner', 'address', True), ('sellToken', 'address', False), ('buyToken', 'address', False), ('sellAmount', 'uint256', False), ('buyAmount', 'uint256', False), ('feeAmount', 'uint256', False), ('orderUid', 'bytes', False)), 'https://github.com/cowprotocol/contracts/blob/main/src/contracts/GPv2Settlement.sol'),
    Event('zeroex', 'LimitOrderFilled', (('orderHash', 'bytes32', False), ('maker', 'address', False), ('taker', 'address', False), ('feeRecipient', 'address', False), ('makerToken', 'address', False), ('takerToken', 'address', False), ('takerTokenFilledAmount', 'uint128', False), ('makerTokenFilledAmount', 'uint128', False), ('takerTokenFeeFilledAmount', 'uint128', False), ('protocolFeePaid', 'uint256', False), ('pool', 'bytes32', False)), 'https://github.com/0xProject/protocol/blob/development/contracts/zero-ex/contracts/src/features/interfaces/INativeOrdersEvents.sol'),
    Event('zeroex', 'RfqOrderFilled', (('orderHash', 'bytes32', False), ('maker', 'address', False), ('taker', 'address', False), ('makerToken', 'address', False), ('takerToken', 'address', False), ('takerTokenFilledAmount', 'uint128', False), ('makerTokenFilledAmount', 'uint128', False), ('pool', 'bytes32', False)), 'https://github.com/0xProject/protocol/blob/development/contracts/zero-ex/contracts/src/features/interfaces/INativeOrdersEvents.sol'),
    Event('clipper', 'Swapped', (('inAsset', 'address', False), ('outAsset', 'address', False), ('recipient', 'address', False), ('inAmount', 'uint256', False), ('outAmount', 'uint256', False), ('auxiliaryData', 'bytes', False)), 'https://github.com/shipyard-software/clipper-dex-contracts/blob/main/contracts/ClipperExchangeInterface.sol'),
    Event('clipper', 'Swapped', (('inAsset', 'address', True), ('outAsset', 'address', True), ('recipient', 'address', True), ('inAmount', 'uint256', False), ('outAmount', 'uint256', False), ('auxiliaryData', 'bytes', False)), 'https://sourcify.dev/server/v2/contract/1/0x655edce464cc797526600a462a8154650eee4b77?fields=abi'),
    Event('infinity_cl', 'Initialize', (('id', 'bytes32', True), ('currency0', 'address', True), ('currency1', 'address', True), ('hooks', 'address', False), ('fee', 'uint24', False), ('parameters', 'bytes32', False), ('sqrtPriceX96', 'uint160', False), ('tick', 'int24', False)), 'https://github.com/pancakeswap/infinity-core/blob/main/src/pool-cl/interfaces/ICLPoolManager.sol'),
    Event('infinity_cl', 'Swap', (('id', 'bytes32', True), ('sender', 'address', True), ('amount0', 'int128', False), ('amount1', 'int128', False), ('sqrtPriceX96', 'uint160', False), ('liquidity', 'uint128', False), ('tick', 'int24', False), ('fee', 'uint24', False), ('protocolFee', 'uint16', False)), 'https://github.com/pancakeswap/infinity-core/blob/main/src/pool-cl/interfaces/ICLPoolManager.sol'),
    Event('infinity_bin', 'Initialize', (('id', 'bytes32', True), ('currency0', 'address', True), ('currency1', 'address', True), ('hooks', 'address', False), ('fee', 'uint24', False), ('parameters', 'bytes32', False), ('activeId', 'uint24', False)), 'https://github.com/pancakeswap/infinity-core/blob/main/src/pool-bin/interfaces/IBinPoolManager.sol'),
    Event('infinity_bin', 'Swap', (('id', 'bytes32', True), ('sender', 'address', True), ('amount0', 'int128', False), ('amount1', 'int128', False), ('activeId', 'uint24', False), ('fee', 'uint24', False), ('protocolFee', 'uint16', False)), 'https://github.com/pancakeswap/infinity-core/blob/main/src/pool-bin/interfaces/IBinPoolManager.sol'),
    Event('v1', 'TokenPurchase', (('buyer', 'address', True), ('eth_sold', 'uint256', True), ('tokens_bought', 'uint256', True)), 'https://github.com/Uniswap/v1-contracts/blob/master/contracts/uniswap_exchange.vy'),
    Event('v1', 'EthPurchase', (('buyer', 'address', True), ('tokens_sold', 'uint256', True), ('eth_bought', 'uint256', True)), 'https://github.com/Uniswap/v1-contracts/blob/master/contracts/uniswap_exchange.vy'),
    Event('weth', 'Deposit', (('dst', 'address', True), ('wad', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/weth.abi.json'),
    Event('weth', 'Withdrawal', (('src', 'address', True), ('wad', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/weth.abi.json'),
]

FACTORIES = {1: {'0x5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f': {'name': 'UniswapV2', 'family': 'v2', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/uniswap-v2/config.ts'}, '0x9deb29c9a4c7a88a3c0257393b7f3335338d9a9d': {'name': 'DefiSwap', 'family': 'v2', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/uniswap-v2/config.ts'}, '0x1097053fd2ea711dad45caccc45eff7548fcb362': {'name': 'PancakeSwapV2', 'family': 'v2', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/uniswap-v2/config.ts'}, '0xc0aee478e3658e2610c5f7a4a2e1777ce9e4f2ac': {'name': 'SushiSwap', 'family': 'v2', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/uniswap-v2/config.ts'}, '0x115934131916c8b277dd010ee02de363c09d037c': {'name': 'ShibaSwap', 'family': 'v2', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/uniswap-v2/config.ts'}, '0xee3e9e46e34a27dc755a63e2849c9913ee1a06e2': {'name': 'Verse', 'family': 'v2', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/uniswap-v2/config.ts'}, '0x1f98431c8ad98523631ae4a59f267346ea31f984': {'name': 'UniswapV3', 'family': 'v3', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/uniswap-v3/config.ts'}, '0xbaceb8ec6b9355dfc0269c18bac9d6e2bdc29c4f': {'name': 'SushiSwapV3', 'family': 'v3', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/uniswap-v3/config.ts'}, '0x70fe4a44ea505cfa3a57b95cf2862d4fd5f0f687': {'name': 'SolidlyV3', 'family': 'v3', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/solidly-v3/config.ts'}, '0x44b7fbd4d87149efa5347c451e74b9fd18e89c55': {'name': 'Supernova', 'family': 'algebra_integral', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/algebra-integral/config.ts'}, '0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865': {'name': 'PancakeswapV3', 'family': 'pancake_v3', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/pancakeswap-v3/config.ts'}}, 8453: {'0x8909dc15e40173ff4699343b6eb8132c65e18ec6': {'name': 'UniswapV2', 'family': 'v2', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/uniswap-v2/config.ts'}, '0x3e84d913803b02a4a7f027165e8ca42c14c0fde7': {'name': 'Alien', 'family': 'v2', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/uniswap-v2/config.ts'}, '0x33128a8fc17869897dce68ed026d694621f6fdfd': {'name': 'UniswapV3', 'family': 'v3', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/uniswap-v3/config.ts'}, '0xc35dadb65012ec5796536bd9864ed8773abc74c4': {'name': 'SushiSwapV3', 'family': 'v3', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/uniswap-v3/config.ts'}, '0x0fd83557b2be93617c9c1c1b6fd549401c74558c': {'name': 'AlienBaseV3', 'family': 'v3', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/uniswap-v3/config.ts'}, '0x5e7bb104d84c7cb9b682aac2f3d509f5f406809a': {'name': 'AerodromeSlipstream', 'family': 'v3', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/uniswap-v3/config.ts'}, '0xade65c38cd4849adba595a4323a8c7ddfe89716a': {'name': 'AerodromeSlipstreamNewFactory', 'family': 'v3', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/uniswap-v3/config.ts'}, '0xf8f2eb4940cfe7d13603dddd87f123820fc061ef': {'name': 'AerodromeSlipstreamFactory3', 'family': 'v3', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/uniswap-v3/config.ts'}, '0x420dd381b31aef6683db6b902084cb0ffece40da': {'name': 'Aerodrome', 'family': 'solidly', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/solidly/config.ts'}, '0xed8db60acc29e14bc867a497d94ca6e3ceb5ec04': {'name': 'Equalizer', 'family': 'solidly', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/solidly/config.ts'}, '0x70fe4a44ea505cfa3a57b95cf2862d4fd5f0f687': {'name': 'SolidlyV3', 'family': 'v3', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/solidly-v3/config.ts'}, '0xc5396866754799b9720125b104ae01d935ab9c7b': {'name': 'QuickSwapV4', 'family': 'algebra_integral', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/algebra-integral/config.ts'}, '0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865': {'name': 'PancakeswapV3', 'family': 'pancake_v3', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/pancakeswap-v3/config.ts'}, '0xb5620f90e803c7f957a9ef351b8db3c746021bea': {'name': 'SwapBasedV3', 'family': 'pancake_v3', 'source': 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/dex/pancakeswap-v3/config.ts'}}}

# Singleton deployments from the official integration/deployment references.
EMITTERS = {
    1: {
        '0xbbcb91440523216e2b87052a99f69c604a7b6e00': ('Fluid Dex Lite', 'fluid_lite'),
        '0x5d1a34369686ae59ac97ae4e1df5635ffda9ee7c': ('Native RFQ', 'native_rfq'),
        '0xf6e72db5454dd049d0788e411b06cfaf16853042': ('Maker/Sky Lite PSM', 'maker_psm'),
        '0x89b78cfa322f6c5de0abceecab66aee45393cc5a': ('Maker PSM USDC', 'maker_psm'),
        '0xae8999da4d81cb81b42288b12176fe20d7ead578': ('Revert V3Utils', 'v3utils_summary'),
        '0x410b72ccfeacbb20a31785edaa358d25a07e143d': ('Revert V3Utils', 'v3utils_summary'),
        '0x20f6ee51340adeed01a59b0e65cb3703f3dc860c': ('DexAggregator', 'dexaggregator_summary'),
        '0x000000000004444c5dc75cb358380d2e3de08a90': ('Uniswap v4', 'v4'),
        '0xba12222222228d8ba445958a75a0704d566bf2c8': ('Balancer v2', 'balancer_v2'),
        '0xba1333333333a1ba1108e8412f11850a5c319ba9': ('Balancer v3', 'balancer_v3'),
        '0x9008d19f58aabd9ed0d60971565aa8510560ab41': ('CoW Protocol', 'cow'),
        '0xdef1c0ded9bec7f1a1670819833240f027b25eff': ('0x Exchange Proxy', 'zeroex'),
        '0xe92b586627cca7a83dc919cc7127196d70f55a06': ('Velora/ParaSwap RFQ', 'paraswap_rfq'),
        '0xe0e0e08a6a4b9dc7bd67bcb7aade5cf48157d444': ('Ekubo v1', 'ekubo'),
        '0x00000000000014aa86c5d3c41765bb24e11bd701': ('Ekubo v3', 'ekubo'),
        '0x111111125421ca6dc452d289314280a0f8842a65': ('1inch Limit Order Protocol', 'oneinch_limit'),
        '0x5979458912f80b96d30d4220af8e2e4925a33320': ('Fermi', 'propamm'),
    },
    8453: {
        '0x20f6ee51340adeed01a59b0e65cb3703f3dc860c': ('DexAggregator', 'dexaggregator_summary'),
        '0x498581ff718922c3f8e6a244956af099b2652b2b': ('Uniswap v4', 'v4'),
        '0xba12222222228d8ba445958a75a0704d566bf2c8': ('Balancer v2', 'balancer_v2'),
        '0xba1333333333a1ba1108e8412f11850a5c319ba9': ('Balancer v3', 'balancer_v3'),
        '0x5520385bfcf07ec87c4c53a7d8d65595dff69fa4': ('WOOFi v2', 'woo'),
        '0xdef1c0ded9bec7f1a1670819833240f027b25eff': ('0x Exchange Proxy', 'zeroex'),
        '0x9008d19f58aabd9ed0d60971565aa8510560ab41': ('CoW Protocol', 'cow'),
        '0x111111125421ca6dc452d289314280a0f8842a65': ('1inch Limit Order Protocol', 'oneinch_limit'),
        '0x55555522005bcae1c2424d474bfd5ed477749e3e': ('Tessera', 'tessera'),
        '0xf0f0f0f0fb0d738452efd03a28e8be14c76d5f73': ('ElfomoFi', 'elfomofi'),
        '0x5d2806b9af9e9f740b55eda02a8484af5805803a': ('Manta AMM v2', 'propamm'),
    },
}

# Additional verified event schemas.
TRON_SOURCES = {
    'events': 'https://github.com/sun-protocol/transactionAnalysis/blob/master/Events_For_Liquidity_And_Exchange.md',
    'v1': 'https://docs.sun.io/protocols/sunswap-v1/reference/contract/',
    'v2': 'https://github.com/sunswapteam/sunswap2.0-contracts',
    'v3': 'https://docs.sun.io/protocols/sunswap-v3/reference/contract/',
    'curve': 'https://docs.sun.io/protocols/suncurve/reference/contract/',
    'metadata': 'https://developers.tron.network/reference/eth_call',
}
FACTORIES[TRON_CHAIN_ID] = {
    tron_address_hex(a): {'name': name, 'family': family, 'source': TRON_SOURCES[source]}
    for a, name, family, source in (
        ('TXk8rQSAvPvBBNtqSoY6nCfsXWCSSpTVQF', 'SunSwapV1', 'tron_v1', 'v1'),
        ('TKWJdrQkqHisa1X8HUdHEfREvTzw4pMAaY', 'SunSwapV2', 'v2', 'v2'),
        ('TThJt8zaJzJMhCEScH7zWKnp5buVZqys9x', 'SunSwapV3', 'v3', 'v3'),
    )
}
# Current and legacy pools listed by SUN's contract/event documentation.
EMITTERS[TRON_CHAIN_ID] = {tron_address_hex(a): ('SunCurve', 'curve') for a in (
    'THJCvwFmu6uDqKY59JbULHDgfddy2Zojty', 'TNTfaTpkdd4AQDeqr8SGG7tgdkdjdhbP5c',
    'TKcEU8ekq2ZoFzLSGFYCUY6aocJBX9X31b', 'TAUGwRhmCP518Bm4VBqv7hDun9fg8kYjC4',
    'TS8d3ZrSxiGZkqhJqMzFKHEC1pjaowFMBJ', 'TE7SB1v9vRbYRe5aJMWQWp9yfE2k9hnn3s',
    'TKBqNLyGJRQbpuMhaT49qG7adcxxmFaVxd', 'TLssvTsY4YZeDPwemQvUzLdoqhFCbVxDGo',
    'TExeaZuD5YPi747PN5yEwk3Ro9eT2jJfB6', 'TGG5AWMNjssDtLgsHg2QSN8CTwVTCHQMF6',
    'TNU9LfegfzLcJo2ZxTQXDYE2uh7JuxZfnP', 'TSbahrnT5sJwjCzN6LPa1pE5d9pdVwRQ1E',
    'TLZacPrPKfrfbsimu5dDdrgMT16m9cnpL9', 'TKVsYedAY23WFchBniU7kcx1ybJnmRSbGt',
    'TQx6CdLHqjwVmJ45ecRzodKfVumAsdoRXH',
)}
EVENTS += [
    Event('tron_v1', 'TokenPurchase', (('buyer', 'address', True), ('trx_sold', 'uint256', True), ('tokens_bought', 'uint256', True)), TRON_SOURCES['events']),
    Event('tron_v1', 'TrxPurchase', (('buyer', 'address', True), ('tokens_sold', 'uint256', True), ('trx_bought', 'uint256', True)), TRON_SOURCES['events']),
]

EVENTS += [
    Event('curve', 'TokenExchange', (('buyer', 'address', True), ('sold_id', 'uint256', False), ('tokens_sold', 'uint256', False), ('bought_id', 'uint256', False), ('tokens_bought', 'uint256', False), ('fee', 'uint256', False), ('packed_price_scale', 'uint256', False)), 'https://sourcify.dev/server/v2/contract/1/0x4ebdf703948ddcea3b11f675b4d1fba9d2414a14?fields=abi'),
    Event('elfomofi', 'ElfomoTrade', (('quoteId', 'uint256', True), ('partnerId', 'uint256', True), ('executor', 'address', False), ('receiver', 'address', False), ('fromToken', 'address', False), ('toToken', 'address', False), ('fromAmount', 'uint256', False), ('toAmount', 'uint256', False)), 'https://sourcify.dev/server/v2/contract/8453/0xf0f0f0f0fb0d738452efd03a28e8be14c76d5f73?fields=abi'),
    Event('kyber_summary', 'Swapped', (('sender', 'address', False), ('srcToken', 'address', False), ('dstToken', 'address', False), ('dstReceiver', 'address', False), ('spentAmount', 'uint256', False), ('returnAmount', 'uint256', False)), 'https://sourcify.dev/server/v2/contract/1/0x6131b5fae19ea4f9d964eac0408e4408b66337b5?fields=abi'),
    Event('rfq_order', 'OrderFilledRFQ', (('orderHash', 'bytes32', False), ('maker', 'address', True), ('taker', 'address', True), ('makerAsset', 'address', False), ('takerAsset', 'address', False), ('makingAmount', 'uint256', False), ('takingAmount', 'uint256', False)), 'https://sourcify.dev/server/v2/contract/1/0x7a819fa46734a49d0112796f9377e024c350fb26?fields=abi'),
    Event('rfq_extended', 'OrderFilledRFQ', (('rfqId', 'uint256', True), ('expiry', 'uint256', False), ('makerAsset', 'address', True), ('takerAsset', 'address', True), ('makerAddress', 'address', False), ('expectedMakerAmount', 'uint256', False), ('expectedTakerAmount', 'uint256', False), ('filledMakerAmount', 'uint256', False), ('filledTakerAmount', 'uint256', False), ('usePermit2', 'bool', False), ('permit2Signature', 'bytes', False), ('permit2Witness', 'bytes32', False), ('permit2WitnessType', 'string', False)), 'https://sourcify.dev/server/v2/contract/8453/0x4efbd630205dd9b987c3bcbee257600abc1e3c11?fields=abi'),
    Event('balancer_buffer', 'Unwrap', (('wrappedToken', 'address', True), ('burnedShares', 'uint256', False), ('withdrawnUnderlying', 'uint256', False), ('bufferBalances', 'bytes32', False)), 'https://sourcify.dev/server/v2/contract/8453/0xba1333333333a1ba1108e8412f11850a5c319ba9?fields=abi'),
    Event('balancer_buffer', 'Wrap', (('wrappedToken', 'address', True), ('depositedUnderlying', 'uint256', False), ('mintedShares', 'uint256', False), ('bufferBalances', 'bytes32', False)), 'https://sourcify.dev/server/v2/contract/8453/0xba1333333333a1ba1108e8412f11850a5c319ba9?fields=abi'),
    Event('v4_hook_summary', 'HookSwap', (('poolId', 'bytes32', True), ('sender', 'address', True), ('amount0', 'int256', False), ('amount1', 'int256', False), ('swapFee', 'uint24', False)), 'https://sourcify.dev/server/v2/contract/1/0x958942af77dcd973b815b2a16bd88a5134c46888?fields=abi'),
    Event('oneinch_limit', 'OrderFilled', (('orderHash', 'bytes32', False), ('remainingAmount', 'uint256', False)), 'https://sourcify.dev/server/v2/contract/1/0x111111125421ca6dc452d289314280a0f8842a65?fields=abi'),
    Event('tessera', 'TesseraTrade', (('tokenIn', 'address', False), ('tokenOut', 'address', False), ('amountIn', 'uint256', False), ('amountOut', 'uint256', False), ('recipient', 'address', False)), 'https://sourcify.dev/server/v2/contract/8453/0x55555522005bcae1c2424d474bfd5ed477749e3e?fields=abi'),
    Event('hashflow', 'Trade', (('trader', 'address', False), ('effectiveTrader', 'address', False), ('txid', 'bytes32', False), ('baseToken', 'address', False), ('quoteToken', 'address', False), ('baseTokenAmount', 'uint256', False), ('quoteTokenAmount', 'uint256', False)), 'https://github.com/hashflownetwork/x-protocol/blob/main/evm/contracts/interfaces/IHashflowPool.sol'),
    Event('carbon', 'TokensTraded', (('trader', 'address', True), ('sourceToken', 'address', True), ('targetToken', 'address', True), ('sourceAmount', 'uint256', False), ('targetAmount', 'uint256', False), ('tradingFeeAmount', 'uint128', False), ('byTargetAmount', 'bool', False)), 'https://github.com/bancorprotocol/carbon-contracts/blob/dev/contracts/carbon/Strategies.sol'),
    Event('bancor_v3', 'TokensTraded', (('contextId', 'bytes32', True), ('sourceToken', 'address', True), ('targetToken', 'address', True), ('sourceAmount', 'uint256', False), ('targetAmount', 'uint256', False), ('bntAmount', 'uint256', False), ('targetFeeAmount', 'uint256', False), ('bntFeeAmount', 'uint256', False), ('trader', 'address', False)), 'https://github.com/bancorprotocol/contracts-v3/blob/master/contracts/network/BancorNetwork.sol'),
    Event('rubicon', 'LogTake', (('id', 'bytes32', False), ('pair', 'bytes32', True), ('maker', 'address', True), ('pay_gem', 'address', False), ('buy_gem', 'address', False), ('taker', 'address', True), ('take_amt', 'uint128', False), ('give_amt', 'uint128', False), ('timestamp', 'uint64', False)), 'https://github.com/rubicondefi/rubicon_protocol/blob/master/contracts/RubiconMarket.sol'),
    Event('liquidity_book', 'Swap', (('sender', 'address', True), ('to', 'address', True), ('id', 'uint24', False), ('amountsIn', 'bytes32', False), ('amountsOut', 'bytes32', False), ('volatilityAccumulator', 'uint24', False), ('totalFees', 'bytes32', False), ('protocolFees', 'bytes32', False)), 'https://github.com/lfj-gg/joe-v2/blob/main/src/interfaces/ILBPair.sol'),
]

EVENTS += [
    Event('euler', 'Swap', (('sender', 'address', True), ('amount0In', 'uint256', False), ('amount1In', 'uint256', False), ('amount0Out', 'uint256', False), ('amount1Out', 'uint256', False), ('fee0', 'uint256', False), ('fee1', 'uint256', False), ('reserve0', 'uint112', False), ('reserve1', 'uint112', False), ('to', 'address', True)), 'https://github.com/euler-xyz/euler-swap/blob/master/src/libraries/SwapLib.sol'),
    Event('dfx', 'Trade', (('trader', 'address', True), ('origin', 'address', True), ('target', 'address', True), ('originAmount', 'uint256', False), ('targetAmount', 'uint256', False), ('rawProtocolFee', 'int128', False)), 'https://github.com/dfx-finance/protocol-v2/blob/main/src/Curve.sol'),
]

EVENTS += [
    Event('smardex', 'Swap', (('sender', 'address', True), ('to', 'address', True), ('amount0', 'int256', False), ('amount1', 'int256', False)), 'https://github.com/SmarDex-Dev/smart-contracts/blob/main/contracts/core/interfaces/ISmardexPair.sol'),
    Event('wombat', 'Swap', (('sender', 'address', True), ('fromToken', 'address', False), ('toToken', 'address', False), ('fromAmount', 'uint256', False), ('toAmount', 'uint256', False), ('to', 'address', True)), 'https://github.com/wombat-exchange/v1-core/blob/master/contracts/wombat-core/pool/Pool.sol'),
    Event('mstable', 'Swapped', (('swapper', 'address', True), ('input', 'address', False), ('output', 'address', False), ('outputAmount', 'uint256', False), ('scaledFee', 'uint256', False), ('recipient', 'address', False)), 'https://github.com/mstable/mStable-contracts/blob/master/contracts/masset/Masset.sol'),
    Event('propamm', 'Swapped', (('sender', 'address', True), ('tokenIn', 'address', True), ('tokenOut', 'address', True), ('amountIn', 'uint256', False), ('amountOut', 'uint256', False), ('recipient', 'address', False)), 'https://sourcify.dev/server/v2/contract/1/0x5979458912f80b96d30d4220af8e2e4925a33320?fields=sources,abi'),
]

# WETH Deposit shares its signature with one event in the ERC4626 artifact.
EVENTS += [
    Event('fluid_lite', 'LogSwap', (('swapData', 'uint256', False), ('dexVariables', 'uint256', False)), 'https://github.com/Instadapp/fluid-contracts-public/blob/main/contracts/protocols/dexLite/other/events.sol'),
    Event('native_rfq', 'RFQTrade', (('recipient', 'address', False), ('sellerToken', 'address', False), ('buyerToken', 'address', False), ('sellerTokenAmount', 'uint256', False), ('buyerTokenAmount', 'uint256', False), ('quoteId', 'bytes16', False), ('signer', 'address', False)), 'https://sourcify.dev/server/v2/contract/1/0x5d1a34369686ae59ac97ae4e1df5635ffda9ee7c?fields=compilation,sources'),
    Event('maker_psm', 'BuyGem', (('owner', 'address', True), ('value', 'uint256', False), ('fee', 'uint256', False)), 'https://github.com/makerdao/dss-lite-psm/blob/master/src/DssLitePsm.sol'),
    Event('maker_psm', 'SellGem', (('owner', 'address', True), ('value', 'uint256', False), ('fee', 'uint256', False)), 'https://github.com/makerdao/dss-lite-psm/blob/master/src/DssLitePsm.sol'),
    Event('paraswap_summary', 'BoughtV3', (('uuid', 'bytes16', False), ('partner', 'address', False), ('feePercent', 'uint256', False), ('initiator', 'address', False), ('beneficiary', 'address', True), ('srcToken', 'address', True), ('destToken', 'address', True), ('srcAmount', 'uint256', False), ('receivedAmount', 'uint256', False), ('expectedAmount', 'uint256', False)), 'https://github.com/VeloraDEX/paraswap-dex-lib/blob/master/src/abi/IParaswap.json'),
    # Swap(address,address,uint256,uint256) also describes unrelated protocols.
    # V3Utils is accepted only at its verified deployment, never by topic alone.
    Event('v3utils_summary', 'Swap', (('tokenIn', 'address', True), ('tokenOut', 'address', True), ('amountIn', 'uint256', False), ('amountOut', 'uint256', False)), 'https://sourcify.dev/server/v2/contract/1/0xae8999da4d81cb81b42288b12176fe20d7ead578?fields=compilation,sources'),
    Event('dexaggregator_summary', 'Swap', (('fromAddress', 'address', False), ('toAddress', 'address', False), ('fromAssetAddress', 'address', False), ('toAssetAddress', 'address', False), ('amountIn', 'uint256', False), ('amountOut', 'uint256', False), ('expectedAmountOut', 'uint256', False), ('amountInSurplus', 'uint256', False), ('amountOutSurplus', 'uint256', False), ('consumerId', 'bytes32', False), ('swapFeeAssetAddresses', 'address[]', False), ('swapFeeReceivers', 'address[]', False), ('swapFeeAmounts', 'uint256[]', False)), 'https://sourcify.dev/server/v2/contract/1/0x20f6ee51340adeed01a59b0e65cb3703f3dc860c?fields=compilation,sources'),
]

EVENTS = [e for e in EVENTS if not (e.family == 'erc4626' and len(e.fields) == 2)]
EVENT_BY_TOPIC: dict[str, list[Event]] = {}
for _event in EVENTS:
    EVENT_BY_TOPIC.setdefault(_event.topic, []).append(_event)


def _json_amounts(value: Any) -> Any:
    # Keep event integers exact even in JavaScript JSON consumers.
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_amounts(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_amounts(v) for v in value]
    return value


def successful_calls(trace: dict, path: tuple[int, ...] = ()):
    """A reverted parent invalidates every descendant, even successful children."""
    if not isinstance(trace, dict) or trace.get('error') or trace.get('revertReason'):
        return
    if trace.get('type', '').upper() not in ('DELEGATECALL', 'STATICCALL', 'CALLCODE'):
        yield path, trace
    for i, child in enumerate(trace.get('calls', [])):
        yield from successful_calls(child, path + (i,))


def execution_context_logs(frame: dict):
    if frame.get('error') or frame.get('revertReason'):
        return
    yield from frame.get('logs', [])
    for child in frame.get('calls', []):
        if child.get('type', '').upper() in ('DELEGATECALL', 'CALLCODE'):
            yield from execution_context_logs(child)


def own_log_matches(call: dict, log: dict) -> bool:
    return any(l.get('address', '').lower() == log['address'].lower() and
               [t.lower() for t in l.get('topics', [])] == [t.lower() for t in log.get('topics', [])] and
               l.get('data', '').lower() == log.get('data', '').lower()
               for l in execution_context_logs(call))


def fluid_lite_fill(meta: Metadata, log: dict) -> dict:
    """Resolve packed 9-decimal amounts against successful single/hop calls.

    Scaling the event alone loses input/output dust. The specified amount comes
    from the executed call; the other side must equal its actual return value.
    Hop intermediates use the same integer scaling as the deployed contract.
    """
    manager = address(log['address'])
    if EMITTERS.get(meta.chain_id, {}).get(manager, (None, None))[1] != 'fluid_lite':
        raise DecodeError('Fluid Dex Lite requires a registered emitter')
    single = selector('swapSingle((address,address,bytes32),bool,int256,uint256,address,bool,bytes,bytes)')
    hop = selector('swapHop(address[],(address,address,bytes32)[],int256,uint256[],(address,bool,bytes,bytes))')
    spec = next(e for e in EVENTS if e.family == 'fluid_lite')
    matches = []
    for path, call in successful_calls(meta.trace()):
        data = call.get('input', '').lower()
        if call.get('to', '').lower() != manager or data[:10] not in (single, hop) or not own_log_matches(call, log):
            continue
        if data[:10] == single:
            key, direction, specified, limit, recipient, _, _, _ = abi_decode(
                ['(address,address,bytes32)', 'bool', 'int256', 'uint256', 'address', 'bool', 'bytes', 'bytes'], hex_bytes(data)[4:])
            keys, directions, limits = [key], [direction], [limit]
        else:
            route, keys, specified, limits, transfer = abi_decode(
                ['address[]', '(address,address,bytes32)[]', 'int256', 'uint256[]', '(address,bool,bytes,bytes)'], hex_bytes(data)[4:])
            if not keys or len(route) != len(keys) + 1 or len(limits) != len(keys):
                raise DecodeError('Invalid Fluid Dex Lite hop path')
            directions = []
            for i, key in enumerate(keys):
                if (route[i], route[i + 1]) not in (tuple(key[:2]), tuple(key[:2][::-1])):
                    raise DecodeError('Fluid Dex Lite hop tokens differ from pool key')
                directions.append(route[i] == key[0])
            recipient = transfer[0]
        if not specified:
            raise DecodeError('Fluid Dex Lite specified amount is zero')
        returned = abi_decode(['uint256'], hex_bytes(call.get('output', '0x')))[0]
        logs = [l for l in execution_context_logs(call)
                if l.get('address', '').lower() == manager and l.get('topics', []) == [spec.topic]]
        if len(logs) != len(keys):
            raise DecodeError('Fluid Dex Lite call/log count mismatch')
        if all('index' in l for l in logs):
            logs.sort(key=lambda l: quantity(l['index']))
        elif call.get('calls') and any(c.get('type', '').upper() in ('DELEGATECALL', 'CALLCODE') for c in call['calls']) and len(logs) > 1:
            raise MetadataError('Fluid Dex Lite hop needs ordered trace log indexes')
        order = list(range(len(keys))) if specified > 0 else list(reversed(range(len(keys))))
        current, selected = abs(specified), []
        for i, executed_log in zip(order, logs):
            key, direction = keys[i], directions[i]
            packed = spec.decode(executed_log)
            swap, variables = packed['swapData'], packed['dexVariables']
            key_hash = keccak256(bytes.fromhex(''.join(map(word, key))))
            if swap & ((1 << 64) - 1) != int.from_bytes(key_hash[:8], 'big') or bool((swap >> 64) & 1) != direction:
                raise DecodeError('Fluid Dex Lite event/key/direction mismatch')
            if key[0] >= key[1] or swap >> 185:
                raise DecodeError('Invalid Fluid Dex Lite key or packed event')
            decimals = ((variables >> 126) & 31, (variables >> 131) & 31)
            if any(x < 6 or x > 18 for x in decimals):
                raise DecodeError('Invalid Fluid Dex Lite packed token decimals')
            a, b = key[:2] if direction else key[:2][::-1]
            da, db = decimals if direction else decimals[::-1]
            adjusted_in, adjusted_out = (swap >> 65) & ((1 << 60) - 1), (swap >> 125) & ((1 << 60) - 1)
            if specified > 0:
                ain, aout = current, adjusted_out * 10**db // 10**9
                if ain * 10**9 // 10**da != adjusted_in or aout < limits[i]:
                    raise DecodeError('Fluid Dex Lite exact-input execution/event mismatch')
                current = aout
            else:
                ain, aout = adjusted_in * 10**da // 10**9, current
                if aout * 10**9 // 10**db != adjusted_out or ain > limits[i]:
                    raise DecodeError('Fluid Dex Lite exact-output execution/event mismatch')
                current = ain
            if executed_log.get('data', '').lower() == log.get('data', '').lower():
                selected.append({'inputs': [(a, ain)], 'outputs': [(b, aout)],
                                 'pool_id': '0x' + key_hash.hex(),
                                 'sender': address(call['from']),
                                 'recipient': (address(call['from']) if recipient == ZERO else recipient) if i == len(keys) - 1 else None,
                                 'pool_key': dict(zip(('token0', 'token1', 'salt'), key)),
                                 'trace_address': list(path), 'hop_index': i,
                                 'amount_scope': 'executed_call_and_packed_pool_event_with_return_validation'})
        if current != returned:
            raise DecodeError('Fluid Dex Lite packed amounts differ from executed return value')
        if len(selected) != 1:
            raise DecodeError('Ambiguous Fluid Dex Lite log within call')
        matches.extend(selected)
    if not matches:
        raise MetadataError('Fluid Dex Lite requires a successful matching swap trace')
    if len(matches) != 1:
        raise DecodeError('Ambiguous Fluid Dex Lite execution scope')
    return matches[0]


def successful_logs(call: dict):
    if call.get('error') or call.get('revertReason'):
        return
    yield from call.get('logs', [])
    for child in call.get('calls', []):
        yield from successful_logs(child)


def scoped_input_transfer(meta: Metadata, log: dict, token: str, sender: str) -> tuple[int, list]:
    scopes = [c for _, c in successful_calls(meta.trace()) if
              c.get('to', '').lower() == log['address'].lower() and own_log_matches(c, log)]
    if len(scopes) != 1:
        raise MetadataError('Swap requires one successful trace scope for its transfer evidence')
    transfers = []
    for item in successful_logs(scopes[0]):
        ts = item.get('topics', [])
        if item.get('address', '').lower() != token or len(ts) != 3 or ts[0].lower() != topic('Transfer(address,address,uint256)'):
            continue
        source, dest = (abi_decode(['address'], hex_bytes(s))[0] for s in ts[1:])
        amount = abi_decode(['uint256'], hex_bytes(item['data']))[0]
        if source == sender and dest not in (sender, ZERO) and amount:
            transfers.append({'token': token, 'from': source, 'to': dest, 'amount_raw': str(amount)})
    if len(transfers) != 1:
        raise MetadataError('Input transfer is absent or ambiguous within this swap call')
    return int(transfers[0]['amount_raw']), transfers


def oneinch_fill(meta: Metadata, log: dict, order_hash: str) -> tuple:
    if log['address'].lower() != '0x111111125421ca6dc452d289314280a0f8842a65':
        raise DecodeError('OrderFilled emitter is not the registered 1inch Limit Order Protocol')
    order_type = '(' + ','.join(['uint256'] * 8) + ')'
    layouts = {}
    for name, suffix in (
            ('fillOrder', ['bytes32', 'bytes32', 'uint256', 'uint256']),
            ('fillOrderArgs', ['bytes32', 'bytes32', 'uint256', 'uint256', 'bytes']),
            ('fillContractOrder', ['bytes', 'uint256', 'uint256']),
            ('fillContractOrderArgs', ['bytes', 'uint256', 'uint256', 'bytes'])):
        types = [order_type] + suffix
        layouts[selector(name + '(' + ','.join(types) + ')')] = types
    matches = []
    for path, call in successful_calls(meta.trace()):
        data = call.get('input', '')
        if call.get('to', '').lower() != log['address'].lower() or data[:10] not in layouts or not own_log_matches(call, log):
            continue
        making, taking, filled_hash = abi_decode(['uint256', 'uint256', 'bytes32'], hex_bytes(call.get('output', '0x')))
        if filled_hash != order_hash:
            raise DecodeError('1inch fill return hash differs from its event')
        args = abi_decode(layouts[data[:10]], hex_bytes(data)[4:])
        order = args[0]
        addr = lambda n: '0x' + (n & ((1 << 160) - 1)).to_bytes(20, 'big').hex()
        matches.append((addr(order[4]), addr(order[3]), taking, making, addr(order[1]), call.get('from'), path))
    if len(matches) != 1:
        raise MetadataError('1inch fill requires one successful trace call with this exact event and return amounts')
    return matches[0]


def pair_tokens(meta: Metadata, pool: str, family: str) -> tuple[str, str]:
    if family.startswith('maverick'):
        tokens = meta.call(pool, 'tokenA()'), meta.call(pool, 'tokenB()')
    elif family == 'dodo_v1':
        tokens = meta.call(pool, '_BASE_TOKEN_()'), meta.call(pool, '_QUOTE_TOKEN_()')
    elif family == 'fluid':
        const = meta.call(pool, 'constantsView()', returns=(
            '(uint256,address,address,(address,address,address,address,address),address,address,address,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint256)',))
        tokens = const[5], const[6]
    elif family == 'euler':
        tokens = tuple(meta.call(pool, 'getAssets()', returns=('address', 'address')))
    else:
        try:
            tokens = meta.call(pool, 'token0()'), meta.call(pool, 'token1()')
        except MetadataError:
            if family != 'v2':
                raise
            tokens = tuple(meta.call(pool, 'getAssets()', returns=('address', 'address')))
    if tokens[0] == tokens[1]:
        raise DecodeError('Pool declares the same token for both sides')
    if family != 'fluid' and any(t in (ZERO, NATIVE) for t in tokens):
        raise DecodeError('ERC20 pool declares a native/zero token')
    return tokens


def identify_pool(meta: Metadata, pool: str, family: str, tokens: tuple[str, str] | None) -> dict:
    result = {'protocol': family + '-compatible', 'protocol_verified': False,
              'verification': 'event_schema', 'factory': None}
    known = EMITTERS.get(meta.chain_id, {}).get(pool)
    if known and known[1] == family:
        result.update(protocol=known[0], protocol_verified=True, verification='registered_emitter')
        return result
    if not tokens or family == 'fluid':
        if tokens:
            result['verification'] = 'historical_pool_interface'
        return result
    result['verification'] = 'latest_pool_interface' if meta.chain_id == TRON_CHAIN_ID else 'historical_pool_interface'
    try:
        factory = meta.call(pool, 'factoryAddress()' if family == 'tron_v1' else 'factory()')
    except DecodeError:
        return result
    result['factory'] = factory
    registered = FACTORIES.get(meta.chain_id, {}).get(factory)
    if not registered or (meta.chain_id == TRON_CHAIN_ID and registered['family'] != family):
        return result
    a, b = tokens
    candidates = []
    if family == 'tron_v1':
        candidates.append(('getExchange(address)', (b,)))
    elif family in ('v2', 'solidly', 'euler'):
        candidates.append(('getPair(address,address)', (a, b)))
        if meta.chain_id != TRON_CHAIN_ID:
            try:
                stable = meta.call(pool, 'stable()', returns=('bool',))
                candidates += [(fn, (a, b, int(stable))) for fn in (
                    'getPool(address,address,bool)', 'getPair(address,address,bool)')]
            except DecodeError:
                pass
    elif family in ('v3', 'pancake_v3', 'algebra_integral'):
        if 'Slipstream' in registered['name'] or registered['name'] == 'SolidlyV3':
            try:
                spacing = meta.call(pool, 'tickSpacing()', returns=('int24',))
                candidates.append(('getPool(address,address,int24)', (a, b, spacing)))
            except DecodeError:
                pass
        else:
            try:
                fee = meta.call(pool, 'fee()', returns=('uint24',))
                candidates.append(('getPool(address,address,uint24)', (a, b, fee)))
            except DecodeError:
                pass
        candidates.append(('poolByPair(address,address)', (a, b)))
    resolved = []
    for signature, args in candidates:
        try:
            found = meta.call(factory, signature, args)
            resolved.append(found)
            if found == pool:
                result.update(protocol=registered['name'], protocol_verified=True,
                              verification='registered_factory_membership_latest_state' if meta.chain_id == TRON_CHAIN_ID else 'registered_factory_membership')
                return result
        except DecodeError:
            pass
    if any(found != ZERO for found in resolved):
        raise DecodeError('Emitter is not the pool registered by its claimed factory')
    if resolved:
        result['factory_membership'] = 'not_resolved_by_supported_factory_getters'
    return result


def initialization(meta: Metadata, manager: str, pool_id: str, family: str) -> dict:
    # Singleton pool IDs are hashes, not contract addresses. A successful call
    # to that manager carries the complete pool key; verify its hash before use.
    # This also works for pools without NFT positions or a public pool-key getter.
    if family in ('v4', 'infinity_cl', 'infinity_bin'):
        key_type = '(address,address,uint24,int24,address)' if family == 'v4' else '(address,address,address,address,uint24,bytes32)'
        types = [key_type, '(bool,int256,uint160)', 'bytes']
        if family == 'infinity_bin':
            types = [key_type, 'bool', 'int128', 'bytes']
        signature = 'swap(' + ','.join(types) + ')'
        wanted = selector(signature)
        for _, call in successful_calls(meta.trace()):
            if call.get('to', '').lower() != manager or not call.get('input', '').startswith(wanted):
                continue
            values = abi_decode(types, hex_bytes(call['input'])[4:])[0]
            calculated = '0x' + keccak256(bytes.fromhex(''.join(map(word, values)))).hex()
            if calculated != pool_id:
                continue
            keys = ('currency0', 'currency1', 'fee', 'tickSpacing', 'hooks') if family == 'v4' else ('currency0', 'currency1', 'hooks', 'manager', 'fee', 'parameters')
            return dict(zip(keys, values))
    spec = next(e for e in EVENTS if e.family == family and e.name in ('Initialize', 'PoolInitialized'))
    key = manager + ':' + spec.topic + ':' + pool_id
    if key not in meta.cache['logs']:
        logs = [l for l in meta.bundle['receipt'].get('logs', []) if
                l['address'].lower() == manager and len(l.get('topics', [])) > 1 and
                l['topics'][0].lower() == spec.topic and l['topics'][1].lower() == pool_id]
        if not logs:
            if not meta.rpc:
                raise MetadataError('Pool initialization log is not saved')
            try:
                logs = meta.rpc.call('eth_getLogs', [{'address': manager, 'fromBlock': '0x0',
                    'toBlock': meta.block, 'topics': [spec.topic, pool_id]}])
            except RpcError:
                raise MetadataError('RPC could not retrieve the historical pool initialization log') from None
        meta.cache['logs'][key] = logs
    logs = meta.cache['logs'][key]
    valid = [l for l in logs if not l.get('removed') and l['address'].lower() == manager and
             quantity(l['blockNumber']) <= quantity(meta.block) and
             len(l.get('topics', [])) > 1 and l['topics'][1].lower() == pool_id]
    if len(valid) != 1:
        raise MetadataError('Expected one canonical initialization event for this pool ID')
    data = spec.decode(valid[0])
    if family == 'v4':
        values = [data['currency0'], data['currency1'], data['fee'], data['tickSpacing'], data['hooks']]
    elif family.startswith('infinity'):
        values = [data['currency0'], data['currency1'], data['hooks'], manager, data['fee'], data['parameters']]
    else:
        values = data['poolKey']
    if '0x' + keccak256(bytes.fromhex(''.join(map(word, values)))).hex() != pool_id:
        raise DecodeError('Initialization key does not hash to the swap pool ID')
    return data


def curve_coin(meta: Metadata, pool: str, index: int, underlying: bool) -> str:
    if index < 0 or index > 255:
        raise DecodeError('Invalid Curve coin index')
    if not underlying:
        return meta.first_call(pool, ['coins(uint256)', 'coins(int128)'], (index,))
    try:
        return meta.first_call(pool, ['underlying_coins(uint256)', 'underlying_coins(int128)'], (index,))
    except MetadataError:
        # StableSwap NG metapools store [meta coin, base LP]. Underlying indices
        # 1..n resolve through the declared base pool, never through coins(i).
        base_pool = meta.call(pool, 'base_pool()')
        if base_pool == ZERO:
            raise MetadataError('Underlying Curve coin list is unavailable')
        if index == 0:
            return curve_coin(meta, pool, 0, False)
        return curve_coin(meta, base_pool, index - 1, False)


def signed_legs(tokens: tuple[str, str], amounts: tuple[int, int], caller_delta: bool = False) -> tuple[list, list]:
    amounts = tuple(-a for a in amounts) if caller_delta else amounts
    if not (amounts[0] > 0 > amounts[1] or amounts[1] > 0 > amounts[0]):
        raise DecodeError('Swap event does not have opposite nonzero token deltas')
    return ([(t, a) for t, a in zip(tokens, amounts) if a > 0],
            [(t, -a) for t, a in zip(tokens, amounts) if a < 0])


def decode_event(event: Event, log: dict, meta: Metadata) -> tuple[str, dict] | None:
    family, name, pool = event.family, event.name, address(log['address'])
    if name in ('Initialize', 'PoolInitialized'):
        return None
    d = event.decode(log)
    category, kind = 'swaps', 'amm'
    sender = d.get('sender', d.get('buyer', d.get('trader', d.get('from'))))
    recipient = d.get('recipient', d.get('to', d.get('receiver')))
    tokens, inputs, outputs, pool_id = None, [], [], None
    extra: dict = {}
    if family in ('maker_psm', 'v3utils_summary', 'dexaggregator_summary'):
        if EMITTERS.get(meta.chain_id, {}).get(pool, (None, None))[1] != family:
            raise DecodeError('This event layout requires a registered emitter; topic alone is ambiguous')
    if family in ('v2', 'solidly', 'euler'):
        tokens = pair_tokens(meta, pool, family)
        inputs = [(t, d[f'amount{i}In']) for i, t in enumerate(tokens) if d[f'amount{i}In'] > 0]
        outputs = [(t, d[f'amount{i}Out']) for i, t in enumerate(tokens) if d[f'amount{i}Out'] > 0]
        if len(inputs) != 1 or len(outputs) != 1 or inputs[0][0] == outputs[0][0]:
            # V2 flash swaps can repay in the same asset or both assets. Preserve
            # the original legs without inventing a token-for-token exchange.
            category, kind = 'non_swap_events', 'flash_or_multi_asset_pool_flow'
    elif family in ('v3', 'pancake_v3', 'algebra_integral', 'smardex'):
        tokens = pair_tokens(meta, pool, family)
        inputs, outputs = signed_legs(tokens, (d['amount0'], d['amount1']))
    elif family in ('v4', 'infinity_cl', 'infinity_bin'):
        pool_id = d['id']
        key = initialization(meta, pool, pool_id, family)
        tokens = (key['currency0'], key['currency1'])
        if d['amount0'] == d['amount1'] == 0:
            category, kind = 'non_swap_events', 'zero_pool_delta'
        else:
            inputs, outputs = signed_legs(tokens, (d['amount0'], d['amount1']), caller_delta=True)
        extra['pool_key'] = {k: v for k, v in key.items() if k not in ('sqrtPriceX96', 'tick', 'activeId', 'id')}
        extra['amount_scope'] = 'pool_swap_delta_before_external_hook_adjustments'
    elif family in ('balancer_v1', 'balancer_v2', 'balancer_v3'):
        inputs = [(d['tokenIn'], d.get('amountIn', d.get('tokenAmountIn')))]
        outputs = [(d['tokenOut'], d.get('amountOut', d.get('tokenAmountOut')))]
        if family == 'balancer_v2':
            pool_id = d['poolId']
            extra['vault'] = pool
            resolved = meta.call(pool, 'getPool(bytes32)', (pool_id,), ('address', 'uint8'))
            extra['pool_address'] = resolved[0]
            if resolved[0] != '0x' + pool_id[2:42]:
                raise DecodeError('Balancer vault pool ID/address mismatch')
        elif family == 'balancer_v3':
            extra['vault'], extra['pool_address'] = pool, d['pool']
    elif family == 'curve':
        a = curve_coin(meta, pool, d['sold_id'], name == 'TokenExchangeUnderlying')
        b = curve_coin(meta, pool, d['bought_id'], name == 'TokenExchangeUnderlying')
        inputs, outputs = [(a, d['tokens_sold'])], [(b, d['tokens_bought'])]
        extra['amount_scope'] = 'pool_event; native_vs_wrapped_crypto_settlement_may_require_trace'
    elif family == 'saddle':
        getter = 'getUnderlyingToken(uint8)' if name == 'TokenSwapUnderlying' else 'getToken(uint8)'
        a = meta.call(pool, getter, (d['soldId'],))
        b = meta.call(pool, getter, (d['boughtId'],))
        inputs, outputs = [(a, d['tokensSold'])], [(b, d['tokensBought'])]
    elif family in ('maverick_v1', 'maverick_v2', 'fluid'):
        tokens = pair_tokens(meta, pool, family)
        zero_in = d['tokenAIn'] if family == 'maverick_v1' else d['params'][1] if family == 'maverick_v2' else d['swap0to1']
        a, b = tokens if zero_in else tokens[::-1]
        inputs, outputs = [(a, d['amountIn'])], [(b, d['amountOut'])]
    elif family == 'fluid_lite':
        extra = fluid_lite_fill(meta, log)
        inputs, outputs = extra.pop('inputs'), extra.pop('outputs')
        sender, recipient, pool_id = extra.pop('sender'), extra.pop('recipient'), extra.pop('pool_id')
    elif family in ('woo', 'dodo_v2', 'wombat'):
        inputs, outputs = [(d['fromToken'], d['fromAmount'])], [(d['toToken'], d['toAmount'])]
    elif family == 'dodo_v1':
        tokens = pair_tokens(meta, pool, family)
        if name == 'SellBaseToken':
            inputs, outputs, sender = [(tokens[0], d['payBase'])], [(tokens[1], d['receiveQuote'])], d['seller']
        else:
            inputs, outputs = [(tokens[1], d['payQuote'])], [(tokens[0], d['receiveBase'])]
    elif family == 'tron_v1':
        erc20 = meta.call(pool, 'tokenAddress()')
        if erc20 in (NATIVE, ZERO):
            raise DecodeError('SunSwap V1 pool declares a native/zero token')
        tokens = (NATIVE, erc20)
        if name == 'TokenPurchase':
            inputs, outputs = [(NATIVE, d['trx_sold'])], [(erc20, d['tokens_bought'])]
        else:
            inputs, outputs = [(erc20, d['tokens_sold'])], [(NATIVE, d['trx_bought'])]
    elif family == 'v1':
        erc20 = meta.call(pool, 'tokenAddress()')
        if name == 'TokenPurchase':
            inputs, outputs = [(NATIVE, d['eth_sold'])], [(erc20, d['tokens_bought'])]
        else:
            inputs, outputs = [(erc20, d['tokens_sold'])], [(NATIVE, d['eth_bought'])]
    elif family in ('angle', 'psm3'):
        a, b = (d['tokenIn'], d['tokenOut']) if family == 'angle' else (d['assetIn'], d['assetOut'])
        inputs, outputs = [(a, d['amountIn'])], [(b, d['amountOut'])]
        category, kind = 'conversions', 'token_conversion'
    elif family == 'maker_psm':
        dai = meta.call(pool, 'dai()')
        if pool == '0x89b78cfa322f6c5de0abceecab66aee45393cc5a':
            # Original DssPsm keeps the factor internal; its immutable gemJoin
            # exposes the collateral and the decimals used by the constructor.
            join = meta.call(pool, 'gemJoin()')
            gem = meta.call(join, 'gem()')
            decimals = meta.call(join, 'dec()', returns=('uint256',))
            if not 0 <= decimals <= 18:
                raise DecodeError('Invalid Maker PSM collateral decimals')
            factor = 10**(18 - decimals)
        else:
            gem = meta.call(pool, 'gem()')
            factor = meta.call(pool, 'to18ConversionFactor()', returns=('uint256',))
        if factor not in {10**i for i in range(19)}:
            raise DecodeError('Invalid Lite PSM decimal conversion factor')
        gross = d['value'] * factor
        if name == 'BuyGem':
            inputs, outputs = [(dai, gross + d['fee'])], [(gem, d['value'])]
        else:
            inputs, outputs = [(gem, d['value'])], [(dai, gross - d['fee'])]
        category, kind, recipient = 'conversions', 'peg_stability_conversion', d['owner']
        extra['fee_amount_raw'] = str(d['fee'])
        extra['fee_token'] = dai
        extra['amount_scope'] = 'executed_gem_event_and_dai_fee; uses_immutable_decimal_factor'
    elif family == 'clipper':
        inputs, outputs = [(d['inAsset'], d['inAmount'])], [(d['outAsset'], d['outAmount'])]
        kind = 'rfq_pool'
    elif family == 'native_rfq':
        inputs, outputs = [(d['sellerToken'], d['sellerTokenAmount'])], [(d['buyerToken'], d['buyerTokenAmount'])]
        kind = 'rfq_pool'
        extra['maker'] = d['signer']
        extra['amount_scope'] = 'executed_effective_input_and_adjusted_output; event_normalizes_native_ETH_to_WETH'
    elif family == 'mstable':
        amount, evidence = scoped_input_transfer(meta, log, d['input'], d['swapper'])
        inputs, outputs = [(d['input'], amount)], [(d['output'], d['outputAmount'])]
        sender, recipient = d['swapper'], d['recipient']
        extra['transfers'] = evidence
        extra['amount_scope'] = 'executed_call_input_transfer_and_output_event'
    elif family in ('elfomofi', 'tessera', 'hashflow', 'carbon', 'bancor_v3', 'dfx', 'propamm'):
        names = {
            'elfomofi': ('fromToken', 'toToken', 'fromAmount', 'toAmount'),
            'tessera': ('tokenIn', 'tokenOut', 'amountIn', 'amountOut'),
            'hashflow': ('baseToken', 'quoteToken', 'baseTokenAmount', 'quoteTokenAmount'),
            'carbon': ('sourceToken', 'targetToken', 'sourceAmount', 'targetAmount'),
            'bancor_v3': ('sourceToken', 'targetToken', 'sourceAmount', 'targetAmount'),
            'dfx': ('origin', 'target', 'originAmount', 'targetAmount'),
            'propamm': ('tokenIn', 'tokenOut', 'amountIn', 'amountOut'),
        }[family]
        inputs, outputs = [(d[names[0]], d[names[2]])], [(d[names[1]], d[names[3]])]
        if family == 'hashflow':
            sender, recipient, kind = d['effectiveTrader'], d['trader'], 'rfq_pool'
        elif family in ('elfomofi', 'tessera'):
            kind = 'rfq_pool'
        elif family == 'propamm':
            kind = 'proprietary_amm'
        elif family == 'carbon':
            kind = 'strategy_trade'
        elif family == 'bancor_v3':
            kind = 'network_trade'
            extra['amount_scope'] = 'Bancor network executed trade; BNT intermediate not separately emitted by this event'
    elif family in ('rfq_order', 'rfq_extended', 'oneinch_limit'):
        kind = 'rfq'
        if family == 'rfq_order':
            inputs, outputs = [(d['takerAsset'], d['takingAmount'])], [(d['makerAsset'], d['makingAmount'])]
            sender, recipient, extra['maker'] = d['taker'], d['taker'], d['maker']
        elif family == 'rfq_extended':
            inputs, outputs = [(d['takerAsset'], d['filledTakerAmount'])], [(d['makerAsset'], d['filledMakerAmount'])]
            extra['maker'] = d['makerAddress']
        else:
            a, b, ain, aout, maker, taker, path = oneinch_fill(meta, log, d['orderHash'])
            inputs, outputs = [(a, ain)], [(b, aout)]
            sender, extra['maker'], extra['trace_address'] = taker, maker, list(path)
            extra['amount_scope'] = 'successful_fill_return_values; excludes separate protocol fees'
    elif family == 'rubicon':
        inputs, outputs = [(d['buy_gem'], d['give_amt'])], [(d['pay_gem'], d['take_amt'])]
        sender, recipient, kind = d['taker'], d['taker'], 'order_book_fill'
        extra['maker'] = d['maker']
    elif family == 'liquidity_book':
        tokens = meta.call(pool, 'getTokenX()'), meta.call(pool, 'getTokenY()')
        def unpack(value):
            n = int(value, 16)
            return n & ((1 << 128) - 1), n >> 128
        inputs = [(t, n) for t, n in zip(tokens, unpack(d['amountsIn'])) if n]
        outputs = [(t, n) for t, n in zip(tokens, unpack(d['amountsOut'])) if n]
    elif family == 'balancer_buffer':
        wrapped = d['wrappedToken']
        asset = meta.call(wrapped, 'asset()')
        if name == 'Wrap':
            inputs, outputs = [(asset, d['depositedUnderlying'])], [(wrapped, d['mintedShares'])]
        else:
            inputs, outputs = [(wrapped, d['burnedShares'])], [(asset, d['withdrawnUnderlying'])]
        category, kind = 'conversions', 'vault_buffer_conversion'
    elif family == 'v4_hook_summary':
        manager = next(a for a, v in EMITTERS[meta.chain_id].items() if v[1] == 'v4')
        pool_id = d['poolId']
        key = initialization(meta, manager, pool_id, 'v4')
        if key['hooks'] != pool:
            raise DecodeError('Hook emitter does not match the pool key')
        inputs, outputs = signed_legs((key['currency0'], key['currency1']), (d['amount0'], d['amount1']))
        category, kind = 'route_summaries', 'custom_accounting_hook_summary'
        extra['manager'] = manager
    elif family in ('zeroex', 'paraswap_rfq', 'dexalot'):
        kind = 'rfq'
        if family == 'zeroex':
            a, b, ain, aout = d['takerToken'], d['makerToken'], d['takerTokenFilledAmount'], d['makerTokenFilledAmount']
        elif family == 'paraswap_rfq':
            a, b, ain, aout = d['takerAsset'], d['makerAsset'], d['takerAmount'], d['makerAmount']
        else:
            a, b, ain, aout = d['takerAsset'], d['makerAsset'], d['makerAmountReceived'], d['takerAmountReceived']
        inputs, outputs, sender, recipient = [(a, ain)], [(b, aout)], d['taker'], d['taker']
        extra['maker'] = d['maker']
    elif family == 'cow':
        inputs, outputs = [(d['sellToken'], d['sellAmount'])], [(d['buyToken'], d['buyAmount'])]
        category, kind, sender = 'settlements', 'order_settlement', d['owner']
        extra['fee_amount_raw'] = str(d['feeAmount'])
    elif family == 'erc4626':
        asset = meta.call(pool, 'asset()')
        if name == 'Deposit':
            inputs, outputs = [(asset, d['assets'])], [(pool, d['shares'])]
        else:
            inputs, outputs = [(pool, d['shares'])], [(asset, d['assets'])]
        category, kind = 'conversions', 'vault_share_conversion'
    elif family == 'weth':
        if pool != WETH[meta.chain_id]:
            raise DecodeError('Deposit/Withdrawal emitter is not this chain\'s canonical wrapped native token')
        inputs, outputs = ([(NATIVE, d['wad'])], [(pool, d['wad'])]) if name == 'Deposit' else ([(pool, d['wad'])], [(NATIVE, d['wad'])])
        category, kind = 'conversions', 'wrap' if name == 'Deposit' else 'unwrap'
        if not d['wad']:
            category, kind = 'non_swap_events', 'zero_value_wrap_or_unwrap'
        sender = d.get('src', d.get('dst'))
    elif family == 'paraswap_summary':
        inputs, outputs = [(d['srcToken'], d['srcAmount'])], [(d['destToken'], d['receivedAmount'])]
        category, kind = 'route_summaries', 'aggregator_summary'
        sender, recipient = d['initiator'], d['beneficiary']
    elif family == 'v3utils_summary':
        inputs, outputs = [(d['tokenIn'], d['amountIn'])], [(d['tokenOut'], d['amountOut'])]
        category, kind = 'route_summaries', 'aggregator_summary'
        extra['amount_scope'] = 'V3Utils_router_balance_deltas; may_overlap_underlying_pool_swaps'
    elif family == 'dexaggregator_summary':
        inputs, outputs = [(d['fromAssetAddress'], d['amountIn'])], [(d['toAssetAddress'], d['amountOut'])]
        category, kind = 'route_summaries', 'aggregator_summary'
        sender, recipient = d['fromAddress'], d['toAddress']
        if not (len(d['swapFeeAssetAddresses']) == len(d['swapFeeReceivers']) == len(d['swapFeeAmounts'])):
            raise DecodeError('DexAggregator fee arrays have different lengths')
        extra['amount_scope'] = 'router_spent_input_and_recipient_output_balance_delta'
    elif family == 'kyber_summary':
        inputs, outputs = [(d['srcToken'], d['spentAmount'])], [(d['dstToken'], d['returnAmount'])]
        category, kind, recipient = 'route_summaries', 'aggregator_summary', d['dstReceiver']
    elif family == 'dodo_summary':
        inputs, outputs = [(d['fromToken'], d['fromAmount'])], [(d['toToken'], d['returnAmount'])]
        category, kind = 'route_summaries', 'aggregator_summary'
    elif family == 'curve_summary':
        route = d['route']
        active = [a for a in route if a != ZERO]
        if len(active) < 3:
            raise DecodeError('Curve route has no complete leg')
        inputs, outputs = [(route[0], d['in_amount'])], [(active[-1], d['out_amount'])]
        category, kind = 'route_summaries', 'aggregator_summary'
    else:
        raise DecodeError('No handler for this recognized event family')
    if category != 'non_swap_events' and (not inputs or not outputs or any(n is None or n <= 0 for _, n in inputs + outputs)):
        raise DecodeError('Swap does not contain positive executed amounts on both sides')
    if category not in ('non_swap_events', 'settlements') and len(inputs) == len(outputs) == 1 and inputs[0][0] == outputs[0][0]:
        raise DecodeError('Event exchanges a token for itself')
    identity = identify_pool(meta, pool, family, tokens if family not in ('v4', 'infinity_cl', 'infinity_bin') else None)
    if family == 'weth':
        identity.update(protocol='WTRX' if meta.chain_id == TRON_CHAIN_ID else 'WETH',
                        protocol_verified=True, verification='registered_emitter')
    input_details, output_details = [meta.token(a, n) for a, n in inputs], [meta.token(a, n) for a, n in outputs]
    result = {'log_index': quantity(log['logIndex']), 'event': event.signature,
              'adapter': family, 'venue_type': kind, 'emitter': pool,
              'pool': None if kind in ('rfq', 'order_settlement', 'aggregator_summary') else extra.pop('pool_address', pool),
              'pool_id': pool_id, **identity, 'sender': sender, 'recipient': recipient,
              'inputs': input_details, 'outputs': output_details,
              'input': input_details[0] if len(input_details) == 1 else None,
              'output': output_details[0] if len(output_details) == 1 else None,
              'amount_scope': 'executed_event; may differ from net wallet receipts for taxed tokens',
              'evidence': {'source': 'receipt_log', 'topic0': log['topics'][0],
                           'fields': _json_amounts(d)}, **_json_amounts(extra)}
    return category, result


def decode_ekubo(log: dict, meta: Metadata) -> dict:
    """Ekubo emits a 116-byte LOG0 record; its pool key is in the swap call."""
    manager = address(log['address'])
    known = EMITTERS.get(meta.chain_id, {}).get(manager)
    raw = hex_bytes(log.get('data', '0x'))
    if not known or known[1] != 'ekubo' or log.get('topics') or len(raw) != 116:
        raise DecodeError('Not a registered Ekubo packed swap event')
    v3 = manager == '0x00000000000014aa86c5d3c41765bb24e11bd701'
    wanted = selector('swap_6269342730()') if v3 else selector('swap_611415377((address,address,bytes32),int128,bool,uint96,uint256)')
    pool_id = '0x' + raw[20:52].hex()
    amounts = (int.from_bytes(raw[52:68], 'big', signed=True), int.from_bytes(raw[68:84], 'big', signed=True))
    matches = []
    for path, call in successful_calls(meta.trace()):
        data = call.get('input', '')
        if call.get('to', '').lower() != manager or not data.startswith(wanted) or not own_log_matches(call, log):
            continue
        body = hex_bytes(data)[4:]
        if len(body) < 96 or '0x' + keccak256(body[:96]).hex() != pool_id:
            continue
        a, b, config = abi_decode(['address', 'address', 'bytes32'], body[:96])
        returned = hex_bytes(call.get('output', '0x'))
        if v3:
            if returned != raw[52:116]:
                raise DecodeError('Ekubo packed event differs from executed return values')
        elif tuple(abi_decode(['int128', 'int128'], returned)) != amounts:
            raise DecodeError('Ekubo event differs from executed return deltas')
        matches.append((a, b, config, path))
    if not matches:
        raise MetadataError('Ekubo pool key requires a successful matching swap trace')
    if len({(a, b, c) for a, b, c, _ in matches}) != 1:
        raise DecodeError('Ambiguous Ekubo pool key')
    a, b, config, path = matches[0]
    inputs, outputs = signed_legs((a, b), amounts)
    inp, out = meta.token(*inputs[0]), meta.token(*outputs[0])
    return {'log_index': quantity(log['logIndex']), 'event': 'anonymous Ekubo Swap',
            'adapter': 'ekubo_v3' if v3 else 'ekubo_v1', 'venue_type': 'amm',
            'emitter': manager, 'pool': manager, 'pool_id': pool_id,
            'protocol': known[0], 'protocol_verified': True, 'factory': None,
            'verification': 'registered_emitter_and_trace_key_hash',
            'sender': '0x' + raw[:20].hex(), 'recipient': None,
            'inputs': [inp], 'outputs': [out], 'input': inp, 'output': out,
            'pool_key': {'token0': a, 'token1': b, 'config': config},
            'amount_scope': 'executed_pool_event_and_matching_call_return',
            'evidence': {'source': 'receipt_log_and_call_trace', 'topic0': None,
                         'data': log['data'], 'trace_address': list(path)}}


def fetch_transaction(tx_hash: str, rpc_url: str, chain: str | None = None,
                      timeout: float = 30, trace: bool = False) -> tuple[dict, Rpc]:
    tx_hash = normalize_tx_hash(tx_hash, chain)
    rpc = Rpc(rpc_url, timeout)
    chain_id = quantity(rpc.call('eth_chainId', []))
    if chain_id not in CHAIN_IDS.values():
        raise DecodeError('RPC is not Ethereum, Base, or TRON mainnet')
    if chain and CHAIN_IDS[chain] != chain_id:
        raise DecodeError('RPC chain ID does not match --chain')
    if chain_id == TRON_CHAIN_ID:
        # TRON constant calls can fail transiently with node execution errors.
        # Retry a bounded number of times; never substitute guessed metadata.
        rpc.retry_rpc_codes = {-32000}
    receipt = rpc.call('eth_getTransactionReceipt', [tx_hash])
    if receipt is None:
        raise DecodeError('Receipt not found: transaction is pending, unknown, or on another chain')
    tx = rpc.call('eth_getTransactionByHash', [tx_hash])
    if tx is None or tx.get('hash', '').lower() != tx_hash or receipt.get('transactionHash', '').lower() != tx_hash:
        raise DecodeError('Transaction/receipt hash mismatch')
    if tx.get('blockHash') != receipt.get('blockHash'):
        raise DecodeError('Transaction/receipt block mismatch; retry after the reorganization')
    block = rpc.call('eth_getBlockByNumber', [receipt['blockNumber'], False])
    if not block or block.get('hash') != receipt.get('blockHash'):
        raise DecodeError('Receipt is no longer in the canonical chain')
    bundle = {'chain_id': chain_id, 'transaction': tx, 'receipt': receipt,
              'block': {k: block[k] for k in ('hash', 'number', 'timestamp')},
              'metadata': {'calls': {}, 'logs': {}}}
    if trace:
        try:
            bundle['trace'] = rpc.call('debug_traceTransaction', [tx_hash,
                {'tracer': 'callTracer', 'tracerConfig': {'withLog': True}, 'timeout': '20s'}])
        except RpcError as exc:
            bundle['trace_error'] = str(exc)
    return bundle, rpc


def decode_transaction(bundle: dict, rpc: Rpc | None = None) -> dict:
    """Decode a fetched/saved bundle. With rpc=None this makes no network calls."""
    receipt, tx = bundle['receipt'], bundle['transaction']
    chain_id = quantity(bundle['chain_id'])
    if chain_id not in CHAIN_IDS.values():
        raise DecodeError('Unsupported chain ID')
    if receipt.get('transactionHash', '').lower() != tx.get('hash', '').lower():
        raise DecodeError('Transaction/receipt hash mismatch')
    if not re.fullmatch(r'0x[0-9a-fA-F]{64}', tx.get('hash', '')):
        raise DecodeError('Invalid transaction hash in saved bundle')
    if tx.get('blockHash') != receipt.get('blockHash') or quantity(tx['blockNumber']) != quantity(receipt['blockNumber']):
        raise DecodeError('Transaction/receipt block mismatch')
    block = bundle.get('block')
    if block and (block.get('hash') != receipt['blockHash'] or quantity(block['number']) != quantity(receipt['blockNumber'])):
        raise DecodeError('Saved block does not match the transaction receipt')
    result = {'chain': next(k for k, v in CHAIN_IDS.items() if v == chain_id),
              'chain_id': chain_id, 'tx_hash': tx['hash'],
              'block_number': quantity(receipt['blockNumber']), 'block_hash': receipt['blockHash'],
              'transaction_index': quantity(receipt['transactionIndex']),
              'transaction_sender': tx['from'], 'transaction_to': tx.get('to'),
              'status': 'success' if quantity(receipt.get('status', '0x0')) == 1 else 'failed',
              'swaps': [], 'conversions': [], 'settlements': [], 'route_summaries': [],
              'non_swap_events': [], 'undecoded_events': [], 'unclassified_logs': [],
              'warnings': [], 'coverage': {'all_swaps_guaranteed': False,
              'decoded_swap_events': 0, 'undecoded_recognized_events': 0, 'unclassified_log_count': 0,
              'scope': 'implemented executed event schemas; unknown/eventless contracts may require additional adapters'}}
    if chain_id == TRON_CHAIN_ID:
        result['metadata_scope'] = 'latest_state'
        result['warnings'].append('TRON eth_call uses latest state: token identities, decimals, symbols and factory membership are current-state metadata, not historical snapshots. Raw swap amounts come from the transaction receipt.')
        _add_tron_addresses(result)
    if result['status'] != 'success':
        return result
    meta = Metadata(bundle, rpc)
    standard_topics = {topic('Transfer(address,address,uint256)'), topic('Approval(address,address,uint256)')}
    seen = set()
    for log in sorted(receipt.get('logs', []), key=lambda x: quantity(x['logIndex'])):
        idx = quantity(log['logIndex'])
        if idx in seen:
            raise DecodeError('Duplicate receipt log index')
        seen.add(idx)
        if log.get('removed') or log.get('transactionHash', tx['hash']).lower() != tx['hash'].lower() or log.get('blockHash', receipt['blockHash']) != receipt['blockHash']:
            raise DecodeError('Noncanonical or foreign log in transaction receipt')
        topics = log.get('topics', [])
        raw = {'log_index': idx, 'emitter': log['address'], 'topics': topics, 'data': log.get('data', '0x')}
        specs = EVENT_BY_TOPIC.get(topics[0].lower(), []) if topics else []
        specs = [s for s in specs if (s.family in TRON_FAMILIES if chain_id == TRON_CHAIN_ID else s.family != 'tron_v1')]
        if not topics and EMITTERS.get(chain_id, {}).get(log['address'].lower(), ('', ''))[1] == 'ekubo':
            try:
                result['swaps'].append(decode_ekubo(log, meta))
            except DecodeError as exc:
                result['undecoded_events'].append({**raw, 'possible_adapters': ['ekubo'], 'reasons': [str(exc)]})
            continue
        if specs and all(s.family == 'weth' for s in specs) and log['address'].lower() != WETH[chain_id]:
            # Many unrelated contracts use Deposit(address,uint256). It is not
            # a recognized swap failure merely because WETH has the same ABI.
            result['unclassified_logs'].append(raw)
            continue
        if not specs:
            if not topics or topics[0].lower() not in standard_topics:
                result['unclassified_logs'].append(raw)
            continue
        failures = []
        for spec in specs:
            try:
                decoded = decode_event(spec, log, meta)
                if decoded:
                    category, entry = decoded
                    result[category].append(entry)
                break
            except DecodeError as exc:
                failures.append(str(exc))
        else:
            result['undecoded_events'].append({**raw, 'possible_adapters': [s.family for s in specs],
                                                'reasons': sorted(set(failures))})
    result['coverage'].update(decoded_swap_events=len(result['swaps']),
                              undecoded_recognized_events=len(result['undecoded_events']),
                              unclassified_log_count=len(result['unclassified_logs']))
    if any(not s['protocol_verified'] for s in result['swaps']):
        result['warnings'].append('Some events match supported schemas but their protocol identity is not verified; inspect protocol_verified and verification.')
    if bundle.get('trace_error'):
        result['warnings'].append('Optional transaction trace unavailable: ' + bundle['trace_error'])
    if rpc:
        current = rpc.call('eth_getBlockByNumber', [receipt['blockNumber'], False])
        if not current or current.get('hash') != receipt['blockHash']:
            raise DecodeError('Block reorganized during metadata lookup; retry the transaction')
    if chain_id == TRON_CHAIN_ID:
        _add_tron_addresses(result)
    return result


def protocol_catalog(chain: str | None = None) -> dict:
    tron_only = chain == 'tron'
    result = {'chains': {'tron': TRON_CHAIN_ID} if tron_only else CHAIN_IDS, 'event_schemas': [
        {'adapter': e.family, 'event': e.signature, 'topic0': e.topic, 'source': e.source,
         'indexed_fields': [name for name, _, indexed in e.fields if indexed]}
        for e in EVENTS if e.name not in ('Initialize', 'PoolInitialized') and (not tron_only or e.family in TRON_FAMILIES)],
        'anonymous_schemas': [
            {'adapter': 'ekubo_' + version, 'event': 'anonymous 116-byte packed Swap',
             'requires': 'registered core and successful matching call trace',
             'source': 'https://github.com/EkuboProtocol/evm-contracts'}
            for version in (() if tron_only else ('v1', 'v3'))],
        'factories': {TRON_CHAIN_ID: FACTORIES[TRON_CHAIN_ID]} if tron_only else FACTORIES,
        'registered_emitters': {TRON_CHAIN_ID: EMITTERS[TRON_CHAIN_ID]} if tron_only else EMITTERS,
        'all_swaps_guaranteed': False}
    if tron_only:
        result.update(metadata_scope='latest_state', sources=TRON_SOURCES,
                      wrapped_native_token=WETH[TRON_CHAIN_ID])
    return result


def main(argv: list[str] | None = None, default_chain: str | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tx_hash', nargs='?')
    parser.add_argument('--chain', choices=CHAIN_IDS, default=default_chain)
    parser.add_argument('--rpc-url', help='RPC URL; prefer BASE_RPC_URL / ETHEREUM_RPC_URL / TRON_RPC_URL')
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--from-json', type=Path, help='Decode a previously saved raw bundle offline')
    parser.add_argument('--raw-output', type=Path, help='Save receipt, transaction and decoder metadata')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--trace', action='store_true', help='Request an optional read-only call trace')
    parser.add_argument('--list-protocols', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.list_protocols:
            output, bundle = protocol_catalog(args.chain), None
        elif args.from_json:
            bundle = json.loads(args.from_json.read_text())
            if args.chain and quantity(bundle['chain_id']) != CHAIN_IDS[args.chain]:
                raise DecodeError('Saved bundle does not match --chain')
            if args.tx_hash and normalize_tx_hash(args.tx_hash, args.chain or ('tron' if quantity(bundle['chain_id']) == TRON_CHAIN_ID else None)) != bundle['transaction']['hash'].lower():
                raise DecodeError('Saved bundle does not match the requested transaction hash')
            output = decode_transaction(bundle)
        else:
            if not args.tx_hash:
                parser.error('provide a transaction hash or --from-json')
            env_name = args.chain.upper() + '_RPC_URL' if args.chain else 'EVM_RPC_URL'
            url = args.rpc_url or os.environ.get(env_name)
            if not url and args.chain != 'tron':
                url = os.environ.get('EVM_RPC_URL')
            if not url:
                parser.error('set ' + env_name + ' or pass --rpc-url')
            bundle, rpc = fetch_transaction(args.tx_hash, url, args.chain, args.timeout, args.trace)
            output = decode_transaction(bundle, rpc)
        if args.raw_output and bundle:
            args.raw_output.parent.mkdir(parents=True, exist_ok=True)
            args.raw_output.write_text(json.dumps(bundle, indent=2) + '\n')
        serialized = json.dumps(output, indent=2) + '\n'
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized)
        else:
            sys.stdout.write(serialized)
        return 0
    except (ValueError, RpcError, OSError, KeyError, TypeError) as exc:
        # Do not print arbitrary RPC/server objects or supplied URLs.
        print('Decode failed: ' + (str(exc) if isinstance(exc, (DecodeError, RpcError)) else type(exc).__name__), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
