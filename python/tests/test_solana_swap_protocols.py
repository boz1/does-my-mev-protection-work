import copy
import json
from pathlib import Path
import unittest

import solana_decode_swaps as d
import solana_swap_protocols as p
from test_solana_decode_swaps import routed_two_swaps


FIXTURES = Path(__file__).parent / "fixtures" / "solana"


def fixture(name):
    return json.loads((FIXTURES / (name + ".json")).read_text())


def two_hop_transaction(v2=True):
    """Three assets and distinct pools; v2 bridges vaults without a user ATA."""
    mint_in, mint_mid, mint_out = "input_mint", "intermediate_mint", "output_mint"
    if v2:
        a = ["pool_one", "pool_two", mint_in, mint_mid, mint_out, d.TOKEN, d.TOKEN, d.TOKEN,
             "user_input", "vault_one_input", "vault_one_mid", "vault_two_mid", "vault_two_output",
             "user_output", "trader"]
        flows = [(8, 9, mint_in, 100), (10, 11, mint_mid, 200), (12, 13, mint_out, 300)]
        name = "two_hop_swap_v2"
    else:
        a = [d.TOKEN, "trader", "pool_one", "pool_two", "user_input", "vault_one_input",
             "user_mid", "vault_one_mid", "user_mid", "vault_two_mid", "user_output", "vault_two_output"]
        flows = [(4, 5, mint_in, 100), (7, 6, mint_mid, 200), (8, 9, mint_mid, 200), (11, 10, mint_out, 300)]
        name = "two_hop_swap"
    raw = d.discriminator(name) + (100).to_bytes(8, "little") + (250).to_bytes(8, "little") + bytes([1, 1, 1]) + bytes(32)
    if v2:
        raw += b"\0"  # remaining_accounts_info: None
    root = {"programId": d.ORCA, "accounts": a, "data": d.b58encode(raw), "stackHeight": 1}
    transfers = [{"programId": d.TOKEN, "stackHeight": 2, "parsed": {"type": "transferChecked", "info": {
        "source": a[src], "destination": a[dst], "mint": mint,
        "tokenAmount": {"amount": str(amount), "decimals": 6}}}} for src, dst, mint, amount in flows]
    logs = [f"Program {d.ORCA} invoke [1]"]
    for _ in transfers:
        logs += [f"Program {d.TOKEN} invoke [2]", f"Program {d.TOKEN} success"]
    logs.append(f"Program {d.ORCA} success")
    return {"slot": 1, "version": 0, "transaction": {"signatures": [d.b58encode(bytes(64))],
            "message": {"accountKeys": ["trader", *dict.fromkeys(a)], "instructions": [root]}},
            "meta": {"err": None, "fee": 5000, "innerInstructions": [{"index": 0, "instructions": transfers}],
                     "logMessages": logs, "preTokenBalances": [], "postTokenBalances": []}}


class ExpandedProtocolsTest(unittest.TestCase):
    def test_real_routes_preserve_pool_order_and_exact_executed_amounts(self):
        # Amounts checked against the saved, parsed SPL transfers for each pool.
        cases = {
            "raydium_stable_obric_route": [
                (d.DLMM, "4426534814", "40776731"), (p.RAYDIUM_STABLE, "40776731", "40791626"),
                (p.OBRIC_V2, "40791626", "291882456")],
            "clmm_whirlpool_route": [(d.CLMM, "1821750982", "264874515"), (d.ORCA, "264874515", "1822062295")],
            "launchlab_roundtrip": [(p.LAUNCHLAB, "16080810", "564675739516"), (p.LAUNCHLAB, "564675739516", "15760800")],
            "byreal_damm_v2_route": [
                (d.DLMM, "9327196674", "2276395"), (p.BYREAL, "1707296", "7121061"),
                (p.DAMM_V2, "523570", "2181720"), (d.CLMM, "45529", "191499"), (d.CPMM, "9494280", "170348866673")],
            "meteora_dbc": [(p.DBC, "43206764398", "1010597")],
            "pancakeswap_route": [(d.PUMP, "4087369334271", "751787972"), (p.PANCAKE, "742390622", "87733260")],
            "bonkswap_route": [(d.PUMP_AMM, "8228447", "2944839"), (p.BONKSWAP, "1686449", "602381"),
                               (p.SCORCH, "3547220", "1173400"),
                               (d.ORCA, "1173400", "152708")],
            "stabble_weighted": [(p.MANIFEST, "5546877", "7228074"), (d.CLMM, "4061228", "1454681"),
                                  (p.STABBLE, "3166846", "1134146"), (p.ZERO_FI, "2588827", "855141")],
            "tessera_stabble_stable_route": [(d.PUMP_AMM, "7383820145", "34403631"),
                                             (p.TESSERA, "4816508", "569609"), (p.KIPSELI, "29587123", "3496164"),
                                             (p.STABBLE_STABLE, "569609", "569268")],
            "gamma_tessera_route": [(p.GAMMA, "10000", "6713"), (d.ORCA, "6713", "4977"), (p.TESSERA, "4977", "1180")],
            "orca_v2_raydium_compact": [(p.ORCA_V2, "6782337", "19935743038258"),
                                        (d.RAYDIUM, "19935743038258", "3463663"), (p.TESSERA, "3463663", "6784173")],
            "saber_aldrin_saros_route": [
                (p.SABER, "7016", "6055089"), (p.SABER, "6055089", "1120749"),
                (p.ALDRIN_V1, "1120729", "42207"), (p.SAROS, "42204", "7152"),
                (d.ORCA, "1024335", "1644012319"), (d.ORCA, "1644012319", "23918061"),
                (d.ORCA, "23918061", "1029756")],
        }
        for name, expected in cases.items():
            with self.subTest(transaction=name):
                result = d.decode_transaction(fixture(name))
                self.assertFalse(result["undecoded_swaps"])
                self.assertEqual([(s["program_id"], s["amount_in_raw"], s["amount_out_raw"]) for s in result["swaps"]], expected)
                self.assertEqual([s["swap_index"] for s in result["swaps"]], list(range(len(expected))))
                locations = [(s["outer_instruction_index"], s["inner_instruction_index"] or -1, s["hop_index"])
                             for s in result["swaps"]]
                self.assertEqual(locations, sorted(locations))

    def test_meteora_vault_cpis_exclude_separate_referral_fee(self):
        result = d.decode_transaction(fixture("meteora_damm_v1_vaults"))
        self.assertFalse(result["undecoded_swaps"])
        self.assertEqual(len(result["swaps"]), 1)
        swap = result["swaps"][0]
        # 1 SOL user debit includes a separate 0.003 SOL fee; vault receives 0.997.
        self.assertEqual(swap["amount_in_raw"], "997000000")
        self.assertEqual(swap["amount_out_raw"], "21500097493")
        self.assertEqual(len(swap["transfers"]), 2)

    def test_unknown_nested_program_cannot_supply_meteora_swap_amounts(self):
        tx = fixture("meteora_damm_v1_vaults")
        text = json.dumps(tx).replace(p.METEORA_VAULT, "UnrecognizedVault111")
        result = d.decode_transaction(json.loads(text))
        self.assertFalse(result["swaps"])
        self.assertEqual(len(result["undecoded_swaps"]), 1)

    def test_moonit_buys_and_sells_use_event_not_requested_maximum(self):
        for name, amount_in, amount_out, side in [
            ("moonit_buy", "4950000", "172904389391231", "token_in"),
            ("moonit_sell", "59948049312246101", "1778891396", "token_out"),
            ("moonit_jupiter", "98159", "3405902709116", "token_in"),
        ]:
            with self.subTest(transaction=name):
                result = d.decode_transaction(fixture(name))
                self.assertFalse(result["undecoded_swaps"])
                swap = result["swaps"][0]
                self.assertEqual((swap["amount_in_raw"], swap["amount_out_raw"]), (amount_in, amount_out))
                self.assertIsNone(swap[side]["mint"])
                self.assertEqual(swap[side]["symbol"], "SOL")
                self.assertEqual(swap["evidence"], "moonit_trade_event_and_executed_base_token_transfer")

    def test_moonit_trade_event_requires_matching_token_transfer(self):
        tx = fixture("moonit_buy")
        for group in tx["meta"]["innerInstructions"]:
            for ix in group["instructions"]:
                info = ix.get("parsed", {}).get("info", {})
                if "tokenAmount" in info:
                    info["tokenAmount"]["amount"] = "1"
        result = d.decode_transaction(tx)
        self.assertFalse(result["swaps"])
        self.assertTrue(result["undecoded_swaps"])

    def test_whirlpool_two_hop_v1_and_v2_have_two_distinct_pool_legs(self):
        for v2 in (False, True):
            with self.subTest(v2=v2):
                result = d.decode_transaction(two_hop_transaction(v2))
                self.assertFalse(result["warnings"])
                swaps = result["swaps"]
                self.assertEqual([s["pool"] for s in swaps], ["pool_one", "pool_two"])
                self.assertEqual([s["hop_index"] for s in swaps], [0, 1])
                self.assertEqual([(s["amount_in_raw"], s["amount_out_raw"]) for s in swaps], [("100", "200"), ("200", "300")])
                self.assertEqual([s["inner_instruction_index"] for s in swaps], [None, None])
                self.assertEqual(swaps[0]["token_out"], swaps[1]["token_in"])
                if v2:
                    self.assertEqual(swaps[0]["transfers"][1], swaps[1]["transfers"][0])

    def test_two_hop_partial_evidence_does_not_silently_return_first_leg(self):
        tx = two_hop_transaction()
        tx["meta"]["innerInstructions"][0]["instructions"][2]["parsed"]["info"]["destination"] = "different_account"
        result = d.decode_transaction(tx)
        self.assertFalse(result["swaps"])
        self.assertEqual(len(result["undecoded_swaps"]), 1)

    def test_reversed_two_hop_directions_come_from_actual_transfers(self):
        tx = two_hop_transaction(False)
        # Reverse each pool's flow, retaining account order and mint metadata.
        raw = bytearray(d.b58decode(tx["transaction"]["message"]["instructions"][0]["data"]))
        raw[8:16] = (200).to_bytes(8, "little")
        raw[16:24] = (100).to_bytes(8, "little")
        raw[25:27] = bytes([0, 0])
        tx["transaction"]["message"]["instructions"][0]["data"] = d.b58encode(raw)
        for ix in tx["meta"]["innerInstructions"][0]["instructions"]:
            info = ix["parsed"]["info"]
            info["source"], info["destination"] = info["destination"], info["source"]
            if info["mint"] == "output_mint":
                info["mint"] = "input_mint"
                info["tokenAmount"]["amount"] = "100"
        result = d.decode_transaction(tx)
        self.assertEqual([(s["amount_in_raw"], s["amount_out_raw"]) for s in result["swaps"]], [("200", "100"), ("100", "200")])
        self.assertEqual(result["swaps"][0]["token_out"], result["swaps"][1]["token_in"])

    def test_refunds_are_net_against_input_without_absorbing_other_pool(self):
        tx = routed_two_swaps()
        legs = tx["meta"]["innerInstructions"][0]["instructions"]
        refund = copy.deepcopy(legs[1])
        info = refund["parsed"]["info"]
        info["source"], info["destination"] = info["destination"], info["source"]
        info["amount"] = "1000000"
        legs.insert(3, refund)
        logs = tx["meta"]["logMessages"]
        end = logs.index(f"Program {d.RAYDIUM} success")
        logs[end:end] = [f"Program {d.TOKEN} invoke [3]", f"Program {d.TOKEN} success"]
        result = d.decode_transaction(tx)
        self.assertFalse(result["undecoded_swaps"])
        self.assertEqual([s["amount_in_raw"] for s in result["swaps"]], ["157673523", "200000000"])
        self.assertEqual([len(s["transfers"]) for s in result["swaps"]], [3, 2])

    def test_complete_earlier_swap_survives_later_log_truncation(self):
        tx = routed_two_swaps()
        logs = tx["meta"]["logMessages"]
        end = logs.index(f"Program {d.RAYDIUM} success") + 1
        tx["meta"]["logMessages"] = logs[:end] + ["Log truncated"]
        result = d.decode_transaction(tx)
        self.assertEqual(len(result["swaps"]), 1)
        self.assertEqual(result["swaps"][0]["inner_instruction_index"], 0)
        self.assertEqual(len(result["undecoded_swaps"]), 1)

    def test_open_inner_swap_cannot_be_verified_from_transaction_success(self):
        tx = routed_two_swaps()
        logs = tx["meta"]["logMessages"]
        end = logs.index(f"Program {d.RAYDIUM} success")
        tx["meta"]["logMessages"] = logs[:end] + ["Log truncated"]
        result = d.decode_transaction(tx)
        self.assertFalse(result["swaps"])
        self.assertEqual(len(result["undecoded_swaps"]), 2)

    def test_actual_truncated_transaction_keeps_explicit_missing_swap(self):
        result = d.decode_transaction(fixture("dlmm_truncated_logs"))
        self.assertEqual([s["outer_instruction_index"] for s in result["swaps"]], [7])
        self.assertEqual([s["outer_instruction_index"] for s in result["undecoded_swaps"]], [17])

    def test_new_dex_non_swap_call_is_kept_unclassified(self):
        tx = fixture("meteora_dbc")
        for ix in tx["transaction"]["message"]["instructions"]:
            if ix["programId"] == p.DBC:
                ix["data"] = d.b58encode(d.discriminator("remove_liquidity") + bytes(16))
        result = d.decode_transaction(tx)
        self.assertFalse(result["swaps"])
        self.assertTrue(any(i["program_id"] == p.DBC for i in result["unclassified_instructions"]))


if __name__ == "__main__":
    unittest.main()
