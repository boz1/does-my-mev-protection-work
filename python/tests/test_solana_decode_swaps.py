import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import URLError

import solana_decode_swaps as decoder


FIXTURES = Path(__file__).parent / "fixtures" / "solana"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def routed_two_swaps(first_fails=False):
    """Two separate calls to the same pool inside one router, with missing heights."""
    tx = fixture("raydium_jupiter_legacy.json")
    root = tx["transaction"]["message"]["instructions"][4]
    leg = tx["meta"]["innerInstructions"][-1]["instructions"]
    second = copy.deepcopy(leg)
    second[1]["parsed"]["info"]["amount"] = "200000000"
    second[2]["parsed"]["info"]["amount"] = "9007199254740993"
    tx["transaction"]["message"]["instructions"] = [root]
    tx["meta"]["innerInstructions"] = [{"index": 0, "instructions": leg + second}]
    logs = [f"Program {root['programId']} invoke [1]"]
    for index in range(2):
        logs += [f"Program {decoder.RAYDIUM} invoke [2]"]
        for _ in range(2):
            logs += [f"Program {decoder.TOKEN} invoke [3]", f"Program {decoder.TOKEN} success"]
        status = "failed: custom program error: 1" if first_fails and index == 0 else "success"
        logs.append(f"Program {decoder.RAYDIUM} {status}")
    logs.append(f"Program {root['programId']} success")
    tx["meta"]["logMessages"] = logs
    return tx


class DecodeSwapsTest(unittest.TestCase):
    def test_historical_jupiter_exact_output_uses_executed_amount(self):
        result = decoder.decode_transaction(fixture("raydium_jupiter_legacy.json"))
        self.assertFalse(result["warnings"])
        self.assertEqual(len(result["swaps"]), 1)
        swap = result["swaps"][0]
        self.assertEqual(swap["instruction"], "swap_base_out")
        self.assertEqual(swap["amount_in_raw"], "158673523")
        self.assertEqual(swap["amount_in"], "0.158673523")
        self.assertEqual(swap["amount_out"], "106141040.17138162")
        self.assertEqual(swap["outer_instruction_index"], 4)
        self.assertEqual(swap["inner_instruction_index"], 0)
        self.assertEqual(swap["parent_programs"], ["JUP4Fb2cqiRUcaTHdrPC8h2gNsA2ETXiPDD33WcGuJB"])
        self.assertEqual(swap["transfers"][1]["amount_raw"], "10614104017138162")

    def test_lookup_tables_and_wrapped_sol_created_and_closed_in_same_tx(self):
        result = decoder.decode_transaction(fixture("raydium_v0_closed_wsol.json"))
        self.assertEqual(result["transaction_version"], 0)
        swap = result["swaps"][0]
        self.assertEqual(swap["token_in"]["mint"], decoder.WSOL)
        self.assertEqual(swap["amount_in"], "0.7")
        self.assertEqual(swap["amount_out"], "457855254.62376151")
        self.assertEqual(len(result["swaps"]), 1)  # Account funding and closure are not swaps.

    def test_backrun_direction(self):
        swap = decoder.decode_transaction(fixture("raydium_back.json"))["swaps"][0]
        self.assertEqual(swap["token_out"]["mint"], decoder.WSOL)
        self.assertEqual(swap["amount_out"], "0.163749394")

    def test_two_swaps_in_same_router_keep_distinct_amounts_and_boundaries(self):
        result = decoder.decode_transaction(routed_two_swaps())
        self.assertFalse(result["warnings"])
        self.assertEqual([s["inner_instruction_index"] for s in result["swaps"]], [0, 3])
        self.assertEqual([s["amount_in"] for s in result["swaps"]], ["0.158673523", "0.2"])
        self.assertEqual(result["swaps"][1]["amount_out_raw"], "9007199254740993")
        self.assertEqual(result["swaps"][1]["amount_out"], "90071992.54740993")

    def test_failed_cpi_caught_by_router_is_not_reported_as_executed_swap(self):
        result = decoder.decode_transaction(routed_two_swaps(first_fails=True))
        self.assertTrue(result["success"])
        self.assertEqual(len(result["swaps"]), 1)
        self.assertEqual(result["swaps"][0]["inner_instruction_index"], 3)

    def test_failed_transaction_has_no_committed_swaps(self):
        tx = fixture("raydium_back.json")
        tx["meta"]["err"] = {"InstructionError": [1, {"Custom": 1}]}
        result = decoder.decode_transaction(tx)
        self.assertFalse(result["success"])
        self.assertEqual(result["swaps"], [])
        self.assertTrue(result["warnings"])

    def test_unavailable_logs_do_not_guess_swap_boundaries(self):
        tx = fixture("raydium_jupiter_legacy.json")
        tx["meta"]["logMessages"] = None
        result = decoder.decode_transaction(tx)
        self.assertFalse(result["swaps"])
        self.assertEqual(len(result["undecoded_swaps"]), 1)

    def test_liquidity_instruction_is_not_mislabeled_as_a_swap(self):
        tx = fixture("raydium_back.json")
        tx["transaction"]["message"]["instructions"][0]["data"] = decoder.b58encode(b"\x03" + b"\0" * 16)
        result = decoder.decode_transaction(tx)
        self.assertFalse(result["swaps"])
        self.assertEqual(len(result["unclassified_instructions"]), 1)

    def test_legacy_pump_event_deduplicated_between_logs_and_event_cpi(self):
        result = decoder.decode_transaction(fixture("pump_buy_legacy.json"))
        self.assertEqual(len(result["swaps"]), 1)
        swap = result["swaps"][0]
        self.assertIsNone(swap["token_in"]["mint"])
        self.assertEqual(swap["token_in"]["symbol"], "SOL")
        self.assertEqual(swap["amount_in"], "1.223877601")
        self.assertEqual(swap["amount_out"], "41269230.759742")
        self.assertIsNone(swap["event_fees_raw"]["fee"])

    def test_pump_v2_and_token2022(self):
        result = decoder.decode_transaction(fixture("pump_buy_v2.json"))
        swap = result["swaps"][0]
        self.assertEqual(swap["instruction"], "buy_exact_quote_in_v2")
        self.assertEqual(swap["amount_in"], "0.353580246")
        self.assertEqual(swap["amount_out"], "1474744.94502")
        self.assertEqual(swap["event_fees_raw"]["fee"], "3359013")
        self.assertTrue(any("Token-2022" in w for w in result["warnings"]))

    def test_pump_event_must_match_executed_transfer(self):
        tx = fixture("pump_buy_legacy.json")
        tx["meta"]["innerInstructions"][0]["instructions"][0]["parsed"]["info"]["amount"] = "1"
        result = decoder.decode_transaction(tx)
        self.assertEqual(result["swaps"], [])
        self.assertEqual(len(result["undecoded_swaps"]), 1)

    def test_legacy_pump_sell_inside_custom_router(self):
        result = decoder.decode_transaction(fixture("pump_sell_legacy.json"))
        self.assertFalse(result["undecoded_swaps"])
        swap = result["swaps"][0]
        self.assertEqual(swap["instruction"], "sell")
        self.assertEqual(swap["inner_instruction_index"], 0)
        self.assertEqual(swap["amount_in"], "10312295.066639")
        self.assertEqual(swap["amount_out"], "0.314996854")
        self.assertIsNone(swap["token_out"]["mint"])
        self.assertTrue(result["unclassified_instructions"])  # Keep the custom caller visible.

    def test_native_pump_sell_v2_uses_event_even_without_sol_transfer_cpi(self):
        result = decoder.decode_transaction(fixture("pump_sell_v2.json"))
        self.assertFalse(result["undecoded_swaps"])
        swap = result["swaps"][0]
        self.assertEqual(swap["instruction"], "sell_v2")
        self.assertEqual(swap["amount_in_raw"], "1682762351840")
        self.assertEqual(swap["amount_out_raw"], "411325942")
        self.assertEqual(swap["token_out"]["symbol"], "SOL")
        self.assertEqual(swap["event_fees_raw"]["buyback_fee"], "1953798")

    def test_missing_token_decimals_never_assumes_nine(self):
        tx = fixture("raydium_back.json")
        tx["meta"]["preTokenBalances"] = []
        tx["meta"]["postTokenBalances"] = []
        result = decoder.decode_transaction(tx)
        self.assertFalse(result["swaps"])
        self.assertTrue(result["undecoded_swaps"])
        self.assertIsNone(decoder.decimal_amount(1234, None))

    def test_envelope_and_null_transaction(self):
        tx = fixture("raydium_back.json")
        self.assertEqual(decoder.decode_transaction({"result": tx})["signature"], tx["transaction"]["signatures"][0])
        with self.assertRaises(decoder.DecodeError):
            decoder.decode_transaction({"result": None})

    def test_rpc_request_parameters_and_no_endpoint_in_transport_errors(self):
        tx = fixture("raydium_back.json")
        sig = tx["transaction"]["signatures"][0]
        endpoint = "https://example.invalid/private-api-key/"
        with patch.object(decoder, "urlopen", return_value=io.StringIO(json.dumps({"result": tx}))) as mocked:
            self.assertEqual(decoder.fetch_transaction(sig, endpoint), tx)
            body = json.loads(mocked.call_args.args[0].data)
            self.assertEqual(body["method"], "getTransaction")
            self.assertEqual(body["params"][1]["encoding"], "jsonParsed")
            self.assertEqual(body["params"][1]["maxSupportedTransactionVersion"], 0)
        with patch.object(decoder, "urlopen", side_effect=URLError(endpoint)):
            with self.assertRaises(decoder.DecodeError) as caught:
                decoder.fetch_transaction(sig, endpoint)
            self.assertNotIn("private-api-key", str(caught.exception))

    def test_transaction_not_found_and_invalid_signature(self):
        sig = fixture("raydium_back.json")["transaction"]["signatures"][0]
        with patch.object(decoder, "urlopen", return_value=io.StringIO('{"result":null}')):
            with self.assertRaisesRegex(decoder.DecodeError, "not found"):
                decoder.fetch_transaction(sig, "https://example.invalid")
        with patch.object(decoder, "urlopen") as mocked:
            with self.assertRaises(decoder.DecodeError):
                decoder.fetch_transaction("not-a-signature", "https://example.invalid")
            mocked.assert_not_called()


if __name__ == "__main__":
    unittest.main()
