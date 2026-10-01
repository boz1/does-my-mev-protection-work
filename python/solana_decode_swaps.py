#!/usr/bin/env python3
"""Decode individual Solana swaps from a transaction signature and standard RPC.

Usage: SOLANA_RPC_URL='your RPC URL' python3 solana_decode_swaps.py SIGNATURE
       python3 solana_decode_swaps.py --from-json saved_getTransaction.json

Python standard library only. Output is JSON; raw and decimal amounts are strings.
Decode executed transfers/events, not quoted instruction amounts or wallet deltas.
See SOLANA_SWAPS.md for coverage, amount definitions, and protocol sources.
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from solana_swap_protocols import EXTRA_DEX_NAMES, LayoutError, expanded_layouts, extra_catalog, PHOENIX, OPENBOOK_V2, MANIFEST
from solana_registry_protocols import NAMES as REGISTRY_NAMES, SCORCH_PRICING, registry_layouts, registry_catalog

BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
SYSTEM = "11111111111111111111111111111111"
TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
WSOL = "So11111111111111111111111111111111111111112"
RAYDIUM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
CLMM = "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK"
CPMM = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"
ORCA = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
DLMM = "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_AMM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
DEX_NAMES = {RAYDIUM: "Raydium AMM v4", CLMM: "Raydium CLMM",
             CPMM: "Raydium CPMM", ORCA: "Orca Whirlpool", DLMM: "Meteora DLMM",
             PUMP: "Pump.fun bonding curve", PUMP_AMM: "PumpSwap", **EXTRA_DEX_NAMES, **REGISTRY_NAMES}
ROUTERS = {"JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB": "Jupiter v4",
           "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": "Jupiter v6"}
CORE_PROGRAMS = {
    SYSTEM, TOKEN, TOKEN2022, "ComputeBudget111111111111111111111111111111",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",
    "Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo",
    "Ed25519SigVerify111111111111111111111111111",
    "KeccakSecp256k11111111111111111111111111111",
    "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ",
}
EVENT_CPI_TAG = bytes.fromhex("e445a52e51cb9a1d")


class DecodeError(ValueError):
    """Insufficient evidence to decode a swap without guessing."""


class MarketMetadataRequired(DecodeError):
    def __init__(self, market):
        self.market = market
        super().__init__("Market mint/lot metadata is required for this order-book fill; use RPC mode or --market-accounts")


def b58decode(value: str) -> bytes:
    n = 0
    try:
        for char in value:
            n = n * 58 + BASE58.index(char)
    except ValueError:
        raise DecodeError("Invalid Base58 value") from None
    return b"\0" * (len(value) - len(value.lstrip("1"))) + n.to_bytes((n.bit_length() + 7) // 8, "big")


def b58encode(value: bytes) -> str:
    n, result = int.from_bytes(value, "big"), ""
    while n:
        n, r = divmod(n, 58)
        result = BASE58[r] + result
    return "1" * (len(value) - len(value.lstrip(b"\0"))) + result


def discriminator(name: str, namespace: str = "global") -> bytes:
    return hashlib.sha256(f"{namespace}:{name}".encode()).digest()[:8]


def decimal_amount(amount: int, decimals: int | None) -> str | None:
    if decimals is None:
        return None
    if decimals == 0:
        return str(amount)
    whole, fraction = divmod(amount, 10 ** decimals)
    return f"{whole}.{fraction:0{decimals}d}".rstrip("0").rstrip(".")


def validate_signature(signature: str) -> None:
    if not signature or len(signature) > 88 or len(b58decode(signature)) != 64:
        raise DecodeError("A transaction signature must be Base58 encoding 64 bytes")


def rpc_call(method: str, params: list, rpc_url: str, timeout: float = 30):
    if urlsplit(rpc_url).scheme not in {"https", "http"} or not urlsplit(rpc_url).netloc:
        raise DecodeError("SOLANA_RPC_URL must be an HTTP(S) URL")
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    request = Request(rpc_url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except HTTPError as exc:
        raise DecodeError(f"RPC returned HTTP {exc.code}") from None
    except (URLError, OSError):
        # URL paths often contain API keys; never print transport exceptions.
        raise DecodeError("Cannot reach RPC; check the endpoint, network, and timeout") from None
    except (ValueError, UnicodeError):
        raise DecodeError("RPC returned invalid JSON") from None
    if not isinstance(result, dict):
        raise DecodeError("RPC returned an unexpected response")
    if result.get("error"):
        raise DecodeError(f"{method} failed (RPC code {result['error'].get('code')})")
    return result.get("result")


def fetch_transaction(signature: str, rpc_url: str, timeout: float = 30) -> dict:
    validate_signature(signature)
    result = rpc_call("getTransaction", [signature, {
        "encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": 0}], rpc_url, timeout)
    if result is None:
        raise DecodeError("Transaction not found or not finalized; the RPC may lack historical data")
    tx = result
    if tx.get("transaction", {}).get("signatures", [None])[0] != signature:
        raise DecodeError("RPC returned a different transaction signature")
    return tx


@dataclass
class Instruction:
    ix: dict
    outer: int
    inner: int | None
    depth: int | None = None
    parent: Instruction | None = None
    events: list[bytes] = field(default_factory=list)
    failed: bool = False
    trace_verified: bool = False
    batch_path: tuple[int, ...] = ()

    @property
    def program(self) -> str:
        return self.ix["programId"]

    def location(self) -> dict:
        return {"outer_instruction_index": self.outer, "inner_instruction_index": self.inner,
                **({"token_batch_path": list(self.batch_path)} if self.batch_path else {})}

    def is_within(self, ancestor: Instruction) -> bool:
        parent = self.parent
        while parent is not None:
            if parent is ancestor:
                return True
            parent = parent.parent
        return False

    def reverted(self) -> bool:
        node: Instruction | None = self
        while node is not None:
            if node.failed:
                return True
            node = node.parent
        return False


def log_invocations(logs: list[str]) -> list[list[dict]]:
    """Keep events attached to their emitting invocation, including caught failures."""
    groups: list[list[dict]] = []
    stack: list[dict] = []
    for line in logs:
        match = re.fullmatch(r"Program (\w+) invoke \[(\d+)\]", line)
        if match:
            program, depth = match[1], int(match[2])
            if depth == 1:
                groups.append([])
                stack = []
            if not groups:
                continue
            record = {"program": program, "depth": depth, "events": [], "failed": False, "closed": False}
            groups[-1].append(record)
            stack.append(record)
        elif line.startswith("Program data: ") and stack:
            try:
                stack[-1]["events"].append(base64.b64decode(line[14:], validate=True))
            except ValueError:
                pass
        else:
            match = re.fullmatch(r"Program (\w+) (success|failed:.*)", line)
            if match and stack and stack[-1]["program"] == match[1]:
                record = stack.pop()
                record["closed"] = True
                record["failed"] = match[2] != "success"
    return groups


def instruction_tree(tx: dict, warnings: list[str]) -> list[Instruction]:
    message, meta = tx["transaction"]["message"], tx["meta"]
    if any("programId" not in ix for ix in message["instructions"]):
        raise DecodeError("Use getTransaction encoding=jsonParsed, not raw compiled instructions")
    inner = {g["index"]: g["instructions"] for g in meta.get("innerInstructions") or []}
    trace = log_invocations(meta.get("logMessages") or [])
    trace_index, result = 0, []
    for outer, ix in enumerate(message["instructions"]):
        nodes = [Instruction(ix, outer, None, 1)]
        nodes += [Instruction(child, outer, j, child.get("stackHeight")) for j, child in enumerate(inner.get(outer, []))]
        if trace_index < len(trace) and trace[trace_index][0]["program"] == ix["programId"]:
            group = trace[trace_index]
            trace_index += 1
            if (len(group) <= len(nodes)
                    and all(n.program == g["program"] and (n.depth is None or n.depth == g["depth"])
                            for n, g in zip(nodes, group))):
                for node, entry in zip(nodes, group):
                    node.depth, node.events, node.failed = entry["depth"], entry["events"], entry["failed"]
                    # A successful transaction proves a top-level call committed.
                    # Nested calls require their own closing log (failure can be caught).
                    node.trace_verified = entry["closed"] or (node.inner is None and meta.get("err") is None)
        # Matching log prefixes can prove a completed swap even if later logs were
        # truncated. Each decoder still requires its entire subtree and ancestors.
        stack: list[Instruction] = []
        for node in nodes:
            if node.depth is None:
                stack = []
            else:
                while stack and stack[-1].depth >= node.depth:
                    stack.pop()
                if stack and stack[-1].depth == node.depth - 1:
                    node.parent = stack[-1]
                stack.append(node)
        if len(nodes) > 1 and not all(n.trace_verified for n in nodes):
            warnings.append(f"Instruction {outer}: execution logs are incomplete; only fully verified swap scopes can be decoded")
        for node in nodes:
            result.append(node)
            # RPC's parsed SPL Batch contains several operations in ONE runtime
            # invocation. Expand only after aligning that invocation to its logs.
            # Each operation inherits its outcome and keeps the original position.
            parsed_batch = node.ix.get("parsed", {})
            if node.program in {TOKEN, TOKEN2022} and parsed_batch.get("type") == "batch":
                for index, parsed in enumerate(parsed_batch.get("info", {}).get("instructions", [])):
                    if parsed.get("type") == "batch":
                        raise DecodeError("SPL Token does not permit nested batches")
                    result.append(Instruction({**node.ix, "parsed": parsed}, node.outer, node.inner, node.depth,
                                              parent=node.parent, failed=node.failed, trace_verified=node.trace_verified,
                                              batch_path=(index,)))
    return result


def token_metadata(tx: dict, nodes: list[Instruction]) -> tuple[dict, dict]:
    keys = tx["transaction"]["message"]["accountKeys"]
    # jsonParsed already expands address lookup tables; do not append them again.
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in keys]
    accounts, decimals = {}, {WSOL: 9}
    for balance in (tx["meta"].get("preTokenBalances") or []) + (tx["meta"].get("postTokenBalances") or []):
        accounts[keys[balance["accountIndex"]]] = {"mint": balance["mint"], "owner": balance.get("owner")}
        decimals[balance["mint"]] = balance["uiTokenAmount"]["decimals"]
    for node in nodes:
        parsed = node.ix.get("parsed")
        if not isinstance(parsed, dict):
            continue
        info, kind = parsed.get("info", {}), parsed.get("type")
        if node.program in {TOKEN, TOKEN2022}:
            if kind in {"initializeAccount", "initializeAccount2", "initializeAccount3"}:
                accounts[info["account"]] = {"mint": info["mint"], "owner": info.get("owner")}
            if "tokenAmount" in info and "mint" in info:
                decimals[info["mint"]] = info["tokenAmount"]["decimals"]
                for side in ("source", "destination"):
                    if side in info:
                        accounts.setdefault(info[side], {})["mint"] = info["mint"]
    return accounts, decimals


def token_transfer(node: Instruction, accounts: dict) -> dict | None:
    parsed = node.ix.get("parsed")
    if node.program not in {TOKEN, TOKEN2022} or not isinstance(parsed, dict):
        return None
    if parsed.get("type") not in {"transfer", "transferChecked", "transferCheckedWithFee"}:
        return None
    info = parsed["info"]
    mint = info.get("mint") or accounts.get(info["source"], {}).get("mint") or accounts.get(info["destination"], {}).get("mint")
    amount = info.get("amount", info.get("tokenAmount", {}).get("amount"))
    fee = info.get("feeAmount")
    fee = fee.get("amount") if isinstance(fee, dict) else fee
    return {"source": info["source"], "destination": info["destination"], "mint": mint,
            "amount": int(amount), "fee_raw": str(fee) if fee is not None else ("0" if node.program == TOKEN else None),
            "token_program": node.program, **node.location()}


def public_transfer(transfer: dict) -> dict:
    """Preserve u64 precision for JSON consumers that use floating-point numbers."""
    return {**{k: v for k, v in transfer.items() if k != "amount"}, "amount_raw": str(transfer["amount"])}


def anchor_name(raw: bytes, names: tuple[str, ...]) -> str | None:
    return next((name for name in names if raw[:8] == discriminator(name)), None)


def swap_layout(node: Instruction, raw: bytes) -> dict | None:
    """Account positions come from the protocol sources linked in SOLANA_SWAPS.md."""
    program, a = node.program, node.ix.get("accounts", [])
    name, pool, user, source, dest, vaults = None, None, None, None, None, ()
    if program == RAYDIUM and raw[:1] in {b"\x09", b"\x0b"}:
        if len(a) not in {17, 18} or len(raw) != 17:
            raise DecodeError("Unrecognized Raydium AMM v4 swap layout")
        name = "swap_base_in" if raw[0] == 9 else "swap_base_out"
        pool, user, source, dest = 1, -1, -3, -2
        vaults = (len(a) - 13, len(a) - 12)
    elif program == RAYDIUM and raw[:1] in {b"\x10", b"\x11"}:
        if len(a) != 8 or len(raw) != 17:
            raise DecodeError("Unrecognized Raydium AMM v4 compact swap layout")
        name = "swap_base_in_v2" if raw[0] == 16 else "swap_base_out_v2"
        pool, user, source, dest, vaults = 1, 7, 5, 6, (3, 4)
    elif program == CLMM:
        name = anchor_name(raw, ("swap", "swap_v2"))
        pool, user, source, dest, vaults = 2, 0, 3, 4, (5, 6)
    elif program == CPMM:
        name = anchor_name(raw, ("swap_base_input", "swap_base_output"))
        pool, user, source, dest, vaults = 3, 0, 4, 5, (6, 7)
    elif program == DLMM:
        name = anchor_name(raw, ("swap", "swap2", "swap_exact_out", "swap_exact_out2", "swap_with_price_impact", "swap_with_price_impact2"))
        pool, user, source, dest, vaults = 0, 10, 4, 5, (2, 3)
    elif program == ORCA:
        name = anchor_name(raw, ("swap", "swap_v2"))
        if name:
            if len(raw) < 42 or raw[41] not in (0, 1):
                raise DecodeError("Invalid Whirlpool swap direction")
            v2, a_to_b = name == "swap_v2", bool(raw[41])
            pool, user = (4, 3) if v2 else (2, 1)
            source, dest = (7, 9) if v2 else (3, 5)
            vaults = (8, 10) if v2 else (4, 6)
            if not a_to_b:
                source, dest = dest, source
    elif program == PUMP_AMM:
        name = anchor_name(raw, ("buy", "buy_exact_quote_in", "sell"))
        pool, user, vaults = 0, 1, (7, 8)
        source, dest = (5, 6) if name == "sell" else (6, 5)
    elif program == PUMP:
        name = anchor_name(raw, ("buy", "sell", "buy_exact_sol_in", "buy_v2", "sell_v2", "buy_exact_quote_in_v2"))
        if name:
            return {"instruction": name, "pump_event": True}
    if not name:
        return None
    try:
        return {"instruction": name, "pool": a[pool], "trader": a[user], "source": a[source],
                "destination": a[dest], "vaults": [a[i] for i in vaults]}
    except IndexError:
        raise DecodeError("Swap instruction has fewer accounts than its protocol layout") from None


def asset(mint: str | None, decimals: dict) -> dict:
    return {"mint": mint, "symbol": "SOL" if mint is None else ("WSOL" if mint == WSOL else None),
            "decimals": 9 if mint is None else decimals.get(mint)}


def amounts(mint_in: str | None, mint_out: str | None, amount_in: int, amount_out: int, decimals: dict) -> dict:
    token_in, token_out = asset(mint_in, decimals), asset(mint_out, decimals)
    return {"token_in": token_in, "token_out": token_out, "amount_in_raw": str(amount_in),
            "amount_out_raw": str(amount_out), "amount_in": decimal_amount(amount_in, token_in["decimals"]),
            "amount_out": decimal_amount(amount_out, token_out["decimals"])}


def decode_token_swap(node: Instruction, layout: dict, nodes: list[Instruction], accounts: dict, decimals: dict) -> dict:
    verify_scope(node, nodes)
    transfers = [t for n in nodes if transfer_in_scope(n, node, layout.get("nested_programs", ())) and not n.reverted()
                 if (t := token_transfer(n, accounts)) is not None]
    users = set(layout.get("user_accounts") or (layout["source"], layout["destination"]))
    vaults = set(layout["vaults"])
    if users & vaults:
        raise DecodeError("User and vault accounts overlap in this swap layout")
    net, evidence = {}, []
    for t in transfers:
        sign = (1 if t["source"] in users and t["destination"] in vaults else
                -1 if t["source"] in vaults and t["destination"] in users else 0)
        if not sign or not t["amount"]:
            continue
        if not t["mint"]:
            raise DecodeError("Cannot resolve token mint for a swap boundary transfer")
        net[t["mint"]] = net.get(t["mint"], 0) + sign * t["amount"]
        evidence.append((sign, t))
    incoming = [(mint, amount) for mint, amount in net.items() if amount > 0]
    outgoing = [(mint, -amount) for mint, amount in net.items() if amount < 0]
    if len(incoming) != 1 or len(outgoing) != 1 or len(net) != 2:
        raise DecodeError("Expected exactly two mints with opposing user/vault flows in this swap invocation")
    mint_in, amount_in = incoming[0]
    mint_out, amount_out = outgoing[0]
    if layout.get("declared_input_mint_bytes") is not None and b58decode(mint_in) != layout["declared_input_mint_bytes"]:
        raise DecodeError("Swap transfers do not match the instruction's input mint")

    def fee(mint, direction):
        relevant = [t["fee_raw"] for sign, t in evidence if sign == direction and t["mint"] == mint]
        return str(sum(int(f) for f in relevant)) if all(f is not None for f in relevant) else None

    return {"pool": layout["pool"], "trader": layout["trader"],
            **{key: layout[key] for key in ("venue_type", "fill_authority", "pool_addresses") if key in layout},
            **amounts(mint_in, mint_out, amount_in, amount_out, decimals),
            "amount_basis": ("gross executed user/maker token transfers, net of refunds" if layout.get("venue_type") == "rfq" else
                             "gross transfers between declared user and pool accounts, net of refunds; fees paid to other accounts excluded"),
            "input_transfer_fee_raw": fee(mint_in, 1), "output_transfer_fee_raw": fee(mint_out, -1),
            "evidence": "executed_token_transfers", "transfers": [public_transfer(t) for _, t in evidence]}


def verify_scope(node: Instruction, nodes: list[Instruction]) -> None:
    ancestor = node
    while ancestor is not None:
        if not ancestor.trace_verified or ancestor.failed:
            raise DecodeError("Cannot verify the swap's execution scope and successful outcome from logs")
        ancestor = ancestor.parent
    if any(not n.trace_verified for n in nodes if n.is_within(node)):
        raise DecodeError("Incomplete execution trace inside the swap invocation")


def transfer_in_scope(child: Instruction, node: Instruction, nested_programs: tuple | list = ()) -> bool:
    parent = child.parent
    while parent is not None and parent is not node:
        if parent.program not in nested_programs:
            return False
        parent = parent.parent
    return parent is node


# TradeEvent has grown by appending fields. Stop at an older event's boundary.
PUMP_EVENT_FIELDS = (
    ("mint", "pubkey"), ("sol_amount", "u64"), ("token_amount", "u64"), ("is_buy", "bool"),
    ("user", "pubkey"), ("timestamp", "i64"), ("virtual_sol_reserves", "u64"),
    ("virtual_token_reserves", "u64"), ("real_sol_reserves", "u64"), ("real_token_reserves", "u64"),
    ("fee_recipient", "pubkey"), ("fee_basis_points", "u64"), ("fee", "u64"), ("creator", "pubkey"),
    ("creator_fee_basis_points", "u64"), ("creator_fee", "u64"), ("track_volume", "bool"),
    ("total_unclaimed_tokens", "u64"), ("total_claimed_tokens", "u64"), ("current_sol_volume", "u64"),
    ("last_update_timestamp", "i64"), ("ix_name", "string"), ("mayhem_mode", "bool"),
    ("cashback_fee_basis_points", "u64"), ("cashback", "u64"), ("buyback_fee_basis_points", "u64"),
    ("buyback_fee", "u64"), ("shareholders", "shareholders"), ("quote_mint", "pubkey"),
    ("quote_amount", "u64"),
)


def pump_trade_event(raw: bytes) -> dict | None:
    if raw[:8] != discriminator("TradeEvent", "event") or len(raw) < 129:
        return None
    pos, result = 8, {}
    for name, kind in PUMP_EVENT_FIELDS:
        if pos == len(raw):
            break
        size = {"pubkey": 32, "bool": 1, "u64": 8, "i64": 8}.get(kind, 4)
        if pos + size > len(raw):
            raise DecodeError("Truncated Pump.fun trade event")
        value = raw[pos:pos + size]
        pos += size
        if kind in {"string", "shareholders"}:
            count = int.from_bytes(value, "little")
            width = count * (34 if kind == "shareholders" else 1)
            if pos + width > len(raw):
                raise DecodeError("Invalid Pump.fun trade event extension")
            value = raw[pos:pos + width]
            pos += width
            result[name] = value.decode() if kind == "string" else count
        elif kind == "pubkey":
            result[name] = b58encode(value)
        else:
            result[name] = int.from_bytes(value, "little", signed=kind == "i64")
    return result


def decode_pump(node: Instruction, layout: dict, nodes: list[Instruction], accounts: dict, decimals: dict) -> dict:
    verify_scope(node, nodes)
    name, a = layout["instruction"], node.ix["accounts"]
    v2, buy = name.endswith("_v2"), name.startswith("buy")
    try:
        mint, curve, user, vault, user_token = (a[1], a[10], a[13], a[11], a[14]) if v2 else (a[2], a[3], a[6], a[4], a[5])
        quote_mint = a[2] if v2 else WSOL
    except IndexError:
        raise DecodeError("Unknown Pump.fun account layout") from None
    payloads = set(node.events)
    for child in nodes:
        if child.parent is node and child.program == PUMP and not child.reverted():
            raw = b58decode(child.ix.get("data", ""))
            if raw.startswith(EVENT_CPI_TAG):
                payloads.add(raw[8:])
    events = [event for raw in payloads if (event := pump_trade_event(raw)) is not None
              and event["mint"] == mint and event["user"] == user and bool(event["is_buy"]) == buy]
    if len(events) != 1:
        raise DecodeError("Expected exactly one matching Pump.fun TradeEvent")
    event = events[0]
    transfers = [t for n in nodes if n.parent is node and not n.reverted()
                 if (t := token_transfer(n, accounts)) is not None]
    source, destination = (vault, user_token) if buy else (user_token, vault)
    matches = [t for t in transfers if t["source"] == source and t["destination"] == destination
               and t["mint"] == mint and t["amount"] == event["token_amount"]]
    if len(matches) != 1:
        raise DecodeError("Pump.fun event does not match its executed base-token transfer")
    native = quote_mint in {WSOL, SYSTEM}
    if not native and (event.get("quote_mint") != quote_mint or "quote_amount" not in event):
        raise DecodeError("Missing matching quote-token fields in Pump.fun v2 event")
    quote_amount = event["sol_amount"] if native else event["quote_amount"]
    quote_mint = None if native else quote_mint
    input_mint, output_mint = (quote_mint, mint) if buy else (mint, quote_mint)
    input_amount, output_amount = (quote_amount, event["token_amount"]) if buy else (event["token_amount"], quote_amount)
    return {"pool": curve, "trader": user, **amounts(input_mint, output_mint, input_amount, output_amount, decimals),
            "amount_basis": "Pump.fun TradeEvent amounts before separately charged trade/creator/buyback fees",
            "event_fees_raw": {k: str(event[k]) if k in event else None for k in ("fee", "creator_fee", "buyback_fee", "cashback")},
            "evidence": "pump_trade_event_and_executed_base_token_transfer", "transfers": [public_transfer(t) for t in matches]}


def decode_moonit(node: Instruction, layout: dict, nodes: list[Instruction], accounts: dict, decimals: dict) -> dict:
    """Moonit can move owned lamports without a System CPI; use its trade event."""
    verify_scope(node, nodes)
    a, offset = node.ix["accounts"], layout["account_offset"]
    trader, user_token, curve, vault, mint = (a[i + offset] for i in (0, 1, 2, 3, 6))
    buy = layout["instruction"].startswith("buy")
    matches = []
    for raw in set(node.events):
        # Five u64 fields, curve/cost_token/sender pubkeys, TradeType, then label.
        if len(raw) < 149 or raw[:8] != discriminator("TradeEvent", "event"):
            continue
        if b58encode(raw[48:80]) != curve or b58encode(raw[112:144]) != trader or raw[144] != (0 if buy else 1):
            continue
        if b58encode(raw[80:112]) not in {SYSTEM, WSOL}:
            raise DecodeError("Moonit event uses an unsupported collateral currency")
        label_size = int.from_bytes(raw[145:149], "little")
        if len(raw) < 149 + label_size:
            raise DecodeError("Truncated Moonit TradeEvent")
        matches.append([int.from_bytes(raw[i:i + 8], "little") for i in (8, 16, 24, 32)])
    if len(matches) != 1:
        raise DecodeError("Expected exactly one matching Moonit TradeEvent")
    token_amount, sol_amount, dex_fee, helio_fee = matches[0]
    source, destination = (vault, user_token) if buy else (user_token, vault)
    transfers = [t for n in nodes if n.parent is node and not n.reverted()
                 if (t := token_transfer(n, accounts)) is not None
                 and t["source"] == source and t["destination"] == destination and t["mint"] == mint
                 and t["amount"] == token_amount]
    if len(transfers) != 1:
        raise DecodeError("Moonit event does not match the executed base-token transfer")
    return {"pool": curve, "trader": trader,
            **amounts(None if buy else mint, mint if buy else None,
                      sol_amount if buy else token_amount, token_amount if buy else sol_amount, decimals),
            "amount_basis": "Moonit TradeEvent exchange amounts; dex_fee and helio_fee reported separately",
            "event_fees_raw": {"dex_fee": str(dex_fee), "helio_fee": str(helio_fee)},
            "evidence": "moonit_trade_event_and_executed_base_token_transfer",
            "transfers": [public_transfer(t) for t in transfers]}


def decode_boop(node: Instruction, layout: dict, nodes: list[Instruction], accounts: dict, decimals: dict) -> dict:
    """Verify Boop's trade event against both the SPL and native SOL transfers."""
    verify_scope(node, nodes)
    a, buy = node.ix["accounts"], layout["instruction"] == "buy_token"
    mint, pool, fee_vault, vault, sol_vault, user_token, trader = a[:7]
    recipient = trader if buy else a[7]
    tag = discriminator("TokenBoughtEvent" if buy else "TokenSoldEvent", "event")
    events = [raw for raw in node.events if raw[:8] == tag and len(raw) == 128
              and b58encode(raw[8:40]) == mint and b58encode(raw[64:96]) == trader
              and b58encode(raw[96:128]) == recipient]
    if len(events) != 1:
        raise DecodeError("Expected exactly one matching Boop.fun trade event")
    event = events[0]
    amount_in, amount_out, swap_fee = (int.from_bytes(event[i:i + 8], "little") for i in (40, 48, 56))
    token_amount, sol_amount = (amount_out, amount_in) if buy else (amount_in, amount_out)
    source, destination = (vault, user_token) if buy else (user_token, vault)
    transfers = [t for child in nodes if child.parent is node and not child.reverted()
                 if (t := token_transfer(child, accounts)) is not None
                 and t["source"] == source and t["destination"] == destination and t["mint"] == mint]
    if len(transfers) != 1 or transfers[0]["amount"] != token_amount:
        raise DecodeError("Boop.fun event does not match its executed base-token transfer")
    sol = []
    for child in nodes:
        parsed = child.ix.get("parsed", {})
        if child.parent is node and child.program == SYSTEM and not child.reverted() and parsed.get("type") == "transfer":
            sol.append(parsed["info"])
    sol_source, sol_destination = (trader, sol_vault) if buy else (sol_vault, recipient)
    exchanged = sum(int(t["lamports"]) for t in sol if t["source"] == sol_source and t["destination"] == sol_destination)
    paid_fee = sum(int(t["lamports"]) for t in sol if t["source"] == sol_source and t["destination"] == fee_vault)
    if exchanged != sol_amount or paid_fee != swap_fee:
        raise DecodeError("Boop.fun event does not match its executed SOL/fee transfers")
    return {"pool": pool, "trader": trader, "recipient": recipient,
            **amounts(None if buy else mint, mint if buy else None, amount_in, amount_out, decimals),
            "amount_basis": "Boop.fun executed exchange: buy SOL excludes the separate fee; sell SOL is net of the fee",
            "event_fees_raw": {"swap_fee": str(swap_fee)},
            "evidence": "boop_trade_event_and_executed_token_and_sol_transfers",
            "native_transfers": [{**t, "lamports": str(t["lamports"])} for t in sol
                                 if t["source"] == sol_source and t["destination"] in {sol_destination, fee_vault}],
            "transfers": [public_transfer(t) for t in transfers]}


def decode_token_conversion(node: Instruction, layout: dict, nodes: list[Instruction], accounts: dict, decimals: dict) -> dict:
    """Explicit conversion entry points burn the input and mint the output."""
    verify_scope(node, nodes)
    actions = []
    for child in nodes:
        if child.program not in {TOKEN, TOKEN2022} or child.reverted() or not transfer_in_scope(child, node, layout.get("nested_programs", ())):
            continue
        parsed = child.ix.get("parsed", {})
        kind, info = parsed.get("type"), parsed.get("info", {})
        if kind not in {"burn", "burnChecked", "mintTo", "mintToChecked"}:
            continue
        side = "input" if kind.startswith("burn") else "output"
        if info.get("account") != layout[f"{side}_account"] or info.get("mint") != layout[f"{side}_mint"]:
            continue
        value = int(info.get("amount", info.get("tokenAmount", {}).get("amount", 0)))
        actions.append({"kind": kind, "side": side, "account": info["account"], "mint": info["mint"],
                        "amount_raw": str(value), "token_program": child.program, **child.location()})
    inputs, outputs = ([a for a in actions if a["side"] == side] for side in ("input", "output"))
    if len(inputs) != 1 or len(outputs) != 1 or layout["input_mint"] == layout["output_mint"]:
        raise DecodeError("Expected one executed input burn and output mint in this conversion")
    amount_in, amount_out = int(inputs[0]["amount_raw"]), int(outputs[0]["amount_raw"])
    if amount_in <= 0 or amount_out <= 0:
        raise DecodeError("Token conversion has no positive exchange")
    return {"pool": layout["pool"], "trader": layout["trader"], "venue_type": layout["venue_type"],
            **amounts(layout["input_mint"], layout["output_mint"], amount_in, amount_out, decimals),
            "amount_basis": "executed user-token burn and mint; underlying backing transfers excluded",
            "evidence": "executed_token_burn_and_mint", "token_actions": actions, "transfers": []}


def decode_receipt_conversion(node: Instruction, layout: dict, nodes: list[Instruction], accounts: dict, decimals: dict) -> dict:
    """Match each declared conversion side to committed token/native actions."""
    verify_scope(node, nodes)
    transfers, actions, native, sides = [], [], [], []
    for side_name in ("input", "output"):
        spec = layout[side_name]
        kind, user, vault = spec["kind"], spec["account"], spec["vault"]
        mint = spec["mint"] or accounts.get(user, {}).get("mint")
        total, matched = 0, False
        for child in nodes:
            if child.reverted() or not transfer_in_scope(child, node, layout.get("nested_programs", ())):
                continue
            parsed = child.ix.get("parsed", {})
            info, action = parsed.get("info", {}), parsed.get("type")
            if kind == "transfer":
                t = token_transfer(child, accounts)
                if t is None or user == vault:
                    continue
                src, dst = (user, vault) if side_name == "input" else (vault, user)
                sign = 1 if (t["source"], t["destination"]) == (src, dst) else -1 if (t["source"], t["destination"]) == (dst, src) else 0
                if not sign:
                    continue
                if not t["mint"] or (mint and t["mint"] != mint):
                    raise DecodeError("Conversion transfer mint does not match its declared token account")
                mint = t["mint"]
                total += sign * t["amount"]
                transfers.append(public_transfer(t))
            elif kind in ("burn", "mint"):
                if child.program not in {TOKEN, TOKEN2022} or action not in {"burn", "burnChecked", "mintTo", "mintToChecked"} or info.get("account") != user:
                    continue
                if not info.get("mint") or (mint and info["mint"] != mint):
                    raise DecodeError("Conversion mint/burn does not match its declared mint")
                mint = info["mint"]
                sign = 1 if action.startswith(kind) else -1
                value = int(info.get("amount", info.get("tokenAmount", {}).get("amount", 0)))
                total += sign * value
                if "tokenAmount" in info:
                    decimals[mint] = int(info["tokenAmount"]["decimals"])
                actions.append({"kind": action, "side": side_name, "account": user, "mint": mint,
                                "amount_raw": str(value), "token_program": child.program, **child.location()})
            elif kind == "native":
                if child.program == SYSTEM and action == "transfer":
                    src, dst = info.get("source"), info.get("destination")
                elif child.program == "Stake11111111111111111111111111111111111111" and action == "withdraw":
                    src, dst = info.get("stakeAccount"), info.get("destination")
                else:
                    continue
                expected = (user, vault) if side_name == "input" else (vault, user)
                if (src, dst) != expected:
                    continue
                value = int(info["lamports"])
                total += value
                native.append({"source": src, "destination": dst, "lamports": str(value), **child.location()})
                mint = None
            else:
                raise DecodeError("Unknown conversion evidence kind")
            matched = True
        if not matched or total <= 0 or (kind != "native" and not mint):
            raise DecodeError(f"Missing positive executed {side_name} {kind} for this conversion")
        sides.append((mint, total))
    if sides[0][0] == sides[1][0]:
        raise DecodeError("Conversion must exchange distinct assets")
    return {"pool": layout["pool"], "trader": layout["trader"], "venue_type": layout["venue_type"],
            **amounts(sides[0][0], sides[1][0], sides[0][1], sides[1][1], decimals),
            "amount_basis": "executed transfers, burns and mints at the declared conversion boundary, net of refunds; separate fees excluded",
            "evidence": "executed_conversion_actions", "transfers": transfers,
            "token_actions": actions, "native_transfers": native}


def decode_heaven_initial_buy(node: Instruction, layout: dict, nodes: list[Instruction], accounts: dict, decimals: dict) -> dict | None:
    """Pool creation seeds liquidity first; only the subsequent purchase is a swap."""
    verify_scope(node, nodes)
    if not layout["requested_tokens"]:
        return None
    a = node.ix["accounts"]
    if a[6] != WSOL:
        raise DecodeError("Unknown Heaven initial-purchase quote asset")
    transfers = [t for child in nodes if child.parent is node and not child.reverted()
                 if (t := token_transfer(child, accounts)) is not None]
    purchased = [t for t in transfers if t["source"] == a[8] and t["destination"] == a[7] and t["mint"] == a[5]]
    if len(purchased) != 1 or purchased[0]["amount"] != layout["requested_tokens"]:
        raise DecodeError("Heaven initial purchase does not match its executed token delivery")
    native = [child.ix["parsed"]["info"] for child in nodes
              if child.parent is node and not child.reverted() and child.program == SYSTEM
              and child.ix.get("parsed", {}).get("type") == "transfer"
              and child.ix["parsed"]["info"]["source"] == a[4]
              and child.ix["parsed"]["info"]["destination"] == a[9]]
    if len(native) != 1 or int(native[0]["lamports"]) <= 0:
        raise DecodeError("Missing unambiguous Heaven initial-purchase SOL payment")
    return {"pool": a[10], "trader": a[3], "payer": a[4], "suboperation": "initial_buy",
            **amounts(None, a[5], int(native[0]["lamports"]), purchased[0]["amount"], decimals),
            "amount_basis": "initial purchase SOL payment and gross token delivery; pool seed liquidity and rent excluded",
            "evidence": "heaven_initial_purchase_executed_transfers",
            "native_transfers": [{**native[0], "lamports": str(native[0]["lamports"])}],
            "transfers": [public_transfer(purchased[0])]}


def trench_trade_event(raw: bytes, buy: bool) -> dict:
    """Borsh event layout published in Trench's on-chain IDL, including referrals."""
    pos, result = 8, {}
    def take(size):
        nonlocal pos
        if pos + size > len(raw):
            raise DecodeError("Truncated Trench trade event")
        data = raw[pos:pos + size]
        pos += size
        return data
    def number():
        return int.from_bytes(take(8), "little")
    for name in ("trader", "mint", "protocol_fee_recipient", "creator", "creator_vault", "cashback_vault"):
        result[name] = b58encode(take(32))
    names = (("sol_in", "sol_to_curve", "sol_to_fee", "protocol_fee", "creator_fee", "cashback") if buy else
             ("tokens_in", "sol_gross", "sol_to_user", "sol_to_fee", "protocol_fee", "creator_fee", "cashback"))
    for name in names:
        result[name] = number()
    for referral in ("direct", "indirect"):
        for field in ("profile", "wallet"):
            present = take(1)[0]
            if present not in (0, 1):
                raise DecodeError("Invalid Trench optional referral")
            result[f"{referral}_{field}"] = b58encode(take(32)) if present else None
        result[f"{referral}_fee"] = number()
    if buy:
        result["tokens_out"] = number()
    take(40)  # Four reserve values and timestamp, unused in amount calculation.
    if pos != len(raw):
        raise DecodeError("Unknown Trench trade event version")
    return result


def decode_trench(node: Instruction, layout: dict, nodes: list[Instruction], accounts: dict, decimals: dict) -> dict:
    verify_scope(node, nodes)
    a, buy = node.ix["accounts"], layout["instruction"] != "sell"
    tag = discriminator("BuyEvent" if buy else "SellEvent", "event")
    payloads = set(node.events)
    for child in nodes:
        if child.parent is node and child.program == node.program and not child.reverted():
            raw = b58decode(child.ix.get("data", ""))
            if raw.startswith(EVENT_CPI_TAG):
                payloads.add(raw[8:])
    events = [trench_trade_event(raw, buy) for raw in payloads if raw[:8] == tag]
    if len(events) != 1:
        raise DecodeError("Expected exactly one Trench trade event")
    event = events[0]
    if any(event[key] != a[index] for key, index in
           (("trader", 0), ("mint", 3), ("protocol_fee_recipient", 11), ("creator_vault", 8), ("cashback_vault", 9))):
        raise DecodeError("Trench event identities disagree with the instruction")
    token_amount = event["tokens_out" if buy else "tokens_in"]
    source, destination = (a[5], a[6]) if buy else (a[6], a[5])
    transfers = [t for child in nodes if child.parent is node and not child.reverted()
                 if (t := token_transfer(child, accounts)) is not None
                 and t["source"] == source and t["destination"] == destination and t["mint"] == a[3]]
    if len(transfers) != 1 or transfers[0]["amount"] != token_amount:
        raise DecodeError("Trench event does not match its executed token transfer")
    fees = {key: event[key] for key in ("protocol_fee", "creator_fee", "cashback", "direct_fee", "indirect_fee")}
    if sum(fees.values()) != event["sol_to_fee"]:
        raise DecodeError("Trench fee fields disagree")
    if buy:
        paid = sum(int(child.ix["parsed"]["info"]["lamports"]) for child in nodes
                   if child.parent is node and not child.reverted() and child.program == SYSTEM
                   and child.ix.get("parsed", {}).get("type") == "transfer"
                   and child.ix["parsed"]["info"]["source"] == a[0]
                   and child.ix["parsed"]["info"]["destination"] == a[4])
        if paid != event["sol_to_curve"] or event["sol_in"] != paid + event["sol_to_fee"]:
            raise DecodeError("Trench event disagrees with the executed SOL payment")
        sol_amount = paid
    else:
        # Selling updates lamports directly, without a System Program CPI.
        if event["sol_gross"] != event["sol_to_user"] + event["sol_to_fee"]:
            raise DecodeError("Trench sell proceeds and fee fields disagree")
        sol_amount = event["sol_to_user"]
    if token_amount <= 0 or sol_amount <= 0:
        raise DecodeError("Trench event has no positive exchange")
    return {"pool": a[4], "trader": a[0],
            **amounts(None if buy else a[3], a[3] if buy else None,
                      sol_amount if buy else token_amount, token_amount if buy else sol_amount, decimals),
            "amount_basis": "Trench buy SOL excludes separate fees; sell SOL is the event's net user receipt",
            "event_fees_raw": {k: str(v) for k, v in fees.items()},
            "evidence": "trench_trade_event_and_executed_token_transfer" + ("_and_sol_payment" if buy else ""),
            "transfers": [public_transfer(t) for t in transfers]}


def market_metadata(program: str, market: str, saved: dict, decimals: dict) -> dict:
    """Read only immutable mint/lot configuration, never current balances/prices."""
    value = saved.get(market)
    if not value:
        raise MarketMetadataRequired(market)
    if value.get("owner") != program:
        raise DecodeError("Market metadata account has the wrong program owner")
    try:
        data, encoding = value["data"]
        if encoding != "base64":
            raise ValueError()
        raw = base64.b64decode(data, validate=True)
    except (KeyError, ValueError, TypeError):
        raise DecodeError("Invalid saved market account data") from None
    u = lambda pos, size=8: int.from_bytes(raw[pos:pos + size], "little")
    if program == PHOENIX and len(raw) >= 200 and raw[:8] == bytes.fromhex("77df7173b7205871"):
        result = {"base_mint": b58encode(raw[48:80]), "quote_mint": b58encode(raw[128:160]),
                  "base_vault": b58encode(raw[80:112]), "quote_vault": b58encode(raw[160:192]),
                  "base_lot_size": u(112), "quote_lot_size": u(192)}
        db, dq = u(40, 4), u(120, 4)
        if not result["base_lot_size"] or not result["quote_lot_size"]:
            raise DecodeError("Invalid Phoenix lot sizes")
    elif program == OPENBOOK_V2 and len(raw) >= 712 and raw[:8] == discriminator("Market", "account"):
        result = {"base_mint": b58encode(raw[576:608]), "quote_mint": b58encode(raw[608:640]),
                  "base_vault": b58encode(raw[640:672]), "quote_vault": b58encode(raw[680:712])}
        db, dq = raw[9], raw[10]
    else:
        raise DecodeError("Unknown or truncated order-book market account layout")
    if db > 255 or dq > 255 or result["base_mint"] == result["quote_mint"]:
        raise DecodeError("Invalid order-book mint configuration")
    for mint, count in ((result["base_mint"], db), (result["quote_mint"], dq)):
        if mint in decimals and decimals[mint] != count:
            raise DecodeError("Market metadata decimals conflict with the transaction")
        decimals[mint] = count
    return result


def phoenix_events(node: Instruction, layout: dict, nodes: list[Instruction]) -> list[tuple[int, bytes]]:
    """Phoenix records Borsh enum events in authenticated self-CPI Log calls."""
    widths = {2: 66, 3: 42, 4: 34, 5: 58, 6: 42, 7: 10, 8: 26, 9: 58}
    events = []
    for child in nodes:
        if child.parent is not node or child.program != PHOENIX or child.reverted():
            continue
        raw = b58decode(child.ix.get("data", ""))
        if raw[:1] != b"\x0f":
            continue
        if (len(raw) < 93 or raw[1] != 1 or raw[2] != layout["tag"]
                or b58encode(raw[27:59]) != layout["pool"] or b58encode(raw[59:91]) != layout["trader"]):
            raise DecodeError("Phoenix event header does not match this invocation")
        pos = 93
        for _ in range(int.from_bytes(raw[91:93], "little")):
            tag = raw[pos] if pos < len(raw) else -1
            size = widths.get(tag)
            if size is None or pos + 1 + size > len(raw):
                raise DecodeError("Unknown or truncated Phoenix market event")
            body = raw[pos + 1:pos + 1 + size]
            if int.from_bytes(body[:2], "little") != len(events):
                raise DecodeError("Missing or duplicate Phoenix event batch")
            events.append((tag, body))
            pos += size + 1
        if pos != len(raw):
            raise DecodeError("Unexpected trailing Phoenix event data")
    return events


def decode_orderbook(node: Instruction, layout: dict, nodes: list[Instruction], accounts: dict,
                     decimals: dict, saved_markets: dict) -> list[dict]:
    verify_scope(node, nodes)
    book, pool, trader = layout["orderbook"], layout["pool"], layout["trader"]
    if book == "phoenix":
        events = phoenix_events(node, layout, nodes)
        if not events and layout["tag"] in (0, 1):
            raise DecodeError("Missing Phoenix market event trace for the swap")
        fills = [body for tag, body in events if tag == 2]
        summaries = [body for tag, body in events if tag == 6]
        if not fills:
            if any(int.from_bytes(body[18:26], "little") for body in summaries):
                raise DecodeError("Phoenix fill summary has no corresponding fills")
            return []  # Unfilled IOC or posted limit order; no executed swap.
        if len(summaries) != 1:
            raise DecodeError("Expected one Phoenix FillSummary for the filled order")
        summary = summaries[0]
        base_lots, quote_lots, fee_lots = (int.from_bytes(summary[i:i + 8], "little") for i in (18, 26, 34))
        if not base_lots or base_lots != sum(int.from_bytes(body[50:58], "little") for body in fills) or not quote_lots:
            raise DecodeError("Phoenix FillSummary does not agree with its maker fills")
        if layout["tag"] == 0:
            # Swap cannot use deposited funds; exact transfers suffice, no metadata RPC.
            a = node.ix["accounts"]
            swap = decode_token_swap(node, {"pool": pool, "trader": trader,
                                     "user_accounts": [a[4], a[5]], "vaults": [a[6], a[7]]}, nodes, accounts, decimals)
            base_mint = accounts.get(a[6], {}).get("mint")
            quote_mint = accounts.get(a[7], {}).get("mint")
            expected = (quote_mint, base_mint) if layout["side"] == 0 else (base_mint, quote_mint)
            if (swap["token_in"]["mint"], swap["token_out"]["mint"]) != expected:
                raise DecodeError("Phoenix order side conflicts with its executed transfers")
            base_amount, quote_amount = ((int(swap["amount_out_raw"]), int(swap["amount_in_raw"]))
                                        if layout["side"] == 0 else (int(swap["amount_in_raw"]), int(swap["amount_out_raw"])))
            if base_amount % base_lots or quote_amount % quote_lots:
                raise DecodeError("Phoenix transfers are inconsistent with whole token lots")
            swap["event_fees_raw"] = {"quote_fee": str(fee_lots * (quote_amount // quote_lots))}
            swap["evidence"] = "phoenix_fill_summary_and_executed_token_transfers"
        else:
            m = market_metadata(PHOENIX, pool, saved_markets, decimals)
            if layout["tag"] == 2:
                a = node.ix["accounts"]
                if len(a) < 10 or (a[7], a[8]) != (m["base_vault"], m["quote_vault"]):
                    raise DecodeError("Phoenix market metadata vaults do not match the order")
            base_amount, quote_amount = base_lots * m["base_lot_size"], quote_lots * m["quote_lot_size"]
            buy = layout["side"] == 0
            swap = {"pool": pool, "trader": trader,
                    **amounts(m["quote_mint"] if buy else m["base_mint"], m["base_mint"] if buy else m["quote_mint"],
                              quote_amount if buy else base_amount, base_amount if buy else quote_amount, decimals),
                    "amount_basis": "Phoenix FillSummary lots converted with immutable market lot sizes; quote amount includes taker fees",
                    "event_fees_raw": {"quote_fee": str(fee_lots * m["quote_lot_size"])},
                    "evidence": "phoenix_fill_summary_and_market_lot_sizes", "transfers": []}
        return [{**swap, "fill_count": len(fills), "client_order_id": str(int.from_bytes(summary[2:18], "little")),
                 "base_lots_filled": str(base_lots), "quote_lots_filled": str(quote_lots)}]
    if book == "openbook":
        events = [e for e in node.events if e[:8] == discriminator("TotalOrderFillEvent", "event")]
        if not events:
            return []
        decoded = []
        # Multiple-order instructions expose both vaults; single orders may only
        # expose the deposit side and therefore need the market mint configuration.
        base = accounts.get(layout.get("base_vault"), {}).get("mint")
        quote = accounts.get(layout.get("quote_vault"), {}).get("mint")
        if not base or not quote:
            m = market_metadata(OPENBOOK_V2, pool, saved_markets, decimals)
            base, quote = m["base_mint"], m["quote_mint"]
        for index, e in enumerate(events):
            if len(e) != 65 or e[8] not in (0, 1) or b58encode(e[9:41]) not in {trader, layout["open_orders"]}:
                raise DecodeError("OpenBook fill event does not match this order")
            paid, received, fee = (int.from_bytes(e[i:i + 8], "little") for i in (41, 49, 57))
            if not paid or not received:
                raise DecodeError("OpenBook emitted an invalid zero-amount fill")
            buy = e[8] == 0
            decoded.append({"pool": pool, "trader": trader, "open_orders": layout["open_orders"],
                            **amounts(quote if buy else base, base if buy else quote, paid, received, decimals),
                            "amount_basis": "OpenBook TotalOrderFillEvent native amounts including taker fees; unfilled deposits excluded",
                            "event_fees_raw": {"quote_fee": str(fee)}, "order_event_index": index,
                            "evidence": "openbook_total_order_fill_event", "transfers": []})
        return decoded
    if book == "manifest":
        groups = {}
        for e in node.events:
            if e[:8] != bytes.fromhex("3ae6f2034b7104a9"):
                continue
            if len(e) != 232 or b58encode(e[8:40]) != pool or b58encode(e[72:104]) != trader or e[216] not in (0, 1):
                raise DecodeError("Manifest FillLog does not match this batch update")
            base, quote = b58encode(e[104:136]), b58encode(e[136:168])
            base_amount, quote_amount = (int.from_bytes(e[i:i + 8], "little") for i in (184, 192))
            if not base_amount or not quote_amount or base == quote:
                raise DecodeError("Manifest emitted an invalid zero-amount or same-mint fill")
            sequence = str(int.from_bytes(e[208:216], "little"))
            key = (sequence, e[216], base, quote)
            group = groups.setdefault(key, {"base": 0, "quote": 0, "fills": []})
            group["base"] += base_amount
            group["quote"] += quote_amount
            group["fills"].append({"maker": b58encode(e[40:72]), "base_amount_raw": str(base_amount),
                                   "quote_amount_raw": str(quote_amount),
                                   "maker_order_sequence": str(int.from_bytes(e[200:208], "little"))})
        return [{"pool": pool, "trader": trader,
                 **amounts(quote if buy else base, base if buy else quote,
                           group["quote"] if buy else group["base"], group["base"] if buy else group["quote"], decimals),
                 "amount_basis": "Manifest FillLog traded atom amounts; settlement may occur separately",
                 "order_sequence": sequence, "fill_count": len(group["fills"]), "fills": group["fills"],
                 "evidence": "manifest_fill_logs", "transfers": []}
                for (sequence, buy, base, quote), group in groups.items()]
    raise DecodeError("Unknown order-book decoder")


def decode_transaction(tx: dict, market_accounts: dict | None = None) -> dict:
    """Decode a jsonParsed getTransaction result (or its JSON-RPC envelope)."""
    if isinstance(tx, dict) and "result" in tx:
        tx = tx["result"]
    if not isinstance(tx, dict) or not tx.get("transaction") or not isinstance(tx.get("meta"), dict):
        raise DecodeError("Missing transaction or metadata in RPC response")
    message, meta = tx["transaction"]["message"], tx["meta"]
    saved_markets = {**tx.get("_swap_decoder_market_accounts", {}), **(market_accounts or {})}
    block_time = tx.get("blockTime")
    keys = message["accountKeys"]
    result: dict[str, Any] = {
        "signature": tx["transaction"]["signatures"][0], "slot": tx["slot"],
        "block_time": block_time, "timestamp_utc": datetime.fromtimestamp(block_time, timezone.utc).isoformat() if block_time is not None else None,
        "transaction_version": tx.get("version"), "success": meta.get("err") is None,
        "transaction_error": meta.get("err"), "fee_lamports": meta.get("fee"),
        "fee_payer": keys[0]["pubkey"] if isinstance(keys[0], dict) else keys[0],
        "signers": [k["pubkey"] for k in keys if isinstance(k, dict) and k.get("signer")],
        "swaps": [], "undecoded_swaps": [], "unclassified_instructions": [], "non_swap_instructions": [], "warnings": [],
    }
    if not result["success"]:
        result["warnings"].append("Transaction failed: all swaps reverted; transaction fees may still be charged")
        return result
    nodes = instruction_tree(tx, result["warnings"])
    accounts, decimals = token_metadata(tx, nodes)
    conversion_wrappers = []
    for node in nodes:
        if node.reverted() or node.program in CORE_PROGRAMS:
            continue
        raw = b58decode(node.ix.get("data", ""))
        if (node.program in DEX_NAMES or node.program in ROUTERS) and raw.startswith(EVENT_CPI_TAG):
            continue
        if node.program == PHOENIX and raw[:1] == b"\x0f" and node.parent and node.parent.program == PHOENIX:
            continue  # Fill records are consumed once by the parent order.
        identity = {"program_id": node.program, "dex": DEX_NAMES.get(node.program), **node.location()}
        try:
            if (node.program == SCORCH_PRICING and len(raw) == 25 and node.parent is not None
                    and node.parent.program == "SCoRcH8c2dpjvcJD6FiPbCSQyQgu3PcUAWj2Xxx3mqn"):
                verify_scope(node, nodes)
                result["non_swap_instructions"].append({**identity, "instruction": "pricing_cpi",
                    "execution_program_id": node.parent.program, "execution_location": node.parent.location(),
                    "reason": "Pricing CPI inside Scorch execution; the parent owns the token exchange"})
                continue
            layouts = registry_layouts(node.program, raw, node.ix.get("accounts", []))
            if layouts is None:
                layouts = expanded_layouts(node.program, raw, node.ix.get("accounts", []))
            if layouts is None:
                layout = swap_layout(node, raw)
                layouts = [layout] if layout else None
            if layouts:
                parents, parent = [], node.parent
                while parent is not None:
                    parents.append(parent.program)
                    parent = parent.parent
                # Decode all legs before appending: a partial two-hop result must
                # remain visibly incomplete, never silently lose its second pool.
                decoded_legs = []
                for layout in layouts:
                    identity["instruction"] = layout["instruction"]
                    if layout.get("receipt_conversion") and any(node.is_within(wrapper) for wrapper in conversion_wrappers):
                        continue
                    decode = (decode_pump if layout.get("pump_event") else
                              decode_moonit if layout.get("moonit_event") else
                              decode_boop if layout.get("boop_event") else
                              decode_heaven_initial_buy if layout.get("heaven_initial_buy") else
                              decode_token_conversion if layout.get("token_conversion") else
                              decode_receipt_conversion if layout.get("receipt_conversion") else
                              decode_trench if layout.get("trench_event") else decode_token_swap)
                    swaps = (decode_orderbook(node, layout, nodes, accounts, decimals, saved_markets) if layout.get("orderbook")
                             else [decode(node, layout, nodes, accounts, decimals)])
                    for index, swap in enumerate(swaps):
                        if swap is None:  # Pool creation without an initial purchase.
                            continue
                        decoded_legs.append({**identity, **swap, "hop_index": layout.get("hop_index", index),
                                             "stack_height": node.depth, "parent_programs": list(reversed(parents))})
                    if swaps and layout.get("suppress_nested_conversions"):
                        conversion_wrappers.append(node)
                for swap in decoded_legs:
                    result["swaps"].append({"swap_index": len(result["swaps"]), **swap})
            elif node.program in ROUTERS and any(n.is_within(node) for n in nodes):
                continue  # Report the underlying pool swaps, not an additional router swap.
            else:
                result["unclassified_instructions"].append({**identity, "data_prefix_hex": raw[:8].hex()})
        except (DecodeError, LayoutError, IndexError, KeyError, UnicodeError) as exc:
            extra = {"required_market_account": exc.market} if isinstance(exc, MarketMetadataRequired) else {}
            result["undecoded_swaps"].append({**identity, "reason": str(exc), **extra})
    if result["unclassified_instructions"]:
        result["warnings"].append("Unclassified application instructions remain; these may be non-swap actions or unsupported swaps")
    if result["undecoded_swaps"]:
        result["warnings"].append("Some recognized swaps could not be decoded without guessing; see undecoded_swaps")
    if any(s[side]["decimals"] is None for s in result["swaps"] for side in ("token_in", "token_out")):
        result["warnings"].append("Some token decimals are unavailable; raw amounts remain exact")
    if any(t["token_program"] == TOKEN2022 and t["fee_raw"] is None for s in result["swaps"] for t in s["transfers"]):
        result["warnings"].append("Token-2022 transfer fees are not fully exposed; gross transfers may differ from net token receipts")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("signature", nargs="?", help="Solana transaction signature")
    parser.add_argument("--from-json", type=Path, help="Decode saved jsonParsed RPC data without network access")
    parser.add_argument("--output", type=Path, help="Save decoded JSON here instead of stdout")
    parser.add_argument("--raw-output", type=Path, help="Also save the jsonParsed RPC result")
    parser.add_argument("--market-accounts", type=Path, help="Offline market pubkey -> base64 RPC account mapping for order-book fills")
    parser.add_argument("--timeout", type=float, default=30, help="RPC timeout in seconds (default: 30)")
    parser.add_argument("--list-protocols", action="store_true", help="List supported programs/instruction families without RPC")
    args = parser.parse_args()
    try:
        if args.list_protocols:
            catalog = [
                (RAYDIUM, ["swap_base_in", "swap_base_out", "swap_base_in_v2", "swap_base_out_v2"]),
                (CLMM, ["swap", "swap_v2"]), (CPMM, ["swap_base_input", "swap_base_output"]),
                (ORCA, ["swap", "swap_v2", "two_hop_swap", "two_hop_swap_v2"]),
                (DLMM, ["swap", "swap2", "swap_exact_out", "swap_exact_out2", "swap_with_price_impact", "swap_with_price_impact2"]),
                (PUMP_AMM, ["buy", "buy_exact_quote_in", "sell"]),
                (PUMP, ["buy", "sell", "buy_exact_sol_in", "buy_v2", "sell_v2", "buy_exact_quote_in_v2"]),
            ]
            print(json.dumps([{"program_id": p, "dex": DEX_NAMES[p], "instructions": names} for p, names in catalog]
                             + extra_catalog() + registry_catalog(), indent=2))
            return 0
        if args.timeout <= 0:
            raise DecodeError("Timeout must be positive")
        if args.from_json:
            tx = json.loads(args.from_json.read_text())
        else:
            if not args.signature:
                parser.error("Provide a transaction signature or --from-json")
            rpc_url = os.environ.get("SOLANA_RPC_URL", "")
            if not rpc_url:
                raise DecodeError("Set SOLANA_RPC_URL to your RPC endpoint")
            tx = fetch_transaction(args.signature, rpc_url, args.timeout)
        market_accounts = json.loads(args.market_accounts.read_text()) if args.market_accounts else None
        decoded = decode_transaction(tx, market_accounts)
        needed = sorted({s["required_market_account"] for s in decoded["undecoded_swaps"] if "required_market_account" in s})
        if needed and not args.from_json:
            try:
                # Only immutable configuration is used. These current account
                # snapshots are never used to infer historical balances or fills.
                reply = rpc_call("getMultipleAccounts", [needed, {"encoding": "base64", "commitment": "finalized",
                                 "dataSlice": {"offset": 0, "length": 800}}], rpc_url, args.timeout)
                tx["_swap_decoder_market_accounts"] = {**tx.get("_swap_decoder_market_accounts", {}),
                                                       **dict(zip(needed, reply["value"], strict=True))}
                decoded = decode_transaction(tx, market_accounts)
            except (DecodeError, KeyError, TypeError, ValueError) as exc:
                decoded["warnings"].append(f"Could not load order-book market configuration: {exc}")
        if args.signature and decoded["signature"] != args.signature:
            raise DecodeError("Saved transaction does not match the supplied signature")
        for path, content in ((args.raw_output, tx), (args.output, decoded)):
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(content, indent=2) + "\n")
        if not args.output:
            print(json.dumps(decoded, indent=2))
        return 0
    except (DecodeError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
