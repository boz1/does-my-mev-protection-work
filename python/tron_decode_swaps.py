#!/usr/bin/env python3
"""Decode TRON mainnet swaps using TRON_RPC_URL. See TRON_SWAPS.md.

Accepts a 64-digit txID with or without 0x. Receipt amounts are historical;
contract metadata is read at latest state because java-tron eth_call requires it.
"""
import evm_decode_swaps as evm


def fetch_transaction(tx_hash: str, rpc_url: str, timeout: float = 30):
    return evm.fetch_transaction(tx_hash, rpc_url, 'tron', timeout)


def decode_transaction(bundle: dict, rpc: evm.Rpc | None = None) -> dict:
    if evm.quantity(bundle['chain_id']) != evm.TRON_CHAIN_ID:
        raise evm.DecodeError('Saved bundle is not on TRON mainnet')
    return evm.decode_transaction(bundle, rpc)


def main(argv=None) -> int:
    return evm.main(argv, default_chain='tron')


if __name__ == '__main__':
    raise SystemExit(main())
