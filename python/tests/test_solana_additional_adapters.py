"""Additional real swap regressions and explicitly synthetic ABI scenarios."""

import json
from pathlib import Path
import unittest

import solana_decode_swaps as d
import solana_swap_protocols as p
from test_solana_all_venues import standalone
from test_solana_swap_protocols import two_hop_transaction


FIXTURES = Path(__file__).parent / "fixtures" / "solana"
CASES = json.loads((FIXTURES / "additional_swap_cases.json").read_text())


def case_transaction(program, instruction=None):
    case = next(c for c in CASES if c["program_id"] == program and
                (instruction is None or c["instruction"] == instruction))
    tx = json.loads((FIXTURES / case["fixture"]).read_text())
    nodes = d.instruction_tree(tx, [])
    node = next(n for n in nodes if (n.outer, n.inner) ==
                (case["outer_instruction_index"], case["inner_instruction_index"]))
    return tx, node, nodes


def tuna_two_hop():
    tx = two_hop_transaction(v2=True)
    ix = tx["transaction"]["message"]["instructions"][0]
    ix["programId"] = p.DEFITUNA
    ix["accounts"] += [f"tick_array_{i}" for i in range(6)] + ["memo_program"]
    ix["data"] = d.b58encode(d.discriminator("two_hop_swap") + d.b58decode(ix["data"])[8:])
    tx["meta"]["logMessages"] = [line.replace(d.ORCA, p.DEFITUNA) for line in tx["meta"]["logMessages"]]
    return tx


def parsed_transfer(source, destination, mint, amount):
    return {"programId": d.TOKEN, "parsed": {"type": "transferChecked", "info": {
        "source": source, "destination": destination, "mint": mint,
        "tokenAmount": {"amount": str(amount), "decimals": 6}}}}


class AdditionalAdaptersTest(unittest.TestCase):
    def test_saved_on_chain_swaps_have_exact_amounts_and_positions(self):
        self.assertEqual(len({c["program_id"] for c in CASES}), 31)
        for case in CASES:
            with self.subTest(dex=case["dex"], instruction=case["instruction"], signature=case["signature"][:8]):
                result = d.decode_transaction(json.loads((FIXTURES / case["fixture"]).read_text()))
                matches = [s for s in result["swaps"] if s["program_id"] == case["program_id"] and
                           (s["outer_instruction_index"], s["inner_instruction_index"]) ==
                           (case["outer_instruction_index"], case["inner_instruction_index"])]
                self.assertEqual(len(matches), 1)
                swap = matches[0]
                for key in ("instruction", "pool", "trader", "amount_in_raw", "amount_out_raw", "evidence"):
                    self.assertEqual(swap[key], case[key])
                self.assertEqual((swap["token_in"]["mint"], swap["token_out"]["mint"]), (case["mint_in"], case["mint_out"]))
                self.assertFalse(result["undecoded_swaps"])

    def test_unknown_instruction_in_registered_program_does_not_create_swap(self):
        for case in CASES:
            tx, node, _ = case_transaction(case["program_id"], case["instruction"])
            raw = d.b58decode(node.ix["data"])
            node.ix["data"] = d.b58encode(b"\xfe" * min(8, len(raw)) + raw[8:])
            result = d.decode_transaction(tx)
            self.assertFalse(any((s["outer_instruction_index"], s["inner_instruction_index"]) == (node.outer, node.inner)
                                 for s in result["swaps"]), case["dex"])

    def test_reverted_added_venue_transactions_have_no_swaps(self):
        for program in {c["program_id"] for c in CASES}:
            tx, _, _ = case_transaction(program)
            tx["meta"]["err"] = {"InstructionError": [0, {"Custom": 1}]}
            self.assertFalse(d.decode_transaction(tx)["swaps"])

    def test_real_two_asset_withdrawals_are_not_swaps(self):
        for name, program in (("additional_goonfi_withdrawal", p.GOONFI_V1),
                              ("additional_metric_non_swap", "Bvs46DPFxiFE6YHxLDLD6QAUcmy51FyRVPZJusPxLk3j")):
            tx = json.loads((FIXTURES / (name + ".json")).read_text())
            result = d.decode_transaction(tx)
            self.assertFalse(any(s["program_id"] == program for s in result["swaps"]))

    def test_real_riptide_oracle_update_does_not_create_a_swap(self):
        tx = json.loads((FIXTURES / "additional_riptide_non_swap.json").read_text())
        result = d.decode_transaction(tx)
        self.assertFalse(any(s["program_id"] == p.RIPTIDE for s in result["swaps"]))

    def test_gavel_reports_pool_not_log_authority(self):
        tx, node, _ = case_transaction(p.GAVEL)
        swap = next(s for s in d.decode_transaction(tx)["swaps"] if s["program_id"] == p.GAVEL)
        self.assertEqual(swap["pool"], node.ix["accounts"][2])
        self.assertNotEqual(swap["pool"], node.ix["accounts"][1])

    def test_heaven_purchase_excludes_seed_liquidity_and_rent(self):
        cases = [c for c in CASES if c["program_id"] == p.HEAVEN]
        self.assertEqual({(c["amount_in_raw"], c["amount_out_raw"]) for c in cases},
                         {("3000000000", "78947368421052631"), ("532994924", "15000000000000000")})
        for case in cases:
            result = d.decode_transaction(json.loads((FIXTURES / case["fixture"]).read_text()))
            swap = next(s for s in result["swaps"] if s["program_id"] == p.HEAVEN)
            self.assertEqual(len(swap["transfers"]), 1)
            self.assertEqual(len(swap["native_transfers"]), 1)
            self.assertIsNone(swap["token_in"]["mint"])

    def test_heaven_pool_creation_without_purchase_is_not_a_swap(self):
        _, node, _ = case_transaction(p.HEAVEN)
        raw = bytearray(d.b58decode(node.ix["data"]))
        raw[14:22] = bytes(8)
        tx = standalone(p.HEAVEN, node.ix["accounts"], bytes(raw))
        result = d.decode_transaction(tx)
        self.assertFalse(result["swaps"])
        self.assertFalse(result["undecoded_swaps"])

    def test_heaven_purchase_needs_matching_sol_destination(self):
        tx, node, nodes = case_transaction(p.HEAVEN)
        for n in nodes:
            parsed = n.ix.get("parsed", {})
            if n.parent is node and n.program == d.SYSTEM and parsed.get("type") == "transfer":
                parsed["info"]["destination"] = "unrelated_recipient"
        result = d.decode_transaction(tx)
        self.assertTrue(any(x["program_id"] == p.HEAVEN for x in result["undecoded_swaps"]))

    def test_rfqs_identify_counterparty_without_fabricating_pool(self):
        tx, node, _ = case_transaction(p.JUPITER_RFQ)
        swap = next(s for s in d.decode_transaction(tx)["swaps"] if s["program_id"] == p.JUPITER_RFQ)
        self.assertEqual(swap["venue_type"], "rfq")
        self.assertIsNone(swap["pool"])
        self.assertEqual(swap["fill_authority"], node.ix["accounts"][1])

    def test_rfqs_reject_truncated_quote_levels(self):
        tx, node, _ = case_transaction(p.JUPITER_RFQ)
        node.ix["data"] = d.b58encode(d.b58decode(node.ix["data"])[:-1])
        result = d.decode_transaction(tx)
        self.assertTrue(any(x["program_id"] == p.JUPITER_RFQ for x in result["undecoded_swaps"]))

    def test_tuna_two_hop_preserves_intermediate_transfer_and_pool_order(self):
        result = d.decode_transaction(tuna_two_hop())
        self.assertFalse(result["warnings"])
        swaps = result["swaps"]
        self.assertEqual([s["pool"] for s in swaps], ["pool_one", "pool_two"])
        self.assertEqual([(s["amount_in_raw"], s["amount_out_raw"]) for s in swaps], [("100", "200"), ("200", "300")])
        self.assertEqual(swaps[0]["transfers"][1], swaps[1]["transfers"][0])
        self.assertEqual([s["hop_index"] for s in swaps], [0, 1])

    def test_tuna_partial_two_hop_evidence_stays_explicitly_undecoded(self):
        tx = tuna_two_hop()
        tx["meta"]["innerInstructions"][0]["instructions"][-1]["parsed"]["info"]["destination"] = "other"
        result = d.decode_transaction(tx)
        self.assertFalse(result["swaps"])
        self.assertEqual(len(result["undecoded_swaps"]), 1)

    def test_jup_lend_requires_the_declared_liquidity_program_path(self):
        tx, node, _ = case_transaction(p.JUPLEND_AMM)
        node.ix["accounts"][20] = "unrelated_program"
        result = d.decode_transaction(tx)
        self.assertTrue(any(x["program_id"] == p.JUPLEND_AMM for x in result["undecoded_swaps"]))

    def test_trench_buy_event_must_match_native_payment(self):
        tx, node, nodes = case_transaction(p.TRENCH, "buy")
        for n in nodes:
            parsed = n.ix.get("parsed", {})
            if n.parent is node and n.program == d.SYSTEM and parsed.get("type") == "transfer" and parsed["info"]["destination"] == node.ix["accounts"][4]:
                parsed["info"]["lamports"] += 1
        result = d.decode_transaction(tx)
        self.assertTrue(any(x["program_id"] == p.TRENCH for x in result["undecoded_swaps"]))

    def test_trench_sell_event_cannot_name_another_mint(self):
        tx, node, nodes = case_transaction(p.TRENCH, "sell")
        for n in nodes:
            if n.parent is node and n.program == p.TRENCH:
                raw = bytearray(d.b58decode(n.ix["data"]))
                raw[48:80] = bytes(32)  # Event CPI prefix + event tag + trader precede mint.
                n.ix["data"] = d.b58encode(raw)
        result = d.decode_transaction(tx)
        self.assertTrue(any(x["program_id"] == p.TRENCH for x in result["undecoded_swaps"]))

    def test_m_swap_uses_user_burn_and_mint_not_backing_transfers(self):
        tx, _, _ = case_transaction(p.M_SWAP)
        swap = next(s for s in d.decode_transaction(tx)["swaps"] if s["program_id"] == p.M_SWAP)
        self.assertEqual(swap["venue_type"], "token_conversion")
        self.assertEqual([a["side"] for a in swap["token_actions"]], ["input", "output"])
        self.assertEqual(swap["amount_in_raw"], swap["amount_out_raw"])
        self.assertFalse(swap["transfers"])

    def test_m_swap_rejects_burn_from_another_account(self):
        tx, node, _ = case_transaction(p.M_SWAP)
        node.ix["accounts"][9] = "another_input_account"
        result = d.decode_transaction(tx)
        self.assertTrue(any(x["program_id"] == p.M_SWAP for x in result["undecoded_swaps"]))

    def test_perena_share_conversion_uses_actual_minted_shares(self):
        # Synthetic scenario from the on-chain IDL; not a mined Perena example.
        a = [f"account_{i}" for i in range(12)]
        a[8:10] = [d.TOKEN, d.TOKEN2022]
        children = [{"programId": d.TOKEN, "parsed": {"type": "burn", "info": {
            "account": a[6], "mint": a[4], "amount": "1000000"}}},
            {"programId": d.TOKEN2022, "parsed": {"type": "mintTo", "info": {
                "account": a[7], "mint": a[5], "amount": "1500000"}}}]
        raw = d.discriminator("execute_share_swap") + b"\0" + (1000000).to_bytes(8, "little")
        tx = standalone(p.PERENA_STAR, a, raw, children=children)
        result = d.decode_transaction(tx)
        self.assertFalse(result["undecoded_swaps"])
        swap = result["swaps"][0]
        self.assertEqual((swap["amount_in_raw"], swap["amount_out_raw"]), ("1000000", "1500000"))
        self.assertEqual(swap["venue_type"], "share_conversion")
        tx["meta"]["innerInstructions"][0]["instructions"][1]["parsed"]["info"]["account"] = "someone_else"
        self.assertTrue(d.decode_transaction(tx)["undecoded_swaps"])

    def test_hylo_lst_exchange_excludes_other_fee_recipient(self):
        # Synthetic scenario from Hylo's published on-chain swap_lst_to_lst ABI.
        a = [f"account_{i}" for i in range(17)]
        a[14] = d.TOKEN
        children = [parsed_transfer(a[3], a[5], a[2], 123),
                    parsed_transfer(a[10], a[8], a[7], 456),
                    parsed_transfer(a[3], a[13], a[2], 7)]
        tx = standalone(p.HYLO, a, d.discriminator("swap_lst_to_lst") + (130).to_bytes(8, "little") + b"\0", children=children)
        result = d.decode_transaction(tx)
        self.assertFalse(result["undecoded_swaps"])
        self.assertEqual((result["swaps"][0]["amount_in_raw"], result["swaps"][0]["amount_out_raw"]), ("123", "456"))


if __name__ == "__main__":
    unittest.main()
