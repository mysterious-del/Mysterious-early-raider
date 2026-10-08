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

SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


# ============================================================
# HELPERS
# ============================================================

def safe_float(value, default=0):
    try:
        return float(value)
    except Exception:
        return default


def fmt_money(value):
    if value is None:
        return "Unavailable"

    value = safe_float(value, None)

    if value is None:
        return "Unavailable"

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:.2f}"


def fmt_number(value):
    if value is None:
        return "Unavailable"

    value = safe_float(value, None)

    if value is None:
        return "Unavailable"

    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"

    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"{value / 1_000:.2f}K"

    return f"{value:.2f}"


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
            print(
                f"⚠️ Helius RPC error: {data['error']}"
            )
            return None

        return data.get("result")

    except Exception as e:

        print(
            f"⚠️ Helius request failed "
            f"({method}): {e}"
        )

        return None


# ============================================================
# DEXSCREENER
# ============================================================

def get_market_data(chain, mint):

    try:

        url = (
            f"{DEXSCREENER_BASE}"
            f"/token-pairs/v1/{chain}/{mint}"
        )

        response = requests.get(
            url,
            timeout=20
        )

        response.raise_for_status()

        pairs = response.json()

    except Exception as e:

        print(
            f"⚠️ DexScreener error: {e}"
        )

        return None

    if not isinstance(pairs, list) or not pairs:
        return None

    if chain == "solana":

        solana_pairs = [
            p for p in pairs
            if p.get("chainId") == "solana"
        ]

        if solana_pairs:
            pairs = solana_pairs

    with_mc = [
        p for p in pairs
        if p.get("marketCap") is not None
    ]

    if with_mc:
        pairs = with_mc

    pair = max(
        pairs,
        key=lambda p: safe_float(
            (p.get("liquidity") or {}).get("usd")
        )
    )

    base = pair.get("baseToken") or {}
    txns = pair.get("txns") or {}
    volume = pair.get("volume") or {}
    liquidity = pair.get("liquidity") or {}

    h24 = txns.get("h24") or {}

    buys = int(
        safe_float(h24.get("buys"), 0)
    )

    sells = int(
        safe_float(h24.get("sells"), 0)
    )

    total_txns = buys + sells

    age_hours = None

    pair_created = pair.get("pairCreatedAt")

    if pair_created:

        try:

            created = datetime.fromtimestamp(
                pair_created / 1000,
                tz=timezone.utc
            )

            age_hours = (
                datetime.now(timezone.utc)
                - created
            ).total_seconds() / 3600

        except Exception:
            pass

    symbol = str(
        base.get("symbol") or "UNKNOWN"
    ).lstrip("$").strip()

    return {
        "name": base.get("name") or "Unknown",
        "symbol": symbol or "UNKNOWN",
        "market_cap": safe_float(
            pair.get("marketCap"),
            None
        ),
        "liquidity": safe_float(
            liquidity.get("usd"),
            None
        ),
        "volume": safe_float(
            volume.get("h24"),
            0
        ),
        "buys": buys,
        "sells": sells,
        "total_txns": total_txns,
        "age_hours": age_hours,
        "url": pair.get("url"),
        "pair_address": pair.get("pairAddress"),
    }


# ============================================================
# HOLDERS
# ============================================================

def get_holder_data(mint):

    print("🔎 Checking holder distribution...")

    holders = {}

    page = 1

    while page <= 10:

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

        accounts = (
            result.get("token_accounts") or []
        )

        if not accounts:
            break

        for account in accounts:

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
                holders.get(owner, 0)
                + amount
            )

        if len(accounts) < 1000:
            break

        page += 1

    ranked = sorted(
        holders.items(),
        key=lambda x: x[1],
        reverse=True
    )

    total = sum(
        amount for _, amount in ranked
    )

    if total <= 0:

        return {
            "holders": 0,
            "top1_pct": None,
            "top5_pct": None,
            "top10_pct": None,
            "top_holder": None,
            "top_holder_balance": 0,
            "total": 0
        }

    top1_amount = (
        ranked[0][1]
        if ranked
        else 0
    )

    top5_amount = sum(
        x[1] for x in ranked[:5]
    )

    top10_amount = sum(
        x[1] for x in ranked[:10]
    )

    return {
        "holders": len(ranked),

        "top1_pct": (
            top1_amount / total
        ) * 100,

        "top5_pct": (
            top5_amount / total
        ) * 100,

        "top10_pct": (
            top10_amount / total
        ) * 100,

        "top_holder": (
            ranked[0][0]
            if ranked
            else None
        ),

        "top_holder_balance": (
            ranked[0][1]
            if ranked
            else 0
        ),

        "total": total
    }


# ============================================================
# MINT / FREEZE AUTHORITY
# ============================================================

def get_mint_authorities(mint):

    print(
        "🔐 Checking mint and freeze authorities..."
    )

    result = helius_request(
        "getAccountInfo",
        [
            mint,
            {
                "encoding": "jsonParsed"
            }
        ]
    )

    if not result or not result.get("value"):
        return {
            "mint_authority": None,
            "freeze_authority": None,
            "mint_authority_status": "UNKNOWN",
            "freeze_authority_status": "UNKNOWN",
            "token_program": None,
            "supply": None,
            "decimals": None,
        }

    value = result["value"]

    data = value.get("data") or {}

    parsed = data.get("parsed") or {}

    info = parsed.get("info") or {}

    mint_authority = info.get(
        "mintAuthority"
    )

    freeze_authority = info.get(
        "freezeAuthority"
    )

    return {
        "mint_authority": mint_authority,

        "freeze_authority": freeze_authority,

        "mint_authority_status": (
            "ACTIVE"
            if mint_authority
            else "REVOKED"
        ),

        "freeze_authority_status": (
            "ACTIVE"
            if freeze_authority
            else "REVOKED"
        ),

        "token_program": value.get("owner"),

        "supply": info.get("supply"),

        "decimals": info.get("decimals"),
    }


# ============================================================
# CREATION TRANSACTION
# ============================================================

def get_oldest_signature(address):

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

        before = result[-1].get(
            "signature"
        )

        if not before:
            break

    return (
        oldest.get("signature")
        if oldest
        else None
    )


def get_transaction(signature):

    if not signature:
        return None

    return helius_request(
        "getTransaction",
        [
            signature,
            {
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 1
            }
        ]
    )


def get_creation_info(mint):

    print(
        "👤 Tracing likely creator / deployer..."
    )

    signature = get_oldest_signature(
        mint
    )

    if not signature:

        return {
            "creator": None,
            "signature": None
        }

    tx = get_transaction(
        signature
    )

    if not tx:

        return {
            "creator": None,
            "signature": signature
        }

    message = (
        tx.get("transaction", {})
        .get("message", {})
    )

    keys = message.get(
        "accountKeys"
    ) or []

    creator = None

    for key in keys:

        if isinstance(key, dict):

            if key.get("signer"):

                creator = key.get(
                    "pubkey"
                )

                break

        elif isinstance(key, str):

            creator = key
            break

    return {
        "creator": creator,
        "signature": signature
    }


# ============================================================
# WALLET SOL BALANCE
# ============================================================

def get_sol_balance(wallet):

    if not wallet:
        return None

    result = helius_request(
        "getBalance",
        [wallet]
    )

    if not result:
        return None

    return (
        safe_float(
            result.get("value"),
            0
        ) / 1_000_000_000
    )


# ============================================================
# WALLET TOKEN BALANCE
# ============================================================

def get_wallet_token_balance(
    wallet,
    mint
):

    if not wallet:
        return None

    result = helius_request(
        "getTokenAccountsByOwner",
        [
            wallet,
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

    total = 0

    for item in result.get("value", []):

        account = (
            item.get("account") or {}
        )

        data = (
            account.get("data") or {}
        )

        parsed = (
            data.get("parsed") or {}
        )

        info = (
            parsed.get("info") or {}
        )

        token_amount = (
            info.get("tokenAmount") or {}
        )

        try:
            total += int(
                token_amount.get(
                    "amount",
                    0
                )
            )
        except Exception:
            pass

    return total


# ============================================================
# WALLET HISTORY
# ============================================================

def get_wallet_history(wallet):

    if not wallet:
        return {
            "transactions": 0,
            "failed": 0,
            "token_activity": 0,
            "recent_signatures": []
        }

    result = helius_request(
        "getTransactionsForAddress",
        [
            wallet,
            {
                "transactionDetails": "full",
                "sortOrder": "desc",
                "limit": 50,
                "filters": {
                    "tokenAccounts": "balanceChanged",
                    "status": "succeeded"
                }
            }
        ]
    )

    if not result:

        # Fallback to standard Solana RPC
        result = helius_request(
            "getSignaturesForAddress",
            [
                wallet,
                {
                    "limit": 20
                }
            ]
        )

        if not result:

            return {
                "transactions": 0,
                "failed": 0,
                "token_activity": 0,
                "recent_signatures": []
            }

        return {
            "transactions": len(result),
            "failed": sum(
                1 for x in result
                if x.get("err")
            ),
            "token_activity": 0,
            "recent_signatures": [
                x.get("signature")
                for x in result
                if x.get("signature")
            ]
        }

    transactions = (
        result.get("data")
        if isinstance(result, dict)
        else result
    )

    if not isinstance(transactions, list):
        transactions = []

    token_activity = 0

    for tx in transactions:

        if (
            tx.get("tokenTransfers")
            or tx.get("balanceChanges")
        ):
            token_activity += 1

    return {
        "transactions": len(transactions),
        "failed": 0,
        "token_activity": token_activity,
        "recent_signatures": [
            tx.get("signature")
            for tx in transactions
            if tx.get("signature")
        ]
    }


# ============================================================
# TOKEN SECURITY
# ============================================================

def get_token_security(mint):

    print(
        "🔐 Checking token security..."
    )

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

    token_info = (
        result.get("token_info") or {}
    )

    return {
        "interface": result.get(
            "interface"
        ),

        "token_program": token_info.get(
            "token_program"
        ),

        "decimals": token_info.get(
            "decimals"
        ),

        "supply": token_info.get(
            "supply"
        )
    }


# ============================================================
# RISK ANALYSIS
# ============================================================

def analyze_wallet_risk(
    wallet,
    label,
    token_balance_raw,
    supply_raw,
    decimals
):

    if not wallet:

        return {
            "risk": "UNKNOWN",
            "warnings": [],
            "positives": [],
            "share": None,
            "sol": None,
            "history": {}
        }

    sol = get_sol_balance(wallet)

    history = get_wallet_history(
        wallet
    )

    share = None

    if (
        token_balance_raw is not None
        and supply_raw is not None
    ):

        try:

            supply = int(supply_raw)
            balance = int(
                token_balance_raw
            )

            if supply > 0:

                share = (
                    balance / supply
                ) * 100

        except Exception:
            pass

    warnings = []
    positives = []

    if share is not None:

        if share >= 30:
            warnings.append(
                f"{label} controls more than 30% of supply"
            )

        elif share >= 20:
            warnings.append(
                f"{label} controls more than 20% of supply"
            )

        elif share >= 10:
            warnings.append(
                f"{label} controls more than 10% of supply"
            )

        elif share == 0:
            positives.append(
                f"{label} currently holds no token supply"
            )

    tx_count = history.get(
        "transactions",
        0
    )

    token_activity = history.get(
        "token_activity",
        0
    )

    if tx_count >= 30:
        warnings.append(
            f"{label} shows high recent wallet activity"
        )

    elif tx_count == 0:
        warnings.append(
            f"{label} history could not be verified"
        )

    if token_activity >= 10:
        warnings.append(
            f"{label} shows significant recent token activity"
        )

    return {
        "risk": (
            "HIGH"
            if any(
                "more than 30%"
                in w
                or "high recent"
                in w
                for w in warnings
            )
            else (
                "MEDIUM"
                if warnings
                else "LOW"
            )
        ),

        "warnings": warnings,

        "positives": positives,

        "share": share,

        "sol": sol,

        "history": history
    }


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    market,
    holders,
    authorities,
    creator_risk,
    top_holder_risk
):

    score = 0

    positives = []
    warnings = []

    mc = market.get(
        "market_cap"
    )

    age = market.get(
        "age_hours"
    )

    volume = market.get(
        "volume",
        0
    )

    txns = market.get(
        "total_txns",
        0
    )

    # --------------------------------------------------------
    # MARKET CAP
    # --------------------------------------------------------

    if mc is not None:

        if 1000 <= mc <= 4000:

            score += 25
            positives.append(
                "very early MC"
            )

        elif 4000 < mc <= 10000:

            score += 15
            positives.append(
                "early MC"
            )

    # --------------------------------------------------------
    # AGE
    # --------------------------------------------------------

    if age is not None:

        if age <= 0.5:

            score += 25
            positives.append(
                "under 30 minutes old"
            )

        elif age <= 1:

            score += 20
            positives.append(
                "under 1 hour old"
            )

        elif age <= 6:

            score += 10
            positives.append(
                "under 6 hours old"
            )

        elif age > 24:

            warnings.append(
                "older than 24 hours"
            )

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    if volume <= 500:

        score += 20
        positives.append(
            "very low volume"
        )

    elif volume <= 3000:

        score += 12
        positives.append(
            "low volume"
        )

    elif volume > 100000:

        warnings.append(
            "very high volume"
        )

    # --------------------------------------------------------
    # TRANSACTIONS
    # --------------------------------------------------------

    if txns <= 20:

        score += 15
        positives.append(
            "very low transaction count"
        )

    elif txns <= 60:

        score += 10
        positives.append(
            "low transaction count"
        )

    elif txns > 1500:

        warnings.append(
            "very high transaction count"
        )

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    liquidity = market.get(
        "liquidity"
    )

    if liquidity is None:

        warnings.append(
            "liquidity unavailable"
        )

    elif liquidity <= 0:

        warnings.append(
            "zero liquidity"
        )

    elif liquidity < 1000:

        warnings.append(
            "very low liquidity"
        )

    # --------------------------------------------------------
    # HOLDER CONCENTRATION
    # --------------------------------------------------------

    holder_count = holders.get(
        "holders"
    )

    top1 = holders.get(
        "top1_pct"
    )

    top10 = holders.get(
        "top10_pct"
    )

    if holder_count is not None:

        if holder_count < 20:

            score -= 10
            warnings.append(
                "very few holders"
            )

    if top1 is not None:

        if top1 >= 30:

            score -= 20
            warnings.append(
                "top holder concentration above 30%"
            )

        elif top1 >= 20:

            score -= 10
            warnings.append(
                "top holder concentration above 20%"
            )

        elif top1 >= 10:

            score -= 5
            warnings.append(
                "top holder concentration above 10%"
            )

    if top10 is not None:

        if top10 >= 60:

            score -= 20
            warnings.append(
                "top 10 concentration above 60%"
            )

        elif top10 >= 40:

            score -= 10
            warnings.append(
                "top 10 concentration above 40%"
            )

    # --------------------------------------------------------
    # AUTHORITIES
    # --------------------------------------------------------

    mint_status = authorities.get(
        "mint_authority_status"
    )

    freeze_status = authorities.get(
        "freeze_authority_status"
    )

    if mint_status == "REVOKED":

        score += 5
        positives.append(
            "mint authority revoked"
        )

    elif mint_status == "ACTIVE":

        score -= 10
        warnings.append(
            "mint authority still active"
        )

    else:

        warnings.append(
            "mint authority could not be verified"
        )

    if freeze_status == "REVOKED":

        score += 5
        positives.append(
            "freeze authority revoked"
        )

    elif freeze_status == "ACTIVE":

        score -= 10
        warnings.append(
            "freeze authority still active"
        )

    else:

        warnings.append(
            "freeze authority could not be verified"
        )

    # --------------------------------------------------------
    # WALLET RISK
    # --------------------------------------------------------

    warnings.extend(
        creator_risk.get(
            "warnings",
            []
        )
    )

    warnings.extend(
        top_holder_risk.get(
            "warnings",
            []
        )
    )

    positives.extend(
        creator_risk.get(
            "positives",
            []
        )
    )

    positives.extend(
        top_holder_risk.get(
            "positives",
            []
        )
    )

    return {
        "score": max(
            0,
            min(100, score)
        ),

        "warnings": warnings,

        "positives": positives
    }


# ============================================================
# VERDICT
# ============================================================

def get_verdict(
    score,
    market,
    warnings
):

    mc = market.get(
        "market_cap"
    )

    age = market.get(
        "age_hours"
    )

    txns = market.get(
        "total_txns",
        0
    )

    volume = market.get(
        "volume",
        0
    )

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
            "Usage: python research.py "
            "<chain> <contract>"
        )

        sys.exit(1)

    chain = sys.argv[1].lower()

    contract = sys.argv[2].strip()

    print()
    print(
        "🥷 MYSTERIOUS TOKEN CHECKER"
    )
    print(
        "============================"
    )

    print(
        f"⛓ Chain: {chain}"
    )

    print(
        f"📍 Contract: {contract}"
    )

    print()

    market = get_market_data(
        chain,
        contract
    )

    if not market:

        print(
            "❌ Could not retrieve market data."
        )

        sys.exit(1)

    print(
        f"🪙 {market['name']} "
        f"(${market['symbol']})"
    )

    print()

    # --------------------------------------------------------
    # HOLDERS
    # --------------------------------------------------------

    holders = get_holder_data(
        contract
    )

    # --------------------------------------------------------
    # SECURITY
    # --------------------------------------------------------

    security = get_token_security(
        contract
    )

    authorities = get_mint_authorities(
        contract
    )

    # --------------------------------------------------------
    # CREATOR
    # --------------------------------------------------------

    creation = get_creation_info(
        contract
    )

    creator = creation.get(
        "creator"
    )

    creator_token_raw = None

    if creator:

        creator_token_raw = (
            get_wallet_token_balance(
                creator,
                contract
            )
        )

    creator_risk = analyze_wallet_risk(
        creator,
        "Creator",
        creator_token_raw,
        authorities.get("supply"),
        authorities.get("decimals")
    )

    # --------------------------------------------------------
    # TOP HOLDER
    # --------------------------------------------------------

    top_holder = holders.get(
        "top_holder"
    )

    top_holder_balance = holders.get(
        "top_holder_balance"
    )

    top_holder_risk = analyze_wallet_risk(
        top_holder,
        "Top holder",
        top_holder_balance,
        authorities.get("supply"),
        authorities.get("decimals")
    )

    # --------------------------------------------------------
    # CREATOR / HOLDER RELATIONSHIP
    # --------------------------------------------------------

    relationship_warning = None

    if (
        creator
        and top_holder
        and creator == top_holder
    ):

        relationship_warning = (
            "creator is also the top holder"
        )

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    result = calculate_score(
        market,
        holders,
        authorities,
        creator_risk,
        top_holder_risk
    )

    if relationship_warning:

        result["warnings"].append(
            relationship_warning
        )

    score = result["score"]

    verdict = get_verdict(
        score,
        market,
        result["warnings"]
    )

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    print()

    print(
        f"🎯 Research Score: "
        f"{score}/100"
    )

    print(
        f"🚦 Verdict: {verdict}"
    )

    # --------------------------------------------------------
    # MARKET
    # --------------------------------------------------------

    print()

    print(
        f"💰 Market Cap: "
        f"{fmt_money(market.get('market_cap'))}"
    )

    print(
        f"💧 Liquidity: "
        f"{fmt_money(market.get('liquidity'))}"
    )

    age = market.get(
        "age_hours"
    )

    if age is None:

        age_text = "Unavailable"

    elif age < 1:

        age_text = (
            f"{age * 60:.0f} minutes"
        )

    else:

        age_text = (
            f"{age:.1f} hours"
        )

    print(
        f"⏱ Pair Age: {age_text}"
    )

    print(
        f"📊 24H Volume: "
        f"{fmt_money(market.get('volume'))}"
    )

    print(
        f"🟢 Buys: {market.get('buys')}"
    )

    print(
        f"🔴 Sells: {market.get('sells')}"
    )

    print(
        f"🔄 Total Txns: "
        f"{market.get('total_txns')}"
    )

    # --------------------------------------------------------
    # HOLDERS
    # --------------------------------------------------------

    print()
    print(
        "👥 HOLDER DISTRIBUTION"
    )

    print(
        f"👤 Holders: "
        f"{holders.get('holders')}"
    )

    print(
        f"🥇 Top 1 Holder: "
        f"{holders.get('top1_pct'):.1f}%"
        if holders.get("top1_pct") is not None
        else "🥇 Top 1 Holder: Unavailable"
    )

    print(
        f"🏆 Top 5 Holders: "
        f"{holders.get('top5_pct'):.1f}%"
        if holders.get("top5_pct") is not None
        else "🏆 Top 5 Holders: Unavailable"
    )

    print(
        f"📊 Top 10 Holders: "
        f"{holders.get('top10_pct'):.1f}%"
        if holders.get("top10_pct") is not None
        else "📊 Top 10 Holders: Unavailable"
    )

    if top_holder:

        print(
            f"🐋 Top Holder: "
            f"{top_holder}"
        )

    # --------------------------------------------------------
    # SECURITY
    # --------------------------------------------------------

    print()
    print(
        "🔐 TOKEN SECURITY"
    )

    if security.get("interface"):

        print(
            f"🧩 Interface: "
            f"{security.get('interface')}"
        )

    print(
        f"⚙️ Token Program: "
        f"{authorities.get('token_program') or 'Unknown'}"
    )

    print(
        f"🔢 Decimals: "
        f"{authorities.get('decimals')}"
    )

    print(
        f"📦 Supply: "
        f"{authorities.get('supply')}"
    )

    if (
        authorities.get(
            "mint_authority_status"
        ) == "REVOKED"
    ):

        print(
            "🪙 Mint Authority: "
            "🟢 REVOKED"
        )

    else:

        print(
            "🪙 Mint Authority: "
            "🔴 ACTIVE"
        )

    if (
        authorities.get(
            "freeze_authority_status"
        ) == "REVOKED"
    ):

        print(
            "❄️ Freeze Authority: "
            "🟢 REVOKED"
        )

    else:

        print(
            "❄️ Freeze Authority: "
            "🔴 ACTIVE"
        )

    # --------------------------------------------------------
    # CREATOR
    # --------------------------------------------------------

    print()
    print(
        "👤 CREATOR / DEPLOYER"
    )

    if creator:

        print(
            f"🧑 Likely Creator: "
            f"{creator}"
        )

        if creator_risk.get("sol") is not None:

            print(
                f"💰 Creator SOL: "
                f"{creator_risk['sol']:.4f} SOL"
            )

        if creator_risk.get("share") is not None:

            print(
                f"📊 Creator Supply: "
                f"{creator_risk['share']:.2f}%"
            )

        history = creator_risk.get(
            "history",
            {}
        )

        print(
            f"📜 Creator Recent Txns: "
            f"{history.get('transactions', 0)}"
        )

        print(
            f"🪙 Creator Token Activity: "
            f"{history.get('token_activity', 0)}"
        )

        print(
            f"🧠 Creator Risk: "
            f"{creator_risk.get('risk')}"
        )

    else:

        print(
            "🧑 Likely Creator: "
            "Unavailable"
        )

    if creation.get("signature"):

        print(
            f"🧾 Creation Tx: "
            f"{creation.get('signature')}"
        )

    # --------------------------------------------------------
    # TOP HOLDER
    # --------------------------------------------------------

    print()
    print(
        "🐋 TOP HOLDER ANALYSIS"
    )

    if top_holder:

        print(
            f"👛 Wallet: "
            f"{top_holder}"
        )

        if top_holder_risk.get("sol") is not None:

            print(
                f"💰 SOL Balance: "
                f"{top_holder_risk['sol']:.4f} SOL"
            )

        if top_holder_risk.get("share") is not None:

            print(
                f"📊 Supply Controlled: "
                f"{top_holder_risk['share']:.2f}%"
            )

        history = top_holder_risk.get(
            "history",
            {}
        )

        print(
            f"📜 Recent Txns: "
            f"{history.get('transactions', 0)}"
        )

        print(
            f"🪙 Token Activity: "
            f"{history.get('token_activity', 0)}"
        )

        print(
            f"🧠 Top Holder Risk: "
            f"{top_holder_risk.get('risk')}"
        )

    else:

        print(
            "👛 Top holder: Unavailable"
        )

    # --------------------------------------------------------
    # RELATIONSHIP
    # --------------------------------------------------------

    print()
    print(
        "🔗 WALLET RELATIONSHIP"
    )

    if relationship_warning:

        print(
            "🚩 Creator and top holder "
            "are the same wallet"
        )

    elif creator and top_holder:

        print(
            "🟢 Creator and top holder "
            "are different wallets"
        )

    else:

        print(
            "⚪ Relationship could not "
            "be determined"
        )

    # --------------------------------------------------------
    # POSITIVES
    # --------------------------------------------------------

    print()
    print(
        "✅ POSITIVE SIGNALS"
    )

    unique_positive = []

    for item in result["positives"]:

        if item not in unique_positive:
            unique_positive.append(item)

    if unique_positive:

        for item in unique_positive:
            print(f"• {item}")

    else:

        print("• None identified")

    # --------------------------------------------------------
    # WARNINGS
    # --------------------------------------------------------

    print()
    print(
        "🚩 WARNINGS"
    )

    unique_warnings = []

    for item in result["warnings"]:

        if item not in unique_warnings:
            unique_warnings.append(item)

    if unique_warnings:

        for item in unique_warnings:
            print(f"• {item}")

    else:

        print("• None identified")

    # --------------------------------------------------------
    # FINAL VERDICT
    # --------------------------------------------------------

    print()
    print(
        "🧠 RESEARCH VERDICT"
    )

    security_status = (
        "CLEAN"
        if (
            authorities.get(
                "mint_authority_status"
            ) == "REVOKED"
            and
            authorities.get(
                "freeze_authority_status"
            ) == "REVOKED"
        )
        else "RISK"
    )

    print(
        f"🔐 Security: {security_status}"
    )

    if holders.get("top1_pct") is not None:

        if holders["top1_pct"] >= 30:

            print(
                "👥 Holder Risk: HIGH"
            )

        elif holders["top1_pct"] >= 10:

            print(
                "👥 Holder Risk: MEDIUM"
            )

        else:

            print(
                "👥 Holder Risk: LOW"
            )

    print(
        f"👤 Creator Risk: "
        f"{creator_risk.get('risk')}"
    )

    print(
        f"🐋 Top Holder Risk: "
        f"{top_holder_risk.get('risk')}"
    )

    print(
        f"🚦 FINAL: {verdict}"
    )

    # --------------------------------------------------------
    # LINK
    # --------------------------------------------------------

    print()

    if market.get("url"):

        print(
            f"🔗 DexScreener: "
            f"{market.get('url')}"
        )

    print()
    print(
        "============================"
    )

    print(
        "🥷 RESEARCH COMPLETED"
    )

    print(
        "============================"
    )


if __name__ == "__main__":
    main()
