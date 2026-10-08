import sys
import os
import time
import requests
from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")

DEXSCREENER_BASE = "https://api.dexscreener.com"

if not HELIUS_API_KEY:
    print("❌ HELIUS_API_KEY is missing.")
    sys.exit(1)

HELIUS_RPC = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"


# Solana token programs
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


# ============================================================
# BASIC HELPERS
# ============================================================

def fmt_money(value):
    if value is None:
        return "Unavailable"

    try:
        value = float(value)
    except Exception:
        return "Unavailable"

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:.2f}"


def fmt_number(value):
    if value is None:
        return "Unavailable"

    try:
        value = float(value)
    except Exception:
        return "Unavailable"

    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"

    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"{value / 1_000:.2f}K"

    return f"{value:.2f}"


def short_address(address):
    if not address:
        return "Unavailable"

    if len(address) <= 12:
        return address

    return f"{address[:6]}...{address[-6:]}"


def safe_float(value, default=0):
    try:
        return float(value)
    except Exception:
        return default


# ============================================================
# HELIUS RPC
# ============================================================

def helius_request(method, params):
    payload = {
        "jsonrpc": "2.0",
        "id": "mysterious-research",
        "method": method,
        "params": params,
    }

    try:
        response = requests.post(
            HELIUS_RPC,
            json=payload,
            timeout=30
        )

        response.raise_for_status()

        data = response.json()

        if "error" in data:
            print(f"⚠️ Helius RPC error: {data['error']}")
            return None

        return data.get("result")

    except Exception as e:
        print(f"⚠️ Helius request failed ({method}): {e}")
        return None


# ============================================================
# DEXSCREENER
# ============================================================

def get_dex_pairs(chain, mint):
    try:
        url = f"{DEXSCREENER_BASE}/token-pairs/v1/{chain}/{mint}"

        response = requests.get(
            url,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        if isinstance(data, list):
            return data

        return []

    except Exception as e:
        print(f"⚠️ DexScreener error: {e}")
        return []


# ============================================================
# MARKET DATA
# ============================================================

def get_market_data(chain, mint):
    pairs = get_dex_pairs(chain, mint)

    if not pairs:
        return None

    # Prefer Solana pair if available
    solana_pairs = [
        p for p in pairs
        if p.get("chainId") == "solana"
    ]

    if solana_pairs:
        pairs = solana_pairs

    # Prefer pair with market cap
    with_mc = [
        p for p in pairs
        if p.get("marketCap") is not None
    ]

    if with_mc:
        pairs = with_mc

    pair = max(
        pairs,
        key=lambda p: safe_float(p.get("liquidity", {}).get("usd"))
    )

    base = pair.get("baseToken") or {}
    txns = pair.get("txns") or {}
    volume = pair.get("volume") or {}
    liquidity = pair.get("liquidity") or {}

    buys = safe_float(
        (txns.get("h24") or {}).get("buys")
    )

    sells = safe_float(
        (txns.get("h24") or {}).get("sells")
    )

    total_txns = buys + sells

    pair_created = pair.get("pairCreatedAt")

    age_hours = None

    if pair_created:
        try:
            created_dt = datetime.fromtimestamp(
                pair_created / 1000,
                tz=timezone.utc
            )

            now = datetime.now(timezone.utc)

            age_hours = (
                now - created_dt
            ).total_seconds() / 3600

        except Exception:
            age_hours = None

    raw_symbol = (
        base.get("symbol")
        or "UNKNOWN"
    )

    symbol = str(raw_symbol).lstrip("$").strip()

    if not symbol:
        symbol = "UNKNOWN"

    return {
        "pair": pair,
        "name": base.get("name") or "Unknown",
        "symbol": symbol,
        "market_cap": safe_float(pair.get("marketCap"), None),
        "liquidity": safe_float(liquidity.get("usd"), None),
        "volume": safe_float(volume.get("h24"), 0),
        "buys": int(buys),
        "sells": int(sells),
        "total_txns": int(total_txns),
        "age_hours": age_hours,
        "pair_address": pair.get("pairAddress"),
        "url": pair.get("url"),
    }


# ============================================================
# HOLDER DATA
# ============================================================

def get_holder_data(mint):
    print("🔎 Checking holder distribution...")

    holders = {}

    page = 1
    max_pages = 10

    while page <= max_pages:

        result = helius_request(
            "getTokenAccounts",
            {
                "mint": mint,
                "page": page,
                "limit": 1000
            }
        )

        if not result:
            break

        token_accounts = result.get("token_accounts") or []

        if not token_accounts:
            break

        for account in token_accounts:

            owner = account.get("owner")

            if not owner:
                continue

            amount = safe_float(
                account.get("amount"),
                0
            )

            if amount <= 0:
                continue

            holders[owner] = (
                holders.get(owner, 0) + amount
            )

        if len(token_accounts) < 1000:
            break

        page += 1

    sorted_holders = sorted(
        holders.items(),
        key=lambda x: x[1],
        reverse=True
    )

    total = sum(
        amount for _, amount in sorted_holders
    )

    if total <= 0:
        return {
            "holders": 0,
            "top1_pct": None,
            "top5_pct": None,
            "top10_pct": None,
            "raw_holders": {}
        }

    top1 = sum(
        amount for _, amount in sorted_holders[:1]
    )

    top5 = sum(
        amount for _, amount in sorted_holders[:5]
    )

    top10 = sum(
        amount for _, amount in sorted_holders[:10]
    )

    return {
        "holders": len(sorted_holders),
        "top1_pct": (top1 / total) * 100,
        "top5_pct": (top5 / total) * 100,
        "top10_pct": (top10 / total) * 100,
        "raw_holders": dict(sorted_holders),
        "total_holder_balance": total
    }


# ============================================================
# DIRECT MINT / FREEZE AUTHORITY CHECK
# ============================================================

def get_mint_authorities(mint):
    print("🔐 Checking mint and freeze authorities...")

    result = helius_request(
        "getAccountInfo",
        [
            mint,
            {
                "encoding": "jsonParsed"
            }
        ]
    )

    if not result:
        return {
            "mint_authority": None,
            "freeze_authority": None,
            "mint_authority_status": "UNKNOWN",
            "freeze_authority_status": "UNKNOWN",
            "token_program": None,
            "supply": None,
            "decimals": None,
        }

    value = result.get("value")

    if not value:
        return {
            "mint_authority": None,
            "freeze_authority": None,
            "mint_authority_status": "UNKNOWN",
            "freeze_authority_status": "UNKNOWN",
            "token_program": None,
            "supply": None,
            "decimals": None,
        }

    token_program = value.get("owner")

    data = value.get("data") or {}

    parsed = data.get("parsed") or {}

    info = parsed.get("info") or {}

    mint_authority = info.get("mintAuthority")
    freeze_authority = info.get("freezeAuthority")

    decimals = info.get("decimals")
    supply = info.get("supply")

    if mint_authority:
        mint_status = "ACTIVE"
    elif mint_authority is None:
        mint_status = "REVOKED"
    else:
        mint_status = "UNKNOWN"

    if freeze_authority:
        freeze_status = "ACTIVE"
    elif freeze_authority is None:
        freeze_status = "REVOKED"
    else:
        freeze_status = "UNKNOWN"

    return {
        "mint_authority": mint_authority,
        "freeze_authority": freeze_authority,
        "mint_authority_status": mint_status,
        "freeze_authority_status": freeze_status,
        "token_program": token_program,
        "supply": supply,
        "decimals": decimals,
    }


# ============================================================
# CREATION TRANSACTION / LIKELY CREATOR
# ============================================================

def get_oldest_signature(address):
    """
    Walk backwards through the mint's transaction history and
    return the oldest signature we can find.

    This is used as the likely token creation transaction.
    """

    before = None
    oldest = None

    for _ in range(5):

        config = {
            "limit": 1000
        }

        if before:
            config["before"] = before

        result = helius_request(
            "getSignaturesForAddress",
            [
                address,
                config
            ]
        )

        if not result:
            break

        oldest = result[-1]

        if len(result) < 1000:
            break

        before = result[-1].get("signature")

        if not before:
            break

        time.sleep(0.1)

    if oldest:
        return oldest

    return None


def get_transaction(signature):
    if not signature:
        return None

    result = helius_request(
        "getTransaction",
        [
            signature,
            {
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 1
            }
        ]
    )

    return result


def get_creation_info(mint):
    print("👤 Tracing likely creator / deployer...")

    signature_info = get_oldest_signature(mint)

    if not signature_info:
        return {
            "creator": None,
            "signature": None,
            "block_time": None
        }

    signature = signature_info.get("signature")

    tx = get_transaction(signature)

    if not tx:
        return {
            "creator": None,
            "signature": signature,
            "block_time": signature_info.get("blockTime")
        }

    transaction = tx.get("transaction") or {}
    message = transaction.get("message") or {}

    account_keys = message.get("accountKeys") or []

    creator = None

    # In a parsed Solana transaction, accountKeys contains
    # pubkey/signer/writable information.
    #
    # The first signer is normally the fee payer / creation payer.
    for account in account_keys:

        if isinstance(account, dict):

            pubkey = account.get("pubkey")
            signer = account.get("signer")

            if signer and pubkey:
                creator = pubkey
                break

        elif isinstance(account, str):

            # Fallback for non-parsed account key responses
            if not creator:
                creator = account

    return {
        "creator": creator,
        "signature": signature,
        "block_time": tx.get(
            "blockTime",
            signature_info.get("blockTime")
        )
    }


# ============================================================
# CREATOR SOL BALANCE
# ============================================================

def get_sol_balance(wallet):
    if not wallet:
        return None

    result = helius_request(
        "getBalance",
        [
            wallet
        ]
    )

    if not result:
        return None

    lamports = safe_float(
        result.get("value"),
        0
    )

    return lamports / 1_000_000_000


# ============================================================
# CREATOR TOKEN HOLDINGS
# ============================================================

def get_creator_token_balance(creator, mint):
    if not creator:
        return None

    # Try SPL Token program first
    result = helius_request(
        "getTokenAccountsByOwner",
        [
            creator,
            {
                "mint": mint
            },
            {
                "encoding": "jsonParsed"
            }
        ]
    )

    if not result:
        return None

    accounts = result.get("value") or []

    total_raw = 0

    for account in accounts:

        data = account.get("account") or {}

        parsed_wrapper = data.get("data") or {}

        parsed = parsed_wrapper.get("parsed") or {}

        info = parsed.get("info") or {}

        token_amount = info.get("tokenAmount") or {}

        raw_amount = token_amount.get("amount")

        try:
            total_raw += int(raw_amount)
        except Exception:
            pass

    return total_raw


# ============================================================
# TOKEN SECURITY
# ============================================================

def get_token_security(mint):
    print("🔐 Checking token security...")

    result = helius_request(
        "getAsset",
        {
            "id": mint,
            "displayOptions": {
                "showFungible": True
            }
        }
    )

    if not result:
        return {}

    token_info = result.get("token_info") or {}

    return {
        "interface": result.get("interface"),
        "token_program": token_info.get("token_program"),
        "decimals": token_info.get("decimals"),
        "supply": token_info.get("supply"),
        "authorities": result.get("authorities"),
    }


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    market,
    holders,
    authorities,
    creator_info,
    creator_token_raw
):

    score = 0
    positives = []
    warnings = []

    mc = market.get("market_cap")
    age = market.get("age_hours")
    volume = market.get("volume", 0)
    txns = market.get("total_txns", 0)

    # --------------------------------------------------------
    # MARKET CAP
    # --------------------------------------------------------

    if mc is not None:

        if 1000 <= mc <= 4000:
            score += 25
            positives.append("very early MC")

        elif 4000 < mc <= 10000:
            score += 15
            positives.append("early MC")

    # --------------------------------------------------------
    # AGE
    # --------------------------------------------------------

    if age is not None:

        if age <= 0.5:
            score += 25
            positives.append("under 30 minutes old")

        elif age <= 1:
            score += 20
            positives.append("under 1 hour old")

        elif age <= 6:
            score += 10
            positives.append("under 6 hours old")

        elif age > 24:
            warnings.append("older than 24 hours")

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    if volume <= 500:
        score += 20
        positives.append("very low volume")

    elif volume <= 3000:
        score += 12
        positives.append("low volume")

    elif volume > 100000:
        warnings.append("very high volume")

    # --------------------------------------------------------
    # TRANSACTIONS
    # --------------------------------------------------------

    if txns <= 20:
        score += 15
        positives.append("very low transaction count")

    elif txns <= 60:
        score += 10
        positives.append("low transaction count")

    elif txns > 1500:
        warnings.append("very high transaction count")

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    liquidity = market.get("liquidity")

    if liquidity is None:
        warnings.append("liquidity unavailable")

    elif liquidity <= 0:
        warnings.append("zero liquidity")

    elif liquidity < 1000:
        warnings.append("very low liquidity")

    # --------------------------------------------------------
    # BUY / SELL BALANCE
    # --------------------------------------------------------

    buys = market.get("buys", 0)
    sells = market.get("sells", 0)

    total = buys + sells

    if total > 0:

        buy_ratio = (buys / total) * 100

        if buy_ratio >= 70:
            positives.append("strong buy-side activity")

        elif buy_ratio <= 30:
            warnings.append("strong sell-side activity")

    # --------------------------------------------------------
    # HOLDERS
    # --------------------------------------------------------

    holder_count = holders.get("holders")

    top1 = holders.get("top1_pct")
    top10 = holders.get("top10_pct")

    if holder_count is not None:

        if holder_count < 20:
            score -= 10
            warnings.append("very few holders")

    if top1 is not None:

        if top1 >= 30:
            score -= 20
            warnings.append("top holder concentration above 30%")

        elif top1 >= 20:
            score -= 10
            warnings.append("top holder concentration above 20%")

        elif top1 >= 10:
            score -= 5
            warnings.append("top holder concentration above 10%")

    if top10 is not None:

        if top10 >= 60:
            score -= 20
            warnings.append("top 10 concentration above 60%")

        elif top10 >= 40:
            score -= 10
            warnings.append("top 10 concentration above 40%")

    # --------------------------------------------------------
    # MINT AUTHORITY
    # --------------------------------------------------------

    mint_status = authorities.get(
        "mint_authority_status"
    )

    if mint_status == "REVOKED":

        score += 5
        positives.append("mint authority revoked")

    elif mint_status == "ACTIVE":

        score -= 10
        warnings.append("mint authority still active")

    else:

        warnings.append("mint authority could not be verified")

    # --------------------------------------------------------
    # FREEZE AUTHORITY
    # --------------------------------------------------------

    freeze_status = authorities.get(
        "freeze_authority_status"
    )

    if freeze_status == "REVOKED":

        score += 5
        positives.append("freeze authority revoked")

    elif freeze_status == "ACTIVE":

        score -= 10
        warnings.append("freeze authority still active")

    else:

        warnings.append("freeze authority could not be verified")

    # --------------------------------------------------------
    # CREATOR HOLDINGS
    # --------------------------------------------------------

    supply_raw = authorities.get("supply")

    creator_share = None

    if (
        creator_token_raw is not None
        and supply_raw is not None
    ):

        try:

            supply_int = int(supply_raw)
            creator_int = int(creator_token_raw)

            if supply_int > 0:
                creator_share = (
                    creator_int / supply_int
                ) * 100

        except Exception:
            pass

    # We display creator concentration separately.
    # Holder concentration already penalizes this,
    # so we intentionally avoid double-penalizing it here.

    return {
        "score": max(0, min(100, score)),
        "positives": positives,
        "warnings": warnings,
        "creator_share": creator_share
    }


# ============================================================
# VERDICT
# ============================================================

def get_verdict(score, market, warnings):
    mc = market.get("market_cap")
    age = market.get("age_hours")
    txns = market.get("total_txns", 0)
    volume = market.get("volume", 0)

    serious_activity = (
        txns > 1500
        or volume > 100000
    )

    too_old = (
        age is not None
        and age > 24
    )

    too_high_mc = (
        mc is not None
        and mc > 10000
    )

    if (
        score >= 70
        and not serious_activity
        and not too_old
        and not too_high_mc
    ):
        return "🟢 WATCH"

    if score >= 50:
        return "🟡 CAUTION"

    return "🔴 IGNORE"


# ============================================================
# MAIN
# ============================================================

def main():

    if len(sys.argv) < 3:
        print(
            "Usage: python research.py <chain> <contract>"
        )
        sys.exit(1)

    chain = sys.argv[1].lower()
    contract = sys.argv[2].strip()

    print()
    print("🥷 MYSTERIOUS TOKEN CHECKER")
    print("============================")
    print(f"⛓ Chain: {chain}")
    print(f"📍 Contract: {contract}")
    print()

    if chain != "solana":
        print(
            "⚠️ Creator and mint-authority checks currently "
            "support Solana."
        )

    # --------------------------------------------------------
    # MARKET
    # --------------------------------------------------------

    market = get_market_data(
        chain,
        contract
    )

    if not market:

        print("❌ Could not retrieve market data.")
        sys.exit(1)

    print(
        f"🪙 {market['name']} "
        f"(${market['symbol']})"
    )

    print()

    # --------------------------------------------------------
    # HOLDERS
    # --------------------------------------------------------

    holders = get_holder_data(contract)

    # --------------------------------------------------------
    # SECURITY
    # --------------------------------------------------------

    security = get_token_security(contract)

    authorities = get_mint_authorities(contract)

    # --------------------------------------------------------
    # CREATOR
    # --------------------------------------------------------

    creator_info = get_creation_info(contract)

    creator = creator_info.get("creator")

    creator_token_raw = None

    if creator:
        creator_token_raw = get_creator_token_balance(
            creator,
            contract
        )

    creator_sol = None

    if creator:
        creator_sol = get_sol_balance(creator)

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    result = calculate_score(
        market,
        holders,
        authorities,
        creator_info,
        creator_token_raw
    )

    score = result["score"]

    verdict = get_verdict(
        score,
        market,
        result["warnings"]
    )

    creator_share = result.get(
        "creator_share"
    )

    # --------------------------------------------------------
    # OUTPUT
    # --------------------------------------------------------

    print()
    print(
        f"🎯 Research Score: {score}/100"
    )

    print(
        f"🚦 Verdict: {verdict}"
    )

    print()

    # --------------------------------------------------------
    # MARKET DATA
    # --------------------------------------------------------

    print(
        f"💰 Market Cap: "
        f"{fmt_money(market.get('market_cap'))}"
    )

    print(
        f"💧 Liquidity: "
        f"{fmt_money(market.get('liquidity'))}"
    )

    age = market.get("age_hours")

    if age is None:
        age_text = "Unavailable"
    elif age < 1:
        age_text = f"{age * 60:.0f} minutes"
    else:
        age_text = f"{age:.1f} hours"

    print(
        f"⏱ Pair Age: {age_text}"
    )

    print(
        f"📊 24H Volume: "
        f"{fmt_money(market.get('volume'))}"
    )

    print(
        f"🟢 Buys: {market.get('buys', 0)}"
    )

    print(
        f"🔴 Sells: {market.get('sells', 0)}"
    )

    print(
        f"🔄 Total Txns: "
        f"{market.get('total_txns', 0)}"
    )

    # --------------------------------------------------------
    # HOLDERS
    # --------------------------------------------------------

    print()
    print("👥 HOLDER DISTRIBUTION")

    print(
        f"👤 Holders: "
        f"{holders.get('holders', 'Unavailable')}"
    )

    top1 = holders.get("top1_pct")
    top5 = holders.get("top5_pct")
    top10 = holders.get("top10_pct")

    print(
        f"🥇 Top 1 Holder: "
        f"{top1:.1f}%"
        if top1 is not None
        else "🥇 Top 1 Holder: Unavailable"
    )

    print(
        f"🏆 Top 5 Holders: "
        f"{top5:.1f}%"
        if top5 is not None
        else "🏆 Top 5 Holders: Unavailable"
    )

    print(
        f"📊 Top 10 Holders: "
        f"{top10:.1f}%"
        if top10 is not None
        else "📊 Top 10 Holders: Unavailable"
    )

    # --------------------------------------------------------
    # SECURITY
    # --------------------------------------------------------

    print()
    print("🔐 TOKEN SECURITY")

    interface = security.get("interface")

    if interface:
        print(
            f"🧩 Interface: {interface}"
        )

    token_program = (
        authorities.get("token_program")
        or security.get("token_program")
    )

    if token_program:
        print(
            f"⚙️ Token Program: {token_program}"
        )

    decimals = (
        authorities.get("decimals")
        or security.get("decimals")
    )

    if decimals is not None:
        print(
            f"🔢 Decimals: {decimals}"
        )

    supply = (
        authorities.get("supply")
        or security.get("supply")
    )

    if supply is not None:
        print(
            f"📦 Supply: {supply}"
        )

    mint_status = authorities.get(
        "mint_authority_status"
    )

    freeze_status = authorities.get(
        "freeze_authority_status"
    )

    if mint_status == "REVOKED":
        print("🪙 Mint Authority: 🟢 REVOKED")

    elif mint_status == "ACTIVE":
        print("🪙 Mint Authority: 🔴 ACTIVE")

    else:
        print("🪙 Mint Authority: ⚪ UNKNOWN")

    if freeze_status == "REVOKED":
        print("❄️ Freeze Authority: 🟢 REVOKED")

    elif freeze_status == "ACTIVE":
        print("❄️ Freeze Authority: 🔴 ACTIVE")

    else:
        print("❄️ Freeze Authority: ⚪ UNKNOWN")

    # --------------------------------------------------------
    # CREATOR
    # --------------------------------------------------------

    print()
    print("👤 CREATOR / DEPLOYER")

    if creator:

        print(
            f"🧑 Likely Creator: {creator}"
        )

        if creator_sol is not None:
            print(
                f"💰 Creator SOL: "
                f"{creator_sol:.4f} SOL"
            )

        if creator_token_raw is not None:

            creator_tokens = creator_token_raw

            creator_decimals = (
                authorities.get("decimals")
            )

            if creator_decimals is not None:

                try:
                    creator_tokens_display = (
                        creator_tokens
                        / (10 ** int(creator_decimals))
                    )

                    print(
                        f"🪙 Creator Tokens: "
                        f"{fmt_number(creator_tokens_display)}"
                    )

                except Exception:
                    print(
                        f"🪙 Creator Tokens: "
                        f"{creator_tokens}"
                    )

            else:

                print(
                    f"🪙 Creator Tokens: "
                    f"{creator_tokens}"
                )

        if creator_share is not None:

            print(
                f"📊 Creator Supply: "
                f"{creator_share:.2f}%"
            )

            if creator_share >= 20:
                result["warnings"].append(
                    "creator holds more than 20% of supply"
                )

            elif creator_share >= 10:
                result["warnings"].append(
                    "creator holds more than 10% of supply"
                )

    else:

        print(
            "🧑 Likely Creator: Unavailable"
        )

    creation_signature = creator_info.get(
        "signature"
    )

    if creation_signature:
        print(
            f"🧾 Creation Tx: "
            f"{creation_signature}"
        )

    # --------------------------------------------------------
    # POSITIVES
    # --------------------------------------------------------

    print()
    print("✅ POSITIVE SIGNALS")

    if result["positives"]:

        for item in result["positives"]:
            print(f"• {item}")

    else:
        print("• None identified")

    # --------------------------------------------------------
    # WARNINGS
    # --------------------------------------------------------

    print()
    print("🚩 WARNINGS")

    if result["warnings"]:

        # Remove duplicates while preserving order
        unique_warnings = []

        for warning in result["warnings"]:

            if warning not in unique_warnings:
                unique_warnings.append(warning)

        for warning in unique_warnings:
            print(f"• {warning}")

    else:

        print("• None identified")

    # --------------------------------------------------------
    # LINKS
    # --------------------------------------------------------

    print()

    if market.get("url"):
        print(
            f"🔗 DexScreener: "
            f"{market.get('url')}"
        )

    print()
    print("============================")
    print("🥷 RESEARCH COMPLETED")
    print("============================")


if __name__ == "__main__":
    main()
