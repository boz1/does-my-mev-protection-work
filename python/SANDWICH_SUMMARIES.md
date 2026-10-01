# Summarize one sandwich row

`summarize_sandwich.py` accepts a row from the supplied JSONL dataset in this order:

```python
[front_transactions, back_transactions, victim_transactions]
```

All three groups are lists in the actual file. Some rows contain several front
or back transactions, so none of those legs are discarded. Strings are also
accepted for individual front/back hashes. This summarizes a supplied candidate;
it is not a detector or proof that an attack was profitable.

The code supports Solana, Base, Ethereum, and TRON through the existing swap decoders.
Keep their Python modules and `sandwich_dataset.py` beside this script. No new
packages are required. Website JSON records with `f`, `b`, and `V[*].h` fields
and Solana binary parts are also supported, including multiple legs in every group.

## Python usage

```python
import json
from summarize_sandwich import summarize_row, summarize_sandwich, render_text

with open('sandwiches.jsonl') as f:
    row = json.loads(next(f))

summary = summarize_row(row, chain='solana')
print(render_text(summary))

# Equivalent direct call: FRONT, BACK, VICTIMS, in that order.
summary = summarize_sandwich(*row, chain='solana')

# Also accepts strings for single front/back legs:
summary = summarize_sandwich(front_hash, back_hash, victim_hashes, chain='base')
```

Set `SOLANA_RPC_URL`, `BASE_RPC_URL`, `ETHEREUM_RPC_URL`, or `TRON_RPC_URL` to your endpoint before
running. You can instead pass `rpc_url=...`. URLs/credentials are not saved in
the summary or raw bundle. RPC calls are read-only. `timeout=30` and `workers=4`
are configurable; one block-order lookup is shared by all legs in that block.

## Command line

```sh
# One-based physical line number, JSON output by default:
python3 summarize_sandwich.py \
  sandwiches.jsonl \
  --row 1 --chain solana \
  --output summary.json --raw-output summary.raw.json

# Readable output resembling the screenshot:
python3 summarize_sandwich.py --from-json summary.raw.json --format text

# Different chain, individual hashes; repeat values to supply multiple legs:
python3 summarize_sandwich.py --chain ethereum \
  --front 0xFRONT_HASH --back 0xBACK_HASH --victim 0xVICTIM_1 0xVICTIM_2
```

`--row-json '[["FRONT"],["BACK"],["VICTIM"]]'` accepts an inline row instead of
a file. Use exactly one input mode. `--from-json` replays a saved raw sandwich
bundle entirely offline; it is not a single-transaction decoder file. The raw
bundle includes transactions, decoder metadata/traces, block ordering, and any
fetch errors. In Python, use `fetch_sandwich(...)` and `summarize_bundle(bundle)`
to fetch once and repeatedly summarize offline.

## Website JSON and binary input

```sh
# --row is a one-based array position in a website JSON shard:
python3 summarize_sandwich.py ../base/s/0.json --row 1 --chain base
python3 summarize_sandwich.py ../ethereum/s/0.json --row 1 --chain ethereum
python3 summarize_sandwich.py ../tron/s/0.json --row 1 --chain tron
python3 summarize_sandwich.py ../eth_reorg/s/0.json --row 1 --chain ethereum

# --row is a one-based record number inside this Solana part:
python3 summarize_sandwich.py ../solana/part-0001.bin --row 1 --chain solana
```

The filename extension selects the reader; `--input-format jsonl|json|solana-binary`
overrides it. JSONL numbering remains a physical line number. `summarize_row()`
and `--row-json` accept the website object directly. The mapping is:

```python
[record['f'], record['b'], [victim['h'] for victim in record['V']]]
```

A Solana binary part is a sequence of records, with no file header or separator:

| Bytes | Meaning |
| --- | --- |
| 1 | Unsigned front count `F` |
| 1 | Unsigned back count `B` |
| 2 | Unsigned victim count `V`, little-endian |
| `64 × F` | Front signatures |
| `64 × B` | Back signatures |
| `64 × V` | Victim signatures |

Record size is `4 + 64 × (F + B + V)`. One front, one back, three victims occupy
324 bytes. Base58-encode each raw 64-byte signature to obtain a Solana transaction
ID. Records never span parts. Dates, slots, transaction positions and swap details
are fetched from RPC; they are not present in these binary files. The reader streams
one record at a time and rejects empty groups and truncated headers/signatures.

`sandwich_dataset.iter_records(path)` yields rows plus their record numbers and
binary byte offsets. Manifest checksum verification is separate from parsing;
the reader does not claim that a downloaded file matches a manifest automatically.

## Resumable dataset validation

```sh
# A bounded run; repeat the same command to resume:
python3 audit_swap_dataset.py ../base/s --chain base --state base-audit.sqlite \
  --max-transactions 100 --report base-audit.json

# Entire directory, with no per-run transaction limit:
python3 audit_swap_dataset.py ../solana --chain solana --state solana-audit.sqlite \
  --max-transactions 0 --report solana-audit.json

python3 audit_swap_dataset.py ../ethereum/s --chain ethereum --state ethereum-audit.sqlite
python3 audit_swap_dataset.py ../eth_reorg/s --chain ethereum --state ethereum-audit.sqlite
```

The auditor deduplicates transactions by chain and hash, saves record checkpoints
in SQLite, and resumes safely even when a limit stops halfway through a sandwich.
A decoder change invalidates old results. Changed input files require a new state
file. `--retry-incomplete` retries saved fetch failures and unresolved recognized
swaps; `--raw-dir` optionally saves full evidence for offline decoding. Unknown
logs/instructions remain review items. The `record_issues` table also flags website
legs whose stated pool is absent from decoded swaps.

`input_scan_complete` means all supplied records were attempted, including those
with failures. It does **not** mean every swap was decoded. Totals cover all inputs
for the selected chain in the state file; endpoint URLs are never stored. Running
the full population can require millions of RPC requests and substantial time.

TRON transaction IDs may have or omit `0x`. Its receipt/block ordering follows the
same checks as Base and Ethereum, but its contract metadata uses latest state.
See [TRON_SWAPS.md](TRON_SWAPS.md) for supported venues and this limitation.

## Returned information

| Field | Meaning |
| --- | --- |
| `date`, `timestamp_utc` | Earliest front block/slot's UTC date/time; null if unavailable |
| `date_range_utc` | First/last observed dates across all supplied legs |
| `classification` | `tight`, `within_block`, `cross_block`, or `unknown` |
| `block_relation` | `within_block` or `cross_block`, independently of tightness; `unknown` if not established |
| `is_tight` | True/false, or null when necessary ordering data is unavailable |
| `blocks` | Unique blocks, hashes, timestamps and transaction counts; Solana `number` is a slot, with actual `block_height` separate |
| `front_txs`, `victim_txs`, `back_txs` | Every supplied transaction, preserving order within each original group |
| `execution_order` | All leg references sorted by actual block/transaction index; null if any position is missing |
| `strict_role_order` | Whether every front precedes every victim, and every victim precedes every back |
| `victims_enclosed` | Whether all legs fit between an outer front and outer back with victims inside |
| `span` | Precisely defined same-block index distance and transaction counts |
| `bot_candidates` | Shared front/back transaction senders or Solana fee payers; a fee payer can be a relayer |
| `private_submission` | Null: ordinary mined-transaction RPC data does not establish whether submission was private |
| `coverage`, `warnings` | Missing transactions/indices, decoder failures, unresolved recognized swaps, and ordering caveats |

Each transaction includes its hash/signature, block/slot, transaction index,
UTC timestamp, sender or fee payer, success flag, and complete native `swaps`
array from the chain decoder. `decoding` retains the other decoder fields,
including warnings, unsupported activity, EVM conversions/settlements/summaries,
and execution evidence. Raw token amounts remain exact strings.

Classification rules:

- **Tight:** all legs are in the same block, and the complete sorted sequence is
  consecutive fronts → consecutive victims → consecutive backs with no other
  transactions between. With one front/victim/back this is exactly `F+1=V` and
  `V+1=B`. Every victim participates in the check.
- **Within block:** all legs have the same block/slot. If all indices are known
  and it is not tight, the readable label is `within-block wide`.
- **Cross block:** at least two legs have different blocks/slots. Consecutive
  numeric indices in different blocks never make a row tight.
- **Unknown:** available data cannot establish the block relationship. If block
  equality is known but transaction indices are missing, the relationship is
  `within_block` and `is_tight` is null, rather than assuming wide or tight.

Some source datasets reserve `tight` for exactly one front, one victim and one
back. Their labels therefore differ from this summarizer for consecutive multiple
victims. Dates are always computed from RPC timestamps in UTC, even if a source
record's date label differs. Private-order-flow hints remain source evidence and
are not inferred from ordinary transaction RPC data.

Some supplied rows interleave fronts and victims. These remain intact and are
labeled as interleaved; their actual execution order remains visible. Failed
transactions retain their position but have no executed swaps. Classification
describes placement, not whether the sandwich succeeded.

Solana positions come from the ordered signatures returned by
[`getBlock`](https://solana.com/docs/rpc/http/getblock), including vote transactions.
Indices are zero-based and count all transactions. EVM indices come from receipts
and are checked against the block's transaction list when available. Block hash,
index, and membership mismatches are rejected.

For same-block outer indices `F=10`, `B=14`, the index distance is **4**, strictly
between count **3**, and inclusive count **5**. `unlisted_transactions_between`
subtracts all supplied legs from that inclusive span. Cross-block transaction
counts are null: the script does not subtract unrelated per-block indices or
fetch every intervening block. A date range never substitutes for block equality.

## Real examples and validation

The supplied Solana dataset's first row returns:

```text
2023-10-10 · solana · within-block tight
Slot 222669934
FRONT  index 455: 0.158673523 WSOL → 106141040.17138162 DFrJxDoL…uTKcTk
VICTIM index 456: 0.7 WSOL → 457855254.62376151 DFrJxDoL…uTKcTk
BACK   index 457: 106141040.17138162 DFrJxDoL…uTKcTk → 0.163749394 WSOL
```

Live examples are saved under `outputs/sandwiches/` as summary JSON, readable text,
and raw replay bundles. Row 21 spans three Solana slots and includes two victims;
row 479 preserves two fronts with an intervening victim. `base_tight` uses a real
Base sandwich from the existing project dataset at indices 27, 28 and 29.

```sh
python3 -m unittest discover -s tests -p 'test_summarize_sandwich.py'
```

Tests cover multiple fronts/backs/victims, tight and wide spacing, cross-block and
midnight boundaries, interleaved roles, missing data, failed transactions,
receipt/block mismatches, offline replay and a real Solana fixture. Swap coverage
inherits the existing decoders' limitations and is explicitly not guaranteed to
cover every possible protocol or instruction.
