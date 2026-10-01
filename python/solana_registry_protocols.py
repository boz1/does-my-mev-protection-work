"""Registry conversion adapters and additional explicitly identified exchanges.

Instruction layouts come from published program sources/IDLs and saved executions.
The execution decoder still verifies every amount against transfers, burns or mints.
"""

from solana_swap_protocols import LayoutError, Rule, anchor, resolve

CARROT = "CarrotwivhMpDnm27EHmRLeQ683Z1PufuqEmBZvD282s"
HUMA = "HumaXepHnjaRCpjYTokxY4UtaJcmx41prQ8cxGmFC5fn"
HELIUM = "treaf4wWBBty3fHdyBpo35Mz84M8k3heKXmjmi9vFt5"
JUP_EARN = "jup3YeL8QhtSx1e253b2FDvsMNC87fDrgQZivbrndc9"
VOLTR = "vVoLTRjQmtFpiYoegx285Ze4gsLJ8ZxgFKVcuvmG1a8"
SABER_DECIMALS = "DecZY86MU5Gj7kppfUCEmd4LbXXuyZH1yHaP2NTqdiZB"
SOLAYER = "endoLNCKTqDn8gSVnN2hDdpgACUPWHZTwoYnnMybpAT"
XORCA = "StaKE6XNKVVhG8Qu9hDJBqCW3eRe7MDGLz17nJZetLT"
SANCTUM_INF = "5ocnV1qiCgaQR8Jb8xWnVbApfaygJ8tNoZfgPwsgx9kx"
SANCTUM_ROUTER = "stkitrT1Uoy18Dk1fTrgPw8W6MVzoCfYoAFT4MLsmhq"
SANCTUM_PROP = "pegVkBpfR9GFi5Jaa9YXAHACyM9CzCNvhc5bf8GWNyW"
SANCTUM_SOLS = "so1f7APRw5pJ5iNJrM9g5X9tQgXzk8kMUnXxDNdt99b"
AQUIFER = "AQU1FRd7papthgdrwPTTq5JacJh8YtwEXaBfKU3bTz45"
ARCHER = "Archer8kgiavM61GyusMzaaS2ft5sALtNsD1HxkUPMhy"
BINARYFI = "B72M6nyCLFgWiJtAN4naUTminMiTmyGcEqQHXwVeRdht"
BISON_PREDICT = "2DNbzPochEcyCcWMbL4d9S3u9QqQEj5bbe6cSZFvKsbh"
FLINT = "FLiNTXPwppyoJabCoxc2uiiRygAHpmMXajiDXo2Ub1z"
GATOR = "gatorLx9aC1e5ZWAXscv5QRKiLXnLPLXjftVc81h1Hr"
HADRON = "HADRoNbLovyqhCsocfYQYB7QdfCAAinN9HTePvBCVDQ8"
LEMMINGS = "BQEJZUB4CzoT6UhRffoCkqCyqQNrCPCSGHcPEmsdbEsX"
METRIC = "Bvs46DPFxiFE6YHxLDLD6QAUcmy51FyRVPZJusPxLk3j"
QUAY = "QUayE6nexQWYNZAEqfN8FxoNwQDSu3CAzT2qq9J1ArG"
TAURUS = "9VX8EKBg6vM6tA68xaDsPkbrx26XConZjkQmhVApUptc"
WHALE = "FW6zUqn4iKRaeopwwhwsquTY6ABWLLgjxtrC3VPnaWBf"
VAULT_UNSTAKE = "2rU1oCHtQ7WJUvy15tKtFvxdYNNSc3id7AzUcjeFSddo"
SCORCH_PRICING = "ojh19ojaKduoJZuaJADhcVGp4xt1TcdAvZmpVsCorch"
STAKE_POOLS = {
    "SPoo1Ku8WFXoNDMHPsrGSTSG1Y47rzgn41SLUNakuHy": "SPL Stake Pool / Sanctum",
    "SP12tWFxD9oJsVWNavTTBZvMbA6gkAmxtVgxdqvyvhY": "Sanctum SPL Stake Pool",
    "SPMBzsVUuoHA4Jm6KunbsotaahvVikZs1JyTW6iJvbn": "Sanctum Multi Stake Pool",
}

NAMES = {CARROT: "Carrot", HUMA: "Huma", HELIUM: "Helium Network",
         JUP_EARN: "Jupiter Lend Earn", VOLTR: "Voltr", SABER_DECIMALS: "Saber Decimals",
         SOLAYER: "Solayer", XORCA: "XOrca", SANCTUM_INF: "Sanctum Infinity",
         SANCTUM_ROUTER: "Sanctum StakeDex", SANCTUM_PROP: "Sanctum Prop S",
         SANCTUM_SOLS: "Sanctum SOLS", **STAKE_POOLS}
NAMES.update({AQUIFER:"Aquifer", ARCHER:"Archer", BINARYFI:"BinaryFi", BISON_PREDICT:"BisonFi Predict",
              FLINT:"Flint", GATOR:"GatorSwap", HADRON:"Hadron", LEMMINGS:"LemmingsFi",
              METRIC:"Metric", QUAY:"Quay", TAURUS:"TaurusFi", WHALE:"WhaleStreet", VAULT_UNSTAKE:"VaultLiquidUnstake"})
NAMES[SCORCH_PRICING] = "Scorch pricing"

# Opaque interfaces are restricted to verified tags, sizes and account positions.
# Their administrative withdrawals and oracle updates have different shapes.
SWAPS = {
    AQUIFER: (Rule("swap_tag_1", b"\x01", 9, None, 1, (3, 6), (13, 15), exact_bytes=(9,), minimum_accounts=16),),
    ARCHER: (Rule("swap_tag_15", b"\x0f", 19, 1, 0, (2, 7, 8), (5, 6), exact_bytes=(19,), minimum_accounts=12),),
    BINARYFI: (Rule("swap_tag_8", b"\x08", 17, 2, 0, (8, 9), (6, 7), exact_bytes=(17,), minimum_accounts=14),),
    BISON_PREDICT: (
        Rule("swap_tag_5", b"\x05", 19, 3, 0, (1, 2), (4, 5, 8), exact_bytes=(19,), minimum_accounts=18),
        Rule("swap_tag_14", b"\x0e", 20, 3, 0, (1, 2), (4, 5, 8), exact_bytes=(20,), minimum_accounts=18)),
    FLINT: (Rule("swap_tag_6", b"\x06", 10, 2, 0, (4, 6), (5, 7), exact_bytes=(10,), minimum_accounts=11),),
    HADRON: (Rule("swap_tag_3", b"\x03", 26, 2, 7, (8, 11), (9, 10), exact_bytes=(26,), minimum_accounts=16),),
    LEMMINGS: (Rule("swap", anchor("swap"), 25, 2, 0, (5, 6), (3, 4), exact_bytes=(25,), minimum_accounts=8),),
    METRIC: (Rule("swap_tag_1", b"\x01", 27, 1, 0, (6, 7), (4, 5), exact_bytes=(27,), minimum_accounts=15),),
    QUAY: (Rule("swap_tag_32", b"\x20", 18, 2, 8, (6, 7), (4, 5), exact_bytes=(18,), minimum_accounts=13),),
    TAURUS: (Rule("swap_tag_8", b"\x08", 19, 6, 2, (3, 4), (0, 1), exact_bytes=(19,), minimum_accounts=14),),
    WHALE: (Rule("swap_2b04ed0b1ac91e62", bytes.fromhex("2b04ed0b1ac91e62"), 41, 0, 5, (1, 2), (3, 4), exact_bytes=(41,), minimum_accounts=8),),
    SANCTUM_PROP: (Rule("swap", b"\x00", 17, 4, 5, (0, 2), (1, 3), exact_bytes=(17,), minimum_accounts=8),),
    VAULT_UNSTAKE: (Rule("buy_lst", anchor("buy_lst"), 25, 2, 0, (1, 11), (3, 4), exact_bytes=(25,), minimum_accounts=17),),
}

INSTRUCTIONS = {
    CARROT: ["issue", "redeem"],
    HUMA: ["deposit", "make_initial_deposit", "instant_withdraw", "instant_withdraw_privileged", "withdraw_after_pool_closure"],
    HELIUM: ["redeem_v0"],
    JUP_EARN: ["deposit", "mint", "deposit_with_min_amount_out", "withdraw", "redeem", "withdraw_with_max_shares_burn", "redeem_with_min_amount_out"],
    VOLTR: ["deposit_vault", "instant_withdraw_vault"],
    SABER_DECIMALS: ["deposit", "withdraw"],
    SOLAYER: ["delegate", "delegate_no_init", "undelegate", "undelegate_no_init"],
    XORCA: ["stake"],
    SANCTUM_INF: ["swap_exact_in", "swap_exact_out", "add_liquidity", "remove_liquidity", "swap_exact_in_v2", "swap_exact_out_v2"],
    SANCTUM_ROUTER: ["stake_wrapped_sol", "withdraw_wrapped_sol"],
    SANCTUM_PROP: ["swap"],
    SANCTUM_SOLS: ["deposit_wrapped_sol"],
    **{p: ["deposit_sol", "withdraw_sol"] for p in STAKE_POOLS},
    **{p: [r.name for r in rules] for p, rules in SWAPS.items()},
    GATOR: ["swap", "swap_signed_quote"],
    SCORCH_PRICING: ["pricing_cpi"],
}


def checked(raw, a, name, size, accounts):
    if (isinstance(size, tuple) and len(raw) not in size) or (isinstance(size, int) and len(raw) != size) or len(a) < accounts:
        raise LayoutError(f"Invalid {name} argument or account layout")


def receipt(name, a, pool, trader, inp, out, *, nested=(), venue="share_conversion"):
    # Side tuples: evidence kind, user account, mint (None = token metadata), vault.
    def side(spec):
        kind, account, mint, vault = spec
        return {"kind": kind, "account": a[account],
                "mint": a[mint] if mint is not None else None,
                "vault": a[vault] if vault is not None else None}
    return [{"instruction": name, "receipt_conversion": True,
             "pool": a[pool] if pool is not None else None, "trader": a[trader],
             "input": side(inp), "output": side(out), "nested_programs": list(nested), "venue_type": venue}]


def registry_layouts(program, raw, a):
    for rule in SWAPS.get(program, ()):
        if raw.startswith(rule.prefix):
            checked(raw, a, rule.name, rule.exact_bytes, rule.minimum_accounts)
            layout = resolve(rule, a)
            if program == AQUIFER:
                layout["pool_addresses"] = [a[12], a[14]]
            if program == FLINT:
                layout["pool_addresses"] = [a[2], a[3]]
            if program == BISON_PREDICT:
                layout["venue_type"] = "prediction_market"
            if program == VAULT_UNSTAKE:
                layout["venue_type"] = "token_conversion"
            return [layout]
    if program == GATOR and len(raw) in (41, 113) and raw[-1:] == b"\x01":
        # The input mint and amount precede the trailing instruction tag.
        checked(raw, a, "swap", (41, 113), 13)
        layout = resolve(Rule("swap" if len(raw) == 41 else "swap_signed_quote", b"", len(raw), 3, 0, (1, 2), (4, 5)), a)
        layout["declared_input_mint_bytes"] = raw[8:40]
        return [layout]
    name = next((n for n in INSTRUCTIONS.get(program, ()) if raw[:8] == anchor(n)), None)
    if program == CARROT and name:
        checked(raw, a, name, 16, 11)
        if name == "issue":
            return receipt(name, a, 0, 6, ("transfer", 5, 3, 4), ("mint", 2, 1, None))
        return receipt(name, a, 0, 6, ("burn", 2, 1, None), ("transfer", 5, 3, 4))
    if program == SABER_DECIMALS and name:
        checked(raw, a, name, 16, 7)
        if name == "deposit":
            return receipt(name, a, 0, 3, ("transfer", 4, None, 2), ("mint", 5, 1, None), venue="token_conversion")
        return receipt(name, a, 0, 3, ("burn", 5, 1, None), ("transfer", 4, None, 2), venue="token_conversion")
    if program == HELIUM and name:
        checked(raw, a, name, 24, 10)
        return receipt(name, a, 0, 7, ("burn", 5, 2, None), ("transfer", 6, 1, 3),
                       nested=(a[8],), venue="token_conversion")
    if program == HUMA and name:
        if name in ("deposit", "make_initial_deposit"):
            extra = int(name == "deposit")
            if len(raw) < 20 + extra:
                raise LayoutError("Truncated Huma deposit commitment")
            checked(raw, a, name, 20 + int.from_bytes(raw[16:20], "little") + extra, 13)
            if extra and raw[-1] not in (0, 1):
                raise LayoutError("Invalid Huma auto-renewal flag")
            return receipt(name, a, 3, 0, ("transfer", 9, 7, 8), ("mint", 10, 5, None))
        if name == "instant_withdraw":
            checked(raw, a, name, 24, 17)
            return receipt(name, a, 3, 0, ("burn", 14, 5, None), ("transfer", 12, 9, 11))
        if name == "instant_withdraw_privileged":
            checked(raw, a, name, 16, 17)
            return receipt(name, a, 3, 0, ("burn", 14, 6, None), ("transfer", 13, 10, 12))
        checked(raw, a, name, 8, 14)
        return receipt(name, a, 3, 0, ("burn", 11, 5, None), ("transfer", 10, 7, 9))
    if program == JUP_EARN and name:
        deposit = name in ("deposit", "mint", "deposit_with_min_amount_out")
        checked(raw, a, name, 24 if "_with_" in name else 16, 17 if deposit else 18)
        if deposit:
            return receipt(name, a, 5, 0, ("transfer", 1, 3, 10), ("mint", 2, 6, None), nested=(a[12],))
        return receipt(name, a, 4, 0, ("burn", 1, 6, None), ("transfer", 2, 5, 10), nested=(a[13],))
    if program == VOLTR and name:
        if name == "deposit_vault":
            checked(raw, a, name, 16, 13)
            return receipt(name, a, 2, 0, ("transfer", 5, 3, 6), ("mint", 8, 4, None))
        checked(raw, a, name, 18, 12)
        if any(b not in (0, 1) for b in raw[16:18]):
            raise LayoutError("Invalid Voltr withdrawal flags")
        return receipt(name, a, 2, 0, ("burn", 5, 4, None), ("transfer", 8, 3, 6))
    if program == SOLAYER:
        if name:
            checked(raw, a, name, 16, 8 if name.endswith("no_init") else 10)
            if name in ("delegate", "delegate_no_init"):
                return receipt(name, a, 1, 0, ("transfer", 5, 4, 3), ("mint", 6, 2, None), venue="token_conversion")
            return receipt(name, a, 1, 0, ("burn", 6, 2, None), ("transfer", 5, 4, 3), venue="token_conversion")
    if program == XORCA and raw[:1] == b"\x00":
        checked(raw, a, "stake", 9, 8)
        return receipt("stake", a, 5, 0, ("transfer", 2, 6, 1), ("mint", 3, 4, None), venue="staking_conversion")
    if program in STAKE_POOLS and raw[:1] in (b"\x0e", b"\x10"):
        deposit = raw[0] == 14
        name = "deposit_sol" if deposit else "withdraw_sol"
        checked(raw, a, name, 9, 10 if deposit else 12)
        if deposit:
            return receipt(name, a, 0, 3, ("native", 3, None, 2), ("mint", 4, 7, None), venue="staking_conversion")
        return receipt(name, a, 0, 2, ("burn", 3, 7, None), ("native", 5, None, 4), venue="staking_conversion")
    if program == SANCTUM_INF and raw[:1] in (b"\x01", b"\x02", b"\x03", b"\x04", b"\x17", b"\x18"):
        tag = raw[0]
        if tag in (1, 2, 23, 24):
            name = {1:"swap_exact_in", 2:"swap_exact_out", 23:"swap_exact_in_v2", 24:"swap_exact_out_v2"}[tag]
            checked(raw, a, name, 27, 12 if tag < 23 else 11)
            v2 = tag >= 23
            return [resolve(Rule(name, raw[:1], 27, 7 if v2 else 8, 0, (3, 4), (9, 10) if v2 else (10, 11)), a)]
        name = "add_liquidity" if tag == 3 else "remove_liquidity"
        checked(raw, a, name, 22, 11)
        if tag == 3:
            return receipt(name, a, 8, 0, ("transfer", 2, 1, 10), ("mint", 3, 4, None))
        return receipt(name, a, 8, 0, ("burn", 3, 4, None), ("transfer", 2, 1, 10))
    if program == SANCTUM_SOLS and raw[:1] == b"\x02":
        checked(raw, a, "deposit_wrapped_sol", 9, 9)
        return receipt("deposit_wrapped_sol", a, 2, 5, ("transfer", 3, 6, 4), ("mint", 1, 0, None), venue="token_conversion")
    if program == SANCTUM_ROUTER and raw[:1] in (b"\x00", b"\x08"):
        if raw[0] == 0:
            checked(raw, a, "stake_wrapped_sol", 9, 11)
            layouts = receipt("stake_wrapped_sol", a, None, 0, ("transfer", 1, 7, 3), ("transfer", 2, 6, 5), venue="staking_conversion")
        else:
            checked(raw, a, "withdraw_wrapped_sol", 9, 8)
            if a[7] not in STAKE_POOLS:
                raise LayoutError("Unknown StakeDex withdrawal backend")
            layouts = receipt("withdraw_wrapped_sol", a, 8, 0, ("burn", 1, 4, None), ("transfer", 2, 5, 3), nested=(a[7],), venue="staking_conversion")
        layouts[0]["suppress_nested_conversions"] = True
        return layouts
    return None


def registry_catalog():
    return [{"program_id": p, "dex": NAMES[p], "instructions": names,
             **({"role": "pricing", "execution_program_id": "SCoRcH8c2dpjvcJD6FiPbCSQyQgu3PcUAWj2Xxx3mqn"} if p == SCORCH_PRICING else {})}
            for p, names in INSTRUCTIONS.items()]
