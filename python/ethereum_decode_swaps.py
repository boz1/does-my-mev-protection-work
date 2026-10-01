#!/usr/bin/env python3
"""Decode an Ethereum transaction; reads ETHEREUM_RPC_URL. See EVM_SWAPS.md."""
from evm_decode_swaps import main

if __name__ == '__main__':
    raise SystemExit(main(default_chain='ethereum'))
