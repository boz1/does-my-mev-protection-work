"""Missing RPC configuration must never fall back to a built-in endpoint."""
import io
import os
import unittest
from unittest.mock import patch

import evm_decode_swaps as evm
import solana_decode_swaps as solana
import summarize_sandwich as sandwich


class RpcConfigurationTests(unittest.TestCase):
    def test_evm_requires_an_explicit_rpc_or_environment_variable(self):
        for chain in ('base', 'ethereum'):
            with self.subTest(chain=chain), patch.dict(os.environ, {}, clear=True), \
                 patch('sys.stderr', new_callable=io.StringIO), patch.object(evm, 'fetch_transaction') as fetch:
                with self.assertRaises(SystemExit) as caught:
                    evm.main(['0x' + '11' * 32], default_chain=chain)
                self.assertEqual(caught.exception.code, 2)
                fetch.assert_not_called()

    def test_solana_requires_an_environment_variable(self):
        signature = solana.b58encode(bytes([1]) * 64)
        with patch.dict(os.environ, {}, clear=True), \
             patch('sys.argv', ['solana_decode_swaps.py', signature]), \
             patch('sys.stderr', new_callable=io.StringIO), patch.object(solana, 'fetch_transaction') as fetch:
            self.assertEqual(solana.main(), 1)
            fetch.assert_not_called()

    def test_summarizer_requires_rpc_configuration_on_every_chain(self):
        for chain in ('solana', 'base', 'ethereum'):
            hashes = [solana.b58encode(bytes([n]) * 64) if chain == 'solana' else '0x' + f'{n:064x}'
                      for n in (1, 2, 3)]
            with self.subTest(chain=chain), patch.dict(os.environ, {}, clear=True), \
                 patch.object(sandwich, '_prepare_transaction') as fetch:
                with self.assertRaisesRegex(sandwich.SummaryError, 'RPC_URL'):
                    sandwich.fetch_sandwich(hashes[0], hashes[2], [hashes[1]], chain=chain)
                fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
