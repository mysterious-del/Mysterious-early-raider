import os
import sys
import time
import requests
from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

DEXSCREENER_BASE = "https://api.dexscreener.com"
HELIUS_RPC = "https://mainnet.helius-rpc.com/"

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")

CHAIN_MAP = {
    "solana": "solana",
}

REQUEST_TIMEOUT = 20


# ============================================================
# BASIC HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def iso_from_ms(ms):
    if not ms:
        return "Unknown"

    try:
        return datetime.fromtimestamp(
            ms / 1000,
            tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return "Unknown"


def short_address(address):
    if not address:
        return "Unknown"

    if len(address) <= 12:
        return address

    return f"{address[:6]}...{address[-6:]}"


def fmt_usd(value):
    if value is None:
        return "Unavailable"

    try:
        value = float(value)

        if value >= 1_000_000:
            return f"${value / 1_000_000:.2f}M"

        if value >= 1_000:
            return f"${value / 1_000:.2f}K"

        return f"${value:.2f}"

    except Exception:
        return "Unavailable"


def fmt_number(value):
    if value is None:
        return "Unavailable"

    try:
        value = float(value)

        if value >= 1_000_000_000:
            return f"{value / 1_000_000_000:.2f}B"

        if value >= 1_000_000:
            return f"{value / 1_000_000:.2f}M"

        if value >= 1_000:
            return f"{value / 1_000:.2f}K"

        return f"{value:.2f}"

    except Exception:
        return "Unavailable"


def safe_float(value):
    try:
        return float(value)
    except Exception:
        return 0.0


def request_json(method, url, **kwargs):
    try:
        response = requests.request(
            method,
            url,
            timeout=REQUEST_TIMEOUT,
            **kwargs
        )

        if response.status_code != 200:
            print(
                f"⚠️ HTTP {response.status_code}: "
                f"{url}"
            )
            return None

        return response.json()

    except Exception as e:
        print(f"⚠️ Request failed: {e}")
        return None


# ============================================================
# HELIUS RPC
# ============================================================

def helius_request(method, params):
    if not HELIUS_API_KEY:
        print("❌ HELIUS_API_KEY is missing.")
        return None

    url = f"{HELIUS_RPC}?api-key={HELIUS_API_KEY}"

    payload = {
        "jsonrpc": "2.0",
        "id": "mysterious-research",
        "method": method,
        "params": params,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:
            print(
                f"⚠️ Helius HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
            return None

        data = response.json()

        if "error" in data:
            print(
                f"⚠️ Helius RPC error: "
                f"{data['error']}"
            )
            return None

        return data.get("result")

    except Exception as e:
        print(f"⚠️ Helius request failed: {e}")
        return None


# ============================================================
# DEXSCREENER
# ============================================================

def get_market_data(chain, mint):
    print("\n📡 Checking market data...")

    url = (
        f"{DEXSCREENER_BASE}/token-pairs/"
        f"v1/{chain}/{mint}"
    )

    data = request_json("GET", url)

    if not data:
        return None

    if not isinstance(data, list):
        return None

    pairs = data

    if not pairs:
        return None

    # Prefer newest pair.
    pairs_sorted = sorted(
        pairs,
        key=lambda p: safe_float(p.get("pairCreatedAt")),
        reverse=True
    )

    pair = pairs_sorted[0]

    liquidity = pair.get("liquidity") or {}

    txns = pair.get("txns") or {}

    # Prefer h24.
    h24 = txns.get("h24") or {}

    buys = h24.get("buys")
    sells = h24.get("sells")

    if buys is None:
        buys = 0

    if sells is None:
        sells = 0

    total_txns = int(buys) + int(sells)

    volume = pair.get("volume") or {}

    volume_24h = volume.get("h24")

    market_cap = pair.get("marketCap")

    if market_cap is None:
        market_cap = pair.get("fdv")

    pair_created_at = pair.get("pairCreatedAt")

    age_hours = None

    if pair_created_at:
        created = datetime.fromtimestamp(
            pair_created_at / 1000,
            tz=timezone.utc
        )

        age_hours = (
            now_utc() - created
        ).total_seconds() / 3600

    pool_addresses = []

    for p in pairs:
        address = p.get("pairAddress")

        if address:
            pool_addresses.append(address)

    return {
        "pair": pair,
        "pairs": pairs,
        "pair_address": pair.get("pairAddress"),
        "pool_addresses": pool_addresses,
        "name": (
            pair.get("baseToken", {})
            .get("name")
            or "Unknown"
        ),
        "symbol": (
            pair.get("baseToken", {})
            .get("symbol")
            or "UNKNOWN"
        ),
        "market_cap": market_cap,
        "liquidity_usd": liquidity.get("usd"),
        "liquidity_base": liquidity.get("base"),
        "liquidity_quote": liquidity.get("quote"),
        "volume_24h": volume_24h,
        "buys": buys,
        "sells": sells,
        "total_txns": total_txns,
        "pair_created_at": pair_created_at,
        "age_hours": age_hours,
        "dex": pair.get("dexId"),
        "url": pair.get("url"),
    }


# ============================================================
# MINT INFORMATION
# ============================================================

def get_mint_info(mint):
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
            "decimals": None,
            "raw_supply": None,
            "human_supply": None,
            "mint_authority": None,
            "freeze_authority": None,
        }

    value = result.get("value")

    if not value:
        return {
            "decimals": None,
            "raw_supply": None,
            "human_supply": None,
            "mint_authority": None,
            "freeze_authority": None,
        }

    data = value.get("data") or {}

    parsed = data.get("parsed") or {}

    info = parsed.get("info") or {}

    decimals = (
        info.get("decimals")
    )

    raw_supply = info.get("supply")

    human_supply = None

    if raw_supply is not None and decimals is not None:
        try:
            human_supply = (
                int(raw_supply)
                / (10 ** int(decimals))
            )
        except Exception:
            human_supply = None

    return {
        "decimals": decimals,
        "raw_supply": raw_supply,
        "human_supply": human_supply,
        "mint_authority": info.get("mintAuthority"),
        "freeze_authority": info.get("freezeAuthority"),
    }


# ============================================================
# TOKEN ACCOUNTS / HOLDERS
# ============================================================

def get_holder_data(
    mint,
    excluded_addresses=None
):
    excluded = {
        str(x).lower()
        for x in (excluded_addresses or [])
        if x
    }

    all_accounts = []

    page = 1

    while page <= 10:
        result = helius_request(
            "getTokenAccounts",
            {
                "mint": mint,
                "page": page,
                "limit": 1000,
                "displayOptions": {},
            }
        )

        if not result:
            break

        accounts = result.get("token_accounts") or []

        if not accounts:
            break

        all_accounts.extend(accounts)

        if len(accounts) < 1000:
            break

        page += 1

    holders = {}

    pool_accounts = []

    for account in all_accounts:
        owner = account.get("owner")
        address = account.get("address")

        raw_amount = account.get("amount")

        if not owner:
            continue

        try:
            amount = float(raw_amount or 0)
        except Exception:
            amount = 0

        owner_lower = owner.lower()

        if owner_lower in excluded:
            pool_accounts.append({
                "token_account": address,
                "owner": owner,
                "amount": amount,
            })
            continue

        if amount <= 0:
            continue

        if owner not in holders:
            holders[owner] = 0

        holders[owner] += amount

    total_real_holder_amount = sum(
        holders.values()
    )

    total_pool_amount = sum(
        x["amount"]
        for x in pool_accounts
    )

    total_supply_raw = (
        total_real_holder_amount
        + total_pool_amount
    )

    sorted_holders = sorted(
        holders.items(),
        key=lambda x: x[1],
        reverse=True
    )

    def pct(amount):
        if total_supply_raw <= 0:
            return 0

        return (
            amount
            / total_supply_raw
            * 100
        )

    top1_pct = 0
    top5_pct = 0
    top10_pct = 0

    if sorted_holders:
        top1_pct = pct(
            sorted_holders[0][1]
        )

        top5_pct = pct(
            sum(
                amount
                for _, amount
                in sorted_holders[:5]
            )
        )

        top10_pct = pct(
            sum(
                amount
                for _, amount
                in sorted_holders[:10]
            )
        )

    pool_pct = pct(
        total_pool_amount
    )

    return {
        "holders": sorted_holders,
        "holder_count": len(sorted_holders),
        "top1_pct": top1_pct,
        "top5_pct": top5_pct,
        "top10_pct": top10_pct,
        "pool_balance_raw": total_pool_amount,
        "pool_pct": pool_pct,
        "pool_accounts": pool_accounts,
        "total_supply_raw": total_supply_raw,
        "all_accounts": all_accounts,
    }


# ============================================================
# POOL TOKEN ACCOUNT DISCOVERY
# ============================================================

def get_pool_token_accounts(
    mint,
    pool_addresses,
    holder_data
):
    pool_lower = {
        x.lower()
        for x in pool_addresses
        if x
    }

    results = []

    for account in holder_data.get(
        "all_accounts",
        []
    ):
        owner = account.get("owner")
        address = account.get("address")

        if not owner:
            continue

        if owner.lower() not in pool_lower:
            continue

        amount = safe_float(
            account.get("amount")
        )

        results.append({
            "token_account": address,
            "owner": owner,
            "amount_raw": amount,
        })

    return results


# ============================================================
# SOLANA TRANSACTION HISTORY
# ============================================================

def get_signatures_for_address(
    address,
    limit=100
):
    result = helius_request(
        "getSignaturesForAddress",
        [
            address,
            {
                "limit": limit
            }
        ]
    )

    if not result:
        return []

    return result


def get_transaction(signature):
    return helius_request(
        "getTransaction",
        [
            signature,
            {
                "encoding": "jsonParsed",
                "commitment": "confirmed",
                "maxSupportedTransactionVersion": 0,
            }
        ]
    )


# ============================================================
# TRANSACTION ACCOUNT MAP
# ============================================================

def get_account_keys(transaction):
    try:
        message = (
            transaction
            .get("transaction", {})
            .get("message", {})
        )

        keys = message.get("accountKeys") or []

        output = []

        for item in keys:
            if isinstance(item, str):
                output.append(item)

            elif isinstance(item, dict):
                pubkey = item.get("pubkey")

                if pubkey:
                    output.append(pubkey)

        return output

    except Exception:
        return []


# ============================================================
# TOKEN BALANCE DELTAS
# ============================================================

def token_balance_deltas(
    transaction,
    mint
):
    """
    Returns token-account balance changes.

    Each item contains:
      account
      owner
      before
      after
      delta
    """

    if not transaction:
        return []

    meta = transaction.get("meta") or {}

    pre = meta.get("preTokenBalances") or []
    post = meta.get("postTokenBalances") or []

    account_keys = get_account_keys(
        transaction
    )

    before_map = {}
    after_map = {}

    for item in pre:
        if item.get("mint") != mint:
            continue

        index = item.get("accountIndex")

        owner = item.get("owner")

        amount = safe_float(
            (
                item.get("uiTokenAmount") or {}
            ).get("uiAmountString")
        )

        before_map[index] = {
            "owner": owner,
            "amount": amount,
        }

    for item in post:
        if item.get("mint") != mint:
            continue

        index = item.get("accountIndex")

        owner = item.get("owner")

        amount = safe_float(
            (
                item.get("uiTokenAmount") or {}
            ).get("uiAmountString")
        )

        after_map[index] = {
            "owner": owner,
            "amount": amount,
        }

    indexes = (
        set(before_map.keys())
        | set(after_map.keys())
    )

    changes = []

    for index in indexes:
        before_item = before_map.get(
            index,
            {}
        )

        after_item = after_map.get(
            index,
            {}
        )

        before_amount = safe_float(
            before_item.get("amount")
        )

        after_amount = safe_float(
            after_item.get("amount")
        )

        delta = (
            after_amount
            - before_amount
        )

        if abs(delta) < 0.0000001:
            continue

        owner = (
            after_item.get("owner")
            or before_item.get("owner")
        )

        account = None

        if (
            index is not None
            and index < len(account_keys)
        ):
            account = account_keys[index]

        changes.append({
            "account": account,
            "owner": owner,
            "before": before_amount,
            "after": after_amount,
            "delta": delta,
        })

    return changes


# ============================================================
# POOL ORIGIN TRACING
# ============================================================

def trace_pool_origin(
    mint,
    pool_token_accounts,
    creator=None
):
    print(
        "\n🏊 Tracing pool token origin..."
    )

    if not pool_token_accounts:
        return {
            "status": "UNKNOWN",
            "pool_token_account": None,
            "pool_owner": None,
            "creation_signature": None,
            "origin": None,
            "origin_owner": None,
            "origin_amount": None,
            "creator_link": None,
            "events_checked": 0,
        }

    # Usually the largest pool token account
    # is the most useful one to trace.
    pool_token_accounts = sorted(
        pool_token_accounts,
        key=lambda x: x.get(
            "amount_raw",
            0
        ),
        reverse=True
    )

    target = pool_token_accounts[0]

    token_account = target.get(
        "token_account"
    )

    pool_owner = target.get(
        "owner"
    )

    if not token_account:
        return {
            "status": "UNKNOWN",
            "pool_token_account": None,
            "pool_owner": pool_owner,
            "creation_signature": None,
            "origin": None,
            "origin_owner": None,
            "origin_amount": None,
            "creator_link": None,
            "events_checked": 0,
        }

    signatures = get_signatures_for_address(
        token_account,
        limit=100
    )

    if not signatures:
        return {
            "status": "UNKNOWN",
            "pool_token_account": token_account,
            "pool_owner": pool_owner,
            "creation_signature": None,
            "origin": None,
            "origin_owner": None,
            "origin_amount": None,
            "creator_link": None,
            "events_checked": 0,
        }

    # RPC returns newest first.
    signatures = list(reversed(signatures))

    first_funding = None
    events_checked = 0

    for sig_info in signatures:
        signature = sig_info.get("signature")

        if not signature:
            continue

        transaction = get_transaction(
            signature
        )

        if not transaction:
            continue

        events_checked += 1

        changes = token_balance_deltas(
            transaction,
            mint
        )

        pool_change = None

        for change in changes:
            account = (
                change.get("account")
                or ""
            )

            owner = (
                change.get("owner")
                or ""
            )

            if (
                account.lower()
                == token_account.lower()
                or
                owner.lower()
                == pool_owner.lower()
            ):
                if change["delta"] > 0:
                    pool_change = change
                    break

        if not pool_change:
            continue

        # Find accounts that lost the tokens
        # during the same transaction.
        sources = [
            change
            for change in changes
            if change["delta"] < 0
            and (
                change.get("account") or ""
            ).lower()
            != token_account.lower()
        ]

        sources = sorted(
            sources,
            key=lambda x: abs(
                x.get("delta", 0)
            ),
            reverse=True
        )

        source = (
            sources[0]
            if sources
            else None
        )

        origin_owner = (
            source.get("owner")
            if source
            else None
        )

        origin_amount = (
            abs(source.get("delta"))
            if source
            else pool_change.get("delta")
        )

        creator_link = None

        if (
            creator
            and origin_owner
            and origin_owner.lower()
            == creator.lower()
        ):
            creator_link = True

        elif origin_owner:
            creator_link = False

        if source:
            origin = (
                "CREATOR"
                if creator_link is True
                else "WALLET"
            )

        else:
            origin = (
                "MINT / PROGRAM / UNKNOWN"
            )

        first_funding = {
            "signature": signature,
            "origin": origin,
            "origin_owner": origin_owner,
            "origin_amount": origin_amount,
            "creator_link": creator_link,
            "slot": transaction.get("slot"),
            "block_time": transaction.get(
                "blockTime"
            ),
        }

        break

    if not first_funding:
        return {
            "status": "NO_CLEAR_FUNDING",
            "pool_token_account": token_account,
            "pool_owner": pool_owner,
            "creation_signature": None,
            "origin": None,
            "origin_owner": None,
            "origin_amount": None,
            "creator_link": None,
            "events_checked": events_checked,
        }

    return {
        "status": "FOUND",
        "pool_token_account": token_account,
        "pool_owner": pool_owner,
        "creation_signature": first_funding.get(
            "signature"
        ),
        "origin": first_funding.get(
            "origin"
        ),
        "origin_owner": first_funding.get(
            "origin_owner"
        ),
        "origin_amount": first_funding.get(
            "origin_amount"
        ),
        "creator_link": first_funding.get(
            "creator_link"
        ),
        "block_time": first_funding.get(
            "block_time"
        ),
        "events_checked": events_checked,
    }


# ============================================================
# CREATOR / DEPLOYER
# ============================================================

def get_oldest_signature(address):
    signatures = get_signatures_for_address(
        address,
        limit=100
    )

    if not signatures:
        return None

    signatures = sorted(
        signatures,
        key=lambda x: (
            x.get("blockTime")
            or 0
        )
    )

    return signatures[0]


def get_creation_info(mint):
    """
    Attempts to identify the wallet that
    created/funded the mint account.
    """

    signatures = get_signatures_for_address(
        mint,
        limit=100
    )

    if not signatures:
        return {
            "creator": None,
            "signature": None,
            "block_time": None,
        }

    signatures = sorted(
        signatures,
        key=lambda x: (
            x.get("blockTime")
            or 0
        )
    )

    first = signatures[0]

    signature = first.get(
        "signature"
    )

    if not signature:
        return {
            "creator": None,
            "signature": None,
            "block_time": None,
        }

    transaction = get_transaction(
        signature
    )

    if not transaction:
        return {
            "creator": None,
            "signature": signature,
            "block_time": first.get(
                "blockTime"
            ),
        }

    message = (
        transaction
        .get("transaction", {})
        .get("message", {})
    )

    account_keys = (
        message.get("accountKeys")
        or []
    )

    signer = None

    for item in account_keys:
        if isinstance(item, dict):
            if item.get("signer"):
                signer = item.get(
                    "pubkey"
                )
                break

    return {
        "creator": signer,
        "signature": signature,
        "block_time": first.get(
            "blockTime"
        ),
    }


# ============================================================
# WALLET BALANCES
# ============================================================

def get_sol_balance(wallet):
    result = helius_request(
        "getBalance",
        [
            wallet
        ]
    )

    if not result:
        return None

    lamports = (
        result.get("value")
    )

    if lamports is None:
        return None

    return (
        float(lamports)
        / 1_000_000_000
    )


def get_wallet_token_balance(
    wallet,
    mint
):
    result = helius_request(
        "getTokenAccounts",
        {
            "owner": wallet,
            "mint": mint,
            "page": 1,
            "limit": 100,
            "displayOptions": {},
        }
    )

    if not result:
        return None

    accounts = (
        result.get("token_accounts")
        or []
    )

    total = 0

    for account in accounts:
        total += safe_float(
            account.get("amount")
        )

    return total


# ============================================================
# CREATOR HISTORY
# ============================================================

def get_wallet_history(wallet):
    if not wallet:
        return {
            "count": None,
            "token_activity": "Unknown",
        }

    # First try Helius enhanced history.
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
                    "status": "succeeded",
                },
            }
        ]
    )

    if isinstance(result, list):
        token_activity = False

        for tx in result:
            if (
                tx.get("tokenTransfers")
                or tx.get("balanceChanges")
            ):
                token_activity = True
                break

        return {
            "count": len(result),
            "token_activity": (
                "Detected"
                if token_activity
                else "None detected"
            ),
        }

    # Fallback.
    signatures = get_signatures_for_address(
        wallet,
        limit=50
    )

    if signatures:
        return {
            "count": len(signatures),
            "token_activity": "Unknown",
        }

    return {
        "count": 0,
        "token_activity": "Unknown",
    }


# ============================================================
# SCORING
# ============================================================

def calculate_score(
    market,
    holders,
    mint_info
):
    score = 0

    market_cap = safe_float(
        market.get("market_cap")
    )

    age_hours = market.get(
        "age_hours"
    )

    volume = safe_float(
        market.get("volume_24h")
    )

    txns = int(
        market.get("total_txns") or 0
    )

    # --------------------------------------------------------
    # Market cap
    # --------------------------------------------------------

    if 1000 <= market_cap <= 2000:
        score += 25

    elif 2000 < market_cap <= 4000:
        score += 20

    elif 4000 < market_cap <= 7000:
        score += 15

    elif 7000 < market_cap <= 10000:
        score += 10

    # --------------------------------------------------------
    # Age
    # --------------------------------------------------------

    if age_hours is not None:

        if age_hours <= 0.5:
            score += 25

        elif age_hours <= 1:
            score += 20

        elif age_hours <= 6:
            score += 10

    # --------------------------------------------------------
    # Volume
    # --------------------------------------------------------

    if volume <= 500:
        score += 20

    elif volume <= 3000:
        score += 12

    # --------------------------------------------------------
    # Transactions
    # --------------------------------------------------------

    if txns <= 20:
        score += 15

    elif txns <= 60:
        score += 10

    # --------------------------------------------------------
    # Holder concentration
    # --------------------------------------------------------

    holder_count = holders.get(
        "holder_count",
        0
    )

    top1 = holders.get(
        "top1_pct",
        0
    )

    top10 = holders.get(
        "top10_pct",
        0
    )

    if holder_count > 0:

        if holder_count < 20:
            score -= 10

        if 10 <= top1 < 20:
            score -= 5

        elif 20 <= top1 < 30:
            score -= 10

        elif top1 >= 30:
            score -= 20

        if 40 <= top10 < 60:
            score -= 10

        elif top10 >= 60:
            score -= 20

    # --------------------------------------------------------
    # Authorities
    # --------------------------------------------------------

    if mint_info.get(
        "mint_authority"
    ) is None:
        score += 5
    else:
        score -= 10

    if mint_info.get(
        "freeze_authority"
    ) is None:
        score += 5
    else:
        score -= 10

    return max(
        0,
        min(100, score)
    )


# ============================================================
# VERDICT
# ============================================================

def get_verdict(
    score,
    market
):
    age_hours = market.get(
        "age_hours"
    )

    txns = int(
        market.get("total_txns") or 0
    )

    market_cap = safe_float(
        market.get("market_cap")
    )

    # Don't call obviously hot projects WATCH.
    if (
        txns > 1500
        or safe_float(
            market.get("volume_24h")
        ) > 100000
        or market_cap > 10000
        or (
            age_hours is not None
            and age_hours > 24
        )
    ):
        return (
            "🔴 IGNORE"
        )

    if score >= 70:
        return (
            "🟢 WATCH"
        )

    if score >= 50:
        return (
            "🟡 CAUTION"
        )

    return (
        "🔴 IGNORE"
    )


# ============================================================
# REPORT HELPERS
# ============================================================

def print_market_report(market):
    print("\n💰 MARKET DATA")

    print(
        f"💰 Market Cap: "
        f"{fmt_usd(market.get('market_cap'))}"
    )

    liquidity = market.get(
        "liquidity_usd"
    )

    if liquidity is None:
        print(
            "💧 Liquidity: Unavailable"
        )
    else:
        print(
            f"💧 Liquidity: "
            f"{fmt_usd(liquidity)}"
        )

    age = market.get(
        "age_hours"
    )

    if age is None:
        print(
            "⏱ Pair Age: Unknown"
        )
    else:
        print(
            f"⏱ Pair Age: "
            f"{age:.1f} hours"
        )

    print(
        f"📊 24H Volume: "
        f"{fmt_usd(market.get('volume_24h'))}"
    )

    print(
        f"🟢 Buys: "
        f"{market.get('buys', 0)}"
    )

    print(
        f"🔴 Sells: "
        f"{market.get('sells', 0)}"
    )

    print(
        f"🔄 Total Txns: "
        f"{market.get('total_txns', 0)}"
    )


def print_holder_report(
    holders,
    mint_info
):
    print("\n👥 HOLDER DISTRIBUTION")

    print(
        f"👤 Real Holders: "
        f"{holders.get('holder_count', 0)}"
    )

    print(
        f"🏊 Pool/LP Share: "
        f"{holders.get('pool_pct', 0):.2f}%"
    )

    sorted_holders = holders.get(
        "holders",
        []
    )

    if not sorted_holders:
        print(
            "🥇 Top Real Holder: "
            "Unavailable"
        )

        print(
            "ℹ️ No non-pool holders "
            "detected yet."
        )

        return

    top_wallet, top_amount = (
        sorted_holders[0]
    )

    print(
        f"🥇 Top Real Holder: "
        f"{short_address(top_wallet)} "
        f"({holders.get('top1_pct', 0):.2f}%)"
    )

    print(
        f"🥈 Top 5 Real Holders: "
        f"{holders.get('top5_pct', 0):.2f}%"
    )

    print(
        f"🥉 Top 10 Real Holders: "
        f"{holders.get('top10_pct', 0):.2f}%"
    )

    if holders.get(
        "pool_pct",
        0
    ) > 0:

        print(
            "🏊 Pool supply is excluded "
            "from real-holder concentration."
        )


def print_security_report(
    mint_info
):
    print("\n🔐 TOKEN SECURITY")

    decimals = mint_info.get(
        "decimals"
    )

    supply = mint_info.get(
        "human_supply"
    )

    if supply is None:
        print(
            "🔢 Supply: Unavailable"
        )

    else:
        print(
            f"🔢 Supply: "
            f"{fmt_number(supply)}"
        )

    if decimals is not None:
        print(
            f"🔢 Decimals: {decimals}"
        )

    mint_authority = (
        mint_info.get(
            "mint_authority"
        )
    )

    freeze_authority = (
        mint_info.get(
            "freeze_authority"
        )
    )

    print(
        "🪙 Mint Authority: "
        + (
            "🟢 REVOKED"
            if mint_authority is None
            else "🔴 ACTIVE"
        )
    )

    print(
        "❄️ Freeze Authority: "
        + (
            "🟢 REVOKED"
            if freeze_authority is None
            else "🔴 ACTIVE"
        )
    )


def print_creator_report(
    creator,
    mint,
    mint_info
):
    print(
        "\n👤 CREATOR / DEPLOYER"
    )

    if not creator:
        print(
            "🧑 Likely Creator: "
            "Unavailable"
        )

        return {
            "creator": None,
            "creator_supply": 0,
            "creator_supply_pct": 0,
            "sol_balance": None,
            "history_count": None,
            "token_activity": "Unknown",
            "risk": "UNKNOWN",
        }

    creator_balance_raw = (
        get_wallet_token_balance(
            creator,
            mint
        )
    )

    total_supply_raw = safe_float(
        mint_info.get("raw_supply")
    )

    creator_pct = 0

    if (
        creator_balance_raw is not None
        and total_supply_raw > 0
    ):
        creator_pct = (
            creator_balance_raw
            / total_supply_raw
            * 100
        )

    sol_balance = get_sol_balance(
        creator
    )

    history = get_wallet_history(
        creator
    )

    risk = "LOW"

    if creator_pct >= 20:
        risk = "HIGH"

    elif creator_pct >= 5:
        risk = "MEDIUM"

    print(
        f"🧑 Likely Creator: "
        f"{creator}"
    )

    if sol_balance is None:
        print(
            "💰 Creator SOL: "
            "Unavailable"
        )
    else:
        print(
            f"💰 Creator SOL: "
            f"{sol_balance:.4f} SOL"
        )

    print(
        f"📊 Creator Supply: "
        f"{creator_pct:.2f}%"
    )

    print(
        f"📜 Creator Recent Txns: "
        f"{history.get('count', 'Unknown')}"
    )

    print(
        f"🪙 Creator Token Activity: "
        f"{history.get('token_activity', 'Unknown')}"
    )

    print(
        f"🧠 Creator Risk: "
        f"{risk}"
    )

    return {
        "creator": creator,
        "creator_supply": (
            creator_balance_raw or 0
        ),
        "creator_supply_pct": creator_pct,
        "sol_balance": sol_balance,
        "history_count": history.get(
            "count"
        ),
        "token_activity": history.get(
            "token_activity"
        ),
        "risk": risk,
    }


def print_pool_report(
    pool_origin,
    market
):
    print(
        "\n🏊 POOL / TOKEN ORIGIN"
    )

    pair_address = market.get(
        "pair_address"
    )

    if pair_address:
        print(
            f"🏊 Pool / Pair: "
            f"{pair_address}"
        )
    else:
        print(
            "🏊 Pool / Pair: "
            "Unavailable"
        )

    token_account = pool_origin.get(
        "pool_token_account"
    )

    if token_account:
        print(
            f"🪙 Pool Token Account: "
            f"{token_account}"
        )
    else:
        print(
            "🪙 Pool Token Account: "
            "Unavailable"
        )

    status = pool_origin.get(
        "status"
    )

    if status == "FOUND":

        print(
            f"📥 First Detected Origin: "
            f"{pool_origin.get('origin')}"
        )

        origin_owner = pool_origin.get(
            "origin_owner"
        )

        if origin_owner:
            print(
                f"👤 Source Wallet: "
                f"{origin_owner}"
            )
        else:
            print(
                "👤 Source Wallet: "
                "Unknown"
            )

        amount = pool_origin.get(
            "origin_amount"
        )

        if amount is not None:
            print(
                f"🪙 Initial Token Movement: "
                f"{fmt_number(amount)}"
            )

        creator_link = (
            pool_origin.get(
                "creator_link"
            )
        )

        if creator_link is True:
            print(
                "🔗 Creator → Pool Link: "
                "🟠 DETECTED"
            )

        elif creator_link is False:
            print(
                "🔗 Creator → Pool Link: "
                "🟢 NOT DIRECTLY DETECTED"
            )

        else:
            print(
                "🔗 Creator → Pool Link: "
                "Unknown"
            )

        signature = pool_origin.get(
            "creation_signature"
        )

        if signature:
            print(
                f"🧾 Origin Tx: "
                f"{signature}"
            )

        block_time = pool_origin.get(
            "block_time"
        )

        if block_time:
            print(
                f"⏱ Origin Time: "
                f"{iso_from_ms(block_time * 1000)}"
            )

    elif status == "NO_CLEAR_FUNDING":

        print(
            "ℹ️ Pool was found, but no clear "
            "initial funding movement was identified."
        )

    else:

        print(
            "ℹ️ Pool origin could not be determined."
        )

    print(
        f"🔍 Pool Events Checked: "
        f"{pool_origin.get('events_checked', 0)}"
    )


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

    chain = (
        sys.argv[1]
        .strip()
        .lower()
    )

    mint = (
        sys.argv[2]
        .strip()
    )

    # Safety: remove accidental prefix.
    if mint.lower().startswith(
        "contract:"
    ):
        mint = mint.split(
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
        f"📍 Contract: {mint}"
    )

    if chain != "solana":
        print(
            "❌ Current research checker "
            "supports Solana."
        )
        sys.exit(1)

    # --------------------------------------------------------
    # MARKET
    # --------------------------------------------------------

    market = get_market_data(
        chain,
        mint
    )

    if not market:
        print(
            "\n❌ Could not retrieve market data."
        )
        sys.exit(1)

    symbol = str(
        market.get("symbol")
        or "UNKNOWN"
    ).lstrip("$")

    name = market.get(
        "name"
    ) or "Unknown"

    print(
        f"\n🪙 {name} (${symbol})"
    )

    # --------------------------------------------------------
    # MINT
    # --------------------------------------------------------

    mint_info = get_mint_info(
        mint
    )

    # --------------------------------------------------------
    # HOLDERS
    # --------------------------------------------------------

    print(
        "\n🔎 Checking holder distribution..."
    )

    holder_data = get_holder_data(
        mint,
        excluded_addresses=market.get(
            "pool_addresses",
            []
        )
    )

    # --------------------------------------------------------
    # SECURITY
    # --------------------------------------------------------

    print(
        "🔐 Checking token security..."
    )

    # --------------------------------------------------------
    # CREATOR
    # --------------------------------------------------------

    print(
        "👤 Tracing likely creator / deployer..."
    )

    creation = get_creation_info(
        mint
    )

    creator = creation.get(
        "creator"
    )

    # --------------------------------------------------------
    # POOL TOKEN ACCOUNTS
    # --------------------------------------------------------

    pool_token_accounts = (
        get_pool_token_accounts(
            mint,
            market.get(
                "pool_addresses",
                []
            ),
            holder_data
        )
    )

    # --------------------------------------------------------
    # POOL ORIGIN
    # --------------------------------------------------------

    pool_origin = trace_pool_origin(
        mint,
        pool_token_accounts,
        creator=creator
    )

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = calculate_score(
        market,
        holder_data,
        mint_info
    )

    verdict = get_verdict(
        score,
        market
    )

    print(
        f"\n🎯 Research Score: "
        f"{score}/100"
    )

    print(
        f"🚦 Verdict: "
        f"{verdict}"
    )

    # --------------------------------------------------------
    # REPORTS
    # --------------------------------------------------------

    print_market_report(
        market
    )

    print_holder_report(
        holder_data,
        mint_info
    )

    print_security_report(
        mint_info
    )

    creator_data = print_creator_report(
        creator,
        mint,
        mint_info
    )

    print_pool_report(
        pool_origin,
        market
    )

    # --------------------------------------------------------
    # TOP HOLDER
    # --------------------------------------------------------

    print(
        "\n🐋 TOP REAL HOLDER ANALYSIS"
    )

    real_holders = holder_data.get(
        "holders",
        []
    )

    if not real_holders:

        print(
            "ℹ️ No real top holder to analyze."
        )

        if holder_data.get(
            "pool_pct",
            0
        ) > 0:
            print(
                "🏊 The detected supply is "
                "associated with the known pool/LP address."
            )

    else:

        top_wallet = (
            real_holders[0][0]
        )

        top_amount = (
            real_holders[0][1]
        )

        top_pct = holder_data.get(
            "top1_pct",
            0
        )

        print(
            f"🐋 Wallet: {top_wallet}"
        )

        print(
            f"📊 Supply: "
            f"{top_pct:.2f}%"
        )

        print(
            f"🪙 Balance: "
            f"{fmt_number(top_amount)}"
        )

    # --------------------------------------------------------
    # WALLET RELATIONSHIP
    # --------------------------------------------------------

    print(
        "\n🔗 WALLET RELATIONSHIP"
    )

    if not creator:
        print(
            "ℹ️ Creator relationship "
            "could not be determined."
        )

    elif not real_holders:
        print(
            "ℹ️ Creator/top-holder relationship "
            "cannot be determined."
        )

    else:
        top_wallet = real_holders[0][0]

        if (
            top_wallet.lower()
            == creator.lower()
        ):
            print(
                "🚩 Creator is currently "
                "the top real holder."
            )

        else:
            print(
                "🟢 Creator is not the "
                "top real holder."
            )

    # --------------------------------------------------------
    # POSITIVES
    # --------------------------------------------------------

    positives = []

    mc = safe_float(
        market.get("market_cap")
    )

    volume = safe_float(
        market.get("volume_24h")
    )

    txns = int(
        market.get("total_txns")
        or 0
    )

    age_hours = market.get(
        "age_hours"
    )

    if 1000 <= mc <= 2000:
        positives.append(
            "very early MC"
        )

    elif 2000 < mc <= 4000:
        positives.append(
            "early MC"
        )

    elif 4000 < mc <= 7000:
        positives.append(
            "$4K-$7K MC"
        )

    if (
        age_hours is not None
        and age_hours <= 1
    ):
        positives.append(
            "under 1 hour old"
        )

    elif (
        age_hours is not None
        and age_hours <= 6
    ):
        positives.append(
            "under 6 hours old"
        )

    if volume <= 500:
        positives.append(
            "very low volume"
        )

    elif volume <= 3000:
        positives.append(
            "low volume"
        )

    if txns <= 20:
        positives.append(
            "very low transaction count"
        )

    elif txns <= 60:
        positives.append(
            "low transaction count"
        )

    if mint_info.get(
        "mint_authority"
    ) is None:
        positives.append(
            "mint authority revoked"
        )

    if mint_info.get(
        "freeze_authority"
    ) is None:
        positives.append(
            "freeze authority revoked"
        )

    if (
        creator_data.get(
            "creator_supply_pct",
            0
        )
        == 0
    ):
        positives.append(
            "creator currently holds "
            "no token supply"
        )

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
            "• None identified"
        )

    # --------------------------------------------------------
    # WARNINGS
    # --------------------------------------------------------

    warnings = []

    liquidity = market.get(
        "liquidity_usd"
    )

    if liquidity is None:
        warnings.append(
            "liquidity unavailable"
        )

    elif safe_float(liquidity) < 1000:
        warnings.append(
            "very low liquidity"
        )

    if holder_data.get(
        "holder_count",
        0
    ) == 0:

        warnings.append(
            "no real holders yet; "
            "supply is currently in pool/LP"
        )

    if (
        pool_origin.get("status")
        == "FOUND"
    ):

        if pool_origin.get(
            "creator_link"
        ) is True:

            warnings.append(
                "creator directly linked "
                "to pool token funding"
            )

    if (
        creator_data.get(
            "creator_supply_pct",
            0
        ) >= 20
    ):
        warnings.append(
            "creator holds significant supply"
        )

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
            "• None identified"
        )

    # --------------------------------------------------------
    # FINAL RESEARCH VERDICT
    # --------------------------------------------------------

    security = "CLEAN"

    if (
        mint_info.get(
            "mint_authority"
        ) is not None
        or mint_info.get(
            "freeze_authority"
        ) is not None
    ):
        security = "REVIEW"

    holder_risk = "LOW"

    if holder_data.get(
        "holder_count",
        0
    ) == 0:

        holder_risk = (
            "POOL CONCENTRATED / "
            "NO REAL HOLDERS YET"
        )

    else:

        top1 = holder_data.get(
            "top1_pct",
            0
        )

        top10 = holder_data.get(
            "top10_pct",
            0
        )

        if top1 >= 30:
            holder_risk = "HIGH"

        elif top1 >= 20:
            holder_risk = "MEDIUM"

        elif top10 >= 60:
            holder_risk = "HIGH"

        else:
            holder_risk = "LOW"

    creator_risk = creator_data.get(
        "risk",
        "UNKNOWN"
    )

    if real_holders:
        top_holder_risk = "ANALYZED"
    else:
        top_holder_risk = (
            "N/A — pool excluded"
        )

    print(
        "\n🧠 RESEARCH VERDICT"
    )

    print(
        f"🔐 Security: {security}"
    )

    print(
        f"👥 Holder Risk: {holder_risk}"
    )

    print(
        f"👤 Creator Risk: {creator_risk}"
    )

    print(
        f"🐋 Top Holder Risk: "
        f"{top_holder_risk}"
    )

    print(
        f"🏊 Pool Origin: "
        f"{pool_origin.get('status', 'UNKNOWN')}"
    )

    print(
        f"🚦 FINAL: {verdict}"
    )

    print(
        "\n🥷 RESEARCH COMPLETED"
    )


if __name__ == "__main__":
    main()
