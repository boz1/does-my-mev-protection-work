"""Read sandwich JSONL rows, website JSON shards, or Solana binary parts.

Binary records are <BBH counts (front, back, victims), followed by that many
64-byte signatures in front/back/victim order. There is no file header, padding,
or separator. Counts delimit records; signatures become Base58 transaction IDs.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import struct
from typing import Iterator

from solana_decode_swaps import b58encode

FORMATS = ('auto', 'jsonl', 'json', 'solana-binary')


class DatasetError(ValueError):
    pass


@dataclass(frozen=True)
class DatasetRecord:
    source: Path
    number: int  # Physical line for JSONL; otherwise one-based record number.
    row: list
    metadata: dict | None = None
    byte_offset: int | None = None


def row_from_record(value):
    """Unwrap website records without changing the ordering of any leg group."""
    if not isinstance(value, dict):
        return value
    if not all(k in value for k in ('f', 'b', 'V')) or not isinstance(value['V'], list):
        raise DatasetError('Website record must contain f, b and a V array')
    if not all(isinstance(v, dict) and isinstance(v.get('h'), str) for v in value['V']):
        raise DatasetError('Every website victim must have a transaction hash in h')
    return [value['f'], value['b'], [v['h'] for v in value['V']]]


def input_format(path: str | Path, requested: str = 'auto') -> str:
    if requested not in FORMATS:
        raise DatasetError('Unknown dataset input format')
    if requested != 'auto':
        return requested
    return {'.bin': 'solana-binary', '.json': 'json'}.get(Path(path).suffix.lower(), 'jsonl')


def _single_row(value) -> bool:
    return (isinstance(value, list) and len(value) == 3 and
            all(isinstance(g, str) or (isinstance(g, list) and all(isinstance(h, str) for h in g))
                for g in value))


def iter_records(path: str | Path, *, format: str = 'auto', start: int = 1) -> Iterator[DatasetRecord]:
    """Stream records starting at a one-based position; never read a whole .bin.

JSON shards are small JSON arrays and are read as one document. JSONL and binary
parts are streamed. Binary skips seek over signatures, avoiding Base58 work for
records preceding a resume point. A truncated final record is an error.
"""
    if start < 1:
        raise DatasetError('Record number must be at least 1 (one-based)')
    path = Path(path)
    fmt = input_format(path, format)
    if fmt == 'solana-binary':
        with path.open('rb') as source:
            size = path.stat().st_size
            number = 0
            while source.tell() < size:
                offset = source.tell()
                header = source.read(4)
                number += 1
                if len(header) != 4:
                    raise DatasetError(f'Truncated binary header at byte {offset}')
                nf, nb, nv = struct.unpack('<BBH', header)
                count = nf + nb + nv
                if not nf or not nb or not nv:
                    raise DatasetError(f'Empty binary leg group at record {number}')
                if offset + 4 + 64 * count > size:
                    raise DatasetError(f'Truncated binary signatures at record {number}')
                if number < start:
                    source.seek(64 * count, 1)
                    continue
                # u8/u8/u16 counts bound this read to at most 4,226,880 bytes.
                payload = source.read(64 * count)
                if len(payload) != 64 * count:
                    raise DatasetError(f'Truncated binary signatures at record {number}')
                sigs = [b58encode(payload[i:i+64]) for i in range(0, len(payload), 64)]
                yield DatasetRecord(path, number, [sigs[:nf], sigs[nf:nf+nb], sigs[nf+nb:]],
                                    byte_offset=offset)
        return
    if fmt == 'json':
        try:
            value = json.loads(path.read_text())
        except (ValueError, UnicodeError):
            raise DatasetError('Dataset is not valid JSON') from None
        if isinstance(value, dict) or _single_row(value):
            values = [value]
        elif isinstance(value, list):
            values = value
        else:
            raise DatasetError('JSON dataset must be a record or an array of records')
        for number, item in enumerate(values, 1):
            if number >= start:
                yield DatasetRecord(path, number, row_from_record(item), item if isinstance(item, dict) else None)
        return
    with path.open() as source:
        for number, line in enumerate(source, 1):
            if number < start:
                continue
            try:
                item = json.loads(line)
            except (ValueError, UnicodeError):
                raise DatasetError(f'JSONL line {number} is not valid JSON') from None
            yield DatasetRecord(path, number, row_from_record(item), item if isinstance(item, dict) else None)


def read_record(path: str | Path, number: int = 1, *, format: str = 'auto') -> DatasetRecord:
    records = iter_records(path, format=format, start=number)
    try:
        return next(records)
    except StopIteration:
        raise DatasetError('Selected row is beyond the end of the file') from None
    finally:
        records.close()


def dataset_paths(path: str | Path, chain: str) -> list[Path]:
    """Resolve a file, shard directory, or the root of a supported repository.

The Ethereum root dataset and eth_reorg are separate inputs. Pass eth_reorg/
explicitly to audit that subset; transaction cache entries are shared by chain.
"""
    path = Path(path)
    if path.is_file():
        return [path.resolve()]
    if not path.is_dir():
        raise DatasetError('Dataset path does not exist')
    if chain == 'solana' and (path / 'solana').is_dir():
        path = path / 'solana'
    elif chain == 'base' and (path / 'base/s').is_dir():
        path = path / 'base/s'
    elif chain == 'tron' and (path / 'tron/s').is_dir():
        path = path / 'tron/s'
    elif chain == 'ethereum' and (path / 'ethereum/s').is_dir():
        path = path / 'ethereum/s'
    elif (path / 's').is_dir():
        path = path / 's'
    paths = [p for p in path.iterdir() if p.is_file() and p.suffix.lower() in ('.bin', '.jsonl', '.json')]
    if not paths:
        raise DatasetError('No binary, JSONL or JSON data files in the dataset directory')
    def natural_key(p):
        return [int(piece) if piece.isdigit() else piece for piece in re.split(r'(\d+)', p.name)]
    return sorted(paths, key=natural_key)
