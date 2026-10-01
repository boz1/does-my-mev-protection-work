# Swap decoders and sandwich summaries

Python 3.10+ and its standard library are sufficient. These are command-line and
importable Python tools. The repository's browser page is a separate static app.

Run commands from this directory:

```sh
cd python
```

## Configure your RPC

**No RPC endpoint or API key is hardcoded.** Configure the chain you want to use:

```sh
export SOLANA_RPC_URL='your Solana RPC URL'
export BASE_RPC_URL='your Base RPC URL'
export ETHEREUM_RPC_URL='your Ethereum RPC URL'
```

Missing configuration produces an error; there is no default public RPC fallback.
The EVM decoder also accepts `EVM_RPC_URL`. EVM and sandwich CLIs accept
`--rpc-url`, and Python fetch/summarize functions accept an explicit RPC argument.
Prefer environment variables to keep credentials out of shell arguments.
No endpoint is written to decoded JSON or saved raw bundles. Calls are read-only.

## Decode one transaction

```sh
python3 solana_decode_swaps.py TRANSACTION_SIGNATURE
python3 base_decode_swaps.py 0xTRANSACTION_HASH
python3 ethereum_decode_swaps.py 0xTRANSACTION_HASH
```

Base and Ethereum share `evm_decode_swaps.py`. Solana uses
`solana_swap_protocols.py` and `solana_registry_protocols.py`; keep those files
beside their respective entry scripts.

`--output FILE` saves the decoded JSON. `--raw-output FILE` also saves the
transaction and decoder metadata for offline replay with `--from-json FILE`.
`--list-protocols` prints the implemented registry without accessing an RPC.

## Summarize a sandwich

The row format is `[front_transactions, back_transactions, victim_transactions]`.
Each group may contain multiple transactions. Single strings are also accepted
for single-transaction groups.

```sh
python3 summarize_sandwich.py sandwiches.jsonl --row 1 --chain solana \
  --output summary.json --raw-output summary.raw.json
python3 summarize_sandwich.py --from-json summary.raw.json --format text
```

Or import it:

```python
from summarize_sandwich import summarize_row, summarize_sandwich

summary = summarize_row(row, chain='solana')
summary = summarize_sandwich(front_tx, back_tx, victim_txs, chain='base')
```

Results include the UTC date, block/slot and transaction positions, decoded swaps
per leg, and `tight`, `within_block`, `cross_block` or `unknown` classification.
Tight means one same-block consecutive sequence of all fronts, then all victims,
then all backs. Interleaved roles and missing data remain explicit.

These decoders cover implemented protocol variants; they do not guarantee every
possible swap. Unsupported activity and missing evidence are retained in output.
The summary describes supplied candidate legs and does not independently prove
economic causality or profitability.

## Offline examples and tests

These commands need no RPC environment variables or network access:

```sh
python3 solana_decode_swaps.py --from-json outputs/solana/example_transaction.json
python3 base_decode_swaps.py --from-json outputs/evm/base_example.raw.json
python3 ethereum_decode_swaps.py --from-json outputs/evm/ethereum_example.raw.json
python3 summarize_sandwich.py --from-json outputs/sandwiches/solana_row_1.raw.json --format text
python3 -m unittest discover -s tests
```

Protocol details and limitations: [Solana](SOLANA_SWAPS.md),
[EVM](EVM_SWAPS.md), [sandwich summaries](SANDWICH_SUMMARIES.md).
`tests/fixtures/` contains the offline transaction and schema fixtures. Curated
examples and coverage reports are in `outputs/`; the full sandwich datasets and
the Solana audit's 1,876 per-transaction records are not bundled.
