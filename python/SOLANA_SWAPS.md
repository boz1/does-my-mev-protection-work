# Decode swaps in one Solana transaction

`solana_decode_swaps.py` accepts a transaction signature and makes one read-only
`getTransaction` request to `SOLANA_RPC_URL`. Certain order-book fills also need
one batched `getMultipleAccounts` request for immutable market mint/lot settings;
the CLI fetches these only when required. Python 3.10+ is sufficient; no packages
or additional API subscriptions are required. No private keys or signing are used.
Keep `solana_decode_swaps.py`, `solana_swap_protocols.py` and
`solana_registry_protocols.py` in the same directory.

```sh
export SOLANA_RPC_URL='paste your Solana RPC endpoint here'
python3 solana_decode_swaps.py 'TRANSACTION_SIGNATURE'
```

For example, the first front-run signature in the supplied sample is:

```sh
python3 solana_decode_swaps.py \
  '1GuJUnoEY6ce7X5gX6F1uDEKFdHXZA7GLq9cEaacDTSUcPya4U3yB9m1feSg7kPyK1KSNVJGWJyLnEBkRUYLEkj' \
  --output outputs/solana/example_swaps.json \
  --raw-output outputs/solana/example_transaction.json
```

Run from the project directory. Omit `--output` to print JSON. The RPC URL is read
from the environment and is not written into the outputs. Reuse a saved response
without another request:

```sh
python3 solana_decode_swaps.py --from-json outputs/solana/example_transaction.json
python3 solana_decode_swaps.py --list-protocols
python3 -m unittest discover -s tests -p 'test_solana*.py'
```

In Python:

```python
import os
from solana_decode_swaps import fetch_transaction, decode_transaction

tx = fetch_transaction(signature, os.environ['SOLANA_RPC_URL'])
swaps = decode_transaction(tx)['swaps']
```

`decode_transaction` is an offline function. It accepts an optional
`market_accounts` dictionary for order-book fills that use deposited funds.
The CLI loads missing market headers automatically in RPC mode. `--raw-output`
embeds fetched headers under `_swap_decoder_market_accounts`, so the saved file
can be decoded offline. Alternatively, supply a JSON mapping of market addresses
to RPC account objects (`owner`, base64 `data`) with `--market-accounts FILE`.
Only mints, decimals, vault addresses and lot sizes are read from those headers;
current balances and order-book prices never determine historical swap amounts.
Closed/unavailable markets require a saved header for the same market instance.

## What the result means

Each `swaps` entry is one pool leg, order-book fill, RFQ fill, or explicitly
identified token conversion, in instruction execution order. It
includes the DEX program, pool or bonding curve address, trader authority, input
and output token mint addresses, exact amounts, and supporting token transfers.
Token symbols other than SOL/WSOL are left null: standard RPC data does not supply
reliable token names. Native SOL has `mint: null`; WSOL retains its actual mint.
Jupiter RFQ fills have `venue_type: "rfq"`, `pool: null` and a `fill_authority`;
the counterparty is not presented as a liquidity pool. M Swap and Perena Star
share exchanges have `venue_type: "token_conversion"` or `"share_conversion"`
and include executed `token_actions` (burn/mint). These are distinguishable
from AMM swaps. WOOFi additionally lists both participating `pool_addresses`.
Other vault, lending, wrapper and staking exchanges use `share_conversion`,
`token_conversion` or `staking_conversion`. BisonFi Predict uses
`prediction_market`. Aquifer exchanges between two asset vaults expose both
`pool_addresses` and leave the single `pool` field null. Flint also exposes both
asset pool addresses. Scorch pricing CPIs appear in `non_swap_instructions`, linked
to the parent execution location; they do not create additional swaps.

`outer_instruction_index` is the zero-based position in the transaction's message.
`inner_instruction_index` is the zero-based position in that outer instruction's
`meta.innerInstructions` list (null for a top-level swap). These positions identify
the internal cuts between swaps. `parent_programs` shows containing routers or
other callers. A Jupiter route containing two supported pool calls produces two
swap entries; the route itself is not counted as an additional swap.
Whirlpool's and DeFiTuna's dedicated two-hop instructions produce two entries with the same
instruction position and different `hop_index` values, 0 and 1. Its v2 instruction
moves the intermediate token directly between the pool vaults; that transfer is
evidence for both adjacent legs, not an extra swap. For a single-pool call,
`hop_index` is 0. Separate calls to the same pool remain separate entries.
Order-book batches can contain several filled orders: each receives a distinct
`hop_index`, with Manifest `order_sequence` or OpenBook `order_event_index` attached.
Several makers filling one Manifest taker order are grouped into one entry with
individual `fills` and a `fill_count`.

Amounts come from executed token transfers, checked bonding-curve trade events,
order-book fill events, or actual user-token burns and mints for the named
conversion instructions. They are not instruction limits, slippage
thresholds, wallet balance changes, or a profit calculation. All raw token amounts
and decimal token amounts are JSON strings to preserve precision.

- Token-pool amounts are gross transfers between the user's token accounts and
  the pool vaults, net of refunds in the same invocation. Multiple transfers of
  the same asset are summed within that invocation. Fees paid to other accounts
  are excluded. If a fee recipient aliases a declared user token account, its
  payment is included in that account's net boundary flow; this can occur when a
  Scale trader is also the creator. For example, a DAMM v1 swap with
  a 1 SOL wallet debit and a separate 0.003 SOL referral fee reports 0.997 SOL
  entering the vault. This definition is not total wallet spend.
- DAMM v1, Stabble and Jupiter Lend AMM transfers can pass through their vault/liquidity programs. Only the
  instruction's known user/vault account pairs and permitted vault program paths
  contribute amounts; LP-token minting, unrelated CPIs and fee recipients do not.
- Pump.fun amounts are the trade-event exchange amounts before separately charged
  fees. Available event fees are reported separately. Older events lack fee fields;
  null means unavailable, not zero. These amounts are not total wallet spend or
  net wallet receipts.
- Moonit/Moonshot amounts come from its matching TradeEvent and executed base
  token transfer. The SOL amount is the curve exchange amount; DEX and Helio fees
  are separate fields. The event uses the WSOL mint as the currency identifier
  even when the curve actually exchanges native lamports, which are reported as SOL.
- Boop.fun events are checked against both the token transfer and the native SOL
  exchange/fee transfers. Buy SOL excludes the separately paid fee; sell SOL is
  the net amount paid to the recipient. `swap_fee` is reported separately.
- Heaven pool creation can include an initial purchase. Its entry uses only the
  executed SOL payment and delivered purchase tokens; the initial pool seed and
  account rent are excluded. Creation without a purchase produces no swap.
- Trench buys use the executed SOL payment to the curve, excluding separately
  charged fees. Sells use the event's net SOL receipt because the program changes
  lamports directly. Events must match the user, mint, fee destinations, token
  transfer, and fee arithmetic. Buy events also require a matching SOL transfer.
- Jupiter RFQ fills use actual user/maker transfers, never the signed quote size.
- M Swap and Perena Star `execute_share_swap` use actual input burns and output
  mints. Internal backing transfers, deposits and withdrawals are excluded.
- Carrot, Huma, Jupiter Lend Earn, Voltr, Saber Decimals, Solayer, Helium, XOrca
  and the named Sanctum conversion instructions match actual user transfers,
  burns and mints. Separate fee transfers/mints are excluded. Stake-pool SOL
  legs use executed System transfers or Stake withdrawals, excluding rent.
  These entries identify conversions and do not imply an AMM price trade.
- StakeDex reports the user's wrapped-SOL conversion once and suppresses its
  nested stake-pool conversion. Ordinary delayed withdrawal requests and later
  settlements are not combined into a fabricated single-transaction exchange.
- SPL Token and Token-2022 parsed `batch` instructions are supported. Each token
  operation inherits the batch invocation's verified outcome and carries a
  `token_batch_path`, retaining its original outer/inner instruction position.
- Phoenix `swap` requires matching Fill/FillSummary records and pool transfers.
  Prefunded swaps and crossing limit orders use FillSummary lots multiplied by
  immutable market lot sizes. Quote amounts already include taker fees. Unfilled
  order quantities and deposits are excluded.
- OpenBook `place_take_order` uses actual pool transfers. Other supported order
  instructions use `TotalOrderFillEvent` native amounts, including taker fees;
  those amounts may remain credited in the order-book account until settlement.
- Manifest wallet swaps use pool transfers. `batch_update` fills use the program's
  FillLog atom amounts and mint addresses, including fills with no SPL transfer.
  Deposits, withdrawals, cancellations and unfilled orders are not trades.
- Token-2022 transfer fees may make net receipts smaller than gross transfers.
  Explicit transfer fees are reported when available; otherwise the output warns
  about this limitation. Current mint state is never substituted for historical
  transfer-fee configuration.
- `input_transfer_fee_raw` and `output_transfer_fee_raw` sum known fees on transfers
  in the main direction of each asset. Fees on refunds remain in the corresponding
  transfer evidence. Gross amounts are not adjusted using guessed transfer fees.
- `fee_lamports` is the transaction's RPC-reported network fee. Account rent,
  tips, and ordinary SOL transfers are not swaps.

## Coverage and limits

The script supports legacy and v0 transactions using `jsonParsed`, including
address lookup tables and temporary token accounts. Runtime invocation logs
recover missing historical `stackHeight` values. Each decoded swap needs a matching
execution trace for its entire scope and a verified outcome for its ancestors.
An earlier completed swap can still decode if later logs are truncated. A
successful transaction proves its top-level instructions committed, but does not
prove every nested call succeeded. Missing evidence leaves recognized swaps in
`undecoded_swaps`. Reverted transactions have no committed swaps, and
failed inner calls are excluded even when a router catches their failure.

There are **111 program IDs and 210 named instruction handlers** in the allowlist. `--list-protocols`
prints their exact addresses and supported instruction names without an RPC call.
This covers **all 106 entries in the Jupiter registry snapshot dated 2026-10-01**:
105 exchange/conversion adapters and one Scorch pricing handler. Five additional
programs are outside that snapshot: Raydium Stable, Lifinity v1/v2, GoonFi v1 and
Scorch's execution program. Program coverage does not imply complete instruction
coverage or measured transaction recall.

| Protocol | Instructions |
| --- | --- |
| Raydium AMM v4 | `swap_base_in`, `swap_base_out`, 17/18-account layouts; compact 8-account `swap_base_in_v2`, `swap_base_out_v2` |
| Raydium CLMM | `swap`, `swap_v2` |
| Raydium CPMM | `swap_base_input`, `swap_base_output` |
| Raydium Stable | `swap_base_in`, `swap_base_out` |
| Raydium LaunchLab | `buy_exact_in`, `buy_exact_out`, `sell_exact_in`, `sell_exact_out` |
| Orca Whirlpool | `swap`, `swap_v2`, `two_hop_swap`, `two_hop_swap_v2` |
| Orca Token Swap v1/v2 (2 programs) | SPL-style `swap` |
| Meteora DLMM | `swap`, `swap2`, exact-output and price-impact variants |
| Meteora DAMM v1 | `swap`, including nested vault deposit/withdraw CPIs |
| Meteora DAMM v2 | `swap`, `swap2` |
| Meteora Dynamic Bonding Curve | `swap`, `swap2`, `swap2_with_transfer_hook` |
| PumpSwap | `buy`, `sell`, `buy_exact_quote_in` |
| Pump.fun bonding curve | `buy`, `sell`, `buy_exact_sol_in`, `buy_v2`, `sell_v2`, `buy_exact_quote_in_v2` |
| Moonit / Moonshot bonding curve | `buy`, `sell`, `buy_with_be_authority`, checked against TradeEvent |
| Saber StableSwap | `swap` |
| Saros AMM | `swap` |
| GooseFX Gamma | `swap_base_input`, `swap_base_output`, `oracle_based_swap_base_input` |
| BonkSwap | `swap` |
| Byreal CLMM | `swap`, `swap_v2`, `swap_v3_dyn` |
| PancakeSwap CLMM | `swap`, `swap_v2` |
| Stabble Stable / Weighted (2 programs) | `swap`, `swap_v2`, including nested vault withdrawals |
| Lifinity v1/v2 (2 programs) | `swap` |
| Aldrin AMM v1/v2 (2 programs) | `swap` |
| Obric v2 | `swap`, `swap2`, each checked against executed calls |
| Tessera V | Verified tags 16 and 17, labeled `swap_tag_16` and `swap_tag_17` |
| Manifest | `swap`, `swap_v2`, filled taker orders in `batch_update` |
| GoonFi v2 | Tag 1, observed 18/19-byte variants |
| ZeroFi | `swap_v4`, observed 17/18/57-byte variants |
| Invariant | `swap` |
| Scorch | Compact tag 1 and tag 2; pricing-program calls do not duplicate swaps |
| 1DEX | `swap_exact_amount_in` |
| Kipseli | `swap_v3` |
| HumidiFi | Deobfuscated 25-byte swap and 113-byte signed-quote swap |
| SolFi v1/v2 (2 programs) | Tag 7, with separate account layouts |
| BisonFi | Tags 2 and 7; signed-quote tag 19 |
| AlphaQ | Tag 12 |
| Quantum | Tag 7 |
| Phoenix | `swap`, `swap_with_free_funds`; fills in regular/prefunded limit orders |
| OpenBook v2 | `place_take_order`, fills in `place_order`, `place_order_pegged`, `place_orders`, `cancel_all_and_place_orders` |
| Saros DLMM | `swap` |
| Stabble CLMM | `swap_v2` |
| Mercurial | N-pool `exchange` with variable vault count |
| FluxBeam | SPL-style `swap`, including Token-2022 transfers |
| Crema CLMM | `swap`, `swap_with_partner` |
| Perena Numeraire | Exact-in/exact-out, hintless and hinted; quote-only calls excluded |
| Heaven | `buy`, `sell`, initial purchase inside `create_standard_liquidity_pool` |
| Boop.fun | `buy_token`, `sell_token`, checked events and SOL/SPL transfers |
| GoonFi v1 | Legacy tag 2, observed 19/20-byte variants |
| DeFiTuna Fusion | `swap`, `two_hop_swap` |
| Guacswap | `swap` |
| Omnipair | `swap` |
| WOOFi | `swap`, with both participating pool addresses |
| DexLab, Dooar/StepN, Penguin, SPL Token Swap (4 programs) | Their verified SPL-style tag-1 swaps |
| Cropper CLMM | `swap` |
| MetaDAO | `spot_swap`; conditional markets are not included |
| Virtuals | `buy`, `sell` |
| Scale AMM / Scale VMM (2 programs) | `buy`, `sell` with direct pool transfers |
| Trends | `swap`, including net refunds |
| Stableswap / Ghost | `swap` |
| Jupiter Lend AMM | `swap_in`, `swap_out`, including the declared liquidity-program CPI |
| Jupiter RFQ v2 | `fill_exact_in`, actual transfers and explicit counterparty metadata |
| Jupiter Perps | Spot `swap2`, `swap_with_token_ledger`; leveraged positions are not swaps |
| Hylo Exchange | LST↔LST, LST↔USDC, EXO↔USDC swaps and the two `_all` variants; schema-based validation only |
| Gavel / Plasma | Tag-0 `swap` |
| Deriverse | Tag-26 spot `swap`, 24/32-byte and 14/15-account layouts |
| RunnerRodeo | `swap` |
| Denali | `swap_exact_in` |
| Flux | Observed tag-3, 18-byte swap |
| Obsidian | Observed tag-1, 9-byte swap |
| Riptide | Observed tag-2, 12-byte swap |
| Trench | `buy`, `buy_exact_out`, `sell`, validated trade events |
| M Swap | `swap`, checked input burn and output mint |
| Perena Star v2 | `execute_share_swap` regular-share→tranche-share conversion; schema-based validation only |
| Aquifer | Observed tag-1, 9-byte exchange between two asset vaults |
| Archer | Observed tag-15, 19-byte swap; liquidity instructions excluded |
| BinaryFi | Observed tag-8, 17-byte swap |
| BisonFi Predict | Observed tag-5 and tag-14 prediction-market swaps |
| Flint | Observed tag-6, 10-byte swap; direct and batched token transfers |
| GatorSwap | 41-byte swap and 113-byte signed-quote layout; executed input mint must match |
| Hadron | Observed tag-3, 26-byte swap; separate fee recipient excluded |
| LemmingsFi | `swap`; withdrawals remain non-swaps |
| Metric | Observed tag-1, 27-byte swap; tag-12/13 withdrawals excluded |
| Quay | Observed tag-32, 18-byte swap |
| TaurusFi | Observed tag-8, 19-byte swap |
| WhaleStreet | Verified 41-byte instruction with discriminator `2b04ed0b1ac91e62` |
| Carrot | `issue`, `redeem`, underlying tokens↔vault shares |
| Huma | `deposit`, `make_initial_deposit`, `instant_withdraw`, `instant_withdraw_privileged`, `withdraw_after_pool_closure` |
| Helium Network | `redeem_v0`, supply-token burn and treasury-token transfer |
| Jupiter Lend Earn | `deposit`, `mint`, `withdraw`, `redeem`, and published minimum-output/maximum-burn variants |
| Voltr | `deposit_vault`, `instant_withdraw_vault` |
| Saber Decimals | `deposit`, `withdraw`, underlying↔wrapped tokens |
| Sanctum / SPL stake pools (3 programs) | `deposit_sol`, `withdraw_sol` |
| Sanctum StakeDex | `stake_wrapped_sol`, `withdraw_wrapped_sol`; nested conversions counted once |
| Sanctum Infinity | v1/v2 exact-in/exact-out swaps; v1 `add_liquidity`/`remove_liquidity` share conversions |
| Sanctum Prop S | Observed tag-0, 17-byte swap |
| Sanctum SOLS | Observed tag-2 wrapped-SOL deposit and receipt mint |
| Solayer | `delegate`, `delegate_no_init`, `undelegate`, `undelegate_no_init` |
| VaultLiquidUnstake | `buy_lst`, executed LST↔WSOL transfers |
| XOrca | `stake`, ORCA transfer and xORCA mint |
| Scorch pricing | Verified pricing CPI inside Scorch execution; recorded separately from swaps |

Other DEXes, unlisted instruction variants, future instruction layouts, and
transaction versions above v0 require additional adapters. Order-book support
covers the specified spot taker fills; it is not a general maker-position,
settlement or profit/loss decoder. A registered program ID does not make
every instruction from that program a supported swap. Liquidity operations are
not reinterpreted as swaps just because tokens moved.
`unclassified_instructions` lists application calls whose meaning was not decoded;
they may be ordinary non-swap instructions or unsupported swaps. An empty `swaps`
array alongside warnings does not prove that no swap happened. A recognized swap
with ambiguous/missing transfer evidence goes into `undecoded_swaps` instead of
being guessed. Missing mint decimals leave decimal amounts null.

The decoder was audited against **1,876 distinct on-chain transactions**, including
150 signatures sampled from the supplied sandwich JSONL, public historical
examples and bounded program/vault activity samples. It decoded **2,027 swaps/fills/conversions
from 107 of the 110 exchange programs**, including **1,937 pool/order-book/prediction entries**,
and classified **40 Scorch pricing calls** separately. The registry completion adds
**28 exchange/conversion adapters, all with real examples, plus one pricing handler**.
It also covers SPL batch transfers and Deriverse's 14-account variant. One additional
recognized DLMM call lacked sufficient logs and stayed
explicitly undecoded. Unclassified instructions also remain in the audit, so
these counts are not a measured percentage of all swaps in those transactions.
See `outputs/solana/decoder_coverage_audit.json` for aggregate live-audit counts.
Per-transaction records from that audit are omitted from this repository export;
`tests/fixtures/solana/` contains the reproducible transaction fixtures, instruction positions,
amounts, protocol counts, undecoded calls and unclassified instructions.

Lifinity v1, Hylo Exchange and Perena Star's share conversion retain schema-based
adapters without live swap validation in this audit. Validation of one variant
does not establish all variants of a program. There are **131 saved on-chain
transaction fixtures and 91 tests**. The earlier fixture
manifest (`tests/fixtures/solana/extended_swap_cases.json`) records exact amounts,
pools and instruction positions for **31 observed variants across the 24 added
programs**. Regression tests also cover routes, nested vaults, fees, refunds,
temporary accounts, Token-2022, failures and truncated logs.
`tests/fixtures/solana/additional_swap_cases.json` adds **36 exact swap records
across 28 newly added programs and the 3 repaired programs**. Actual withdrawals
and oracle updates are also saved as negative regression fixtures.
`tests/fixtures/solana/registry_swap_cases.json` adds **39 exact execution records
across all 28 new exchange/conversion programs**, including a separate batch-transfer
regression. Scorch pricing and additional liquidity operations have separate fixtures.

Whirlpool and DeFiTuna two-hop scenarios use synthetic execution tests based on their definitions.
Manifest batch fills, Phoenix prefunded/crossing limit orders and OpenBook orders
using deposited funds are tested using reconstructed invocation scopes containing
real fill-event bytes and real market headers. These are explicitly synthetic
scenarios, not additional mined-transaction validations. Recent sampled transactions
validated Manifest wallet swaps, Phoenix wallet swaps and OpenBook immediate takes.
Hylo LST exchange and Perena Star share-conversion tests are explicitly synthetic.

Proprietary AMM account layouts are restricted to observed program-specific tags,
lengths and account roles. HumidiFi's standard variant also checks its decoded
direction. None of these adapters treats arbitrary two-token movements as a swap.

`outputs/solana/adapter_coverage_status.json` lists all 106 registry entries,
their implemented instructions, the variants with real validation, and the variants
that still lack it. The missing-program list is empty for this dated snapshot.
Unlisted instruction variants, asynchronous redemptions, future deployments and
incomplete RPC evidence can still prevent decoding. For example, XOrca's delayed
unstake/withdraw flow is not a same-transaction swap. This registry is not a complete
inventory of every Solana DEX, and future registry additions require new adapters.

## Protocol definitions

- Registry completion schemas from program-owned IDLs are saved in
  `tests/fixtures/solana/registry_protocol_schemas.json`. Opaque venue layouts are
  independently constrained by executed calls, token-account ownership, program
  state ownership and the regression fixtures; instruction amounts are never used
  as substitutes for executed amounts.
- [SPL Token batch format](https://github.com/solana-program/token/blob/main/interface/src/instruction.rs)
  and [Agave's parsed batch representation](https://github.com/anza-xyz/agave/blob/master/transaction-status/src/parse_token.rs).
- [LemmingsFi integration](https://github.com/jup-ag/lemmingsfi-jupiter/blob/main/src/lib.rs),
  [Carrot integration](https://github.com/jup-ag/carrot-sdk/blob/main/amm/src/lib.rs),
  [Jupiter Lend account contexts](https://github.com/Instadapp/fluid-solana-programs/blob/main/programs/lending/src/state/context.rs),
  [Solayer integration](https://github.com/solayer-labs/jupiter-amm-integration/blob/main/src/amms/amm.rs),
  and [XOrca staking accounts](https://github.com/orca-so/xorca/blob/main/rust-client/src/generated/instructions/stake.rs).
- [Sanctum Infinity IDL](https://github.com/igneous-labs/inf-1.5/blob/master/idl/inf_controller.json),
  [StakeDex interface](https://github.com/igneous-labs/stakedex-sdk/blob/master/interfaces/stakedex_interface/idl.json),
  and [Sanctum SPL Stake Pool instructions](https://github.com/igneous-labs/sanctum-spl-stake-pool/blob/sanctum-spl-pool-deploy/stake-pool/program/src/instruction.rs).
- The additional Anchor layouts were fetched directly from program-owned on-chain
  IDL accounts. Account addresses, owners and raw-data SHA-256 values are recorded
  in `outputs/solana/adapter_coverage_status.json`; the relevant instruction/type
  snapshots are in `tests/fixtures/solana/additional_protocol_schemas.json`.
- [Gavel / Plasma instruction definitions](https://github.com/Ellipsis-Labs/plasma/blob/master/program/src/program/instruction.rs)
  and [swap implementation](https://github.com/Ellipsis-Labs/plasma/blob/master/program/src/program/processor/swap.rs).
- [Deriverse's official integration](https://github.com/deriverse/okx-deriverse/blob/main/programs/dex-solana/src/adapters/deriverse.rs)
  for the spot instruction's account order, checked against observed 32-byte calls.
- [SPL Token Swap instruction definitions](https://github.com/solana-labs/solana-program-library/blob/master/token-swap/program/src/instruction.rs),
  cross-checked against each added deployment's executed transfers.
- Cropper, RunnerRodeo, Denali, Flux, Obsidian and Riptide layouts were checked
  against the saved program-specific executed calls. Opaque tags retain their
  observed names and length restrictions; a registry label alone is not an adapter.

- [Solana getTransaction](https://solana.com/docs/rpc/http/gettransaction)
- [Solana RPC JSON structures](https://solana.com/docs/rpc/json-structures)
- [Raydium AMM instructions](https://github.com/raydium-io/raydium-amm/blob/master/program/src/instruction.rs)
- [Raydium CLMM swap](https://github.com/raydium-io/raydium-clmm/blob/master/programs/amm/src/instructions/swap.rs) and [swap v2](https://github.com/raydium-io/raydium-clmm/blob/master/programs/amm/src/instructions/swap_v2.rs)
- [Raydium CPMM swap](https://github.com/raydium-io/raydium-cp-swap/blob/master/programs/cp-swap/src/instructions/swap_base_input.rs)
- [Orca Whirlpool swap](https://github.com/orca-so/whirlpools/blob/main/programs/whirlpool/src/instructions/swap.rs) and [swap v2](https://github.com/orca-so/whirlpools/blob/main/programs/whirlpool/src/instructions/v2/swap.rs)
- [Meteora DLMM IDL](https://github.com/MeteoraAg/dlmm-sdk/blob/main/idls/dlmm.json)
- [Pump.fun IDL](https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump.json)
- [PumpSwap IDL](https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump_amm.json)
- [Raydium SDK swap builders, including compact AMM and Stable](https://github.com/raydium-io/raydium-sdk-V2/blob/master/src/raydium/liquidity/instruction.ts)
- [Raydium LaunchLab IDL](https://github.com/raydium-io/raydium-idl/blob/master/raydium_launchpad/raydium_launchpad.json)
- [Orca two-hop v1](https://github.com/orca-so/whirlpools/blob/main/programs/whirlpool/src/instructions/two_hop_swap.rs) and [v2](https://github.com/orca-so/whirlpools/blob/main/programs/whirlpool/src/instructions/v2/two_hop_swap.rs)
- [Meteora DAMM v1 IDL](https://github.com/MeteoraAg/dynamic-bonding-curve-sdk/blob/main/packages/dynamic-bonding-curve/src/idl/damm-v1/idl.json), [DAMM v2 IDL](https://github.com/MeteoraAg/damm-v2-sdk/blob/main/src/idl/cp_amm.json), and [DBC IDL](https://github.com/MeteoraAg/dynamic-bonding-curve-sdk/blob/main/packages/dynamic-bonding-curve/src/idl/dynamic-bonding-curve/idl.json)
- [Saber swap ABI](https://github.com/saber-hq/stable-swap/blob/master/stable-swap-client/src/instruction.rs)
- [SPL Token Swap ABI](https://github.com/solana-labs/solana-program-library/blob/master/token-swap/program/src/instruction.rs) and [Saros swap builder](https://github.com/saros-xyz/saros-sdk/blob/main/src/swap/sarosSwapIntructions.js)
- [GooseFX Gamma current IDL](https://github.com/GooseFX1/gamma-sdk/blob/main/src/gfx/idl/gamma.json)
- [Stabble Stable IDL](https://github.com/stabbleorg/amm-sdk/blob/main/packages/sdk/src/generated/idl/stable_swap.json) and [Weighted IDL](https://github.com/stabbleorg/amm-sdk/blob/main/packages/sdk/src/generated/idl/weighted_swap.json)
- [Lifinity v1 SDK](https://www.npmjs.com/package/@lifinity/sdk) (0.2.25) and [v2 SDK](https://www.npmjs.com/package/@lifinity/sdk-v2) (2.0.3), published `lib/idl` definitions; read without installing/executing package code
- [Moonit current IDL](https://github.com/wen-moon-ser/moonit-sdk/blob/main/src/solana/program/tokenLaunchpadIdlV4.ts)
- [Pinax public IDLs](https://github.com/pinax-network/substreams-solana-idls/tree/develop/src): BonkSwap, Byreal, PancakeSwap and Tessera definitions, cross-checked against saved executed calls. These are third-party maintained definitions, not a claim that every schema is published by the DEX itself.
- [Public parser test cases](https://github.com/cxcx-ai/solana-dex-parser): additional historical signatures used for validation; the decoder does not enable that project's unknown-DEX guessing.
- [Manifest instruction definitions](https://github.com/CKS-Systems/manifest/blob/main/programs/manifest/src/program/instruction.rs) and [fill records](https://github.com/CKS-Systems/manifest/blob/main/client/rust/slim/src/events.rs)
- [Phoenix instructions](https://github.com/Ellipsis-Labs/phoenix-v1/blob/master/src/program/instruction.rs), [events](https://github.com/Ellipsis-Labs/phoenix-v1/blob/master/src/program/events.rs), [market header](https://github.com/Ellipsis-Labs/phoenix-v1/blob/master/src/program/accounts.rs), and [fill/fee accounting](https://github.com/Ellipsis-Labs/phoenix-v1/blob/master/src/state/markets/fifo.rs)
- [OpenBook v2 IDL](https://github.com/openbook-dex/openbook-v2/blob/master/idl/openbook_v2.json) and [native fill-event amounts](https://github.com/openbook-dex/openbook-v2/blob/master/programs/openbook-v2/src/state/orderbook/book.rs)
- [Invariant swap accounts](https://github.com/invariant-labs/protocol/blob/master/programs/invariant/src/instructions/swap.rs)
- [Saros DLMM IDL](https://github.com/saros-xyz/dlmm-saros-sdk/blob/main/src/constants/idl/liquidity_book.json)
- [Stabble CLMM IDL](https://github.com/stabbleorg/clmm/blob/master/sdk/idl/stabble_clmm.json)
- [Mercurial exchange builder](https://github.com/mercurial-finance/stable-swap-n-pool-instructions/blob/master/src/instruction.rs)
- [Aldrin SDK](https://www.npmjs.com/package/@aldrin_exchange/sdk) 0.4.51, `dist/cjs/pools/pool.js`; [Crema SDK](https://www.npmjs.com/package/@cremafinance/crema-sdk-v2) 2.4.18, published `idls/clmmpool.json`; both read as data without executing package code
- Perena's published Anchor IDL account: [A7h6CWx9eMy6mGT43e8YHtfGvJnxnKNpciZFgvxezj1g](https://explorer.solana.com/address/A7h6CWx9eMy6mGT43e8YHtfGvJnxnKNpciZFgvxezj1g). Boop.fun's published IDL account: [3HsS8PecBaFDTBFgdCDSWAgV5PCbkVUenJdSVQi7FtkM](https://explorer.solana.com/address/3HsS8PecBaFDTBFgdCDSWAgV5PCbkVUenJdSVQi7FtkM). Retrieved with standard RPC and checked against executed calls.
- Heaven and FluxBeam: published IDL/decoder references from [Solana IDLs](https://github.com/tenequm/solana-idls/blob/main/idl/heaven.json) and [Carbon](https://github.com/sevenlabs-hq/carbon/blob/main/decoders/fluxbeam-decoder/src/instructions/swap.rs), cross-checked against saved on-chain calls.
- Proprietary venue ABI research and historical example signatures: [solana-dex-parser-go venue layouts](https://github.com/DefaultPerson/solana-dex-parser-go/blob/main/parsers/propamm/venues.go), [HumidiFi deobfuscation](https://github.com/DefaultPerson/solana-dex-parser-go/blob/main/parsers/propamm/humidifi.go), and [SolFi v1](https://github.com/DefaultPerson/solana-dex-parser-go/blob/main/parsers/propamm/solfi.go). These are third-party observations, verified here using actual RPC transfers. See `SOLANA_DECODER_NOTICES.txt`.
- 1DEX and Kipseli layouts are recorded by their executed instructions, program logs and SPL transfers in the saved fixtures. 1DEX's pool at account index 1 was additionally checked against the on-chain `PoolState` discriminator; account 0 is not used as its pool identity.

Obric `swap`/`swap2`, Aldrin v1 and Tessera's newer tag 17 layouts are also recorded by
the actual instruction accounts and parsed transfers in
`raydium_stable_obric_route.json`, `saber_aldrin_saros_route.json`, and
`gamma_tessera_route.json` under `tests/fixtures/solana`. Their coverage is limited
to those verified layouts; other variants stay unclassified.
