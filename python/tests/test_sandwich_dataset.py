import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import sandwich_dataset as d
import solana_decode_swaps as solana
import summarize_sandwich as s


def h(n):
    return '0x' + f'{n:064x}'


def binary_record(nf, nb, nv, seed=1):
    signatures = [(seed + i).to_bytes(64, 'big') for i in range(nf + nb + nv)]
    return struct.pack('<BBH', nf, nb, nv) + b''.join(signatures)


class DatasetTests(unittest.TestCase):
    def test_binary_counts_delimit_multiple_records_and_preserve_leading_zeroes(self):
        first = binary_record(2, 2, 3)
        second = binary_record(1, 1, 300, seed=20)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'part.bin'
            path.write_bytes(first + second)
            records = list(d.iter_records(path))
            self.assertEqual([list(map(len, r.row)) for r in records], [[2, 2, 3], [1, 1, 300]])
            self.assertEqual(records[1].byte_offset, 4 + 64 * 7)
            self.assertEqual(solana.b58decode(records[0].row[0][0]), (1).to_bytes(64, 'big'))
            self.assertEqual(solana.b58decode(records[0].row[1][0]), (3).to_bytes(64, 'big'))
            self.assertEqual(solana.b58decode(records[0].row[2][0]), (5).to_bytes(64, 'big'))
            self.assertEqual(d.read_record(path, 2), records[1])
            self.assertEqual(s.read_row(path, 2), records[1].row)

    def test_binary_truncated_headers_signatures_and_empty_groups_rejected(self):
        cases = [b'\x01', binary_record(1, 1, 1)[:-1], struct.pack('<BBH', 0, 1, 1) + b'\0' * 128]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.bin'
            for content in cases:
                path.write_bytes(content)
                with self.subTest(size=len(content)), self.assertRaises(d.DatasetError):
                    list(d.iter_records(path))
            path.write_bytes(binary_record(1, 1, 1) + b'\x01')
            with self.assertRaisesRegex(d.DatasetError, 'header'):
                d.read_record(path, 2)

    def test_json_shard_with_exactly_three_records_not_confused_with_three_leg_row(self):
        records = [{'f': [h(i)], 'b': [h(i+1)], 'V': [{'h': h(i+2)}], 'i': i}
                   for i in (1, 4, 7)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / '0.json'
            path.write_text(json.dumps(records))
            read = list(d.iter_records(path))
            self.assertEqual(len(read), 3)
            self.assertEqual(read[1].row, [[h(4)], [h(5)], [h(6)]])
            self.assertEqual(read[1].metadata['i'], 4)
            self.assertEqual(s.normalize_row(records[1], 'base'), read[1].row)
            path.write_text(json.dumps(read[1].row))
            self.assertEqual(len(list(d.iter_records(path))), 1)

    def test_jsonl_positions_are_physical_lines_and_web_records_work(self):
        row = {'f': [h(1)], 'b': [h(4)], 'V': [{'h': h(2)}, {'h': h(3)}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'rows.jsonl'
            path.write_text('\n' + json.dumps(row) + '\n')
            self.assertEqual(s.read_row(path, 2), [[h(1)], [h(4)], [h(2), h(3)]])
            for number in (0, 1, 3):
                with self.assertRaises(s.SummaryError):
                    s.read_row(path, number)

    def test_bad_website_victims_and_duplicate_hashes_rejected(self):
        for row in ({'f': [h(1)], 'b': [h(3)], 'V': [h(2)]},
                    {'f': [h(1)], 'b': [h(3)], 'V': [{'a': h(2)}]},
                    {'f': [h(1)], 'b': [h(3)], 'V': [{'h': h(1)}]}):
            with self.assertRaises(s.SummaryError):
                s.normalize_row(row, 'ethereum')

    def test_binary_cannot_be_sent_to_evm_rpc(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'part.bin'
            path.write_bytes(binary_record(1, 1, 1))
            with patch.object(s, 'fetch_sandwich') as fetch, patch('sys.stderr'):
                self.assertEqual(s.main([str(path), '--chain', 'base']), 1)
                fetch.assert_not_called()

    def test_repository_directories_resolve_without_index_shards_and_sort_numerically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('s/10.json', 's/2.json', 'idx/0.json', 'base/s/0.json', 'solana/part-0001.bin'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            self.assertEqual([p.name for p in d.dataset_paths(root, 'ethereum')], ['2.json', '10.json'])
            self.assertEqual(d.dataset_paths(root, 'base'), [root / 'base/s/0.json'])
            self.assertEqual(d.dataset_paths(root, 'solana'), [root / 'solana/part-0001.bin'])


if __name__ == '__main__':
    unittest.main()
