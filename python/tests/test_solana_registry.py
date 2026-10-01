"""Real registry executions, boundary mutations and synthetic conversion cases."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

import solana_decode_swaps as d
import solana_registry_protocols as p
from test_solana_all_venues import standalone
from test_solana_additional_adapters import parsed_transfer

FIX = Path(__file__).parent / "fixtures" / "solana"
CASES = json.loads((FIX / "registry_swap_cases.json").read_text())


def load_case(program, instruction=None):
    case = next(c for c in CASES if c["program_id"] == program and
                (instruction is None or c["instruction"] == instruction))
    tx = json.loads((FIX / case["fixture"]).read_text())
    nodes = d.instruction_tree(tx, [])
    node = next(n for n in nodes if n.program == program and n.location() == {
        "outer_instruction_index": case["outer_instruction_index"], "inner_instruction_index": case["inner_instruction_index"]})
    return tx, node, nodes


def token_action(kind, account, mint, amount):
    return {"programId": d.TOKEN, "parsed": {"type": kind, "info": {
        "account": account, "mint": mint, "amount": str(amount)}}}


class RegistryAdaptersTest(unittest.TestCase):
    def test_all_registry_entries_have_an_explicit_adapter_or_pricing_handler(self):
        registry = json.loads((FIX / "jupiter_registry_2026_10_01.json").read_text())
        catalog = json.loads(subprocess.check_output([sys.executable, str(Path(d.__file__)), "--list-protocols"]))
        by_id = {x["program_id"]: x for x in catalog}
        self.assertEqual(len(registry), 106)
        self.assertEqual(len(by_id), len(catalog))
        self.assertFalse(set(registry) - set(by_id))
        self.assertEqual(by_id[p.SCORCH_PRICING]["role"], "pricing")

    def test_saved_real_executions_preserve_exact_amounts_mints_and_locations(self):
        self.assertEqual({c["program_id"] for c in CASES}, set(p.NAMES) - {p.SCORCH_PRICING})
        for c in CASES:
            with self.subTest(dex=c["dex"], instruction=c["instruction"]):
                result = d.decode_transaction(json.loads((FIX / c["fixture"]).read_text()))
                swaps = [s for s in result["swaps"] if s["program_id"] == c["program_id"] and
                         (s["outer_instruction_index"], s["inner_instruction_index"]) ==
                         (c["outer_instruction_index"], c["inner_instruction_index"])]
                self.assertEqual(len(swaps), 1)
                s = swaps[0]
                for key in ("pool", "trader", "instruction", "amount_in_raw", "amount_out_raw", "evidence"):
                    self.assertEqual(s[key], c[key])
                self.assertEqual((s["token_in"]["mint"], s["token_out"]["mint"]), (c["mint_in"], c["mint_out"]))
                self.assertEqual(s.get("venue_type"), c.get("venue_type"))
                self.assertFalse(result["undecoded_swaps"])

    def test_unknown_instruction_does_not_reuse_known_transfers(self):
        for c in CASES:
            tx, node, _ = load_case(c["program_id"], c["instruction"])
            raw = bytearray(d.b58decode(node.ix["data"]))
            if node.program == p.GATOR:
                raw[-1] = 254
            else:
                raw[:min(8, len(raw))] = b"\xfe" * min(8, len(raw))
            node.ix["data"] = d.b58encode(raw)
            self.assertFalse(any(s["program_id"] == node.program and
                (s["outer_instruction_index"], s["inner_instruction_index"]) == (node.outer, node.inner)
                for s in d.decode_transaction(tx)["swaps"]), c["dex"])

    def test_reverted_transactions_never_report_committed_actions(self):
        for program in {c["program_id"] for c in CASES}:
            tx, _, _ = load_case(program)
            tx["meta"]["err"] = {"InstructionError": [0, {"Custom": 1}]}
            result = d.decode_transaction(tx)
            self.assertFalse(result["swaps"])
            self.assertFalse(result["non_swap_instructions"])

    def test_swap_amounts_require_execution_logs(self):
        for program in {c["program_id"] for c in CASES}:
            tx, _, _ = load_case(program)
            tx["meta"]["logMessages"] = []
            self.assertFalse(d.decode_transaction(tx)["swaps"], p.NAMES[program])

    def test_liquidity_withdrawals_are_not_swaps(self):
        for name, program in (("registry_lemmings_withdrawal", p.LEMMINGS),
                              ("additional_metric_non_swap", p.METRIC)):
            result = d.decode_transaction(json.loads((FIX / (name + ".json")).read_text()))
            self.assertFalse(any(s["program_id"] == program for s in result["swaps"]))

    def test_archer_deposits_and_withdrawals_are_not_swaps(self):
        result = d.decode_transaction(json.loads((FIX / "registry_archer_liquidity.json").read_text()))
        self.assertFalse(any(s["program_id"] == p.ARCHER and s["instruction"] != "swap_tag_15" for s in result["swaps"]))
        self.assertTrue(any(x["program_id"] == p.ARCHER for x in result["unclassified_instructions"]))

    def test_scorch_pricing_links_to_one_parent_swap(self):
        result = d.decode_transaction(json.loads((FIX / "registry_scorch_pricing.json").read_text()))
        self.assertFalse(any(s["program_id"] == p.SCORCH_PRICING for s in result["swaps"]))
        self.assertTrue(result["non_swap_instructions"])
        for call in result["non_swap_instructions"]:
            matches = [s for s in result["swaps"] if s["program_id"] == call["execution_program_id"] and
                       all(s[k] == v for k, v in call["execution_location"].items())]
            self.assertEqual(len(matches), 1)

    def test_scorch_shape_alone_is_not_a_pricing_cpi(self):
        tx = standalone(p.SCORCH_PRICING, ["a"] * 9, bytes(25))
        result = d.decode_transaction(tx)
        self.assertFalse(result["swaps"])
        self.assertFalse(result["non_swap_instructions"])
        self.assertEqual(len(result["unclassified_instructions"]), 1)

    def test_gator_input_mint_must_match_executed_payment(self):
        tx, node, _ = load_case(p.GATOR)
        raw = bytearray(d.b58decode(node.ix["data"]))
        raw[8:40] = bytes(32)
        node.ix["data"] = d.b58encode(raw)
        self.assertTrue(any(s["program_id"] == p.GATOR for s in d.decode_transaction(tx)["undecoded_swaps"]))

    def test_stakedex_wraps_conversion_without_counting_stake_pool_again(self):
        for name in ("stake_wrapped_sol", "withdraw_wrapped_sol"):
            tx, node, nodes = load_case(p.SANCTUM_ROUTER, name)
            child_locations = {(n.outer, n.inner) for n in nodes if n.is_within(node)}
            result = d.decode_transaction(tx)
            self.assertTrue(any(s["program_id"] == p.SANCTUM_ROUTER for s in result["swaps"]))
            self.assertFalse(any(s["program_id"] in p.STAKE_POOLS and
                (s["outer_instruction_index"], s["inner_instruction_index"]) in child_locations for s in result["swaps"]))

    def test_conversion_burn_requires_correct_account_and_mint(self):
        for field in ("account", "mint"):
            tx, node, nodes = load_case(p.SOLAYER, "undelegate")
            burn = next(n for n in nodes if n.parent is node and n.ix.get("parsed", {}).get("type") == "burn")
            burn.ix["parsed"]["info"][field] = "unrelated"
            self.assertTrue(any(s["program_id"] == p.SOLAYER for s in d.decode_transaction(tx)["undecoded_swaps"]))

    def test_nested_conversion_transfer_requires_declared_program(self):
        tx, node, nodes = load_case(p.HELIUM)
        ix = next(n for n in nodes if n.parent is node and n.program == node.ix["accounts"][8])
        old = ix.program
        ix.ix["programId"] = "UnrelatedProgram"
        tx["meta"]["logMessages"] = [line.replace(old, "UnrelatedProgram") for line in tx["meta"]["logMessages"]]
        result = d.decode_transaction(tx)
        self.assertTrue(any(s["program_id"] == p.HELIUM for s in result["undecoded_swaps"]))

    def test_deposit_does_not_use_requested_amount_or_fee_mints(self):
        a = [f"account_{i}" for i in range(11)]
        tx = standalone(p.CARROT, a, d.discriminator("issue") + (999999).to_bytes(8, "little"), children=[
            parsed_transfer(a[5], a[4], a[3], 100),
            token_action("mintTo", a[2], a[1], 97),
            token_action("mintTo", "fee_recipient", a[1], 3)])
        s = d.decode_transaction(tx)["swaps"][0]
        self.assertEqual((s["amount_in_raw"], s["amount_out_raw"]), ("100", "97"))
        self.assertEqual(len(s["token_actions"]), 1)

    def test_conversion_refunds_are_net_of_executed_mint_and_transfer_reversals(self):
        a = [f"account_{i}" for i in range(11)]
        tx = standalone(p.CARROT, a, d.discriminator("issue") + bytes(8), children=[
            parsed_transfer(a[5], a[4], a[3], 120), parsed_transfer(a[4], a[5], a[3], 20),
            token_action("mintTo", a[2], a[1], 102), token_action("burn", a[2], a[1], 5)])
        s = d.decode_transaction(tx)["swaps"][0]
        self.assertEqual((s["amount_in_raw"], s["amount_out_raw"]), ("100", "97"))

    def test_one_sided_conversion_remains_undecoded(self):
        a = [f"account_{i}" for i in range(11)]
        tx = standalone(p.CARROT, a, d.discriminator("issue") + bytes(8), children=[parsed_transfer(a[5], a[4], a[3], 100)])
        result = d.decode_transaction(tx)
        self.assertFalse(result["swaps"])
        self.assertEqual(len(result["undecoded_swaps"]), 1)

    def test_huma_truncated_commitment_is_rejected(self):
        tx, node, _ = load_case(p.HUMA, "deposit")
        node.ix["data"] = d.b58encode(d.b58decode(node.ix["data"])[:-1])
        self.assertTrue(any(s["program_id"] == p.HUMA for s in d.decode_transaction(tx)["undecoded_swaps"]))

    def test_batch_transfers_keep_atomic_scope_and_distinct_operation_positions(self):
        a = [f"account_{i}" for i in range(11)]
        children = [parsed_transfer(a[4], a[5], "mint_in", 91), parsed_transfer(a[7], a[6], "mint_out", 83)]
        batch = {"programId": d.TOKEN, "parsed": {"type": "batch", "info": {"instructions": [c["parsed"] for c in children]}}}
        tx = standalone(p.FLINT, a, b"\x06" + bytes(9), children=[batch])
        s = d.decode_transaction(tx)["swaps"][0]
        self.assertEqual((s["amount_in_raw"], s["amount_out_raw"]), ("91", "83"))
        self.assertEqual([t["token_batch_path"] for t in s["transfers"]], [[0], [1]])
        self.assertEqual({t["inner_instruction_index"] for t in s["transfers"]}, {0})
        tx["meta"]["logMessages"][2] = f"Program {d.TOKEN} failed: custom program error: 1"
        self.assertFalse(d.decode_transaction(tx)["swaps"])

    def test_invalid_nested_token_batch_is_rejected(self):
        batch = {"programId": d.TOKEN, "parsed": {"type": "batch", "info": {"instructions": [{"type": "batch", "info": {"instructions": []}}]}}}
        tx = standalone(p.FLINT, ["a"] * 11, b"\x06" + bytes(9), children=[batch])
        with self.assertRaises(d.DecodeError):
            d.decode_transaction(tx)

    def test_real_batch_transfer_fixture_is_covered(self):
        results = [d.decode_transaction(json.loads((FIX / c["fixture"]).read_text())) for c in CASES if c["program_id"] == p.FLINT]
        self.assertTrue(any("token_batch_path" in t for r in results for s in r["swaps"] for t in s["transfers"]))

    def test_prediction_swaps_and_cross_vault_pools_are_labeled(self):
        tx, _, _ = load_case(p.BISON_PREDICT)
        s = next(s for s in d.decode_transaction(tx)["swaps"] if s["program_id"] == p.BISON_PREDICT)
        self.assertEqual(s["venue_type"], "prediction_market")
        tx, node, _ = load_case(p.AQUIFER)
        s = next(s for s in d.decode_transaction(tx)["swaps"] if s["program_id"] == p.AQUIFER)
        self.assertIsNone(s["pool"])
        self.assertEqual(s["pool_addresses"], [node.ix["accounts"][12], node.ix["accounts"][14]])


if __name__ == "__main__":
    unittest.main()
