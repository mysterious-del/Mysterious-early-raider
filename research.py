import os
import sys
import time
import requests
from datetime import datetime, timezone


# ============================================================
# MYSTERIOUS TOKEN RESEARCH
# ============================================================
#
# Usage:
#
#   python research.py solana TOKEN_ADDRESS
#
# Required GitHub Secret:
#
#   HELIUS_API_KEY
#
# This checker looks at:
#
#   1. Market data
#   2. Holder distribution
#   3. Pool / LP concentration
#   4. Mint authority
#   5. Freeze authority
#   6. Creator / deployer
#   7. Creator token holdings
#   8. Top REAL holder
#   9. Wallet activity
#   10. Research score
#   11. Final verdict
#
# IMPORTANT:
#
# Known liquidity/pair addresses are NOT treated as whales.
#
# ============================================================


# ============================================================
# CONFIG
# ============================================================

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")

DEXSCREENER_BASE = "https://api.dexscreener.com"

HELIUS_RPC = (
    "https://mainnet.helius-rpc.com/"
    f"?api-key={HELIUS_API_KEY}"
)

REQUEST_TIMEOUT = 20

MAX_WALLET_TRANSACTIONS = 50


# ============================================================
# BASIC HELPERS
# ============================================================

def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def shorten_address(address, length=8):
    if not address:
        return "Unavailable"

    address = str(address)

    if len(address) <= length * 2:
        return address

    return (
        address[:length]
        + "..."
        + address[-length:]
    )


def format_money(value):
    if value is None:
        return "Unavailable"

    value = safe_float(value)

    if value <= 0:
        return "$0"

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.2f}K"

    return f"${value:.2f}"


def format_supply(value):
    if value is None:
        return "Unavailable"

    try:
        value = float(value)
    except (TypeError, ValueError):
        return "Unavailable"

    if value >= 1_000_000_000_000:
        return f"{value / 1_000_000_000_000:.2f}T"

    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"

    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"{value / 1_000:.2f}K"

    return f"{value:.2f}"


def now_utc():
    return datetime.now(timezone.utc)


# ============================================================
# HELIUS RPC
# ============================================================

def helius_request(method, params):
    if not HELIUS_API_KEY:
        raise RuntimeError(
            "HELIUS_API_KEY environment variable is missing."
        )

    payload = {
        "jsonrpc": "2.0",
        "id": "mysterious-research",
        "method": method,
        "params": params,
    }

    response = requests.post(
        HELIUS_RPC,
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    data = response.json()

    if "error" in data:
        raise RuntimeError(
            f"Helius RPC error: {data['error']}"
        )

    return data.get("result")


# ============================================================
# DEXSCREENER
# ============================================================

def get_market_data(chain, token_address):

    url = (
        f"{DEXSCREENER_BASE}/token-pairs/"
        f"v1/{chain}/{token_address}"
    )

    response = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    pairs = response.json()

    if not isinstance(pairs, list):
        pairs = []

    if not pairs:
        return None

    # --------------------------------------------------------
    # Collect ALL known pair addresses.
    #
    # These addresses are later excluded from whale analysis.
    # --------------------------------------------------------

    pool_addresses = set()

    for pair in pairs:

        pair_address = pair.get("pairAddress")

        if pair_address:
            pool_addresses.add(
                str(pair_address).lower()
            )

    # --------------------------------------------------------
    # Choose the most useful pair.
    #
    # Prefer the pair with the highest liquidity.
    # --------------------------------------------------------

    def liquidity_value(pair):
        liquidity = pair.get("liquidity") or {}

        return safe_float(
            liquidity.get("usd")
        )

    pairs_sorted = sorted(
        pairs,
        key=liquidity_value,
        reverse=True,
    )

    pair = pairs_sorted[0]

    base_token = pair.get("baseToken") or {}

    name = (
        base_token.get("name")
        or "Unknown Token"
    )

    symbol = (
        base_token.get("symbol")
        or "UNKNOWN"
    )

    symbol = str(symbol).lstrip("$").strip()

    if not symbol:
        symbol = "UNKNOWN"

    market_cap = pair.get("marketCap")

    if market_cap is None:
        market_cap = pair.get("fdv")

    liquidity = (
        pair.get("liquidity") or {}
    ).get("usd")

    volume = (
        pair.get("volume") or {}
    ).get("h24")

    txns = (
        pair.get("txns") or {}
    ).get("h24") or {}

    buys = safe_int(
        txns.get("buys")
    )

    sells = safe_int(
        txns.get("sells")
    )

    total_txns = buys + sells

    pair_created = pair.get(
        "pairCreatedAt"
    )

    pair_age_hours = None

    if pair_created:

        try:
            created_ms = int(pair_created)

            created_dt = datetime.fromtimestamp(
                created_ms / 1000,
                tz=timezone.utc,
            )

            age = now_utc() - created_dt

            pair_age_hours = (
                age.total_seconds() / 3600
            )

        except Exception:
            pair_age_hours = None

    return {
        "name": name,
        "symbol": symbol,
        "market_cap": safe_float(
            market_cap,
            None,
        ),
        "liquidity": (
            safe_float(liquidity, None)
            if liquidity is not None
            else None
        ),
        "volume": (
            safe_float(volume, None)
            if volume is not None
            else None
        ),
        "buys": buys,
        "sells": sells,
        "txns": total_txns,
        "pair_age_hours": pair_age_hours,
        "pair_address": pair.get(
            "pairAddress"
        ),
        "dex": pair.get("dexId"),
        "url": pair.get("url"),
        "pool_addresses": pool_addresses,
    }


# ============================================================
# TOKEN HOLDERS
# ============================================================

def get_holder_data(
    mint,
    excluded_addresses=None,
):
    """
    Gets token holders.

    IMPORTANT:
    Known pool/pair addresses are excluded from
    real-holder concentration.

    Example:

        Pool owns 100%
        Real holders own 0%

    This should NOT become:

        Top holder = 100%

    Instead:

        Pool/LP = 100%
        Real holders = 0
    """

    excluded_addresses = {
        str(address).lower()
        for address in (
            excluded_addresses or set()
        )
        if address
    }

    try:

        result = helius_request(
            "getTokenAccounts",
            {
                "page": 1,
                "limit": 1000,
                "displayOptions": {},
                "mint": mint,
            },
        )

        if not result:
            return {
                "holders": [],
                "holder_count": 0,
                "top1_pct": 0.0,
                "top5_pct": 0.0,
                "top10_pct": 0.0,
                "pool_balance": 0,
                "pool_pct": 0.0,
                "pool_addresses": [],
                "total_supply": 0,
            }

        accounts = result.get(
            "token_accounts",
            [],
        )

        holders_by_owner = {}

        pool_balance = 0

        pool_addresses = set()

        total_balance = 0

        for account in accounts:

            owner = (
                account.get("owner")
                or account.get("ownerAddress")
            )

            if not owner:
                continue

            amount = (
                account.get("amount")
            )

            if amount is None:

                token_amount = (
                    account.get("tokenAmount")
                    or {}
                )

                amount = token_amount.get(
                    "amount"
                )

            amount = safe_int(
                amount,
                0,
            )

            if amount <= 0:
                continue

            total_balance += amount

            owner_lower = (
                str(owner).lower()
            )

            # ------------------------------------------------
            # POOL / LP
            # ------------------------------------------------

            if owner_lower in excluded_addresses:

                pool_balance += amount

                pool_addresses.add(
                    owner
                )

                continue

            # ------------------------------------------------
            # REAL HOLDER
            #
            # Multiple token accounts can belong to the same
            # owner, so aggregate them.
            # ------------------------------------------------

            if owner not in holders_by_owner:
                holders_by_owner[owner] = 0

            holders_by_owner[owner] += amount

        # ----------------------------------------------------
        # Convert to sorted holder list
        # ----------------------------------------------------

        holders = []

        for owner, balance in (
            holders_by_owner.items()
        ):

            holders.append({
                "owner": owner,
                "balance": balance,
            })

        holders.sort(
            key=lambda item: item["balance"],
            reverse=True,
        )

        # ----------------------------------------------------
        # Real holder percentages
        # ----------------------------------------------------

        real_holder_balance = sum(
            holder["balance"]
            for holder in holders
        )

        if real_holder_balance > 0:

            top1_pct = (
                holders[0]["balance"]
                / real_holder_balance
            ) * 100

            top5_pct = (
                sum(
                    h["balance"]
                    for h in holders[:5]
                )
                / real_holder_balance
            ) * 100

            top10_pct = (
                sum(
                    h["balance"]
                    for h in holders[:10]
                )
                / real_holder_balance
            ) * 100

        else:

            top1_pct = 0.0
            top5_pct = 0.0
            top10_pct = 0.0

        pool_pct = 0.0

        if total_balance > 0:

            pool_pct = (
                pool_balance
                / total_balance
            ) * 100

        return {
            "holders": holders,
            "holder_count": len(holders),
            "top1_pct": top1_pct,
            "top5_pct": top5_pct,
            "top10_pct": top10_pct,
            "pool_balance": pool_balance,
            "pool_pct": pool_pct,
            "pool_addresses": list(
                pool_addresses
            ),
            "total_supply": total_balance,
        }

    except Exception as e:

        print(
            f"⚠️ Holder lookup failed: {e}"
        )

        return {
            "holders": [],
            "holder_count": 0,
            "top1_pct": 0.0,
            "top5_pct": 0.0,
            "top10_pct": 0.0,
            "pool_balance": 0,
            "pool_pct": 0.0,
            "pool_addresses": [],
            "total_supply": 0,
        }


# ============================================================
# MINT / FREEZE AUTHORITY
# ============================================================

def get_mint_authorities(mint):

    try:

        result = helius_request(
            "getAccountInfo",
            [
                mint,
                {
                    "encoding": "jsonParsed"
                },
            ],
        )

        if not result:
            return {
                "mint_authority": None,
                "freeze_authority": None,
            }

        value = result.get("value")

        if not value:
            return {
                "mint_authority": None,
                "freeze_authority": None,
            }

        data = value.get(
            "data"
        )

        if not isinstance(data, dict):
            return {
                "mint_authority": None,
                "freeze_authority": None,
            }

        parsed = data.get(
            "parsed"
        ) or {}

        info = parsed.get(
            "info"
        ) or {}

        return {
            "mint_authority": info.get(
                "mintAuthority"
            ),
            "freeze_authority": info.get(
                "freezeAuthority"
            ),
        }

    except Exception as e:

        print(
            f"⚠️ Authority lookup failed: {e}"
        )

        return {
            "mint_authority": None,
            "freeze_authority": None,
        }


# ============================================================
# CREATOR / DEPLOYER
# ============================================================

def get_oldest_signature(address):

    try:

        result = helius_request(
            "getSignaturesForAddress",
            [
                address,
                {
                    "limit": 1000
                },
            ],
        )

        if not result:
            return None

        return result[-1]

    except Exception as e:

        print(
            f"⚠️ Signature lookup failed: {e}"
        )

        return None


def get_transaction(signature):

    try:

        result = helius_request(
            "getTransaction",
            [
                signature,
                {
                    "encoding": "jsonParsed",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        )

        return result

    except Exception as e:

        print(
            f"⚠️ Transaction lookup failed: {e}"
        )

        return None


def get_creation_info(mint):

    """
    Attempts to identify the likely wallet that
    paid for / created the mint account.
    """

    try:

        oldest = get_oldest_signature(
            mint
        )

        if not oldest:
            return {
                "creator": None,
                "signature": None,
            }

        signature = oldest.get(
            "signature"
        )

        if not signature:
            return {
                "creator": None,
                "signature": None,
            }

        tx = get_transaction(
            signature
        )

        if not tx:
            return {
                "creator": None,
                "signature": signature,
            }

        transaction = (
            tx.get("transaction")
            or {}
        )

        message = (
            transaction.get("message")
            or {}
        )

        account_keys = (
            message.get("accountKeys")
            or []
        )

        # ----------------------------------------------------
        # The fee payer is normally the first account key.
        # ----------------------------------------------------

        if account_keys:

            first = account_keys[0]

            if isinstance(first, dict):

                creator = (
                    first.get("pubkey")
                    or first.get("address")
                )

            else:

                creator = first

            return {
                "creator": creator,
                "signature": signature,
            }

        return {
            "creator": None,
            "signature": signature,
        }

    except Exception as e:

        print(
            f"⚠️ Creator tracing failed: {e}"
        )

        return {
            "creator": None,
            "signature": None,
        }


# ============================================================
# WALLET BALANCES
# ============================================================

def get_sol_balance(wallet):

    try:

        result = helius_request(
            "getBalance",
            [wallet],
        )

        if not result:
            return 0.0

        lamports = safe_int(
            result.get("value"),
            0,
        )

        return lamports / 1_000_000_000

    except Exception:
        return 0.0


def get_wallet_token_balance(
    wallet,
    mint,
):

    try:

        result = helius_request(
            "getTokenAccounts",
            {
                "owner": wallet,
                "mint": mint,
                "page": 1,
                "limit": 100,
                "displayOptions": {},
            },
        )

        if not result:
            return 0

        accounts = result.get(
            "token_accounts",
            [],
        )

        total = 0

        for account in accounts:

            amount = account.get(
                "amount"
            )

            if amount is None:

                token_amount = (
                    account.get("tokenAmount")
                    or {}
                )

                amount = token_amount.get(
                    "amount"
                )

            total += safe_int(
                amount,
                0,
            )

        return total

    except Exception:
        return 0


# ============================================================
# WALLET HISTORY
# ============================================================

def get_wallet_history(wallet):

    """
    Gets recent wallet activity.

    This is deliberately treated as a supporting signal,
    not as proof that a wallet has no token activity.

    Helius' tokenAccounts filter exists specifically because
    token activity can occur through token accounts rather
    than directly referencing the wallet. 
    """

    try:

        result = helius_request(
            "getTransactionsForAddress",
            [
                wallet,
                {
                    "transactionDetails": "full",
                    "sortOrder": "desc",
                    "limit": MAX_WALLET_TRANSACTIONS,
                    "filters": {
                        "tokenAccounts": "balanceChanged",
                        "status": "succeeded",
                    },
                },
            ],
        )

        if isinstance(result, list):

            return result

        return []

    except Exception as e:

        print(
            f"⚠️ Helius wallet history unavailable: {e}"
        )

        # ----------------------------------------------------
        # Fallback to standard Solana RPC.
        # ----------------------------------------------------

        try:

            result = helius_request(
                "getSignaturesForAddress",
                [
                    wallet,
                    {
                        "limit": MAX_WALLET_TRANSACTIONS
                    },
                ],
            )

            return result or []

        except Exception:

            return []


# ============================================================
# WALLET RISK
# ============================================================

def analyze_wallet_risk(
    wallet,
    mint,
):

    if not wallet:

        return {
            "sol_balance": 0.0,
            "token_balance": 0,
            "recent_txns": 0,
            "token_activity": None,
            "risk": "UNKNOWN",
        }

    sol_balance = get_sol_balance(
        wallet
    )

    token_balance = get_wallet_token_balance(
        wallet,
        mint,
    )

    history = get_wallet_history(
        wallet
    )

    # --------------------------------------------------------
    # Do NOT claim zero token activity simply because a field
    # wasn't present in a Helius history response.
    # --------------------------------------------------------

    token_activity = None

    if history:

        detected = 0

        for tx in history:

            if not isinstance(tx, dict):
                continue

            transfers = (
                tx.get("tokenTransfers")
                or []
            )

            balance_changes = (
                tx.get("balanceChanges")
                or []
            )

            if transfers or balance_changes:
                detected += 1

        token_activity = detected

    # --------------------------------------------------------
    # Basic wallet-risk assessment
    # --------------------------------------------------------

    risk = "LOW"

    if token_balance > 0:

        # Holding tokens is not automatically bad.
        risk = "MEDIUM"

    if (
        token_balance > 0
        and sol_balance < 0.01
    ):

        risk = "MEDIUM"

    return {
        "sol_balance": sol_balance,
        "token_balance": token_balance,
        "recent_txns": len(history),
        "token_activity": token_activity,
        "risk": risk,
    }


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    market,
    holder_data,
    authorities,
    warnings,
):

    score = 0

    mc = market.get(
        "market_cap"
    )

    age = market.get(
        "pair_age_hours"
    )

    volume = market.get(
        "volume"
    )

    txns = market.get(
        "txns"
    )

    # --------------------------------------------------------
    # MARKET CAP
    # --------------------------------------------------------

    if mc is not None:

        if mc <= 4_000:

            score += 25

        elif mc <= 7_000:

            score += 20

        elif mc <= 10_000:

            score += 15

    # --------------------------------------------------------
    # AGE
    # --------------------------------------------------------

    if age is not None:

        if age <= 0.5:

            score += 25

        elif age <= 1:

            score += 20

        elif age <= 6:

            score += 10

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    if volume is not None:

        if volume <= 500:

            score += 20

        elif volume <= 3_000:

            score += 12

    # --------------------------------------------------------
    # TRANSACTIONS
    # --------------------------------------------------------

    if txns is not None:

        if txns <= 20:

            score += 15

        elif txns <= 60:

            score += 10

    # --------------------------------------------------------
    # HOLDER RISK
    #
    # IMPORTANT:
    # Pool/LP concentration is NOT included here.
    # Only REAL holders count.
    # --------------------------------------------------------

    real_holder_count = holder_data.get(
        "holder_count",
        0,
    )

    if real_holder_count > 0:

        if real_holder_count < 20:

            score -= 10

        top1_pct = holder_data.get(
            "top1_pct",
            0,
        )

        top10_pct = holder_data.get(
            "top10_pct",
            0,
        )

        if top1_pct >= 30:

            score -= 20

        elif top1_pct >= 20:

            score -= 10

        elif top1_pct >= 10:

            score -= 5

        if top10_pct >= 60:

            score -= 20

        elif top10_pct >= 40:

            score -= 10

    # --------------------------------------------------------
    # MINT AUTHORITY
    # --------------------------------------------------------

    if authorities.get(
        "mint_authority"
    ) is None:

        score += 5

    else:

        score -= 10

    # --------------------------------------------------------
    # FREEZE AUTHORITY
    # --------------------------------------------------------

    if authorities.get(
        "freeze_authority"
    ) is None:

        score += 5

    else:

        score -= 10

    # --------------------------------------------------------
    # Keep score between 0 and 100
    # --------------------------------------------------------

    score = max(
        0,
        min(
            100,
            score,
        ),
    )

    return score


# ============================================================
# VERDICT
# ============================================================

def get_verdict(
    score,
    market,
    holder_data,
):

    mc = market.get(
        "market_cap"
    )

    age = market.get(
        "pair_age_hours"
    )

    txns = market.get(
        "txns"
    )

    # --------------------------------------------------------
    # Hard caution conditions
    # --------------------------------------------------------

    if txns is not None and txns > 1500:

        return (
            "IGNORE",
            "🔴",
        )

    if age is not None and age > 24:

        return (
            "IGNORE",
            "🔴",
        )

    if mc is not None and mc > 10_000:

        return (
            "IGNORE",
            "🔴",
        )

    # --------------------------------------------------------
    # Serious real-holder concentration
    # --------------------------------------------------------

    if holder_data.get(
        "holder_count",
        0,
    ) > 0:

        if holder_data.get(
            "top1_pct",
            0,
        ) >= 50:

            return (
                "IGNORE",
                "🔴",
            )

        if holder_data.get(
            "top10_pct",
            0,
        ) >= 80:

            return (
                "IGNORE",
                "🔴",
            )

    # --------------------------------------------------------
    # Score verdict
    # --------------------------------------------------------

    if score >= 70:

        return (
            "WATCH",
            "🟢",
        )

    if score >= 50:

        return (
            "CAUTION",
            "🟡",
        )

    return (
        "IGNORE",
        "🔴",
    )


# ============================================================
# PRINT HOLDER REPORT
# ============================================================

def print_holder_report(
    holder_data,
):

    print(
        "\n👥 HOLDER DISTRIBUTION"
    )

    print(
        f"👤 Real Holders: "
        f"{holder_data['holder_count']}"
    )

    pool_pct = holder_data.get(
        "pool_pct",
        0,
    )

    if pool_pct > 0:

        print(
            f"🏊 Pool/LP Share: "
            f"{pool_pct:.2f}%"
        )

    if holder_data["holder_count"] > 0:

        print(
            f"🥇 Top 1 Real Holder: "
            f"{holder_data['top1_pct']:.2f}%"
        )

        print(
            f"🏆 Top 5 Real Holders: "
            f"{holder_data['top5_pct']:.2f}%"
        )

        print(
            f"📊 Top 10 Real Holders: "
            f"{holder_data['top10_pct']:.2f}%"
        )

        top_holder = (
            holder_data["holders"][0]
            ["owner"]
        )

        print(
            f"🐋 Top Real Holder: "
            f"{top_holder}"
        )

    else:

        print(
            "🥇 Top Real Holder: Unavailable"
        )

        print(
            "ℹ️ No non-pool holders detected yet."
        )


# ============================================================
# MAIN RESEARCH
# ============================================================

def main():

    # --------------------------------------------------------
    # Validate arguments
    # --------------------------------------------------------

    if len(sys.argv) < 3:

        print(
            "Usage:"
        )

        print(
            "python research.py "
            "solana TOKEN_ADDRESS"
        )

        sys.exit(1)

    chain = sys.argv[1].lower()

    contract = sys.argv[2].strip()

    # --------------------------------------------------------
    # Safety check for accidental workflow input mistake
    # --------------------------------------------------------

    if contract.lower().startswith(
        "contract:"
    ):

        contract = contract.split(
            ":",
            1
        )[1].strip()

    print(
        "🥷 MYSTERIOUS TOKEN CHECKER"
    )

    print(
        f"⛓ Chain: {chain}"
    )

    print(
        f"📍 Contract: {contract}"
    )

    print()

    # --------------------------------------------------------
    # MARKET
    # --------------------------------------------------------

    print(
        "📡 Checking market data..."
    )

    try:

        market = get_market_data(
            chain,
            contract,
        )

    except Exception as e:

        print(
            f"❌ Market lookup failed: {e}"
        )

        sys.exit(1)

    if not market:

        print(
            "❌ No DexScreener market found."
        )

        sys.exit(1)

    print(
        f"\n🪙 {market['name']} "
        f"(${market['symbol']})"
    )

    # --------------------------------------------------------
    # HOLDER DATA
    # --------------------------------------------------------

    print(
        "\n🔎 Checking holder distribution..."
    )

    holder_data = get_holder_data(
        contract,
        excluded_addresses=market.get(
            "pool_addresses",
            set(),
        ),
    )

    # --------------------------------------------------------
    # AUTHORITIES
    # --------------------------------------------------------

    print(
        "🔐 Checking token security..."
    )

    authorities = get_mint_authorities(
        contract
    )

    # --------------------------------------------------------
    # CREATOR
    # --------------------------------------------------------

    print(
        "👤 Tracing likely creator / deployer..."
    )

    creation = get_creation_info(
        contract
    )

    creator = creation.get(
        "creator"
    )

    creator_balance = 0

    creator_risk = "UNKNOWN"

    creator_history = []

    if creator:

        creator_balance = get_wallet_token_balance(
            creator,
            contract,
        )

        creator_history = get_wallet_history(
            creator
        )

        creator_risk_data = analyze_wallet_risk(
            creator,
            contract,
        )

        creator_risk = creator_risk_data.get(
            "risk",
            "UNKNOWN",
        )

    # --------------------------------------------------------
    # TOP REAL HOLDER
    #
    # NEVER analyze the pool as a whale.
    # --------------------------------------------------------

    top_holder = None

    top_holder_data = None

    if holder_data["holder_count"] > 0:

        top_holder = (
            holder_data["holders"][0]
            ["owner"]
        )

        top_holder_data = analyze_wallet_risk(
            top_holder,
            contract,
        )

    # --------------------------------------------------------
    # WALLET RELATIONSHIP
    # --------------------------------------------------------

    creator_and_holder_same = False

    if creator and top_holder:

        creator_and_holder_same = (
            creator.lower()
            == top_holder.lower()
        )

    # --------------------------------------------------------
    # WARNINGS / POSITIVES
    # --------------------------------------------------------

    warnings = []

    positives = []

    # Market positives

    mc = market.get(
        "market_cap"
    )

    age = market.get(
        "pair_age_hours"
    )

    volume = market.get(
        "volume"
    )

    txns = market.get(
        "txns"
    )

    if mc is not None:

        if mc <= 4_000:

            positives.append(
                "very early MC"
            )

        elif mc <= 7_000:

            positives.append(
                "early MC"
            )

        elif mc <= 10_000:

            positives.append(
                "sub-$10K MC"
            )

    if age is not None:

        if age < 1:

            positives.append(
                "under 1 hour old"
            )

        elif age < 6:

            positives.append(
                "under 6 hours old"
            )

    if volume is not None:

        if volume <= 500:

            positives.append(
                "very low volume"
            )

        elif volume <= 3_000:

            positives.append(
                "low volume"
            )

    if txns is not None:

        if txns <= 20:

            positives.append(
                "very low transaction count"
            )

        elif txns <= 60:

            positives.append(
                "low transaction count"
            )

    # --------------------------------------------------------
    # Liquidity
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
            "liquidity is zero"
        )

    # --------------------------------------------------------
    # Holder warnings
    # --------------------------------------------------------

    if holder_data["holder_count"] == 0:

        warnings.append(
            "no real holders yet; supply is currently in pool/LP"
        )

    elif holder_data["holder_count"] < 20:

        warnings.append(
            "very few real holders"
        )

    if holder_data["holder_count"] > 0:

        if holder_data["top1_pct"] >= 30:

            warnings.append(
                "top real holder controls "
                "more than 30% of supply"
            )

        elif holder_data["top1_pct"] >= 20:

            warnings.append(
                "top real holder controls "
                "more than 20% of supply"
            )

        elif holder_data["top1_pct"] >= 10:

            warnings.append(
                "top real holder controls "
                "more than 10% of supply"
            )

        if holder_data["top10_pct"] >= 60:

            warnings.append(
                "top 10 real holders control "
                "more than 60% of supply"
            )

        elif holder_data["top10_pct"] >= 40:

            warnings.append(
                "top 10 real holders control "
                "more than 40% of supply"
            )

    # --------------------------------------------------------
    # Security
    # --------------------------------------------------------

    mint_revoked = (
        authorities.get(
            "mint_authority"
        )
        is None
    )

    freeze_revoked = (
        authorities.get(
            "freeze_authority"
        )
        is None
    )

    if mint_revoked:

        positives.append(
            "mint authority revoked"
        )

    else:

        warnings.append(
            "mint authority still active"
        )

    if freeze_revoked:

        positives.append(
            "freeze authority revoked"
        )

    else:

        warnings.append(
            "freeze authority still active"
        )

    # --------------------------------------------------------
    # Creator balance
    # --------------------------------------------------------

    if creator_balance == 0:

        positives.append(
            "creator currently holds no token supply"
        )

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = calculate_score(
        market,
        holder_data,
        authorities,
        warnings,
    )

    verdict, verdict_icon = get_verdict(
        score,
        market,
        holder_data,
    )

    # ========================================================
    # OUTPUT
    # ========================================================

    print()

    print(
        f"🎯 Research Score: "
        f"{score}/100"
    )

    print(
        f"🚦 Verdict: "
        f"{verdict_icon} {verdict}"
    )

    print()

    # --------------------------------------------------------
    # MARKET DATA
    # --------------------------------------------------------

    print(
        "💰 MARKET DATA"
    )

    print(
        f"💰 Market Cap: "
        f"{format_money(market['market_cap'])}"
    )

    print(
        f"💧 Liquidity: "
        f"{format_money(market['liquidity'])}"
    )

    if market["pair_age_hours"] is not None:

        print(
            f"⏱ Pair Age: "
            f"{market['pair_age_hours']:.1f} hours"
        )

    else:

        print(
            "⏱ Pair Age: Unavailable"
        )

    print(
        f"📊 24H Volume: "
        f"{format_money(market['volume'])}"
    )

    print(
        f"🟢 Buys: "
        f"{market['buys']}"
    )

    print(
        f"🔴 Sells: "
        f"{market['sells']}"
    )

    print(
        f"🔄 Total Txns: "
        f"{market['txns']}"
    )

    # --------------------------------------------------------
    # HOLDER DATA
    # --------------------------------------------------------

    print_holder_report(
        holder_data
    )

    # --------------------------------------------------------
    # SECURITY
    # --------------------------------------------------------

    print(
        "\n🔐 TOKEN SECURITY"
    )

    print(
        "🧩 Interface: "
        "FungibleToken"
    )

    print(
        f"🔢 Supply Accounts: "
        f"{format_supply(holder_data['total_supply'])}"
    )

    if mint_revoked:

        print(
            "🪙 Mint Authority: "
            "🟢 REVOKED"
        )

    else:

        print(
            "🪙 Mint Authority: "
            "🔴 ACTIVE"
        )

    if freeze_revoked:

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

    print(
        "\n👤 CREATOR / DEPLOYER"
    )

    print(
        f"🧑 Likely Creator: "
        f"{creator or 'Unavailable'}"
    )

    if creator:

        creator_sol = get_sol_balance(
            creator
        )

        print(
            f"💰 Creator SOL: "
            f"{creator_sol:.4f} SOL"
        )

        if holder_data["total_supply"] > 0:

            creator_pct = (
                creator_balance
                / holder_data["total_supply"]
            ) * 100

        else:

            creator_pct = 0

        print(
            f"📊 Creator Supply: "
            f"{creator_pct:.2f}%"
        )

        print(
            f"📜 Creator Recent Txns: "
            f"{len(creator_history)}"
        )

        if creator_risk_data.get(
            "token_activity"
        ) is None:

            print(
                "🪙 Creator Token Activity: "
                "Unknown"
            )

        else:

            print(
                f"🪙 Creator Token Activity: "
                f"{creator_risk_data['token_activity']}"
            )

        print(
            f"🧠 Creator Risk: "
            f"{creator_risk}"
        )

    print(
        f"🧾 Creation Tx: "
        f"{creation.get('signature') or 'Unavailable'}"
    )

    # --------------------------------------------------------
    # TOP REAL HOLDER
    # --------------------------------------------------------

    print(
        "\n🐋 TOP REAL HOLDER ANALYSIS"
    )

    if top_holder and top_holder_data:

        print(
            f"👛 Wallet: "
            f"{top_holder}"
        )

        print(
            f"💰 SOL Balance: "
            f"{top_holder_data['sol_balance']:.4f} SOL"
        )

        print(
            f"📊 Supply Controlled: "
            f"{holder_data['top1_pct']:.2f}%"
        )

        print(
            f"📜 Recent Txns: "
            f"{top_holder_data['recent_txns']}"
        )

        if top_holder_data.get(
            "token_activity"
        ) is None:

            print(
                "🪙 Token Activity: Unknown"
            )

        else:

            print(
                f"🪙 Token Activity: "
                f"{top_holder_data['token_activity']}"
            )

        print(
            f"🧠 Top Holder Risk: "
            f"{top_holder_data['risk']}"
        )

    else:

        print(
            "ℹ️ No real top holder to analyze."
        )

        print(
            "🏊 The detected supply is associated "
            "with the known pool/LP address."
        )

    # --------------------------------------------------------
    # WALLET RELATIONSHIP
    # --------------------------------------------------------

    print(
        "\n🔗 WALLET RELATIONSHIP"
    )

    if creator and top_holder:

        if creator_and_holder_same:

            print(
                "🔴 Creator and top real holder "
                "are the SAME wallet"
            )

        else:

            print(
                "🟢 Creator and top real holder "
                "are different wallets"
            )

    else:

        print(
            "ℹ️ Creator/top-holder relationship "
            "cannot be determined."
        )

    # --------------------------------------------------------
    # POSITIVES
    # --------------------------------------------------------

    print(
        "\n✅ POSITIVE SIGNALS"
    )

    if positives:

        for item in positives:

            print(
                f"• {item}"
            )

    else:

        print(
            "• None detected"
        )

    # --------------------------------------------------------
    # WARNINGS
    # --------------------------------------------------------

    print(
        "\n🚩 WARNINGS"
    )

    if warnings:

        for item in warnings:

            print(
                f"• {item}"
            )

    else:

        print(
            "• None detected"
        )

    # --------------------------------------------------------
    # FINAL VERDICT
    # --------------------------------------------------------

    print(
        "\n🧠 RESEARCH VERDICT"
    )

    security_status = (
        "CLEAN"
        if mint_revoked and freeze_revoked
        else "CAUTION"
    )

    print(
        f"🔐 Security: "
        f"{security_status}"
    )

    if holder_data["holder_count"] == 0:

        holder_status = (
            "POOL CONCENTRATED / "
            "NO REAL HOLDERS YET"
        )

    elif (
        holder_data["top1_pct"] >= 30
        or holder_data["top10_pct"] >= 60
    ):

        holder_status = "HIGH"

    else:

        holder_status = "NORMAL"

    print(
        f"👥 Holder Risk: "
        f"{holder_status}"
    )

    print(
        f"👤 Creator Risk: "
        f"{creator_risk}"
    )

    if top_holder_data:

        print(
            f"🐋 Top Holder Risk: "
            f"{top_holder_data['risk']}"
        )

    else:

        print(
            "🐋 Top Holder Risk: "
            "N/A — pool excluded"
        )

    print(
        f"🚦 FINAL: "
        f"{verdict_icon} {verdict}"
    )

    print()

    print(
        "🥷 RESEARCH COMPLETED"
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
