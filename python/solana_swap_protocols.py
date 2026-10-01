"""Explicit swap instruction/account schemas; never infer swaps from balance changes.

Sources and validation status are documented in SOLANA_SWAPS.md. Account positions
refer to jsonParsed instruction.accounts (which already resolves lookup tables).
An instruction match only identifies a candidate: the execution decoder must also
verify its scope, outcome, token mints, and actual user/vault transfers.
"""

from dataclasses import dataclass
import hashlib


class LayoutError(ValueError):
    pass


def anchor(name: str) -> bytes:
    return hashlib.sha256(f"global:{name}".encode()).digest()[:8]


@dataclass(frozen=True)
class Rule:
    name: str
    prefix: bytes
    minimum_bytes: int
    pool: int | None
    trader: int
    users: tuple[int, ...]
    vaults: tuple[int, ...]
    nested_programs: tuple[str, ...] = ()
    nested_program_account: int | None = None
    exact_bytes: tuple[int, ...] = ()
    minimum_accounts: int = 0


def anchor_rules(names, pool, trader, users, vaults, minimum_bytes=24, **kwargs):
    return tuple(Rule(n, anchor(n), minimum_bytes, pool, trader, users, vaults, **kwargs) for n in names)


DAMM_V1 = "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB"
DAMM_V2 = "cpamdpZCGKUy5JxQXB4dcpGPiikHawvSWAd6mEn1sGG"
DBC = "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN"
METEORA_VAULT = "24Uqj9JCLxUeoC3hGfh5W3s9FM9uCHDS2SG3LYwBpyTi"
LAUNCHLAB = "LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj"
RAYDIUM_STABLE = "5quBtoiQqxF9Jv6KYKctB59NT3gtJD2Y65kdnB1Uev3h"
SABER = "SSwpkEEcbUqx4vtoEByFjSkhKdCT862DNVb52nZg1UZ"
ORCA_V1 = "DjVE6JNiYqPL2QXyCUUh8rNjHrbz9hXHNYt99MQ59qw1"
ORCA_V2 = "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP"
SAROS = "SSwapUtytfBdBn1b9NUGG6foMVPtcWgpRU32HToDUZr"
GAMMA = "GAMMA7meSFWaBXF25oSUgmGRwaW6sCMFLmBNiMSdbHVT"
BONKSWAP = "BSwp6bEBihVLdqJRKGgzjcGLHkcTuzmSo1TQkHepzH8p"
BYREAL = "REALQqNEomY6cQGZJUGwywTBD2UmDT32rZcNnfxQ5N2"
PANCAKE = "HpNfyc2Saw7RKkQd8nEL4khUcuPhQ7WwY1B2qjx8jxFq"
STABBLE = "swapFpHZwjELNnjvThjajtiVmkz3yPQEHjLtka2fwHW"
STABBLE_STABLE = "swapNyd8XiQwJ6ianp9snpu4brUqFxadzvHebnAXjJZ"
LIFINITY_V1 = "EewxydAPCCVuNEyrVN68PuSYdQ7wKn27V9Gjeoi8dy3S"
LIFINITY_V2 = "2wT8Yq49kHgDzXuPxZSaeLaH1qbmGXtEyPy64bL7aD3c"
TESSERA = "TessVdML9pBGgG9yGks7o4HewRaXVAMuoVj4x83GLQH"
ALDRIN_V1 = "AMM55ShdkoGRB5jVYPjWziwk8m5MpwyDgsMWHaMSQWH6"
OBRIC_V2 = "obriQD1zbpyLz95G5n7nJe6a4DPjpFwa5XYPoNm113y"
MOONIT = "MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG"
WHIRLPOOL = "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc"
MANIFEST = "MNFSTqtC93rEfYHB6hF82sKdZpUDFWkViLByLd1k1Ms"
GOONFI_V2 = "goonuddtQRrWqqn5nFyczVKaie28f3kDkHWkHtURSLE"
ZERO_FI = "ZERor4xhbUycZ6gb9ntrhqscUcZmAbQDjEAtCf4hbZY"
INVARIANT = "HyaB3W9q6XdA5xwpU4XnSZV94htfmbmqJXZcEbRaJutt"
SCORCH = "SCoRcH8c2dpjvcJD6FiPbCSQyQgu3PcUAWj2Xxx3mqn"
ONE_DEX = "DEXYosS6oEGvk8uCDayvwEZz4qEyDJRf9nFgYCaqPMTm"
KIPSELI = "3TK9D8aoBFYjYZtKCjciPrVrRStsnvo7KmpcJqDavpaU"
HUMIDIFI = "9H6tua7jkLhdm3w8BvgpTn5LZNU7g4ZynDmCiNN3q6Rp"
SOLFI_V1 = "SoLFiHG9TfgtdUXUjWAxi3LtvYuFyDLVhBWxdMZxyCe"
SOLFI_V2 = "SV2EYYJyRz2YhfXwXnhNAevDEui5Q6yrfyo13WtupPF"
BISONFI = "BiSoNHVpsVZW2F7rx2eQ59yQwKxzU5NvBcmKshCSUypi"
ALPHAQ = "ALPHAQmeA7bjrVuccPsYPiCvsi428SNwte66Srvs4pHA"
QUANTUM = "QuaNtZsgYRe5Z9Bk4LZ4cTD9tbkVoyCNf1R2BN9bBDv"
PHOENIX = "PhoeNiXZ8ByJGLkxNfZRnkUfjvmuYqLR89jjFHGqdXY"
OPENBOOK_V2 = "opnb2LAfJYbRMAHHvqjCwQxanZn7ReEHp1k81EohpZb"
ALDRIN_V2 = "CURVGoZn8zycx6FXwwevgBTB2gVvdbGTEpvMJDbgs2t4"
SAROS_DLMM = "1qbkdrr3z4ryLA7pZykqxvxWPoeifcVKo6ZG9CfkvVE"
STABBLE_CLMM = "6dMXqGZ3ga2dikrYS9ovDXgHGh5RUsb2RTUj6hrQXhk6"
MERCURIAL = "MERLuDFBMmsHnsBPZw2sDQZHvXFMwp8EdjudcU2HKky"
FLUXBEAM = "FLUXubRmkEi2q6K3Y9kBPg9248ggaZVsoSFhtJHSrm1X"
CREMA = "CLMM9tUoggJu2wagPkkqs9eFG4BWhVBZWkP1qv3Sp7tR"
PERENA = "NUMERUNsFCP3kuNmWZuXtm1AaQCPj9uw6Guv2Ekoi5P"
HEAVEN = "HEAVENoP2qxoeuF8Dj2oT1GHEnu49U5mJYkdeC8BAX2o"
BOOP = "boop8hVGQGqehUK2iVEMEnMrL5RbjywRzHKBmBE7ry4"
GOONFI_V1 = "goonERTdGsjnkZqWuVjs73BZ3Pb9qoCUdBUL17BnS5j"
DEFITUNA = "fUSioN9YKKSa3CUC2YUc4tPkHJ5Y6XW1yz8y6F7qWz9"
GUACSWAP = "Gswppe6ERWKpUTXvRPfXdzHhiCyJvLadVvXGfdpBqcE1"
OMNIPAIR = "omnixgS8fnqHfCcTGKWj6JtKjzpJZ1Y5y9pyFkQDkYE"
WOOFI = "WooFif76YGRNjk1pA8wCsN67aQsD9f9iLsz4NcJ1AVb"
DEXLAB = "DSwpgjMvXhtGn6BsbqmacdBZyfLj6jSWf3HJpdJtmg6N"
DOOAR = "Dooar9JkhdZ7J3LHN3A7YCuoGRUggXhQaG4kijfLGU2j"
PENGUIN = "PSwapMdSai8tjrEXcxFeQth87xC4rRsa4VA5mhGhXkP"
TOKEN_SWAP = "SwaPpA9LAaLfeLi3a68M4DjnLqgtticKg6CnyNwgAC8"
CROPPER = "H8W3ctz92svYg6mkn1UtGfu2aQr2fnUFHM1RhScEtQDt"
METADAO = "FUTARELBfJfQ8RDGhg1wdhddq1odMAJUePHFuBYfUxKq"
VIRTUALS = "5U3EU2ubXtK84QcRjWVmYt9RaDyA8gKxdUrPFXmZyaki"
SCALE_AMM = "SCALEwAvEK5gtkdHiFzXfPgtk2YwJxPDzaV3aDmR7tA"
SCALE_VMM = "SCALEWoRSpVZpMRqHEcDfNvBh3nUSe34jDr9r689gLa"
TRENDS = "CURVEmPpijXDTNdqrA9PGP1io2rkgiVXH26xdXVGLLfz"
GHOST_STABLE = "ghosty4ZU1Qk1HN7Ymz4pZ15QfspzJZgSYFkdKN6ZLK"
JUPLEND_AMM = "jupZ4m2GqUCJ5iueMfzQf8khFfH31d4XAQt3RzCT9Vd"
JUPITER_RFQ = "fd3nMFYTQjX1yr5ER8u7tPdHJB7qt8RpDpNtLQX2Br5"
JUPITER_PERPS = "PERPHjGBqRHArX4DySjwM6UJHiR3sWAatqfdBS2qQJu"
HYLO = "HYEXCHtHkBagdStcJCp3xbbb9B7sdMdWXFNj6mdsG4hn"
GAVEL = "srAMMzfVHVAtgSJc8iH6CfKzuWuUTzLHVCE81QU1rgi"
DERIVERSE = "DRVSpZ2YUYYKgZP8XtLhAGtT1zYSCKzeHfb4DgRnrgqD"
RUNNER = "runnrXXdsSRkdueCRYxKDvSWfv6nAnrG5dcM29qj1HA"
DENALI = "DNL1tgEj3nJovHw9jtyCCQD3arssCJzkmpDizknwzey4"
FLUX = "FLUX6xBayGxLX9UcimVRxXFMHH6q43mAbRvDzSpCsvfK"
OBSIDIAN = "HBVw6bZtcCaezhcBrmfyXBSBRWCdv72271xQ4GPvms2z"
RIPTIDE = "riptK81hDxhe5pW5jSzSM9iRA8azgEgLJ4dXkPtBS7j"
TRENCH = "8ZoPDsvLWXthQaZz6aJJEWNFBZKk2ePuygi6LHT7F7Hh"
M_SWAP = "MSwapi3WhNKMUGm9YrxGhypgUEt7wYQH3ZgG32XoWzH"
PERENA_STAR = "save8RQVPMWNTzU18t3GBvBkN9hT7jsGjiCQ28FpD9H"


SPL_SWAP = (Rule("swap", b"\x01", 17, 0, 2, (3, 6), (4, 5)),)
CLMM_SWAP = anchor_rules(("swap", "swap_v2"), 2, 0, (3, 4), (5, 6), 41)
STABBLE_SWAP = (
    *anchor_rules(("swap",), 6, 0, (1, 2), (3, 4), 17, nested_program_account=10),
    *anchor_rules(("swap_v2",), 8, 0, (3, 4), (5, 6), 17, nested_program_account=12))
LIFINITY_SWAP = anchor_rules(("swap",), 1, 2, (3, 4), (5, 6))

# Keep this an allowlist of specific instructions, not just a list of DEX IDs.
PROTOCOLS = {
    DAMM_V1: ("Meteora DAMM v1", anchor_rules(
        ("swap",), 0, 12, (1, 2), (5, 6), nested_programs=(METEORA_VAULT,))),
    DAMM_V2: ("Meteora DAMM v2", anchor_rules(("swap", "swap2"), 1, 8, (2, 3), (4, 5))),
    DBC: ("Meteora Dynamic Bonding Curve", anchor_rules(
        ("swap", "swap2", "swap2_with_transfer_hook"), 2, 9, (3, 4), (5, 6))),
    LAUNCHLAB: ("Raydium LaunchLab", anchor_rules(
        ("buy_exact_in", "buy_exact_out", "sell_exact_in", "sell_exact_out"), 4, 0, (5, 6), (7, 8))),
    # Stable base-in omits target_orders; base-out includes it before the vaults.
    RAYDIUM_STABLE: ("Raydium Stable", (
        Rule("swap_base_in", b"\x09", 17, 1, -1, (-3, -2), (4, 5)),
        Rule("swap_base_out", b"\x0b", 17, 1, -1, (-3, -2), (5, 6)))),
    SABER: ("Saber StableSwap", SPL_SWAP),
    ORCA_V1: ("Orca Token Swap v1", SPL_SWAP),
    ORCA_V2: ("Orca Token Swap v2", SPL_SWAP),
    SAROS: ("Saros AMM", SPL_SWAP),
    GAMMA: ("GooseFX Gamma", anchor_rules(
        ("swap_base_input", "swap_base_output", "oracle_based_swap_base_input"), 3, 0, (4, 5), (6, 7))),
    BONKSWAP: ("BonkSwap", anchor_rules(("swap",), 1, 8, (6, 7), (4, 5))),
    BYREAL: ("Byreal CLMM", (*CLMM_SWAP,
        *anchor_rules(("swap_v3_dyn",), 2, 0, (3, 4), (5, 6), 41, minimum_accounts=13))),
    PANCAKE: ("PancakeSwap CLMM", CLMM_SWAP),
    STABBLE: ("Stabble Weighted", STABBLE_SWAP),
    STABBLE_STABLE: ("Stabble Stable", STABBLE_SWAP),
    LIFINITY_V1: ("Lifinity v1", LIFINITY_SWAP),
    LIFINITY_V2: ("Lifinity v2", LIFINITY_SWAP),
    # Tessera's opaque variant keeps its observed tag in the instruction name.
    # Both layouts are checked against executed calls in the fixture set.
    TESSERA: ("Tessera V", (
        Rule("swap_tag_16", b"\x10", 18, 1, 2, (5, 6), (3, 4)),
        Rule("swap_tag_17", b"\x11", 19, 1, 2, (5, 6), (3, 4)))),
    ALDRIN_V1: ("Aldrin AMM v1", anchor_rules(("swap",), 0, 6, (7, 8), (3, 4), 25)),
    ALDRIN_V2: ("Aldrin AMM v2", anchor_rules(("swap",), 0, 6, (7, 8), (3, 4), 25, minimum_accounts=11)),
    OBRIC_V2: ("Obric v2", anchor_rules(("swap", "swap2"), 0, 10, (5, 6), (3, 4), 25, minimum_accounts=12)),
    MANIFEST: ("Manifest", (
        Rule("swap", b"\x04", 19, 1, 0, (3, 4), (5, 6)),
        Rule("swap_v2", b"\x0d", 19, 2, 1, (4, 5), (6, 7)))),
    INVARIANT: ("Invariant", anchor_rules(("swap",), 1, 7, (3, 4), (5, 6), 34)),
    # These opaque ABIs are constrained to observed tags, lengths and accounts.
    # Price updates and admin withdrawals are never inferred to be swaps.
    GOONFI_V2: ("GoonFi v2", (
        Rule("swap_tag_1", b"\x01", 18, 1, 0, (2, 3), (4, 5), exact_bytes=(18, 19), minimum_accounts=13),)),
    SOLFI_V1: ("SolFi v1", (
        Rule("swap_tag_7", b"\x07", 18, 1, 0, (4, 5), (2, 3), exact_bytes=(18,), minimum_accounts=8),)),
    SOLFI_V2: ("SolFi v2", (
        Rule("swap_tag_7", b"\x07", 18, 1, 0, (6, 7), (4, 5), exact_bytes=(18, 25), minimum_accounts=13),)),
    BISONFI: ("BisonFi", tuple(
        Rule(name, bytes([tag]), size, 1, 0, (4, 5), (2, 3), exact_bytes=(size,), minimum_accounts=8)
        for name, tag, size in (("swap_tag_2", 2, 18), ("swap_tag_7", 7, 19), ("swap_signed_quote", 19, 153)))),
    ALPHAQ: ("AlphaQ", (
        Rule("swap_tag_12", b"\x0c", 18, 1, 0, (3, 4), (5, 6), exact_bytes=(18,), minimum_accounts=12),)),
    ZERO_FI: ("ZeroFi", (
        Rule("swap_v4", b"\x10", 17, 0, 8, (6, 7), (3, 5), exact_bytes=(17, 18, 57), minimum_accounts=13),)),
    SCORCH: ("Scorch", (
        Rule("swap_compact", b"\x01", 34, 11, 1, (2, 3), (4, 5), exact_bytes=(34,), minimum_accounts=12),
        Rule("swap", b"\x02", 34, 15, 1, (2, 3), (4, 5), exact_bytes=(34,), minimum_accounts=16))),
    QUANTUM: ("Quantum", (
        Rule("swap_tag_7", b"\x07", 18, 3, 0, (1, 2), (4, 5), exact_bytes=(18,), minimum_accounts=10),)),
    SAROS_DLMM: ("Saros DLMM", anchor_rules(("swap",), 0, 9, (7, 8), (5, 6), 26)),
    OPENBOOK_V2: ("OpenBook v2", anchor_rules(("place_take_order",), 2, 0, (9, 10), (6, 7), 35)),
    STABBLE_CLMM: ("Stabble CLMM", anchor_rules(("swap_v2",), 2, 0, (3, 4), (5, 6), 41)),
    FLUXBEAM: ("FluxBeam", SPL_SWAP),
    CREMA: ("Crema CLMM", anchor_rules(("swap", "swap_with_partner"), 1, 9, (4, 5), (6, 7), 42)),
    PERENA: ("Perena Numeraire", (
        *anchor_rules(("swap_exact_in", "swap_exact_out"), 0, 8, (3, 4), (5, 6), 26),
        *anchor_rules(("swap_exact_in_hinted", "swap_exact_out_hinted"), 0, 8, (3, 4), (5, 6), 116))),
    HEAVEN: ("Heaven", anchor_rules(("buy", "sell"), 4, 5, (8, 9), (10, 11))),
    ONE_DEX: ("1DEX", anchor_rules(("swap_exact_amount_in",), 1, 5, (6, 7), (3, 4))),
    KIPSELI: ("Kipseli", anchor_rules(("swap_v3",), 1, 0, (6, 7), (2, 3), 25)),
    GOONFI_V1: ("GoonFi v1", (
        Rule("swap_tag_2", b"\x02", 19, 1, 0, (2, 3), (4, 5), exact_bytes=(19, 20), minimum_accounts=9),)),
    DEFITUNA: ("DeFiTuna Fusion", anchor_rules(("swap",), 4, 3, (7, 8), (9, 10), 43, minimum_accounts=14)),
    GUACSWAP: ("Guacswap", anchor_rules(("swap",), 1, 8, (6, 7), (4, 5), 33, minimum_accounts=17)),
    OMNIPAIR: ("Omnipair", anchor_rules(("swap",), 0, 9, (5, 6), (3, 4), minimum_accounts=14)),
    WOOFI: ("WOOFi", anchor_rules(("swap",), 4, 2, (5, 10), (6, 11), 40, minimum_accounts=17)),
    DEXLAB: ("DexLab", SPL_SWAP),
    DOOAR: ("Dooar / StepN", SPL_SWAP),
    PENGUIN: ("Penguin", SPL_SWAP),
    TOKEN_SWAP: ("SPL Token Swap", SPL_SWAP),
    CROPPER: ("Cropper CLMM", anchor_rules(("swap",), 2, 1, (3, 5), (4, 6), 42, minimum_accounts=11)),
    METADAO: ("MetaDAO", anchor_rules(("spot_swap",), 0, 5, (1, 2), (3, 4), 25, minimum_accounts=9)),
    VIRTUALS: ("Virtuals", anchor_rules(("buy", "sell"), 1, 0, (3, 4), (5, 8), minimum_accounts=10)),
    SCALE_AMM: ("Scale AMM", anchor_rules(("buy", "sell"), 0, 1, (5, 6), (7, 8), minimum_accounts=14)),
    SCALE_VMM: ("Scale VMM", anchor_rules(("buy", "sell"), 0, 1, (4, 5), (6, 7), minimum_accounts=18)),
    TRENDS: ("Trends", anchor_rules(("swap",), 1, 9, (3, 4), (7, 8), minimum_accounts=15)),
    GHOST_STABLE: ("Stableswap / Ghost", anchor_rules(("swap",), 10, 0, (8, 9), (6, 7), minimum_accounts=16)),
    JUPLEND_AMM: ("Jupiter Lend AMM", anchor_rules(("swap_in", "swap_out"), 1, 0,
        (2, 3, 5, 6), (13, 14), 25, minimum_accounts=24, nested_program_account=20)),
    JUPITER_RFQ: ("Jupiter RFQ v2", anchor_rules(("fill_exact_in",), None, 0, (2, 3), (4, 5), 45, minimum_accounts=11)),
    # These are explicit spot exchanges; leveraged position changes are excluded.
    JUPITER_PERPS: ("Jupiter Perps spot swap", (
        *anchor_rules(("swap2",), 5, 0, (1, 2), (9, 13), minimum_accounts=17),
        *anchor_rules(("swap_with_token_ledger",), 5, 0, (1, 2), (8, 11), 16, minimum_accounts=17))),
    HYLO: ("Hylo Exchange", (
        *anchor_rules(("swap_lst_to_lst",), 1, 0, (3, 8), (5, 10), 17, minimum_accounts=17),
        *anchor_rules(("swap_lst_to_usdc", "swap_usdc_to_lst"), 4, 0, (14, 15), (11, 12), 17, minimum_accounts=25),
        *anchor_rules(("swap_lst_to_usdc_all",), 4, 0, (14, 15), (11, 12), 9, minimum_accounts=25),
        *anchor_rules(("swap_exo_to_usdc", "swap_usdc_to_exo"), 3, 0, (13, 14), (10, 11), 17, minimum_accounts=25),
        *anchor_rules(("swap_exo_to_usdc_all",), 3, 0, (13, 14), (10, 11), 9, minimum_accounts=25))),
    GAVEL: ("Gavel / Plasma", (
        Rule("swap", b"\x00", 19, 2, 3, (4, 5), (6, 7), exact_bytes=(19,), minimum_accounts=9),)),
    DERIVERSE: ("Deriverse spot", (
        Rule("swap", b"\x1a", 24, 5, 0, (11, 12), (3, 4), exact_bytes=(24, 32), minimum_accounts=14),)),
    RUNNER: ("RunnerRodeo", anchor_rules(("swap",), 0, 1, (4, 5), (6, 7), minimum_accounts=18)),
    DENALI: ("Denali", anchor_rules(("swap_exact_in",), 1, 0, (10, 11), (4, 5), 25, minimum_accounts=12)),
    FLUX: ("Flux", (
        Rule("swap_tag_3", b"\x03", 18, 1, 0, (4, 5), (2, 3), exact_bytes=(18,), minimum_accounts=11),)),
    OBSIDIAN: ("Obsidian", (
        Rule("swap_tag_1", b"\x01", 9, 1, 0, (4, 5), (2, 3), exact_bytes=(9,), minimum_accounts=8),)),
    RIPTIDE: ("Riptide", (
        Rule("swap_tag_2", b"\x02", 12, 1, 0, (4, 5), (6, 7), exact_bytes=(12,), minimum_accounts=12),)),
}

EXTRA_DEX_NAMES = {p: value[0] for p, value in PROTOCOLS.items()}
EXTRA_DEX_NAMES[MOONIT] = "Moonit / Moonshot bonding curve"
EXTRA_DEX_NAMES[HUMIDIFI] = "HumidiFi"
EXTRA_DEX_NAMES[MERCURIAL] = "Mercurial StableSwap"
EXTRA_DEX_NAMES[BOOP] = "Boop.fun"
EXTRA_DEX_NAMES[PHOENIX] = "Phoenix"
EXTRA_DEX_NAMES[TRENCH] = "Trench"
EXTRA_DEX_NAMES[M_SWAP] = "M Swap"
EXTRA_DEX_NAMES[PERENA_STAR] = "Perena Star v2"


def expanded_layouts(program: str, raw: bytes, accounts: list[str]) -> list[dict] | None:
    """Return pool legs, or None when this registry has no matching instruction."""
    if program == M_SWAP and raw[:8] == anchor("swap"):
        if len(raw) != 17 or len(accounts) < 24:
            raise LayoutError("Invalid M Swap conversion layout")
        return [{"instruction": "swap", "token_conversion": True, "pool": accounts[3], "trader": accounts[0],
                 "input_account": accounts[9], "output_account": accounts[10],
                 "input_mint": accounts[6], "output_mint": accounts[7],
                 "nested_programs": [accounts[21], accounts[22]], "venue_type": "token_conversion"}]
    if program == PERENA_STAR and raw[:8] == anchor("execute_share_swap"):
        if len(raw) != 17 or len(accounts) < 12 or raw[8] not in (0, 1):
            raise LayoutError("Invalid Perena Star share conversion layout")
        return [{"instruction": "execute_share_swap", "token_conversion": True, "pool": accounts[1], "trader": accounts[0],
                 "input_account": accounts[6], "output_account": accounts[7],
                 "input_mint": accounts[4], "output_mint": accounts[5], "venue_type": "share_conversion"}]
    if program == HEAVEN and raw[:8] == anchor("create_standard_liquidity_pool"):
        if len(raw) < 30 or len(accounts) < 13:
            raise LayoutError("Truncated Heaven pool creation arguments or accounts")
        text_size = int.from_bytes(raw[10:14], "little")
        if len(raw) != 30 + text_size:
            raise LayoutError("Unknown Heaven pool creation argument version")
        return [{"instruction": "create_standard_liquidity_pool", "heaven_initial_buy": True,
                 "requested_tokens": int.from_bytes(raw[14 + text_size:22 + text_size], "little")}]
    if program == TRENCH:
        name = next((n for n in ("buy", "buy_exact_out", "sell") if raw[:8] == anchor(n)), None)
        if name:
            if len(raw) != 24 or len(accounts) < (16 if name == "sell" else 17):
                raise LayoutError("Invalid Trench trade layout")
            return [{"instruction": name, "trench_event": True}]
    if program == DEFITUNA and raw[:8] == anchor("two_hop_swap"):
        if len(raw) < 60 or len(accounts) < 22 or any(b not in (0, 1) for b in raw[24:27]):
            raise LayoutError("Invalid DeFiTuna two-hop swap layout")
        rules = (Rule("two_hop_swap", raw[:8], 60, 0, 14, (8, 11), (9, 10)),
                 Rule("two_hop_swap", raw[:8], 60, 1, 14, (10, 13), (11, 12)))
        return [{**resolve(rule, accounts), "hop_index": i} for i, rule in enumerate(rules)]
    if program == PHOENIX and raw[:1] in (b"\x00", b"\x01", b"\x02", b"\x03"):
        if len(raw) < 3 or raw[1] not in (0, 1, 2) or raw[2] not in (0, 1) or len(accounts) < 4:
            raise LayoutError("Invalid Phoenix order packet or account layout")
        tag = raw[0]
        name = ("swap", "swap_with_free_funds", "place_limit_order", "place_limit_order_with_free_funds")[tag]
        return [{"instruction": name, "orderbook": "phoenix", "pool": accounts[2], "trader": accounts[3],
                 "side": raw[2], "tag": tag}]
    if program == MANIFEST and raw[:1] == b"\x06":
        if len(raw) < 10 or len(accounts) < 2:
            raise LayoutError("Invalid Manifest batch update layout")
        return [{"instruction": "batch_update", "orderbook": "manifest", "pool": accounts[1], "trader": accounts[0]}]
    if program == OPENBOOK_V2:
        name = next((n for n in ("place_order", "place_order_pegged", "place_orders", "cancel_all_and_place_orders")
                     if raw[:8] == anchor(n)), None)
        if name:
            multiple = name in ("place_orders", "cancel_all_and_place_orders")
            minimum = 18 if multiple else 60 if name == "place_order_pegged" else 52
            if len(raw) < minimum or len(accounts) < (14 if multiple else 12):
                raise LayoutError("Invalid OpenBook order layout")
            return [{"instruction": name, "orderbook": "openbook", "pool": accounts[5 if multiple else 4],
                     "trader": accounts[0], "open_orders": accounts[1],
                     "base_vault": accounts[10] if multiple else None,
                     "quote_vault": accounts[9] if multiple else None}]
    if program == MERCURIAL and raw[:1] == b"\x04":
        if len(raw) != 17 or not 8 <= len(accounts) <= 10:
            raise LayoutError("Invalid Mercurial exchange layout (2–4 pool vaults expected)")
        # N-pool exchange lists every pool vault, followed by the two user ATAs.
        return [resolve(Rule("exchange", b"\x04", 17, 0, 3, (-2, -1), tuple(range(4, len(accounts) - 2))), accounts)]
    if program == BOOP:
        name = next((n for n in ("buy_token", "sell_token") if raw[:8] == anchor(n)), None)
        if name:
            if len(raw) != 24 or len(accounts) < (13 if name == "buy_token" else 12):
                raise LayoutError("Invalid Boop.fun trade layout")
            return [{"instruction": name, "boop_event": True}]
    if program == HUMIDIFI:
        # Deobfuscation describes the instruction, not its executed amounts.
        # Each eight-byte block XORs a fixed key and a repeated u16 block index.
        key = bytes.fromhex("3aff2fffe2baebc3")
        clear = bytes(value ^ key[i % 8] ^ (((i // 8) >> (8 * (i % 2))) & 255)
                      for i, value in enumerate(raw))
        if len(raw) == 25 and len(accounts) >= 9 and int.from_bytes(clear[16:24], "little") <= 1:
            rule = Rule("swap_obfuscated", b"", 25, 1, 0, (4, 5), (2, 3))
        elif len(raw) == 113 and len(accounts) >= 12:
            rule = Rule("swap_signed_quote", b"", 113, 1, 0, (4, 5), (2, 3))
        else:
            return None
        return [resolve(rule, accounts)]
    if program == WHIRLPOOL and raw[:8] in {anchor("two_hop_swap"), anchor("two_hop_swap_v2")}:
        v2 = raw[:8] == anchor("two_hop_swap_v2")
        if len(raw) < (60 if v2 else 59) or any(b not in (0, 1) for b in raw[24:27]):
            raise LayoutError("Invalid Whirlpool two-hop swap arguments")
        if not v2:
            rules = (Rule("two_hop_swap", raw[:8], 59, 2, 1, (4, 6), (5, 7)),
                     Rule("two_hop_swap", raw[:8], 59, 3, 1, (8, 10), (9, 11)))
        else:
            # V2 transfers the intermediate token directly between pool vaults.
            rules = (Rule("two_hop_swap_v2", raw[:8], 59, 0, 14, (8, 11), (9, 10)),
                     Rule("two_hop_swap_v2", raw[:8], 59, 1, 14, (10, 13), (11, 12)))
        return [{**resolve(rule, accounts), "hop_index": i} for i, rule in enumerate(rules)]
    if program == MOONIT:
        name = next((n for n in ("buy", "sell", "buy_with_be_authority") if raw[:8] == anchor(n)), None)
        if name:
            offset = int(name == "buy_with_be_authority")
            if len(raw) < 32 or len(accounts) < 11 + offset:
                raise LayoutError("Invalid Moonit swap account/argument layout")
            return [{"instruction": name, "moonit_event": True, "account_offset": offset}]
    for rule in PROTOCOLS.get(program, (None, ()))[1]:
        if raw.startswith(rule.prefix):
            if len(raw) < rule.minimum_bytes:
                raise LayoutError(f"Truncated {rule.name} arguments")
            if rule.exact_bytes and len(raw) not in rule.exact_bytes:
                raise LayoutError(f"Unknown {rule.name} argument version ({len(raw)} bytes)")
            if len(accounts) < rule.minimum_accounts:
                raise LayoutError(f"Truncated {rule.name} account layout")
            layout = resolve(rule, accounts)
            if program == JUPITER_RFQ:
                if raw[8] not in (0, 1) or len(raw) != 45 + int.from_bytes(raw[41:45], "little") * 16:
                    raise LayoutError("Invalid Jupiter RFQ side or quote levels")
                layout.update(venue_type="rfq", fill_authority=accounts[1])
            if program == WOOFI:
                layout["pool_addresses"] = list(dict.fromkeys((accounts[4], accounts[9])))
            return [layout]
    return None


def resolve(rule: Rule, accounts: list[str]) -> dict:
    try:
        nested = list(rule.nested_programs)
        if rule.nested_program_account is not None:
            # A recognized Stabble instruction validates its supplied vault program.
            nested.append(accounts[rule.nested_program_account])
        return {"instruction": rule.name, "pool": accounts[rule.pool] if rule.pool is not None else None, "trader": accounts[rule.trader],
                "user_accounts": [accounts[i] for i in rule.users],
                "vaults": [accounts[i] for i in rule.vaults], "nested_programs": nested}
    except IndexError:
        raise LayoutError("Swap instruction has fewer accounts than its protocol layout") from None


def extra_catalog() -> list[dict]:
    additional = {MANIFEST: ["batch_update"], OPENBOOK_V2: ["place_order", "place_order_pegged", "place_orders", "cancel_all_and_place_orders"],
                  HEAVEN: ["create_standard_liquidity_pool"], DEFITUNA: ["two_hop_swap"]}
    return [{"program_id": p, "dex": name, "instructions": [r.name for r in rules] + additional.get(p, [])}
            for p, (name, rules) in PROTOCOLS.items()] + [
        {"program_id": MOONIT, "dex": EXTRA_DEX_NAMES[MOONIT],
         "instructions": ["buy", "sell", "buy_with_be_authority"]},
        {"program_id": HUMIDIFI, "dex": EXTRA_DEX_NAMES[HUMIDIFI],
         "instructions": ["swap_obfuscated", "swap_signed_quote"]},
        {"program_id": MERCURIAL, "dex": EXTRA_DEX_NAMES[MERCURIAL], "instructions": ["exchange"]},
        {"program_id": BOOP, "dex": EXTRA_DEX_NAMES[BOOP], "instructions": ["buy_token", "sell_token"]},
        {"program_id": TRENCH, "dex": EXTRA_DEX_NAMES[TRENCH], "instructions": ["buy", "buy_exact_out", "sell"]},
        {"program_id": M_SWAP, "dex": EXTRA_DEX_NAMES[M_SWAP], "instructions": ["swap"]},
        {"program_id": PERENA_STAR, "dex": EXTRA_DEX_NAMES[PERENA_STAR], "instructions": ["execute_share_swap"]},
        {"program_id": PHOENIX, "dex": EXTRA_DEX_NAMES[PHOENIX],
         "instructions": ["swap", "swap_with_free_funds", "place_limit_order", "place_limit_order_with_free_funds"]}]
