"""Real transfer fixtures plus isolated order-book scenarios built from real events.

The reconstructed order-book transactions are synthetic. Their event payloads and
immutable market headers come from RPC; no test pretends they were mined as built.
"""

import base64
import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import solana_decode_swaps as d
import solana_swap_protocols as p


FIXTURES = Path(__file__).parent / "fixtures" / "solana"
CASES = json.loads((FIXTURES / "extended_swap_cases.json").read_text())
MARKETS = json.loads((FIXTURES / "orderbook_market_accounts.json").read_text())


def load_case(program, instruction=None):
    case = next(c for c in CASES if c["program_id"] == program and
                (instruction is None or c["instruction"] == instruction))
    tx = json.loads((FIXTURES / case["fixture"]).read_text())
    nodes = d.instruction_tree(tx, [])
    node = next(n for n in nodes if (n.outer, n.inner) ==
                (case["outer_instruction_index"], case["inner_instruction_index"]))
    return tx, node, nodes


def standalone(program, accounts, raw, events=(), children=()):
    """A synthetic successful invocation with a complete, ordered CPI trace."""
    instructions = [{**copy.deepcopy(ix), "stackHeight": 2} for ix in children]
    logs = [f"Program {program} invoke [1]"]
    logs += ["Program data: " + base64.b64encode(e).decode() for e in events]
    for ix in instructions:
        logs += [f"Program {ix['programId']} invoke [2]", f"Program {ix['programId']} success"]
    logs += [f"Program {program} success"]
    return {"slot": 1, "version": 0, "transaction": {"signatures": [d.b58encode(bytes(64))],
            "message": {"accountKeys": list(dict.fromkeys(accounts)), "instructions": [
                {"programId": program, "accounts": accounts, "data": d.b58encode(raw), "stackHeight": 1}]}},
            "meta": {"err": None, "fee": 0, "logMessages": logs, "preTokenBalances": [], "postTokenBalances": [],
                     "innerInstructions": [{"index": 0, "instructions": instructions}]}}


def phoenix_funded(tag=1):
    _, node, nodes = load_case(p.PHOENIX)
    log = copy.deepcopy(next(n.ix for n in nodes if n.parent is node and n.program == p.PHOENIX))
    data = bytearray(d.b58decode(log["data"]))
    data[2] = tag
    log["data"] = d.b58encode(data)
    a = node.ix["accounts"]
    accounts = a[:4] + ["seat"] + (a[4:] if tag == 2 else [])
    # Valid Borsh order packets; the requested 100 lots exceed the 34 filled lots.
    u64 = lambda x: x.to_bytes(8, "little")
    if tag == 1:
        raw = bytes([tag, 2, 0, 0]) + u64(100) + u64(10_000_000) + bytes(16) + bytes(2) + bytes(16) + b"\x01\x00\x00"
    else:
        raw = bytes([tag, 1, 0]) + u64(1_000_000) + u64(100) + bytes(2) + bytes(16) + bytes(4)
    return standalone(p.PHOENIX, accounts, raw, children=[log])


def manifest_batch(extra_fill=False, second_order=False):
    _, node, _ = load_case(p.MANIFEST, "swap_v2")
    fill = next(e for e in node.events if e[:8] == bytes.fromhex("3ae6f2034b7104a9"))
    a = node.ix["accounts"]
    events = [fill]
    if extra_fill or second_order:
        other = bytearray(fill)
        other[200:208] = (int.from_bytes(other[200:208], "little") + 1).to_bytes(8, "little")
        if second_order:
            other[208:216] = (int.from_bytes(other[208:216], "little") + 1).to_bytes(8, "little")
        events.append(bytes(other))
    base = int.from_bytes(fill[184:192], "little") * (2 if extra_fill else 1)
    # One or two IOC orders with aggressive limits; no cancellations or index hint.
    order = base.to_bytes(8, "little") + (4_000_000_000 if fill[216] else 1).to_bytes(4, "little")
    order += bytes([0 if fill[216] else 247, fill[216]]) + bytes(4) + b"\x01"
    count = 2 if second_order else 1
    raw = b"\x06\x00" + bytes(4) + count.to_bytes(4, "little") + order * count
    return standalone(p.MANIFEST, [a[1], a[2], d.SYSTEM], raw, events=events)


def openbook_funded():
    _, node, _ = load_case(p.OPENBOOK_V2)
    event = next(e for e in node.events if e[:8] == d.discriminator("TotalOrderFillEvent", "event"))
    a = node.ix["accounts"]
    accounts = [a[0], "open_orders", p.OPENBOOK_V2, a[10], a[2], a[4], a[5], a[8], a[7], a[11], a[12], d.TOKEN]
    args = b"\x00" + b"".join(x.to_bytes(8, "little") for x in (1_000_000_000, 1000, 1_000_000_000_000, 0))
    args += b"\x01" + bytes(8) + b"\x00\x0a"  # IOC, no expiry, self-trade mode, match limit.
    return standalone(p.OPENBOOK_V2, accounts, d.discriminator("place_order") + args, events=[event])


class AllVenuesTest(unittest.TestCase):
    def test_24_added_programs_match_real_exact_swap_records(self):
        self.assertEqual(len({c["program_id"] for c in CASES}), 24)
        for case in CASES:
            with self.subTest(dex=case["dex"], instruction=case["instruction"]):
                result = d.decode_transaction(json.loads((FIXTURES / case["fixture"]).read_text()))
                matches = [s for s in result["swaps"] if (s["outer_instruction_index"], s["inner_instruction_index"])
                           == (case["outer_instruction_index"], case["inner_instruction_index"])]
                self.assertEqual(len(matches), 1)
                swap = matches[0]
                for key in ("program_id", "instruction", "pool", "trader", "amount_in_raw", "amount_out_raw"):
                    self.assertEqual(swap[key], case[key])
                self.assertEqual((swap["token_in"]["mint"], swap["token_out"]["mint"]), (case["mint_in"], case["mint_out"]))
                self.assertFalse(result["undecoded_swaps"])

    def test_new_venue_ids_do_not_turn_unknown_instructions_into_swaps(self):
        for case in CASES:
            tx, node, _ = load_case(case["program_id"], case["instruction"])
            node.ix["data"] = d.b58encode(b"\xff" * 8 + d.b58decode(node.ix["data"])[8:])
            if node.program == p.HUMIDIFI:
                node.ix["data"] = d.b58encode(bytes(65))  # Its oracle-update shape.
            result = d.decode_transaction(tx)
            self.assertFalse(any(s["outer_instruction_index"] == node.outer and s["inner_instruction_index"] == node.inner
                                 for s in result["swaps"]), case["dex"])

    def test_humidifi_invalid_deobfuscated_direction_stays_unclassified(self):
        tx, node, _ = load_case(p.HUMIDIFI, "swap_obfuscated")
        raw = bytearray(d.b58decode(node.ix["data"]))
        key = bytes.fromhex("3aff2fffe2baebc3")
        raw[16:24] = bytes(value ^ key[j] ^ (2 if j % 2 == 0 else 0)
                           for j, value in enumerate((2).to_bytes(8, "little")))
        node.ix["data"] = d.b58encode(raw)
        result = d.decode_transaction(tx)
        self.assertTrue(any(x["program_id"] == p.HUMIDIFI for x in result["unclassified_instructions"]))

    def test_unknown_proprietary_abi_version_is_explicitly_undecoded(self):
        tx, node, _ = load_case(p.SOLFI_V2)
        node.ix["data"] = d.b58encode(d.b58decode(node.ix["data"]) + bytes(100))
        result = d.decode_transaction(tx)
        self.assertTrue(any(x["program_id"] == p.SOLFI_V2 for x in result["undecoded_swaps"]))

    def test_perena_quote_instruction_is_not_a_swap(self):
        tx, node, _ = load_case(p.PERENA)
        node.ix["data"] = d.b58encode(d.discriminator("swap_exact_in_quote") + bytes(108))
        result = d.decode_transaction(tx)
        self.assertFalse(any(x["program_id"] == p.PERENA for x in result["swaps"]))

    def test_boop_rejects_event_that_disagrees_with_paid_sol(self):
        tx, node, nodes = load_case(p.BOOP, "buy_token")
        for child in nodes:
            parsed = child.ix.get("parsed", {})
            if child.parent is node and child.program == d.SYSTEM and parsed.get("type") == "transfer":
                parsed["info"]["lamports"] += 1
                break
        result = d.decode_transaction(tx)
        self.assertTrue(any(x["program_id"] == p.BOOP for x in result["undecoded_swaps"]))

    def test_phoenix_prefunded_and_partial_limit_fills_use_events(self):
        for tag in (1, 2):
            with self.subTest(tag=tag):
                result = d.decode_transaction(phoenix_funded(tag), MARKETS)
                self.assertFalse(result["undecoded_swaps"])
                swap = result["swaps"][0]
                self.assertEqual((swap["amount_in_raw"], swap["amount_out_raw"]), ("2931013", "3400"))
                self.assertEqual(swap["base_lots_filled"], "34")
                self.assertEqual(swap["event_fees_raw"], {"quote_fee": "587"})
                self.assertEqual(swap["transfers"], [])

    def test_phoenix_requires_lot_metadata_for_deposited_funds(self):
        result = d.decode_transaction(phoenix_funded())
        self.assertFalse(result["swaps"])
        self.assertIn("required_market_account", result["undecoded_swaps"][0])

    def test_missing_phoenix_event_cpi_is_explicitly_undecoded(self):
        tx = phoenix_funded()
        tx["meta"]["innerInstructions"][0]["instructions"] = []
        tx["meta"]["logMessages"] = [f"Program {p.PHOENIX} invoke [1]", f"Program {p.PHOENIX} success"]
        result = d.decode_transaction(tx, MARKETS)
        self.assertFalse(result["swaps"])
        self.assertTrue(result["undecoded_swaps"])

    def test_wrong_owner_or_header_cannot_supply_lot_sizes(self):
        for field, value in (("owner", d.TOKEN), ("data", [base64.b64encode(bytes(800)).decode(), "base64"])):
            saved = copy.deepcopy(MARKETS)
            for account in saved.values():
                account[field] = value
            result = d.decode_transaction(phoenix_funded(), saved)
            self.assertFalse(result["swaps"])
            self.assertTrue(result["undecoded_swaps"])

    def test_phoenix_fill_summary_must_match_maker_fills(self):
        tx = phoenix_funded()
        ix = tx["meta"]["innerInstructions"][0]["instructions"][0]
        raw = bytearray(d.b58decode(ix["data"]))
        raw[-24:-16] = (999).to_bytes(8, "little")
        ix["data"] = d.b58encode(raw)
        result = d.decode_transaction(tx, MARKETS)
        self.assertFalse(result["swaps"])
        self.assertTrue(result["undecoded_swaps"])

    def test_phoenix_event_header_cannot_point_to_another_market(self):
        tx = phoenix_funded()
        ix = tx["meta"]["innerInstructions"][0]["instructions"][0]
        raw = bytearray(d.b58decode(ix["data"]))
        raw[27:59] = bytes(32)
        ix["data"] = d.b58encode(raw)
        result = d.decode_transaction(tx, MARKETS)
        self.assertFalse(result["swaps"])
        self.assertTrue(result["undecoded_swaps"])

    def test_phoenix_unfilled_order_is_not_a_swap(self):
        tx = phoenix_funded(tag=2)
        ix = tx["meta"]["innerInstructions"][0]["instructions"][0]
        raw = bytearray(d.b58decode(ix["data"])[:93])
        raw[91:93] = (1).to_bytes(2, "little")
        # One Place event with no Fill or FillSummary event.
        ix["data"] = d.b58encode(raw + b"\x03" + bytes(42))
        result = d.decode_transaction(tx)
        self.assertFalse(result["swaps"])
        self.assertFalse(result["undecoded_swaps"])

    def test_manifest_batch_fill_needs_no_token_transfer(self):
        result = d.decode_transaction(manifest_batch())
        self.assertFalse(result["undecoded_swaps"])
        swap = result["swaps"][0]
        self.assertEqual((swap["amount_in_raw"], swap["amount_out_raw"]), ("2442100", "2442405"))
        self.assertEqual(swap["fill_count"], 1)
        self.assertEqual(swap["transfers"], [])

    def test_manifest_groups_maker_fills_but_preserves_separate_taker_orders(self):
        combined = d.decode_transaction(manifest_batch(extra_fill=True))["swaps"]
        self.assertEqual(len(combined), 1)
        self.assertEqual((combined[0]["amount_in_raw"], combined[0]["amount_out_raw"]), ("4884200", "4884810"))
        self.assertEqual(combined[0]["fill_count"], 2)
        separate = d.decode_transaction(manifest_batch(second_order=True))["swaps"]
        self.assertEqual([s["hop_index"] for s in separate], [0, 1])
        self.assertNotEqual(separate[0]["order_sequence"], separate[1]["order_sequence"])

    def test_manifest_cancellation_and_unfilled_order_do_not_create_swaps(self):
        tx = manifest_batch()
        tx["meta"]["logMessages"] = [line for line in tx["meta"]["logMessages"] if not line.startswith("Program data:")]
        result = d.decode_transaction(tx)
        self.assertFalse(result["swaps"])
        self.assertFalse(result["undecoded_swaps"])

    def test_openbook_prefunded_order_uses_native_fill_amounts(self):
        result = d.decode_transaction(openbook_funded(), MARKETS)
        self.assertFalse(result["undecoded_swaps"])
        swap = result["swaps"][0]
        self.assertEqual((swap["amount_in_raw"], swap["amount_out_raw"]), ("1860954", "860000"))
        self.assertEqual(swap["evidence"], "openbook_total_order_fill_event")
        self.assertEqual(swap["transfers"], [])

    def test_openbook_post_only_without_fills_is_not_a_swap(self):
        tx = openbook_funded()
        tx["meta"]["logMessages"] = [line for line in tx["meta"]["logMessages"] if not line.startswith("Program data:")]
        result = d.decode_transaction(tx)
        self.assertFalse(result["swaps"])
        self.assertFalse(result["undecoded_swaps"])

    def test_orderbook_fills_in_reverted_transaction_are_excluded(self):
        for tx in (phoenix_funded(), openbook_funded(), manifest_batch()):
            tx["meta"]["err"] = {"InstructionError": [0, "Custom"]}
            self.assertFalse(d.decode_transaction(tx, MARKETS)["swaps"])

    def test_cli_fetches_only_needed_immutable_market_header(self):
        tx = phoenix_funded()
        market = tx["transaction"]["message"]["instructions"][0]["accounts"][2]
        sig = tx["transaction"]["signatures"][0]
        stdout = io.StringIO()
        with patch("sys.argv", ["decoder", sig]), patch.dict("os.environ", {"SOLANA_RPC_URL": "https://rpc.invalid"}), \
                patch.object(d, "fetch_transaction", return_value=tx), \
                patch.object(d, "rpc_call", return_value={"value": [MARKETS[market]]}) as rpc, patch("sys.stdout", stdout):
            self.assertEqual(d.main(), 0)
        self.assertEqual(rpc.call_count, 1)
        self.assertEqual(rpc.call_args.args[0], "getMultipleAccounts")
        self.assertEqual(rpc.call_args.args[1][0], [market])
        self.assertEqual(rpc.call_args.args[1][1]["dataSlice"], {"offset": 0, "length": 800})
        self.assertEqual(json.loads(stdout.getvalue())["swaps"][0]["amount_in_raw"], "2931013")


if __name__ == "__main__":
    unittest.main()
