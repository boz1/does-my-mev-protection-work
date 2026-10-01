# Decode swaps on Base and Ethereum

Three chain entry scripts are provided:

| Chain | Entry script | RPC environment variable |
| --- | --- | --- |
| Solana | `solana_decode_swaps.py` | `SOLANA_RPC_URL` |
| Base | `base_decode_swaps.py` | `BASE_RPC_URL` |
| Ethereum | `ethereum_decode_swaps.py` | `ETHEREUM_RPC_URL` |

Base and Ethereum share `evm_decode_swaps.py`; keep it beside their entry scripts.
The EVM decoder uses Python 3.10+ and the standard library only. Solana has its
own two adapter modules; see [SOLANA_SWAPS.md](SOLANA_SWAPS.md). All RPC requests
are read-only. No wallet, signing, or additional API subscription is needed.
The supplied RPCs were tested but are not embedded in these files or outputs.

```sh
export BASE_RPC_URL='paste your Base RPC endpoint here'
export ETHEREUM_RPC_URL='paste your Ethereum RPC endpoint here'

python3 base_decode_swaps.py '0xTRANSACTION_HASH'
python3 ethereum_decode_swaps.py '0xTRANSACTION_HASH'

# Alternatively, use the shared entry point:
python3 evm_decode_swaps.py --chain base '0xTRANSACTION_HASH'
```

Run from this directory, or use an absolute script path. Omit `--output` to print
JSON. `--rpc-url` overrides the environment; `EVM_RPC_URL` is a common fallback.
The chain ID is checked before fetching the transaction.

```sh
python3 base_decode_swaps.py \
  0x3c7f8b013f2bf875cc6fec2a85e6e46a0182cc2d9ae74af9d7860166259e9b9e \
  --output swaps.json --raw-output transaction.raw.json

# Replay without a network connection:
python3 base_decode_swaps.py --from-json transaction.raw.json

# Adapter schemas, sources, factories and singleton addresses:
python3 evm_decode_swaps.py --list-protocols
```

Python API:

```python
import os
from evm_decode_swaps import fetch_transaction, decode_transaction

bundle, rpc = fetch_transaction(tx_hash, os.environ['BASE_RPC_URL'], 'base')
result = decode_transaction(bundle, rpc)
swaps = result['swaps']

# bundle now contains the historical metadata and any traces used above.
assert decode_transaction(bundle) == result  # offline replay
```

## Swap boundaries and amounts

Each `swaps` entry represents an executed pool exchange, order-book fill, RFQ
fill, or a venue's executed trade event. Entries are sorted by `log_index`, the
receipt's block-wide log position. A route through three pools normally creates
three entries. Separate calls to the same pool remain separate entries.
A Liquidity Book event can cover multiple bins; Bancor's network event reports
the end-to-end trade. Internal steps absent from those events are not invented.

Entries include the adapter, emitter, pool or singleton `pool_id`, available
sender/recipient, token addresses, amounts, and event evidence. `inputs` and
`outputs` preserve every decoded leg; `input` and `output` are aliases when
there is exactly one on that side. RFQ fills use `pool: null` where appropriate.

Amounts come from executed events. For 1inch limit orders, successful call return
values are matched to the event's order hash, so partial fills use executed
amounts. mStable's event lacks an input amount: its adapter requires one
unambiguous input transfer in that successful call. Neither uses slippage limits,
quotes, or transaction-wide wallet balance changes as executed amounts.

`amount_raw` and decimal `amount` are strings, preserving integers above
JavaScript's safe range. Decimals/symbols come from historical contract calls;
unavailable metadata stays null. Native ETH has `address: null` and
`is_native: true`; WETH retains its ERC-20 address.

Pool amounts can differ from wallet receipts for taxed tokens or separate fees.
Uniswap v4 and Pancake Infinity report pool deltas before external hook
adjustments. Curve crypto events identify pool coins; native versus wrapped
settlement can require further call analysis. `amount_scope` records these
distinctions.

Other output collections stay separate from the swap count:

| Field | Meaning |
| --- | --- |
| `conversions` | WETH, ERC-4626, Balancer buffers, Angle and Spark PSM3 |
| `settlements` | CoW order settlement; can overlap underlying swaps |
| `route_summaries` | Router/routing-hook summaries; can overlap swaps |
| `non_swap_events` | Zero deltas and ambiguous flash/multi-asset pool flows |
| `undecoded_events` | Recognized schemas that could not decode, with reasons |
| `unclassified_logs` | Other logs, except ordinary ERC-20 Transfer/Approval topics |

Unknown logs remain available for inspection; many are unrelated administrative,
fee or liquidity events. Do not sum swaps, summaries and settlements as if each
described a distinct exchange.

`protocol_verified: true` means a registered emitter matched, or a historical
factory lookup confirmed pool membership. Compatible forks can decode under a
generic adapter label with `protocol_verified: false`. An event ABI match alone
does not authenticate a protocol's identity.

## Implemented coverage

The 2026-10-01 snapshot has **42 swap adapter families**, plus 11 conversion,
settlement and summary families: **64 named event schemas and two anonymous
Ekubo formats**. A family may cover many deployments/forks; these are not counts
of all exchanges on either chain.

| Group | Implemented families |
| --- | --- |
| Constant-product / concentrated liquidity | Uniswap v1/v2/v3/v4 and compatible forks, Aerodrome/Solidly, Pancake v3, Infinity CL/Bin, Algebra Integral, SmarDex |
| Stable / multi-asset | Curve variants, Saddle/Nerve-compatible pools, Balancer v1/v2/v3, Fluid, Wombat, mStable, DFX |
| Other pools | Maverick v1/v2, DODO v1/v2, Liquidity Book, EulerSwap, Ekubo v1/v3, WOOFi, Bancor v3, Carbon |
| RFQ / proprietary / order-book | 1inch limit orders, 0x native orders, Velora/ParaSwap RFQ, Hashflow, Clipper, Dexalot, Rubicon, Tessera, ElfomoFi, Fermi/Manta-compatible events, two additional verified RFQ layouts |
| Related execution | CoW settlement; ERC-4626, WETH, Angle, Spark PSM3 and Balancer buffer conversions; ParaSwap, Kyber, DODO, Curve and routing-hook summaries |

The live audit decoded **477 swap events in 151 distinct transactions**:

| Chain | Transactions | Swap events | Unresolved recognized events |
| --- | ---: | ---: | ---: |
| Base | 67 | 230 | 0 |
| Ethereum | 84 | 247 | 0 |

The sample exercised 35 of 42 swap families and 44 of all 53 adapter families.
Clipper, Dexalot, Infinity Bin, mStable, Rubicon, Wombat and 0x native order fills
have schema/semantic tests but no live validation in this sample. Angle
conversions and DODO summaries also lack live sample validation. This is a
targeted sample, not a measurement of recall across all transactions. Zero
unresolved *recognized* events does not mean every possible swap was recognized;
unclassified logs remain in the sample.

EVM has no single authoritative Jupiter-like registry covering every exchange
and version. This implementation uses
[Velora/ParaSwap DexLib](https://github.com/VeloraDEX/paraswap-dex-lib),
[Dune Spellbook](https://github.com/duneanalytics/spellbook), official contracts
and verified Sourcify ABIs as complementary references. It does **not** claim
complete coverage of either external catalog. Public source URLs and snapshot
hashes are in `outputs/evm/registry_sources.json`.

Every result sets `coverage.all_swaps_guaranteed` to `false`. Unknown/new
contracts, eventless swaps, some historical versions, Native RFQ, Metric,
AirSwap and 0x Settler RFQ still require additional adapters. Custom hooks may
require dedicated accounting. Schema compatibility alone cannot guarantee that
an arbitrary contract's events describe real trades.

## RPC requirements and tests

The decoder verifies the mined transaction, receipt and canonical block. Metadata
is read at that transaction's block number, with no fallback to current values.
Providers need historical `eth_call` support for required pool/token getters.

`debug_traceTransaction` with `callTracer` and `withLog: true` is requested lazily
for singleton keys, 1inch fills, Ekubo and mStable; `--trace` requests it upfront.
Both supplied RPCs supported this in testing. Without traces, v4/Infinity can
use saved initialization logs or historical `eth_getLogs`; provider range limits
may prevent that fallback. Other trace-dependent adapters report missing evidence
instead of guessing. `--raw-output` saves metadata/logs/traces for offline replay.

The tests cover malformed ABI, precision, partial fills, repeated pool calls,
multi-hop ordering, reverted ancestors, proxies, factory membership, signature
collisions, chain/reorganization checks, RPC error redaction, and 22 frozen
real-transaction fixtures.

```sh
python3 -m unittest discover -s tests -p 'test_evm_decode_swaps.py'
python3 -m unittest discover -s tests -p 'test_solana*.py'
```

Detailed artifacts:

- `outputs/evm/decoder_coverage_audit.json`
- `outputs/evm/supported_protocols.json`
- `outputs/evm/base_example.swaps.json` and `base_example.raw.json`
- `outputs/evm/ethereum_example.swaps.json` and `ethereum_example.raw.json`
- `tests/fixtures/evm/manifest.json`
