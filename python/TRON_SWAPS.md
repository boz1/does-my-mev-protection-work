# Decode TRON swaps

`tron_decode_swaps.py` accepts one TRON mainnet transaction ID, with or without
the `0x` prefix. It shares the receipt/ABI decoder in `evm_decode_swaps.py` and
uses Python 3.10+ with no third-party packages.

```sh
export TRON_RPC_URL='your TRON JSON-RPC endpoint'
python3 tron_decode_swaps.py TRANSACTION_ID
python3 tron_decode_swaps.py TRANSACTION_ID --raw-output tron.raw.json --output swaps.json
python3 tron_decode_swaps.py --from-json tron.raw.json
python3 tron_decode_swaps.py --list-protocols
```

`--rpc-url` overrides the environment variable. There is no built-in endpoint,
and TRON does not fall back to `EVM_RPC_URL`. RPCs are read-only and checked for
mainnet chain ID `0x2b6653dc` (728126428). Use the JSON-RPC endpoint, not a native
`/wallet/` API. Saved bundles contain no RPC URL or credentials.

```python
import os
from tron_decode_swaps import fetch_transaction, decode_transaction

raw, rpc = fetch_transaction(tx_id, os.environ['TRON_RPC_URL'])
decoded = decode_transaction(raw, rpc)
assert decode_transaction(raw) == decoded  # replay offline with saved metadata
```

## Supported events

| Venue / schema | Decoding |
| --- | --- |
| SunSwap V1 | `TokenPurchase` and `TrxPurchase`; factory `getExchange` verification |
| SunSwap V2 | Every pool `Swap` event; `token0` / `token1` and factory `getPair` verification |
| SunSwap V3 | Signed pool deltas; fee-tier `getPool` verification |
| SunCurve | `TokenExchange` / `TokenExchangeUnderlying`; pool coin indices; current and legacy registered pools |
| Compatible V2, V3, Curve and Saddle layouts | Decoded if required token metadata resolves; unknown deployments remain explicitly unverified |
| WTRX | Deposit/withdrawal conversions, separate from swap counts |

Multi-hop transactions produce one swap per pool event in log order. Raw amounts
and formatted amounts are exact strings. TRX has **6 decimals**, and WTRX remains
a distinct TRC20 asset. Hex addresses remain the canonical keys for dataset
matching; `address_base58`, `pool_base58`, `sender_base58` and similar fields add
the familiar `T…` representation. `tron_address_hex()` and
`tron_address_base58()` in the shared decoder validate Base58Check checksums.

Amounts come from executed receipts, not transaction inputs, quotes, dataset
floating-point amounts or slippage limits. Failed transactions have no executed
swaps. Flash repayments in the same token remain non-swap pool flows. A matching
event signature alone does not verify the emitting protocol.

## Metadata limitation

TRON's [`eth_call`](https://developers.tron.network/reference/eth_call) executes
against **latest state**. Numeric historical block tags are unsupported; even
object-form historical references do not select historical state. Therefore:

- Swap raw amounts, block timestamp and transaction index come from the original
  mined receipt/block.
- Token identities, symbols, decimals and factory membership use current state.
  Output declares `metadata_scope: "latest_state"`, saves `block_tag: "latest"`
  with each successful call, and includes a warning in JSON and sandwich text.
- Factory verification is labeled `registered_factory_membership_latest_state`.
  Mutable or upgraded contracts need additional historical evidence. Missing
  coin identities leave events unresolved; missing decimals retain raw amounts.

Transient constant-call server errors get bounded retries. Ethereum and Base
continue using their transaction's historical block for metadata.

## Sandwiches and audits

From a checkout's `decode/` directory:

```sh
python3 summarize_sandwich.py ../tron/s/0.json --chain tron --row 1 --format text
python3 summarize_sandwich.py ../tron/s/0.json --chain tron --row 1 --raw-output sandwich.raw.json
python3 summarize_sandwich.py --from-json sandwich.raw.json --format text

python3 audit_swap_dataset.py ../tron/s --chain tron --state tron-audit.sqlite \
  --max-transactions 100 --report tron-audit.json
```

The API also accepts `summarize_sandwich(front, back, victims, chain='tron')`.
All groups may contain multiple hashes. Date, block classification and spacing
use receipt/block evidence; see [SANDWICH_SUMMARIES.md](SANDWICH_SUMMARIES.md).
An audit started at the repository root selects `tron/s`, not Ethereum's `s`.

Support is limited to these implemented layouts. SunPump bonding-curve trades,
SunSwap V4, TRON-specific PSM/router events, native TRC10 exchange operations,
eventless swaps and new/custom venues are not claimed as supported. Underlying
supported pool swaps inside a router transaction still decode. Unknown logs,
including liquidity `Sync` events, stay visible; `all_swaps_guaranteed` is false.

## Sources

- [SUN event layouts, ABIs and legacy pools](https://github.com/sun-protocol/transactionAnalysis)
- [SunSwap V1 factory](https://docs.sun.io/protocols/sunswap-v1/reference/contract/)
- [SunSwap V2 contracts and factory](https://github.com/sunswapteam/sunswap2.0-contracts)
- [SunSwap V3 deployments](https://docs.sun.io/protocols/sunswap-v3/reference/contract/)
- [SunCurve deployments](https://docs.sun.io/protocols/suncurve/reference/contract/)
- [TRON receipt API](https://developers.tron.network/reference/eth_gettransactionreceipt)
