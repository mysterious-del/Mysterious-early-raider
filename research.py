import os
import sys
import requests
from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

DEXSCREENER_BASE = "https://api.dexscreener.com"
HELIUS_RPC = "https://mainnet.helius-rpc.com/"

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY")

REQUEST_TIMEOUT = 20

SUPPORTED_CHAINS = {
    "solana": "solana",
}


# ============================================================
# BASIC HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def safe_float(value):
    try:
        return float(value)
    except Exception:
        return 0.0


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

        if value >= 1:
            return f"{value:.2f}"

        return f"{value:.6f}"

    except Exception:
        return "Unavailable"


def iso_from_seconds(seconds):
    if not seconds:
        return "Unknown"

    try:
        return datetime.fromtimestamp(
            seconds,
            tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return "Unknown"


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
        print(
            f"⚠️ Request failed: {e}"
        )
        return None


# ============================================================
# HELIUS RPC
# ============================================================

def helius_request(method, params):
    if not HELIUS_API_KEY:
        print(
            "❌ HELIUS_API_KEY is missing."
        )
        return None

    url = (
        f"{HELIUS_RPC}"
        f"?api-key={HELIUS_API_KEY}"
    )

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
                f"⚠️ Helius HTTP "
                f"{response.status_code}"
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
        print(
            f"⚠️ Helius request failed: "
            f"{e}"
        )
        return None

def get_market_data(chain, mint):
    print("\n📡 Checking market data...")

    dex_pairs = []
    gecko_pools = []

    # --------------------------------------------------------
    # 1. PRIMARY SOURCE: DEXSCREENER
    # --------------------------------------------------------
    dex_url = (
        f"{DEXSCREENER_BASE}"
        f"/token-pairs/v1/{chain}/{mint}"
    )

    data = request_json("GET", dex_url)

    if isinstance(data, list):
        dex_pairs = [
            p for p in data
            if isinstance(p, dict)
        ]

    print(f"🔎 DexScreener pairs discovered: {len(dex_pairs)}")

    # --------------------------------------------------------
    # 2. FALLBACK SOURCE: GECKOTERMINAL
    # --------------------------------------------------------
    gecko_liquidity = None
    gecko_volume = None
    gecko_market_cap = None
    gecko_fdv = None
    gecko_pool_address = None
    gecko_pool_created_at = None
    gecko_name = None
    gecko_symbol = None
    gecko_dex = None
    gecko_url = None

    if chain.lower() in ("solana", "sol"):
        network = "solana"
    else:
        network = chain.lower()

    gecko_url_api = (
        "https://api.geckoterminal.com/api/v2/"
        f"networks/{network}/tokens/{mint}/pools?page=1"
    )

    try:
        response = requests.get(
            gecko_url_api,
            headers={
                "Accept": "application/json;version=20230302"
            },
            timeout=12,
        )

        if response.status_code == 200:
            gecko_json = response.json()

            if isinstance(gecko_json, dict):
                items = gecko_json.get("data", [])

                if isinstance(items, list):
                    gecko_pools = [
                        item for item in items
                        if isinstance(item, dict)
                    ]

        elif response.status_code == 429:
            print("⚠️ GeckoTerminal rate limit reached.")

        else:
            print(
                "ℹ️ GeckoTerminal response: "
                f"HTTP {response.status_code}"
            )

    except (requests.RequestException, ValueError) as exc:
        print(f"ℹ️ GeckoTerminal unavailable: {exc}")

    # Rank pools by reported liquidity, then 24h volume.
    def gecko_rank(pool):
        attrs = pool.get("attributes") or {}

        reserve = safe_float(
            attrs.get("reserve_in_usd")
        )

        volume_data = attrs.get("volume_usd") or {}
        volume_24h = safe_float(
            volume_data.get("h24")
        )

        return (
            reserve is not None,
            reserve or 0,
            volume_24h or 0,
        )

    gecko_pools.sort(
        key=gecko_rank,
        reverse=True,
    )

    if gecko_pools:
        best_pool = gecko_pools[0]
        attrs = best_pool.get("attributes") or {}

        raw_reserve = attrs.get("reserve_in_usd")

        if raw_reserve is not None:
            try:
                gecko_liquidity = float(raw_reserve)
            except (TypeError, ValueError):
                pass

        volume_data = attrs.get("volume_usd") or {}
        raw_volume = volume_data.get("h24")

        if raw_volume is not None:
            try:
                gecko_volume = float(raw_volume)
            except (TypeError, ValueError):
                pass

        gecko_market_cap = attrs.get("market_cap_usd")
        gecko_fdv = attrs.get("fdv_usd")
        gecko_pool_address = attrs.get("address")
        gecko_pool_created_at = attrs.get("pool_created_at")
        gecko_name = attrs.get("name")
        gecko_dex = attrs.get("dex_id")

        if gecko_pool_address:
            gecko_url = (
                "https://www.geckoterminal.com/"
                f"{network}/pools/{gecko_pool_address}"
            )

        print(
            "🦎 GeckoTerminal pools discovered: "
            f"{len(gecko_pools)}"
        )

    else:
        print("ℹ️ GeckoTerminal returned no pools.")

    # --------------------------------------------------------
    # 3. SELECT THE BEST DEXSCREENER PAIR
    # --------------------------------------------------------
    def dex_rank(pair):
        liquidity = pair.get("liquidity") or {}
        volume = pair.get("volume") or {}

        raw_liquidity = liquidity.get("usd")
        raw_volume = volume.get("h24")

        created = pair.get("pairCreatedAt") or 0

        return (
            raw_liquidity is not None,
            safe_float(raw_liquidity) or 0,
            safe_float(raw_volume) or 0,
            safe_float(created) or 0,
        )

    dex_pairs.sort(
        key=dex_rank,
        reverse=True,
    )

    pair = dex_pairs[0] if dex_pairs else {}

    liquidity_data = pair.get("liquidity") or {}
    raw_dex_liquidity = liquidity_data.get("usd")

    dex_liquidity = None

    if raw_dex_liquidity is not None:
        try:
            dex_liquidity = float(raw_dex_liquidity)
        except (TypeError, ValueError):
            pass

    # Prefer DexScreener liquidity when reported.
    # Otherwise use GeckoTerminal's pool reserve.
    liquidity_usd = (
        dex_liquidity
        if dex_liquidity is not None
        else gecko_liquidity
    )

    volume_data = pair.get("volume") or {}
    raw_dex_volume = volume_data.get("h24")

    volume_24h = (
        raw_dex_volume
        if raw_dex_volume is not None
        else gecko_volume
    )

    txns = pair.get("txns") or {}
    h24 = txns.get("h24") or {}

    buys = int(h24.get("buys") or 0)
    sells = int(h24.get("sells") or 0)

    # Market cap and FDV are different metrics.
    market_cap = pair.get("marketCap")

    if market_cap is None:
        market_cap = gecko_market_cap

    if market_cap is None:
        market_cap = pair.get("fdv")

    if market_cap is None:
        market_cap = gecko_fdv

    pair_created_at = pair.get("pairCreatedAt")
    age_hours = None

    if pair_created_at is not None:
        try:
            created = datetime.fromtimestamp(
                float(pair_created_at) / 1000,
                tz=timezone.utc,
            )

            age_hours = (
                now_utc() - created
            ).total_seconds() / 3600

            if age_hours < 0:
                age_hours = None

        except (TypeError, ValueError, OSError):
            pass

    # If DexScreener has no creation timestamp, try GeckoTerminal.
    if age_hours is None and gecko_pool_created_at:
        try:
            created_text = str(gecko_pool_created_at)

            if created_text.endswith("Z"):
                created_text = (
                    created_text[:-1] + "+00:00"
                )

            created = datetime.fromisoformat(
                created_text
            )

            if created.tzinfo is None:
                created = created.replace(
                    tzinfo=timezone.utc
                )

            age_hours = (
                now_utc() - created.astimezone(
                    timezone.utc
                )
            ).total_seconds() / 3600

            if age_hours < 0:
                age_hours = None

        except (TypeError, ValueError, OSError):
            pass

    pool_addresses = {
        str(p.get("pairAddress"))
        for p in dex_pairs
        if p.get("pairAddress")
    }

    if gecko_pool_address:
        pool_addresses.add(str(gecko_pool_address))

    # Prefer token metadata from DexScreener when available.
    base_token = pair.get("baseToken") or {}

    name = (
        base_token.get("name")
        or gecko_name
        or "Unknown"
    )

    symbol = (
        base_token.get("symbol")
        or "UNKNOWN"
    )

    pair_address = (
        pair.get("pairAddress")
        or gecko_pool_address
    )

    dex_name = (
        pair.get("dexId")
        or gecko_dex
    )

    pair_url = (
        pair.get("url")
        or gecko_url
    )

    if liquidity_usd is None:
        print("⚠️ Liquidity could not be verified.")
    else:
        source = (
            "DexScreener"
            if dex_liquidity is not None
            else "GeckoTerminal"
        )

        print(
            f"💧 Liquidity: {fmt_usd(liquidity_usd)} "
            f"(source: {source})"
        )

    return {
        "pair": pair,
        "pairs": dex_pairs,
        "pair_address": pair_address,
        "pool_addresses": list(pool_addresses),
        "name": name,
        "symbol": symbol,
        "market_cap": market_cap,
        "liquidity_usd": liquidity_usd,
        "volume_24h": volume_24h,
        "buys": buys,
        "sells": sells,
        "total_txns": buys + sells,
        "pair_created_at": pair_created_at,
        "age_hours": age_hours,
        "dex": dex_name,
        "url": pair_url,
        "liquidity_source": (
            "DexScreener"
            if dex_liquidity is not None
            else (
                "GeckoTerminal"
                if gecko_liquidity is not None
                else None
            )
        ),
    }



# ============================================================
# MINT INFO
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

    value = result.get(
        "value"
    )

    if not value:
        return {
            "decimals": None,
            "raw_supply": None,
            "human_supply": None,
            "mint_authority": None,
            "freeze_authority": None,
        }

    data = value.get(
        "data"
    ) or {}

    parsed = data.get(
        "parsed"
    ) or {}

    info = parsed.get(
        "info"
    ) or {}

    decimals = info.get(
        "decimals"
    )

    raw_supply = info.get(
        "supply"
    )

    human_supply = None

    if (
        raw_supply is not None
        and decimals is not None
    ):
        try:
            human_supply = (
                int(raw_supply)
                / (
                    10 ** int(decimals)
                )
            )
        except Exception:
            human_supply = None

    return {
        "decimals": decimals,
        "raw_supply": raw_supply,
        "human_supply": human_supply,
        "mint_authority": (
            info.get("mintAuthority")
        ),
        "freeze_authority": (
            info.get("freezeAuthority")
        ),
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
        for x in (
            excluded_addresses or []
        )
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

        accounts = (
            result.get(
                "token_accounts"
            )
            or []
        )
        print(
            f"🔎 Helius holder page {page}: "
            f"{len(accounts)} accounts"
        )

        if page == 1 and result:
            print(
                "🔎 Helius response fields: "
                f"{list(result.keys())}"
            )
        if not accounts:
            break

        all_accounts.extend(
            accounts
        )

        if len(accounts) < 1000:
            break

        page += 1

    holders = {}
    pool_accounts = []
    print(
        f"🔎 Token accounts fetched: "
        f"{len(all_accounts)}"
    )

    print(
        f"🏊 Excluded pool addresses: "
        f"{len(excluded)}"
    )
    for account in all_accounts:

        owner = account.get(
            "owner"
        )

        address = account.get(
            "address"
        )

        amount = safe_float(
            account.get("amount")
        )

        if not owner:
            continue

        if owner.lower() in excluded:

            pool_accounts.append({
                "token_account": address,
                "owner": owner,
                "amount": amount,
            })

            continue

        if amount <= 0:
            continue

        holders[owner] = (
            holders.get(owner, 0)
            + amount
        )

    total_real = sum(
        holders.values()
    )

    total_pool = sum(
        x["amount"]
        for x in pool_accounts
    )

    total_supply = (
        total_real
        + total_pool
    )

    sorted_holders = sorted(
        holders.items(),
        key=lambda x: x[1],
        reverse=True
    )

    def pct(amount):

        if total_supply <= 0:
            return 0

        return (
            amount
            / total_supply
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
                x[1]
                for x in sorted_holders[:5]
            )
        )

        top10_pct = pct(
            sum(
                x[1]
                for x in sorted_holders[:10]
            )
        )

    return {
        "holders": sorted_holders,
        "holder_count": len(
            sorted_holders
        ),
        "top1_pct": top1_pct,
        "top5_pct": top5_pct,
        "top10_pct": top10_pct,
        "pool_balance_raw": total_pool,
        "pool_pct": pct(
            total_pool
        ),
        "pool_accounts": pool_accounts,
        "total_supply_raw": total_supply,
        "all_accounts": all_accounts,
    }


# ============================================================
# POOL TOKEN ACCOUNTS
# ============================================================

def get_pool_token_accounts(
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

        owner = account.get(
            "owner"
        )

        address = account.get(
            "address"
        )

        if not owner:
            continue

        if owner.lower() not in pool_lower:
            continue

        results.append({
            "token_account": address,
            "owner": owner,
            "amount_raw": safe_float(
                account.get("amount")
            ),
        })

    return results


# ============================================================
# HELIUS TRANSFER API
# ============================================================

def get_pool_transfers(
    token_account,
    mint
):

    if not token_account:
        return {
            "available": False,
            "transfers": [],
            "error": "No token account"
        }

    params = {
        "mint": mint,
        "direction": "in",
        "sortOrder": "asc",
        "limit": 100,
    }

    result = helius_request(
        "getTransfersByAddress",
        [
            token_account,
            params
        ]
    )

    if result is None:
        return {
            "available": False,
            "transfers": [],
            "error": (
                "getTransfersByAddress "
                "unavailable"
            )
        }

    transfers = []

    if isinstance(result, dict):

        transfers = (
            result.get("data")
            or []
        )

    elif isinstance(result, list):

        transfers = result

    return {
        "available": True,
        "transfers": transfers,
        "error": None,
    }


# ============================================================
# TRANSFER FIELD HELPERS
# ============================================================

def transfer_value(
    transfer,
    *names
):

    for name in names:

        value = transfer.get(
            name
        )

        if value is not None:
            return value

    return None


def classify_origin(
    source,
    creator,
    transfer
):

    if not source:

        # Minting/burning events may not
        # have a normal sender.
        transfer_type = str(
            transfer_value(
                transfer,
                "type",
                "transactionType",
                "category"
            )
            or ""
        ).lower()

        if (
            "mint" in transfer_type
            or "create" in transfer_type
        ):
            return (
                "MINT / LAUNCH PROGRAM"
            )

        return (
            "MINT / PROGRAM / UNKNOWN"
        )

    if creator:

        if (
            source.lower()
            == creator.lower()
        ):
            return "CREATOR"

    return "OTHER WALLET"


# ============================================================
# TRANSFER-LEVEL POOL ORIGIN
# ============================================================

def trace_pool_origin(
    mint,
    pool_token_accounts,
    creator
):

    print(
        "\n🏊 Tracing pool token origin..."
    )

    if not pool_token_accounts:

        return {
            "status": "UNKNOWN",
            "method": "none",
            "pool_token_account": None,
            "pool_owner": None,
            "source": None,
            "source_type": None,
            "amount": None,
            "signature": None,
            "block_time": None,
            "creator_link": None,
            "events_checked": 0,
        }

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

    transfer_result = (
        get_pool_transfers(
            token_account,
            mint
        )
    )

    if transfer_result[
        "available"
    ]:

        transfers = (
            transfer_result[
                "transfers"
            ]
        )

        # The API supports ascending
        # ordering, but sort again locally
        # for safety.
        transfers = sorted(
            transfers,
            key=lambda x: (
                x.get("timestamp")
                or x.get("blockTime")
                or x.get("slot")
                or 0
            )
        )

        if transfers:

            first = transfers[0]

            source = transfer_value(
                first,
                "fromUserAccount",
                "from",
                "source",
                "sender",
                "fromAddress"
            )

            destination = transfer_value(
                first,
                "toUserAccount",
                "to",
                "destination",
                "receiver",
                "toAddress"
            )

            amount = transfer_value(
                first,
                "tokenAmount",
                "amount",
                "uiAmount"
            )

            signature = transfer_value(
                first,
                "signature",
                "txSignature",
                "transactionSignature"
            )

            block_time = transfer_value(
                first,
                "timestamp",
                "blockTime"
            )

            if (
                source
                and destination
                and destination.lower()
                != token_account.lower()
            ):
                # Sometimes Helius reports the
                # pool owner rather than the token
                # account as destination.
                if (
                    pool_owner
                    and destination.lower()
                    != pool_owner.lower()
                ):
                    pass

            source_type = classify_origin(
                source,
                creator,
                first
            )

            creator_link = None

            if source and creator:

                creator_link = (
                    source.lower()
                    == creator.lower()
                )

            return {
                "status": "FOUND",
                "method": (
                    "Helius "
                    "getTransfersByAddress"
                ),
                "pool_token_account": (
                    token_account
                ),
                "pool_owner": pool_owner,
                "source": source,
                "source_type": source_type,
                "amount": safe_float(
                    amount
                ),
                "signature": signature,
                "block_time": block_time,
                "creator_link": creator_link,
                "destination": destination,
                "events_checked": len(
                    transfers
                ),
            }

    # ========================================================
    # FALLBACK: RAW TRANSACTION HISTORY
    # ========================================================

    signatures = helius_request(
        "getSignaturesForAddress",
        [
            token_account,
            {
                "limit": 100
            }
        ]
    )

    if not signatures:

        return {
            "status": "UNKNOWN",
            "method": "fallback",
            "pool_token_account": (
                token_account
            ),
            "pool_owner": pool_owner,
            "source": None,
            "source_type": None,
            "amount": None,
            "signature": None,
            "block_time": None,
            "creator_link": None,
            "events_checked": 0,
        }

    signatures = sorted(
        signatures,
        key=lambda x: (
            x.get("blockTime")
            or 0
        )
    )

    events_checked = 0

    for sig_info in signatures:

        signature = sig_info.get(
            "signature"
        )

        if not signature:
            continue

        transaction = helius_request(
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

        if not transaction:
            continue

        events_checked += 1

        changes = (
            get_token_balance_deltas(
                transaction,
                mint
            )
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
                or (
                    pool_owner
                    and owner.lower()
                    == pool_owner.lower()
                )
            ):

                if change.get(
                    "delta",
                    0
                ) > 0:

                    pool_change = change
                    break

        if not pool_change:
            continue

        sources = [
            change
            for change in changes
            if change.get(
                "delta",
                0
            ) < 0
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

        source_wallet = (
            source.get("owner")
            if source
            else None
        )

        creator_link = None

        if source_wallet and creator:

            creator_link = (
                source_wallet.lower()
                == creator.lower()
            )

        return {
            "status": "FOUND",
            "method": "raw fallback",
            "pool_token_account": (
                token_account
            ),
            "pool_owner": pool_owner,
            "source": source_wallet,
            "source_type": (
                "CREATOR"
                if creator_link is True
                else (
                    "OTHER WALLET"
                    if source_wallet
                    else "MINT / PROGRAM / UNKNOWN"
                )
            ),
            "amount": (
                abs(
                    source.get("delta")
                )
                if source
                else pool_change.get("delta")
            ),
            "signature": signature,
            "block_time": (
                transaction.get(
                    "blockTime"
                )
            ),
            "creator_link": creator_link,
            "destination": token_account,
            "events_checked": events_checked,
        }

    return {
        "status": "NO_CLEAR_FUNDING",
        "method": "fallback",
        "pool_token_account": (
            token_account
        ),
        "pool_owner": pool_owner,
        "source": None,
        "source_type": None,
        "amount": None,
        "signature": None,
        "block_time": None,
        "creator_link": None,
        "events_checked": events_checked,
    }


# ============================================================
# RAW TOKEN BALANCE DELTAS
# ============================================================

def get_token_balance_deltas(
    transaction,
    mint
):

    if not transaction:
        return []

    meta = (
        transaction.get("meta")
        or {}
    )

    pre = (
        meta.get(
            "preTokenBalances"
        )
        or []
    )

    post = (
        meta.get(
            "postTokenBalances"
        )
        or []
    )

    message = (
        transaction
        .get("transaction", {})
        .get("message", {})
    )

    keys = (
        message.get(
            "accountKeys"
        )
        or []
    )

    account_keys = []

    for item in keys:

        if isinstance(
            item,
            str
        ):
            account_keys.append(
                item
            )

        elif isinstance(
            item,
            dict
        ):

            pubkey = item.get(
                "pubkey"
            )

            if pubkey:
                account_keys.append(
                    pubkey
                )

    before = {}
    after = {}

    for item in pre:

        if item.get(
            "mint"
        ) != mint:
            continue

        index = item.get(
            "accountIndex"
        )

        ui = (
            item.get(
                "uiTokenAmount"
            )
            or {}
        )

        before[index] = {
            "owner": item.get(
                "owner"
            ),
            "amount": safe_float(
                ui.get(
                    "uiAmountString"
                )
            ),
        }

    for item in post:

        if item.get(
            "mint"
        ) != mint:
            continue

        index = item.get(
            "accountIndex"
        )

        ui = (
            item.get(
                "uiTokenAmount"
            )
            or {}
        )

        after[index] = {
            "owner": item.get(
                "owner"
            ),
            "amount": safe_float(
                ui.get(
                    "uiAmountString"
                )
            ),
        }

    changes = []

    indexes = (
        set(before.keys())
        | set(after.keys())
    )

    for index in indexes:

        before_item = before.get(
            index,
            {}
        )

        after_item = after.get(
            index,
            {}
        )

        before_amount = safe_float(
            before_item.get(
                "amount"
            )
        )

        after_amount = safe_float(
            after_item.get(
                "amount"
            )
        )

        delta = (
            after_amount
            - before_amount
        )

        if abs(delta) < 0.0000001:
            continue

        owner = (
            after_item.get(
                "owner"
            )
            or before_item.get(
                "owner"
            )
        )

        account = None

        if (
            index is not None
            and index < len(account_keys)
        ):
            account = account_keys[
                index
            ]

        changes.append({
            "account": account,
            "owner": owner,
            "before": before_amount,
            "after": after_amount,
            "delta": delta,
        })

    return changes


# ============================================================
# CREATOR / DEPLOYER
# ============================================================

def get_creation_info(mint):
    """Find mint initialization evidence without conflating its actors.

    The RPC signature scan is bounded. A confirmed InitializeMint instruction
    identifies the mint authority encoded at initialization; the transaction
    fee payer is reported separately and is NOT automatically called creator.
    """
    result = {
        "creator": None, "initialization_authority": None,
        "fee_payer": None, "signature": None, "block_time": None,
        "verified": False, "reason": "Mint initialization not verified",
        "signatures_scanned": 0,
    }
    if not mint:
        result["reason"] = "Mint address missing"
        return result

    before = None
    scanned = 0
    oldest = None
    exhausted = False
    for _ in range(10):  # maximum 1,000 signatures; bounded RPC usage
        config = {"limit": 100}
        if before:
            config["before"] = before
        page = helius_request("getSignaturesForAddress", [mint, config])
        if not isinstance(page, list) or not page:
            exhausted = True
            break
        scanned += len(page)
        oldest = page[-1]
        before = oldest.get("signature")
        if len(page) < 100:
            exhausted = True
            break
        if not before:
            break
    result["signatures_scanned"] = scanned
    if not oldest or not oldest.get("signature"):
        result["reason"] = "No mint transaction history available"
        return result
    if not exhausted:
        result["reason"] = f"History scan reached {scanned} signatures; oldest transaction not confirmed"
        return result

    signature = oldest["signature"]
    tx = helius_request("getTransaction", [signature, {
        "encoding": "jsonParsed", "commitment": "confirmed",
        "maxSupportedTransactionVersion": 0,
    }])
    if not isinstance(tx, dict) or (tx.get("meta") or {}).get("err") is not None:
        result.update(signature=signature, reason="Oldest mint transaction unavailable or failed")
        return result

    message = ((tx.get("transaction") or {}).get("message") or {})
    instructions = list(message.get("instructions") or [])
    for group in (tx.get("meta") or {}).get("innerInstructions") or []:
        instructions.extend(group.get("instructions") or [])

    init_info = None
    for instruction in instructions:
        parsed = instruction.get("parsed") if isinstance(instruction, dict) else None
        if not isinstance(parsed, dict):
            continue
        if str(parsed.get("type") or "").lower() in ("initializemint", "initializemint2"):
            info = parsed.get("info") or {}
            if info.get("mint") == mint:
                init_info = info
                break
    block_time = tx.get("blockTime") or oldest.get("blockTime")
    if not init_info:
        result.update(signature=signature, block_time=block_time,
                      reason="Oldest confirmed mint-address transaction is not a parsed InitializeMint instruction")
        return result
    if not block_time:
        result.update(signature=signature, reason="InitializeMint found but timestamp unavailable")
        return result

    account_keys = message.get("accountKeys") or []
    fee_payer = None
    if account_keys:
        first = account_keys[0]
        fee_payer = first if isinstance(first, str) else first.get("pubkey")
    authority = init_info.get("mintAuthority")
    result.update(
        initialization_authority=authority,
        fee_payer=fee_payer,
        signature=signature,
        block_time=block_time,
        verified=True,
        reason="Parsed InitializeMint instruction confirmed in oldest scanned mint history",
    )
    # Keep creator unset: mint authority and fee payer are evidence, not proof
    # of the project's human/team creator.
    return result


def get_sol_balance(wallet):

    result = helius_request(
        "getBalance",
        [
            wallet
        ]
    )

    if not result:
        return None

    value = result.get(
        "value"
    )

    if value is None:
        return None

    return (
        float(value)
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
        result.get(
            "token_accounts"
        )
        or []
    )

    total = 0

    for account in accounts:

        total += safe_float(
            account.get(
                "amount"
            )
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

    result = helius_request(
        "getTransactionsForAddress",
        [
            wallet,
            {
                "transactionDetails": "full",
                "sortOrder": "desc",
                "limit": 50,
                "filters": {
                    "tokenAccounts":
                        "balanceChanged",
                    "status":
                        "succeeded",
                },
            }
        ]
    )

    if isinstance(
        result,
        list
    ):

        token_activity = False

        for tx in result:

            if (
                tx.get(
                    "tokenTransfers"
                )
                or tx.get(
                    "balanceChanges"
                )
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

    signatures = helius_request(
        "getSignaturesForAddress",
        [
            wallet,
            {
                "limit": 50
            }
        ]
    )

    if signatures:

        return {
            "count": len(
                signatures
            ),
            "token_activity":
                "Unknown",
        }

    return {
        "count": 0,
        "token_activity":
            "Unknown",
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
    breakdown = []

    def add_factor(label, points, detail):
        nonlocal score
        score += points

        sign = "+" if points > 0 else ""

        breakdown.append({
            "label": label,
            "points": points,
            "detail": detail,
            "display": (
                f"{label}: {sign}{points} ({detail})"
            )
        })

    # --------------------------------------------------------
    # MARKET DATA
    # --------------------------------------------------------

    market_cap_raw = market.get("market_cap")
    age_raw = market.get("age_hours")
    volume_raw = market.get("volume_24h")
    txns_raw = market.get("total_txns")
    liquidity_raw = market.get("liquidity_usd")

    market_cap = (
        safe_float(market_cap_raw)
        if market_cap_raw is not None else None
    )

    age_hours = (
        safe_float(age_raw)
        if age_raw is not None else None
    )

    volume = (
        safe_float(volume_raw)
        if volume_raw is not None else None
    )

    txns_value = (
        safe_float(txns_raw)
        if txns_raw is not None else None
    )

    txns = (
        int(txns_value)
        if txns_value is not None else None
    )

    liquidity = (
        safe_float(liquidity_raw)
        if liquidity_raw is not None else None
    )

    # --------------------------------------------------------
    # MARKET CAP
    # --------------------------------------------------------

    if market_cap is None or market_cap < 0:
        add_factor("Market cap", 0, "unavailable")

    elif 1000 <= market_cap <= 2000:
        add_factor("Market cap", 20, f"${market_cap:,.0f}")

    elif market_cap <= 4000 and market_cap > 2000:
        add_factor("Market cap", 15, f"${market_cap:,.0f}")

    elif market_cap <= 7000 and market_cap > 4000:
        add_factor("Market cap", 10, f"${market_cap:,.0f}")

    elif market_cap <= 10000 and market_cap > 7000:
        add_factor("Market cap", 5, f"${market_cap:,.0f}")

    else:
        add_factor(
            "Market cap",
            0,
            f"${market_cap:,.0f}; outside target bands"
        )

    # --------------------------------------------------------
    # PAIR AGE
    # --------------------------------------------------------

    if age_hours is None or age_hours < 0:
        add_factor("Pair age", 0, "unavailable")

    elif age_hours <= 0.5:
        add_factor("Pair age", 15, f"{age_hours:.1f} hours")

    elif age_hours <= 1:
        add_factor("Pair age", 12, f"{age_hours:.1f} hours")

    elif age_hours <= 6:
        add_factor("Pair age", 8, f"{age_hours:.1f} hours")

    elif age_hours <= 24:
        add_factor("Pair age", 3, f"{age_hours:.1f} hours")

    else:
        add_factor(
            "Pair age",
            0,
            f"{age_hours:.1f} hours; older pair"
        )

    # --------------------------------------------------------
    # TRADING ACTIVITY
    # --------------------------------------------------------

    if volume is None or volume < 0:
        add_factor("24h volume", 0, "unavailable")

    elif volume >= 10000:
        add_factor("24h volume", 15, f"${volume:,.2f}")

    elif volume >= 3000:
        add_factor("24h volume", 10, f"${volume:,.2f}")

    elif volume >= 500:
        add_factor("24h volume", 5, f"${volume:,.2f}")

    else:
        add_factor(
            "24h volume",
            0,
            f"${volume:,.2f}; low activity"
        )

    if txns is None or txns < 0:
        add_factor("Transactions", 0, "unavailable")

    elif txns >= 100:
        add_factor("Transactions", 10, str(txns))

    elif txns >= 40:
        add_factor("Transactions", 7, str(txns))

    elif txns >= 15:
        add_factor("Transactions", 3, str(txns))

    else:
        add_factor(
            "Transactions",
            0,
            f"{txns}; limited activity"
        )

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    if liquidity is None or liquidity < 0:
        add_factor("Liquidity", 0, "unavailable")

    elif liquidity < 1000:
        add_factor(
            "Liquidity",
            -10,
            f"${liquidity:,.2f}; very low"
        )

    elif liquidity < 3000:
        add_factor(
            "Liquidity",
            -5,
            f"${liquidity:,.2f}; low"
        )

    elif liquidity >= 10000:
        add_factor(
            "Liquidity",
            5,
            f"${liquidity:,.2f}"
        )

    else:
        add_factor(
            "Liquidity",
            0,
            f"${liquidity:,.2f}"
        )

    # --------------------------------------------------------
    # HOLDER DISTRIBUTION
    # --------------------------------------------------------

    holder_count_raw = holders.get("holder_count")
    top1_raw = holders.get("top1_pct")
    top10_raw = holders.get("top10_pct")

    holder_count = (
        int(safe_float(holder_count_raw))
        if holder_count_raw is not None else None
    )

    top1 = (
        safe_float(top1_raw)
        if top1_raw is not None else None
    )

    top10 = (
        safe_float(top10_raw)
        if top10_raw is not None else None
    )

    if holder_count is None:
        add_factor("Holder count", -5, "unverified")

    elif holder_count >= 50:
        add_factor(
            "Holder count",
            10,
            f"{holder_count} detected holders"
        )

    elif holder_count >= 20:
        add_factor(
            "Holder count",
            7,
            f"{holder_count} detected holders"
        )

    elif holder_count >= 10:
        add_factor(
            "Holder count",
            3,
            f"{holder_count} detected holders"
        )

    elif holder_count == 0:
        add_factor("Holder count", -10, "zero detected holders")

    else:
        add_factor(
            "Holder count",
            0,
            f"{holder_count} detected holders"
        )

    if top1 is None:
        add_factor(
            "Top holder concentration",
            -3,
            "unverified"
        )

    elif top1 >= 50:
        add_factor(
            "Top holder concentration",
            -25,
            f"{top1:.2f}%"
        )

    elif top1 >= 30:
        add_factor(
            "Top holder concentration",
            -20,
            f"{top1:.2f}%"
        )

    elif top1 >= 20:
        add_factor(
            "Top holder concentration",
            -12,
            f"{top1:.2f}%"
        )

    elif top1 >= 10:
        add_factor(
            "Top holder concentration",
            -5,
            f"{top1:.2f}%"
        )

    else:
        add_factor(
            "Top holder concentration",
            0,
            f"{top1:.2f}%"
        )

    if top10 is None:
        add_factor(
            "Top 10 concentration",
            -3,
            "unverified"
        )

    elif top10 >= 80:
        add_factor(
            "Top 10 concentration",
            -20,
            f"{top10:.2f}%"
        )

    elif top10 >= 60:
        add_factor(
            "Top 10 concentration",
            -15,
            f"{top10:.2f}%"
        )

    elif top10 >= 40:
        add_factor(
            "Top 10 concentration",
            -8,
            f"{top10:.2f}%"
        )

    else:
        add_factor(
            "Top 10 concentration",
            0,
            f"{top10:.2f}%"
        )

    # --------------------------------------------------------
    # TOKEN AUTHORITIES
    # --------------------------------------------------------

    if mint_info.get("mint_authority") is None:
        add_factor("Mint authority", 5, "revoked")
    else:
        add_factor("Mint authority", -10, "active")

    if mint_info.get("freeze_authority") is None:
        add_factor("Freeze authority", 5, "revoked")
    else:
        add_factor("Freeze authority", -10, "active")

    # --------------------------------------------------------
    # CREATOR RISK
    # --------------------------------------------------------

    creator_data = market.get("_creator_data", {})

    creator_risk = str(
        creator_data.get("risk", "UNKNOWN")
    ).upper()

    if creator_risk == "HIGH":
        add_factor(
            "Creator risk",
            -15,
            "high-risk indicators detected"
        )

    elif creator_risk == "MEDIUM":
        add_factor(
            "Creator risk",
            -7,
            "some risk indicators detected"
        )

    elif creator_risk == "LOW":
        add_factor(
            "Creator risk",
            0,
            "no high-risk indicator from current checks"
        )

    else:
        add_factor(
            "Creator risk",
            -3,
            "unverified; insufficient evidence"
        )

    # --------------------------------------------------------
    # POOL ORIGIN
    # --------------------------------------------------------

    pool_origin = market.get("_pool_origin_data", {})

    creator_link = pool_origin.get("creator_link")
    trace_method = str(
        pool_origin.get("method", "unknown")
    ).lower()

    events_checked = safe_float(
        pool_origin.get("events_checked", 0)
    )

    if creator_link is True:
        add_factor(
            "Creator-to-pool link",
            -3,
            "direct link detected; not proof of wrongdoing"
        )

    elif creator_link is False:
        add_factor(
            "Creator-to-pool link",
            0,
            "no direct link detected"
        )

    else:
        add_factor(
            "Creator-to-pool link",
            -1,
            "unknown"
        )

    if trace_method == "raw fallback" and events_checked < 3:
        add_factor(
            "Pool trace confidence",
            0,
            "limited raw-history evidence"
        )

    # --------------------------------------------------------
    # FINAL SCORE
    # --------------------------------------------------------

    final_score = max(0, min(100, score))

    market["_score_breakdown"] = breakdown
    market["_score_raw"] = score
    market["_score_final"] = final_score

    return final_score


# ============================================================
# VERDICT
# ============================================================

def get_verdict(score, market):
    """Apply non-negotiable market-quality gates before the score label."""
    liquidity = _number_or_none(market.get("liquidity_usd"))
    volume = _number_or_none(market.get("volume_24h"))
    txns = _number_or_none(market.get("total_txns"))

    # A high score must never override near-zero or critically low liquidity.
    if liquidity is None:
        return "🔴 LOW PRIORITY — LIQUIDITY UNVERIFIED"
    if liquidity < 1000:
        return "🔴 LOW PRIORITY — CRITICAL LIQUIDITY (<$1K)"
    if volume is not None and volume < 100 and txns is not None and txns < 5:
        return "🔴 LOW PRIORITY — INSUFFICIENT MARKET ACTIVITY"

    if score >= 70:
        return "🟢 WATCHLIST CANDIDATE"
    if score >= 50:
        return "🟡 NEEDS FURTHER RESEARCH"
    return "🔴 LOW PRIORITY — SKIP FOR NOW"

# ============================================================
# REPORTS
# ============================================================

def print_market_report(market):

    print(
        "\n💰 MARKET DATA"
    )

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
    holders
):

    print(
        "\n👥 HOLDER DISTRIBUTION"
    )

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

    top_wallet = (
        sorted_holders[0][0]
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


def print_security_report(
    mint_info
):

    print(
        "\n🔐 TOKEN SECURITY"
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

    decimals = mint_info.get(
        "decimals"
    )

    if decimals is not None:

        print(
            f"🔢 Decimals: "
            f"{decimals}"
        )

    print(
        "🪙 Mint Authority: "
        + (
            "🟢 REVOKED"
            if mint_info.get(
                "mint_authority"
            ) is None
            else "🔴 ACTIVE"
        )
    )

    print(
        "❄️ Freeze Authority: "
        + (
            "🟢 REVOKED"
            if mint_info.get(
                "freeze_authority"
            ) is None
            else "🔴 ACTIVE"
        )
    )


def print_creator_report(
    creator,
    mint,
    mint_info
):

    print("\n👤 CREATOR / DEPLOYER")

    if not creator:
        print("🧑 Likely Creator: Unavailable")

        return {
            "creator": None,
            "creator_supply_pct": 0,
            "token_activity": "Unknown",
            "risk": "UNKNOWN",
        }

    balance = get_wallet_token_balance(
        creator,
        mint
    )

    raw_supply = safe_float(
        mint_info.get("raw_supply")
    )

    creator_pct = 0

    if balance is not None and raw_supply > 0:
        creator_pct = (
            balance / raw_supply * 100
        )

    sol_balance = get_sol_balance(
        creator
    )

    history = get_wallet_history(
        creator
    )

    token_activity = history.get(
        "token_activity"
    )

    if token_activity is None:
        token_activity = "Unknown"

    activity_text = str(
        token_activity
    ).strip()

    # Unknown activity must not be treated as low risk.
    if activity_text.lower() in (
        "",
        "unknown",
        "unavailable",
        "not checked",
        "none",
    ):
        risk = "UNVERIFIED"

    elif creator_pct >= 20:
        risk = "HIGH"

    elif creator_pct >= 5:
        risk = "MEDIUM"

    else:
        risk = "LOW"

    print(
        f"🧑 Likely Creator: {creator}"
    )

    if sol_balance is None:
        print("💰 Creator SOL: Unavailable")
    else:
        print(
            f"💰 Creator SOL: {sol_balance:.4f} SOL"
        )

    print(
        f"📊 Creator Supply: {creator_pct:.2f}%"
    )

    print(
        f"📜 Creator Recent Txns: "
        f"{history.get('count', 'Unknown')}"
    )

    print(
        f"🪙 Creator Token Activity: "
        f"{activity_text}"
    )

    print(
        f"🧠 Creator Risk: {risk}"
    )

    return {
        "creator": creator,
        "creator_supply_pct": creator_pct,
        "token_activity": activity_text,
        "risk": risk,
    }


def print_pool_report(
    pool_origin,
    market
):

    print(
        "\n🏊 POOL / TOKEN ORIGIN"
    )

    print(
        f"🏊 Pool / Pair: "
        f"{market.get('pair_address', 'Unavailable')}"
    )

    print(
        f"🪙 Pool Token Account: "
        f"{pool_origin.get('pool_token_account', 'Unavailable')}"
    )

    print(
        f"🧪 Trace Method: "
        f"{pool_origin.get('method', 'Unknown')}"
    )

    status = pool_origin.get(
        "status"
    )

    if status in ("FOUND", "CANDIDATE"):
        if status == "CANDIDATE":
            print("ℹ️ Earliest matching incoming movement in scanned history (candidate, not proven first-ever funding).")

        print(
            f"📥 First Detected Origin: "
            f"{pool_origin.get('source_type', 'UNKNOWN')}"
        )

        source = pool_origin.get(
            "source"
        )

        if source:

            print(
                f"👤 Source Wallet: "
                f"{source}"
            )

        else:

            print(
                "👤 Source Wallet: "
                "Unknown"
            )

        amount = pool_origin.get(
            "amount"
        )

        if amount is not None:

            print(
                f"🪙 Initial Token Movement: "
                f"{fmt_number(amount)}"
            )

        destination = pool_origin.get(
            "destination"
        )

        if destination:

            print(
                f"➡️ Destination: "
                f"{destination}"
            )

        creator_link = pool_origin.get(
            "creator_link"
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
            "signature"
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
                f"{iso_from_seconds(block_time)}"
            )

    elif status == "NO_CLEAR_FUNDING":

        print(
            "ℹ️ Pool was found, but "
            "no clear funding transfer "
            "was identified."
        )

    else:

        print(
            "ℹ️ Pool origin could "
            "not be determined."
        )

    print(
        f"🔍 Pool Events Checked: "
        f"{pool_origin.get('events_checked', 0)}"
    )
    if pool_origin.get("history_signatures_scanned") is not None:
        print(f"📚 Pool signatures scanned: {pool_origin.get('history_signatures_scanned')}")
    if pool_origin.get("reason"):
        print(f"ℹ️ Trace note: {pool_origin.get('reason')}")


# ============================================================
# MAIN
# ============================================================

def main():
    import sys

    if len(sys.argv) < 3:
        print("Usage: python research.py <chain> <contract>")
        sys.exit(1)

    chain = sys.argv[1].strip().lower()
    mint = sys.argv[2].strip()
    if mint.lower().startswith("contract:"):
        mint = mint.split(":", 1)[1].strip()

    print("🥷 MYSTERIOUS TOKEN CHECKER")
    print(f"⛓ Chain: {chain}")
    print(f"📍 Contract: {mint}")
    if chain not in SUPPORTED_CHAINS:
        print("❌ Unsupported chain.")
        sys.exit(1)

    market = get_market_data(chain, mint)
    if not market:
        print("\n❌ Could not retrieve market data.")
        sys.exit(1)

    symbol = str(market.get("symbol") or "UNKNOWN").lstrip("$")
    print(f"\n🪙 {market.get('name') or 'Unknown'} (${symbol})")

    mint_info = get_mint_info(mint)
    print("\n🔎 Checking holder distribution...")
    holder_data = get_holder_data(mint, market.get("pool_addresses", []), mint_info)

    print("🔐 Checking token security...")
    print("👤 Tracing likely creator / deployer...")
    creation = get_creation_info(mint) or {}
    market["_creation_info"] = creation
    creator = creation.get("creator")

    pool_token_accounts = get_pool_token_accounts(market.get("pool_addresses", []), holder_data)
    pool_origin = trace_pool_origin(mint, pool_token_accounts, creator)

    print_market_report(market)
    print_holder_report(holder_data)
    print_security_report(mint_info)
    print_creator_report._creation_info = creation
    creator_data = print_creator_report(creator, mint, mint_info)
    market["_creator_data"] = creator_data
    market["_pool_origin_data"] = pool_origin
    print_pool_report(pool_origin, market)

    score = calculate_score(market, holder_data, mint_info)
    verdict = get_verdict(score, market)
    print(f"\n🎯 Research Score: {score}/100")
    print(f"🚦 Verdict: {verdict}")

    breakdown = market.get("_score_breakdown", [])
    print("\n📊 SCORE BREAKDOWN")
    if breakdown:
        for item in breakdown:
            print(f"  {item['display']}")
    else:
        print("  ⚠️ No score breakdown available.")

    real_holders = holder_data.get("holders") or []
    print("\n🐋 TOP TOKEN-ACCOUNT OWNER ANALYSIS")
    if not real_holders:
        print("ℹ️ No verified real top holder available.")
    else:
        top_wallet, raw_balance = real_holders[0]
        print(f"🐋 Owner address (classification unverified): {top_wallet}")
        top1 = holder_data.get("top1_pct")
        print(f"📊 Supply: {top1:.2f}%" if top1 is not None else "📊 Supply: Unavailable")
        decimals = mint_info.get("decimals") if mint_info.get("available") is True else None
        if decimals is not None:
            print(f"🪙 Balance: {fmt_number(raw_balance / (10 ** decimals))}")
        else:
            print("🪙 Balance: Raw balance available; decimals unverified.")

    print("\n🔗 WALLET RELATIONSHIP")
    if not creator:
        print("ℹ️ Creator relationship could not be determined.")
    elif not real_holders:
        print("ℹ️ Creator/top-holder relationship cannot be determined.")
    elif real_holders[0][0].lower() == creator.lower():
        print("🚩 Creator is currently the top real holder.")
    else:
        print("🟢 Creator is not the top real holder.")

    positives = []
    mc = safe_float(market.get("market_cap"))
    if mc is not None:
        if 1000 <= mc <= 2000:
            positives.append("Very early market cap (high risk)")
        elif 2000 < mc <= 4000:
            positives.append("Early market cap (high risk)")
        elif 4000 < mc <= 7000:
            positives.append("Market cap between $4K and $7K")

    if mint_info.get("available") is True:
        if mint_info.get("mint_authority") is None:
            positives.append("Mint authority revoked")
        if mint_info.get("freeze_authority") is None:
            positives.append("Freeze authority revoked")

    creator_supply = creator_data.get("creator_supply_pct")
    if creator_data.get("balance_checked") is True and creator_supply == 0:
        positives.append("Creator holds 0% detected supply (balance check completed)")
    if pool_origin.get("creator_link") is False:
        positives.append("No direct creator-to-pool funding link detected in checked data")

    print("\n✅ POSITIVE SIGNALS")
    if positives:
        for item in positives:
            print(f"• {item}")
    else:
        print("• No verified positive signals identified")

    warnings = []
    liquidity = market.get("liquidity_usd")
    volume = safe_float(market.get("volume_24h"))
    txns = market.get("total_txns")
    holder_count = holder_data.get("holder_count")
    top1 = holder_data.get("top1_pct")
    top10 = holder_data.get("top10_pct")
    age_hours = market.get("age_hours")

    if liquidity is None:
        warnings.append("Liquidity data unavailable")
    elif safe_float(liquidity) < 1000:
        warnings.append("Very low liquidity: under $1,000")

    if volume is None:
        warnings.append("24-hour volume data unavailable")
    elif volume < 100:
        warnings.append("Extremely low 24-hour volume: under $100")
    elif volume < 500:
        warnings.append("Very low 24-hour volume: under $500")

    if txns is None:
        warnings.append("Transaction count unavailable")
    elif txns == 0:
        warnings.append("No transactions recorded in the reported period")
    elif txns < 5:
        warnings.append("Extremely low trading activity: fewer than 5 transactions")
    elif txns < 20:
        warnings.append("Low trading activity: fewer than 20 transactions")

    if holder_data.get("data_complete") is not True:
        warnings.append("Holder results are incomplete or not fully verified")
    if holder_count == 0:
        warnings.append("No real holders detected in the returned holder data")
    elif isinstance(holder_count, int) and holder_count < 10:
        warnings.append("Very small detected holder base: fewer than 10 holders")

    concentration_verified = holder_data.get("concentration_verified") is True
    if not concentration_verified:
        warnings.append("Holder concentration is UNVERIFIED: possible market/pool-like account is included; do not attribute its balance to an individual whale")
    else:
        if top1 is not None:
            if top1 >= 30:
                warnings.append(f"Severe top-holder concentration: {top1:.2f}%")
            elif top1 >= 20:
                warnings.append(f"High top-holder concentration: {top1:.2f}%")
            elif top1 >= 10:
                warnings.append(f"Elevated top-holder concentration: {top1:.2f}%")

        if top10 is not None:
            if top10 >= 80:
                warnings.append(f"Severe top-10 concentration: {top10:.2f}%")
            elif top10 >= 60:
                warnings.append(f"High top-10 concentration: {top10:.2f}%")
            elif top10 >= 40:
                warnings.append(f"Elevated top-10 concentration: {top10:.2f}%")

    pool_pct = holder_data.get("pool_pct")
    if pool_pct is not None and pool_pct >= 50:
        warnings.append(
            f"Possible pool share is high ({pool_pct:.2f}%); "
            "pool-address matching is heuristic and needs manual verification"
        )

    activity = str(creator_data.get("token_activity") or "Unknown").strip().lower()
    if activity in ("unknown", "none", "unavailable", "not checked", ""):
        warnings.append("Creator token activity is unknown or unverified")
    if creator_data.get("balance_checked") is not True:
        warnings.append("Creator token balance could not be independently verified")
    elif creator_supply is not None and creator_supply >= 20:
        warnings.append("Creator holds significant detected token supply")

    if pool_origin.get("creator_link") is True:
        warnings.append("Creator linked to a candidate initial pool funding transfer; review manually")
    elif pool_origin.get("creator_link") is None:
        warnings.append("Creator-to-pool funding relationship is unknown")
    if pool_origin.get("status") == "UNKNOWN":
        warnings.append("Pool token origin could not be verified")
    if pool_origin.get("trace_method") == "raw fallback" or pool_origin.get("method") == "raw fallback":
        warnings.append("Pool origin uses fallback tracing; results may be incomplete")

    if age_hours is not None and age_hours > 24 * 30 and (
        (volume is not None and volume < 500) or (txns is not None and txns < 20)
    ):
        warnings.append("Older selected pair with very low recent activity; this does not establish token creation age")

    print("\n🚩 WARNINGS")
    if warnings:
        for item in warnings:
            print(f"• {item}")
    else:
        print("• No configured warning conditions triggered")

    # Unknown or incomplete holder data must never be labelled low risk.
    if holder_data.get("data_complete") is not True or top1 is None or top10 is None or not concentration_verified:
        holder_risk = "UNVERIFIED"
    elif top1 >= 30 or top10 >= 60:
        holder_risk = "HIGH"
    elif top1 >= 10 or top10 >= 40:
        holder_risk = "ELEVATED"
    else:
        holder_risk = "LOWER CONCENTRATION OBSERVED"

    if mint_info.get("available") is not True:
        security = "CHECK UNAVAILABLE"
    elif mint_info.get("mint_authority") is None and mint_info.get("freeze_authority") is None:
        security = "AUTHORITY CHECKS PASSED"
    else:
        security = "AUTHORITY REVIEW REQUIRED"

    print("\n🧠 RESEARCH VERDICT")
    print(f"🔐 Security: {security}")
    print(f"👥 Holder Risk: {holder_risk}")
    print(f"👤 Creator Risk: {creator_data.get('risk', 'UNKNOWN')}")
    print(f"🐋 Top Holder Risk: {'ANALYZED' if real_holders else 'UNAVAILABLE'}")

    status = pool_origin.get("status", "UNKNOWN")
    if status in ("FOUND", "CANDIDATE"):
        if status == "CANDIDATE":
            print("ℹ️ Earliest matching incoming movement in scanned history (candidate, not proven first-ever funding).")
        pool_label = "ORIGIN CANDIDATE FOUND — VERIFY"
    elif status == "NO_CLEAR_FUNDING":
        pool_label = "NO CLEAR FUNDING FOUND IN CHECKED DATA"
    else:
        pool_label = "UNVERIFIED"
    print(f"🏊 Pool Origin: {pool_label}")
    print(f"🚦 FINAL: {verdict}")
    print("\n🥷 RESEARCH COMPLETED")

 
# ============================================================
# MYSTERIOUS EARLY RADAR — DATA-QUALITY PATCH
# Paste this block immediately BEFORE:
# if __name__ == "__main__":
#     main()
#
# This overrides selected functions above without changing
# your Helius key, endpoints, or GitHub Actions workflows.
# ============================================================

def _number_or_none(value):
    """Return a finite number, or None when the source is missing/invalid."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return number
    except (TypeError, ValueError, OverflowError):
        return None


def _format_pct(value):
    return "Unavailable" if value is None else f"{value:.2f}%"


def get_market_data(chain, mint):
    print("\n📡 Checking market data...")
    dex_pairs = []
    gecko_pools = []

    dex_url = f"{DEXSCREENER_BASE}/token-pairs/v1/{chain}/{mint}"
    data = request_json("GET", dex_url)
    if isinstance(data, list):
        dex_pairs = [p for p in data if isinstance(p, dict)]
    print(f"🔎 DexScreener pairs discovered: {len(dex_pairs)}")

    network = "solana" if chain.lower() in ("solana", "sol") else chain.lower()
    gecko_url_api = (
        "https://api.geckoterminal.com/api/v2/"
        f"networks/{network}/tokens/{mint}/pools?page=1"
    )
    try:
        response = requests.get(
            gecko_url_api,
            headers={"Accept": "application/json;version=20230302"},
            timeout=12,
        )
        if response.status_code == 200:
            payload = response.json()
            if isinstance(payload, dict) and isinstance(payload.get("data"), list):
                gecko_pools = [
                    item for item in payload["data"]
                    if isinstance(item, dict)
                ]
        elif response.status_code == 429:
            print("⚠️ GeckoTerminal rate limit reached.")
        else:
            print(f"ℹ️ GeckoTerminal response: HTTP {response.status_code}")
    except (requests.RequestException, ValueError) as exc:
        print(f"ℹ️ GeckoTerminal unavailable: {exc}")

    def gecko_rank(pool):
        attrs = pool.get("attributes") or {}
        reserve = _number_or_none(attrs.get("reserve_in_usd"))
        volume_obj = attrs.get("volume_usd") or {}
        volume = _number_or_none(volume_obj.get("h24"))
        return (reserve is not None, reserve or 0, volume or 0)

    gecko_pools.sort(key=gecko_rank, reverse=True)
    gp = gecko_pools[0].get("attributes") or {} if gecko_pools else {}

    gecko_liquidity = _number_or_none(gp.get("reserve_in_usd"))
    gecko_volume_obj = gp.get("volume_usd") or {}
    gecko_volume = _number_or_none(gecko_volume_obj.get("h24"))
    gecko_market_cap = _number_or_none(gp.get("market_cap_usd"))
    gecko_fdv = _number_or_none(gp.get("fdv_usd"))
    gecko_pool_address = gp.get("address")
    gecko_created = gp.get("pool_created_at")

    def dex_rank(pair):
        liq = _number_or_none((pair.get("liquidity") or {}).get("usd"))
        vol = _number_or_none((pair.get("volume") or {}).get("h24"))
        created = _number_or_none(pair.get("pairCreatedAt"))
        return (liq is not None, liq or 0, vol or 0, created or 0)

    dex_pairs.sort(key=dex_rank, reverse=True)
    pair = dex_pairs[0] if dex_pairs else {}
    dex_liquidity = _number_or_none((pair.get("liquidity") or {}).get("usd"))
    dex_volume = _number_or_none((pair.get("volume") or {}).get("h24"))

    liquidity = dex_liquidity if dex_liquidity is not None else gecko_liquidity
    volume = dex_volume if dex_volume is not None else gecko_volume

    txns = (pair.get("txns") or {}).get("h24") or {}
    buys = _number_or_none(txns.get("buys"))
    sells = _number_or_none(txns.get("sells"))
    # Missing pair/transaction data is unknown, not verified zero.
    total_txns = (
        int(buys + sells)
        if buys is not None and sells is not None
        else None
    )

    market_cap = _number_or_none(pair.get("marketCap"))
    if market_cap is None:
        market_cap = gecko_market_cap
    fdv = _number_or_none(pair.get("fdv"))
    if fdv is None:
        fdv = gecko_fdv

    age_hours = None
    pair_created = pair.get("pairCreatedAt")
    if pair_created is not None:
        try:
            created_dt = datetime.fromtimestamp(
                float(pair_created) / 1000, tz=timezone.utc
            )
            age_hours = (now_utc() - created_dt).total_seconds() / 3600
            if age_hours < 0:
                age_hours = None
        except (TypeError, ValueError, OSError, OverflowError):
            pass

    if age_hours is None and gecko_created:
        try:
            text_created = str(gecko_created)
            if text_created.endswith("Z"):
                text_created = text_created[:-1] + "+00:00"
            created_dt = datetime.fromisoformat(text_created)
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=timezone.utc)
            age_hours = (
                now_utc() - created_dt.astimezone(timezone.utc)
            ).total_seconds() / 3600
            if age_hours < 0:
                age_hours = None
        except (TypeError, ValueError, OSError):
            pass

    # Retain every discovered market identifier, not only the highest-ranked
    # GeckoTerminal pool. These are candidate pool identifiers, not proof
    # that every matching token account is a vault.
    pool_addresses = {
        str(p.get("pairAddress"))
        for p in dex_pairs if p.get("pairAddress")
    }
    for gecko_pool in gecko_pools:
        attrs = gecko_pool.get("attributes") or {}
        address = attrs.get("address")
        if address:
            pool_addresses.add(str(address))

    base = pair.get("baseToken") or {}
    name = base.get("name") or "Unknown"
    symbol = base.get("symbol") or "UNKNOWN"
    pair_address = pair.get("pairAddress") or gecko_pool_address
    dex_name = pair.get("dexId") or (gp.get("dex_id") if gp else None)
    pair_url = pair.get("url")
    if not pair_url and gecko_pool_address:
        pair_url = f"https://www.geckoterminal.com/{network}/pools/{gecko_pool_address}"

    print(f"🔎 GeckoTerminal pools discovered: {len(gecko_pools)}")
    print(
        f"💧 Liquidity: {fmt_usd(liquidity)}"
        if liquidity is not None else "⚠️ Liquidity could not be verified."
    )

    return {
        "pair": pair, "pairs": dex_pairs, "gecko_pools": gecko_pools, "pair_address": pair_address,
        "pool_addresses": list(pool_addresses), "name": name, "symbol": symbol,
        "market_cap": market_cap, "fdv": fdv,
        "liquidity_usd": liquidity, "volume_24h": volume,
        "buys": int(buys) if buys is not None else None,
        "sells": int(sells) if sells is not None else None,
        "total_txns": total_txns, "pair_created_at": pair_created,
        "pool_created_at": gecko_created,
        "age_hours": age_hours, "dex": dex_name, "url": pair_url,
        "liquidity_source": (
            "DexScreener" if dex_liquidity is not None
            else "GeckoTerminal" if gecko_liquidity is not None else None
        ),
    }


def get_mint_info(mint):
    unavailable = {
        "available": False, "decimals": None, "raw_supply": None,
        "human_supply": None, "mint_authority": None,
        "freeze_authority": None,
    }
    result = helius_request(
        "getAccountInfo", [mint, {"encoding": "jsonParsed"}]
    )
    if not isinstance(result, dict):
        return unavailable
    value = result.get("value")
    if not isinstance(value, dict):
        return unavailable
    parsed = ((value.get("data") or {}).get("parsed") or {})
    info = parsed.get("info") or {}
    if not isinstance(info, dict) or "decimals" not in info or "supply" not in info:
        return unavailable
    try:
        decimals = int(info["decimals"])
        raw_supply = int(info["supply"])
        if decimals < 0 or raw_supply < 0:
            return unavailable
        human_supply = raw_supply / (10 ** decimals)
    except (TypeError, ValueError, OverflowError):
        return unavailable
    return {
        "available": True, "decimals": decimals, "raw_supply": raw_supply,
        "human_supply": human_supply,
        "mint_authority": info.get("mintAuthority"),
        "freeze_authority": info.get("freezeAuthority"),
    }


def get_holder_data(mint, excluded_addresses=None, mint_info=None):
    excluded = {str(x).lower() for x in (excluded_addresses or []) if x}
    all_accounts = []
    expected_total = None
    page = 1
    page_limit = 1000
    failed = False

    while page <= 10:
        result = helius_request(
            "getTokenAccounts",
            {"mint": mint, "page": page, "limit": page_limit, "displayOptions": {}},
        )
        if not isinstance(result, dict):
            failed = True
            break
        if page == 1:
            expected_total = _number_or_none(result.get("total"))
        accounts = result.get("token_accounts") or []
        if not isinstance(accounts, list):
            failed = True
            break
        print(f"🔎 Helius holder page {page}: {len(accounts)} accounts")
        all_accounts.extend(a for a in accounts if isinstance(a, dict))
        if len(accounts) < page_limit:
            break
        page += 1

    fetched = len(all_accounts)
    capped = page > 10 and (expected_total is None or expected_total > fetched)
    if expected_total is not None:
        complete = fetched >= expected_total and not failed
        status = "COMPLETE" if complete else "INCOMPLETE"
    else:
        complete = False
        status = "UNKNOWN — Helius total not supplied"
    if failed:
        status = "INCOMPLETE — one or more pages unavailable"
    if capped:
        status = "INCOMPLETE — pagination cap reached"

    holders = {}
    pool_accounts = []
    excluded_count = 0
    for account in all_accounts:
        owner = account.get("owner")
        address = account.get("address")
        amount = _number_or_none(account.get("amount"))
        if amount is None or amount <= 0:
            continue
        # Pool/pair address matching is a heuristic, not proof of LP ownership.
        if (owner and str(owner).lower() in excluded) or (
            address and str(address).lower() in excluded
        ):
            excluded_count += 1
            pool_accounts.append({
                "token_account": address, "owner": owner, "amount": amount,
                "classification": "possible pool account; heuristic match",
            })
            continue
        if owner:
            holders[owner] = holders.get(owner, 0) + amount

    sorted_holders = sorted(holders.items(), key=lambda x: x[1], reverse=True)
    total_real = sum(holders.values())
    total_pool = sum(x["amount"] for x in pool_accounts)

    raw_supply = None
    if isinstance(mint_info, dict) and mint_info.get("available") is True:
        raw_supply = _number_or_none(mint_info.get("raw_supply"))
    denominator = raw_supply
    if denominator is None and complete and not excluded:
        denominator = total_real + total_pool

    def pct(amount):
        if denominator is None or denominator <= 0:
            return None
        return amount / denominator * 100

    top1 = pct(sorted_holders[0][1]) if sorted_holders else (
        0.0 if complete and denominator else None
    )
    top5 = pct(sum(x[1] for x in sorted_holders[:5])) if sorted_holders else (
        0.0 if complete and denominator else None
    )
    top10 = pct(sum(x[1] for x in sorted_holders[:10])) if sorted_holders else (
        0.0 if complete and denominator else None
    )

    print(f"🔎 Token accounts fetched: {fetched}")
    print(f"🏊 Known pool-address matches (heuristic): {excluded_count}")
    print(f"📋 Holder data status: {status}")

    return {
        "holders": sorted_holders, "holder_count": len(sorted_holders),
        "top1_pct": top1, "top5_pct": top5, "top10_pct": top10,
        "pool_balance_raw": total_pool, "pool_pct": pct(total_pool),
        "pool_accounts": pool_accounts, "total_supply_raw": denominator,
        "all_accounts": all_accounts, "data_complete": complete,
        "data_status": status, "expected_accounts": expected_total,
        "fetched_accounts": fetched, "excluded_account_count": excluded_count,
    }


def get_pool_token_accounts(pool_addresses, holder_data):
    pool_lower = {str(x).lower() for x in pool_addresses if x}
    results = []
    for account in holder_data.get("all_accounts", []):
        owner = account.get("owner")
        address = account.get("address")
        owner_match = bool(owner and str(owner).lower() in pool_lower)
        address_match = bool(address and str(address).lower() in pool_lower)
        if not (owner_match or address_match):
            continue
        amount = _number_or_none(account.get("amount"))
        if amount is None:
            continue
        results.append({
            "token_account": address, "owner": owner,
            "amount_raw": amount,
            "match_type": "owner or address heuristic",
        })
    return results


def get_token_balance_deltas(transaction, mint):
    if not transaction:
        return []
    meta = transaction.get("meta") or {}
    pre = meta.get("preTokenBalances") or []
    post = meta.get("postTokenBalances") or []
    message = (transaction.get("transaction") or {}).get("message") or {}
    keys = message.get("accountKeys") or []
    account_keys = []
    for item in keys:
        if isinstance(item, str):
            account_keys.append(item)
        elif isinstance(item, dict):
            account_keys.append(item.get("pubkey"))

    before, after = {}, {}
    for collection, destination in ((pre, before), (post, after)):
        for item in collection:
            if item.get("mint") != mint:
                continue
            index = item.get("accountIndex")
            ui = item.get("uiTokenAmount") or {}
            raw = _number_or_none(ui.get("amount"))
            decimals = _number_or_none(ui.get("decimals"))
            if raw is None or decimals is None:
                continue
            destination[index] = {
                "owner": item.get("owner"),
                "amount": raw / (10 ** int(decimals)),
            }

    changes = []
    for index in set(before) | set(after):
        b, a = before.get(index, {}), after.get(index, {})
        # If an existing side is missing, it is a true zero only for a
        # newly created or closed token account in this transaction.
        delta = a.get("amount", 0.0) - b.get("amount", 0.0)
        if abs(delta) < 1e-12:
            continue
        account = (
            account_keys[index]
            if isinstance(index, int) and 0 <= index < len(account_keys)
            else None
        )
        changes.append({
            "account": account, "owner": a.get("owner") or b.get("owner"),
            "before": b.get("amount", 0.0),
            "after": a.get("amount", 0.0), "delta": delta,
        })
    return changes


def calculate_score(market, holders, mint_info):
    score = 0
    breakdown = []

    def add(label, points, detail):
        nonlocal score
        score += points
        sign = "+" if points > 0 else ""
        breakdown.append({
            "label": label, "points": points, "detail": detail,
            "display": f"{label}: {sign}{points} ({detail})",
        })

    mc = _number_or_none(market.get("market_cap"))
    age = _number_or_none(market.get("age_hours"))
    vol = _number_or_none(market.get("volume_24h"))
    txns = _number_or_none(market.get("total_txns"))
    liq = _number_or_none(market.get("liquidity_usd"))

    if mc is None:
        add("Market cap", 0, "unavailable; FDV not substituted")
    elif 1000 <= mc <= 2000:
        add("Market cap", 20, f"${mc:,.0f}")
    elif mc <= 4000 and mc > 2000:
        add("Market cap", 15, f"${mc:,.0f}")
    elif mc <= 7000 and mc > 4000:
        add("Market cap", 10, f"${mc:,.0f}")
    elif mc <= 10000 and mc > 7000:
        add("Market cap", 5, f"${mc:,.0f}")
    else:
        add("Market cap", 0, f"${mc:,.0f}; outside target bands")

    if age is None:
        add("Pair age", 0, "unavailable")
    elif age <= 0.5:
        add("Pair age", 15, f"{age:.1f} hours")
    elif age <= 1:
        add("Pair age", 12, f"{age:.1f} hours")
    elif age <= 6:
        add("Pair age", 8, f"{age:.1f} hours")
    elif age <= 24:
        add("Pair age", 3, f"{age:.1f} hours")
    else:
        add("Pair age", 0, f"{age:.1f} hours; older pair")

    if vol is None:
        add("24h volume", 0, "unavailable")
    elif vol >= 10000:
        add("24h volume", 15, f"${vol:,.2f}")
    elif vol >= 3000:
        add("24h volume", 10, f"${vol:,.2f}")
    elif vol >= 500:
        add("24h volume", 5, f"${vol:,.2f}")
    else:
        add("24h volume", 0, f"${vol:,.2f}; low activity")

    if txns is None:
        add("Transactions", 0, "unavailable")
    elif txns >= 100:
        add("Transactions", 10, str(int(txns)))
    elif txns >= 40:
        add("Transactions", 7, str(int(txns)))
    elif txns >= 15:
        add("Transactions", 3, str(int(txns)))
    else:
        add("Transactions", 0, f"{int(txns)}; limited activity")

    if liq is None:
        add("Liquidity", 0, "unavailable")
    elif liq < 1000:
        add("Liquidity", -10, f"${liq:,.2f}; very low")
    elif liq < 3000:
        add("Liquidity", -5, f"${liq:,.2f}; low")
    elif liq >= 10000:
        add("Liquidity", 5, f"${liq:,.2f}")
    else:
        add("Liquidity", 0, f"${liq:,.2f}")

    complete = holders.get("data_complete") is True
    count = holders.get("holder_count")
    top1 = _number_or_none(holders.get("top1_pct"))
    top10 = _number_or_none(holders.get("top10_pct"))

    if count is None or (count == 0 and not complete):
        add("Holder count", 0, "unverified/incomplete")
    elif count >= 50:
        add("Holder count", 10, f"{count} detected holders")
    elif count >= 20:
        add("Holder count", 7, f"{count} detected holders")
    elif count >= 10:
        add("Holder count", 3, f"{count} detected holders")
    elif count == 0:
        add("Holder count", -10, "zero holders in complete account result")
    else:
        add("Holder count", 0, f"{count} detected holders")

    concentration_verified = holders.get("concentration_verified") is True
    if not concentration_verified:
        add("Top holder concentration", 0, "unverified; possible market/pool account included")
    elif top1 is None:
        add("Top holder concentration", 0, "unavailable")
    elif top1 >= 50:
        add("Top holder concentration", -25, f"{top1:.2f}%")
    elif top1 >= 30:
        add("Top holder concentration", -20, f"{top1:.2f}%")
    elif top1 >= 20:
        add("Top holder concentration", -12, f"{top1:.2f}%")
    elif top1 >= 10:
        add("Top holder concentration", -5, f"{top1:.2f}%")
    else:
        add("Top holder concentration", 0, f"{top1:.2f}%")

    if not concentration_verified:
        add("Top 10 concentration", 0, "unverified; possible market/pool account included")
    elif top10 is None:
        add("Top 10 concentration", 0, "unavailable")
    elif top10 >= 80:
        add("Top 10 concentration", -20, f"{top10:.2f}%")
    elif top10 >= 60:
        add("Top 10 concentration", -15, f"{top10:.2f}%")
    elif top10 >= 40:
        add("Top 10 concentration", -8, f"{top10:.2f}%")
    else:
        add("Top 10 concentration", 0, f"{top10:.2f}%")

    if mint_info.get("available") is not True:
        add("Mint authority", 0, "check unavailable")
        add("Freeze authority", 0, "check unavailable")
    else:
        if mint_info.get("mint_authority") is None:
            add("Mint authority", 5, "revoked")
        else:
            add("Mint authority", -10, "active")
        if mint_info.get("freeze_authority") is None:
            add("Freeze authority", 5, "revoked")
        else:
            add("Freeze authority", -10, "active")

    creator = market.get("_creator_data") or {}
    risk = str(creator.get("risk", "UNKNOWN")).upper()
    if risk == "HIGH":
        add("Creator risk", -15, "high-risk indicators detected")
    elif risk == "MEDIUM":
        add("Creator risk", -7, "some risk indicators detected")
    elif risk == "LOW":
        add("Creator risk", 0, "no high-risk indicator from current checks")
    else:
        add("Creator risk", 0, "unverified")

    origin = market.get("_pool_origin_data") or {}
    link = origin.get("creator_link")
    if link is True:
        add("Creator-to-pool link", -3, "direct link candidate; not proof of wrongdoing")
    elif link is False:
        add("Creator-to-pool link", 0, "no direct link detected in checked data")
    else:
        add("Creator-to-pool link", 0, "unknown")

    final = max(0, min(100, score))
    market["_score_breakdown"] = breakdown
    market["_score_raw"] = score
    market["_score_final"] = final
    return final


def print_market_report(market):
    print("\\n💰 MARKET DATA")
    print(f"💰 Market Cap: {fmt_usd(market.get('market_cap'))}")
    print(f"📐 FDV: {fmt_usd(market.get('fdv'))}")
    print(f"💧 Liquidity: {fmt_usd(market.get('liquidity_usd'))}")
    print(f"ℹ️ Liquidity source: {market.get('liquidity_source') or 'Unavailable'}")
    print(f"🔎 Candidate market identifiers: {len(market.get('pool_addresses') or [])}")
    age = market.get("age_hours")
    print(f"⏱ Selected pair age: {age:.1f} hours" if age is not None else "⏱ Selected pair age: Unavailable")
    print("ℹ️ Pair age does not establish the token's actual creation age.")

    creation = market.get("_creation_info") or {}
    mint_time = creation.get("block_time")
    if creation.get("verified") is True and mint_time:
        try:
            created_dt = datetime.fromtimestamp(int(mint_time), tz=timezone.utc)
            token_age_hours = (now_utc() - created_dt).total_seconds() / 3600
            print(f"🪙 Mint initialization: {created_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            print(f"🕒 Time since mint initialization: {token_age_hours:.1f} hours")
            print("✅ Timestamp is tied to a parsed InitializeMint instruction.")
        except (TypeError, ValueError, OSError, OverflowError):
            print("🪙 Mint initialization: Timestamp unavailable")
    else:
        print("🪙 Mint initialization: UNVERIFIED")
        print(f"ℹ️ Reason: {creation.get('reason') or 'No confirmed mint-initialization instruction found'}")

    pool_created = market.get("pool_created_at")
    if pool_created:
        print(f"🏊 GeckoTerminal pool creation: {pool_created}")
        print("ℹ️ Pool creation time is separate from mint initialization.")
    else:
        print("🏊 GeckoTerminal pool creation: Unavailable")

    print(f"📊 24H Volume: {fmt_usd(market.get('volume_24h'))}")
    for label, key in (("🟢 Buys", "buys"), ("🔴 Sells", "sells"), ("🔄 Total Txns", "total_txns")):
        value = market.get(key)
        print(f"{label}: {value if value is not None else 'Unavailable'}")


def print_holder_report(holders):
    print("\n👥 HOLDER DISTRIBUTION")
    print(f"📋 Data status: {holders.get('data_status', 'Unknown')}")
    print(f"📥 Accounts fetched: {holders.get('fetched_accounts', 'Unknown')}")
    print(f"📥 Expected accounts: {holders.get('expected_accounts', 'Unknown')}")
    print(f"👤 Token-account owners detected: {holders.get('holder_count', 'Unknown')}")
    print(f"🏊 Possible pool share: {_format_pct(holders.get('pool_pct'))}")
    rows = holders.get("holders") or []
    if not rows:
        print("🥇 Top Real Holder: Unavailable")
        return
    print(f"🥇 Top Holder Owner (pool status unverified): {short_address(rows[0][0])} ({_format_pct(holders.get('top1_pct'))})")
    print(f"🥈 Top 5 Holder Owners: {_format_pct(holders.get('top5_pct'))}")
    print(f"🥉 Top 10 Holder Owners: {_format_pct(holders.get('top10_pct'))}")


def print_security_report(mint_info):
    print("\n🔐 TOKEN SECURITY")
    if mint_info.get("available") is not True:
        print("🔢 Supply: CHECK UNAVAILABLE")
        print("🪙 Mint Authority: CHECK UNAVAILABLE")
        print("❄️ Freeze Authority: CHECK UNAVAILABLE")
        return
    print(f"🔢 Supply: {fmt_number(mint_info.get('human_supply'))}")
    print("🪙 Mint Authority: " + ("🟢 REVOKED" if mint_info.get("mint_authority") is None else "🔴 ACTIVE"))
    print("❄️ Freeze Authority: " + ("🟢 REVOKED" if mint_info.get("freeze_authority") is None else "🔴 ACTIVE"))


def print_creator_report(creator, mint, mint_info):
    print("\n👤 CREATOR / DEPLOYER")
    if not creator:
        print("🧑 Likely Creator: Unverified")
        creation = getattr(print_creator_report, "_creation_info", {}) or {}
        if creation.get("verified"):
            print(f"🔐 Mint Initialization Authority: {creation.get('initialization_authority') or 'Unavailable'}")
            print(f"💸 Initialization Fee Payer: {creation.get('fee_payer') or 'Unavailable'}")
            print("ℹ️ These addresses are evidence only; neither is confirmed as the project creator.")
            print(f"🧾 Initialization Tx: {creation.get('signature') or 'Unavailable'}")
        else:
            print(f"ℹ️ Evidence: {creation.get('reason') or 'Mint initialization not verified'}")
        return {
            "creator": None, "creator_supply_pct": None,
            "balance_checked": False, "token_activity": "Unknown",
            "creator_token_activity": "Unknown", "risk": "UNKNOWN",
        }

    balance = get_wallet_token_balance(creator, mint)
    raw_supply = mint_info.get("raw_supply") if mint_info.get("available") is True else None
    balance_checked = (
        isinstance(balance, (int, float))
        and not isinstance(balance, bool)
        and raw_supply is not None
        and float(raw_supply) > 0
        and float(balance) >= 0
    )
    creator_pct = float(balance) / float(raw_supply) * 100 if balance_checked else None

    sol_balance = get_sol_balance(creator)
    history = get_wallet_history(creator) or {}
    activity = str(history.get("token_activity") or "Unknown")
    unknown = activity.strip().lower() in ("", "unknown", "unavailable", "not checked", "none")

    if unknown or creator_pct is None:
        risk = "UNVERIFIED"
    elif creator_pct >= 20:
        risk = "HIGH"
    elif creator_pct >= 5:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    print(f"🧑 Likely Creator (unverified): {creator}")
    print(f"💰 Creator SOL: {sol_balance:.4f} SOL" if sol_balance is not None else "💰 Creator SOL: Unavailable")
    print(f"📊 Creator Supply: {creator_pct:.2f}%" if creator_pct is not None else "📊 Creator Supply: Unverified")
    print(f"📜 Creator Recent Txns: {history.get('count', 'Unknown')}")
    print(f"🪙 Creator Token Activity: {activity}")
    print(f"🧠 Creator Risk: {risk}")
    return {
        "creator": creator, "creator_supply_pct": creator_pct,
        "balance_checked": balance_checked, "token_activity": activity,
        "creator_token_activity": activity, "risk": risk,
    }


def trace_pool_origin(mint, pool_token_accounts, creator):
    """Find an observed incoming token movement to a candidate pool account.

    Reports the earliest incoming movement found in a bounded signature window;
    it never claims this is the first-ever pool funding event.
    """
    print("\n🏊 Tracing pool token origin...")
    if not pool_token_accounts:
        return {"status": "UNKNOWN", "method": "none", "pool_token_account": None,
                "pool_owner": None, "source": None, "source_type": None,
                "amount": None, "signature": None, "block_time": None,
                "creator_link": None, "events_checked": 0, "confidence": "none",
                "reason": "No candidate pool token accounts identified"}

    targets = sorted(pool_token_accounts,
                     key=lambda x: _number_or_none(x.get("amount_raw")) or 0,
                     reverse=True)
    target = targets[0]
    token_account = target.get("token_account")
    pool_owner = target.get("owner")
    if not token_account:
        return {"status": "UNKNOWN", "method": "raw paginated history",
                "pool_token_account": None, "pool_owner": pool_owner,
                "source": None, "source_type": None, "amount": None,
                "signature": None, "block_time": None, "creator_link": None,
                "events_checked": 0, "confidence": "none",
                "reason": "Pool token account address unavailable"}

    signatures, before, exhausted = [], None, False
    for _ in range(5):  # up to 500 signatures, oldest-first after pagination
        config = {"limit": 100}
        if before:
            config["before"] = before
        page = helius_request("getSignaturesForAddress", [token_account, config])
        if not isinstance(page, list) or not page:
            exhausted = True
            break
        signatures.extend(page)
        before = page[-1].get("signature")
        if len(page) < 100:
            exhausted = True
            break
        if not before:
            break

    signatures.sort(key=lambda x: x.get("blockTime") or 0)
    checked = 0
    for sig_info in signatures:
        signature = sig_info.get("signature")
        if not signature:
            continue
        tx = helius_request("getTransaction", [signature, {
            "encoding": "jsonParsed", "commitment": "confirmed",
            "maxSupportedTransactionVersion": 0,
        }])
        if not isinstance(tx, dict) or (tx.get("meta") or {}).get("err") is not None:
            continue
        checked += 1
        deltas = get_token_balance_deltas(tx, mint)
        incoming = [d for d in deltas if d.get("delta", 0) > 0 and d.get("account") == token_account]
        if not incoming:
            continue
        # Match a same-transaction debit of approximately the received amount.
        received = incoming[0]
        debits = [d for d in deltas if d.get("delta", 0) < 0]
        source_row = min(debits, key=lambda d: abs(abs(d["delta"]) - received["delta"])) if debits else None
        source = source_row.get("owner") if source_row else None
        creator_link = source.lower() == creator.lower() if source and creator else None
        return {
            "status": "CANDIDATE", "method": "raw paginated history",
            "pool_token_account": token_account, "pool_owner": pool_owner,
            "source": source,
            "source_type": "CREATOR CANDIDATE" if creator_link is True else ("OTHER WALLET CANDIDATE" if source else "UNKNOWN"),
            "amount": received["delta"], "signature": signature,
            "block_time": tx.get("blockTime") or sig_info.get("blockTime"),
            "creator_link": creator_link, "destination": token_account,
            "events_checked": checked, "confidence": "candidate",
            "history_signatures_scanned": len(signatures),
            "history_exhausted": exhausted,
            "reason": "Earliest matching incoming movement found in scanned history; not proof of first-ever pool funding",
        }

    return {
        "status": "NO_CLEAR_FUNDING", "method": "raw paginated history",
        "pool_token_account": token_account, "pool_owner": pool_owner,
        "source": None, "source_type": None, "amount": None,
        "signature": None, "block_time": None, "creator_link": None,
        "events_checked": checked, "confidence": "limited",
        "history_signatures_scanned": len(signatures),
        "history_exhausted": exhausted,
        "reason": "No matching incoming token movement found in scanned history",
    }



# ============================================================
# MYSTERIOUS TOKEN CHECKER — CONSERVATIVE DATA-QUALITY UPGRADE
# ============================================================

# Keep the previous implementation available for comparison/debugging.
_previous_get_market_data = get_market_data
_previous_print_market_report = print_market_report


def get_market_data(chain, mint):
    """Collect market candidates and retain per-market evidence."""
    market = _previous_get_market_data(chain, mint)
    if not isinstance(market, dict):
        return market

    # The previous collector already discovers and ranks these source lists.
    # Preserve the selected values, while exposing the candidate list that is
    # available in the returned structure. Do not pretend identifiers are vaults.
    candidates = []
    for pair in market.get("pairs") or []:
        liq = _number_or_none((pair.get("liquidity") or {}).get("usd"))
        vol = _number_or_none((pair.get("volume") or {}).get("h24"))
        tx = (pair.get("txns") or {}).get("h24") or {}
        buys = _number_or_none(tx.get("buys"))
        sells = _number_or_none(tx.get("sells"))
        created = _number_or_none(pair.get("pairCreatedAt"))
        age = None
        if created is not None:
            try:
                age = max(0.0, (now_utc() - datetime.fromtimestamp(created / 1000, tz=timezone.utc)).total_seconds() / 3600)
            except (ValueError, OSError, OverflowError):
                pass
        candidates.append({
            "source": "DexScreener",
            "address": pair.get("pairAddress"),
            "dex": pair.get("dexId"),
            "liquidity_usd": liq,
            "volume_24h": vol,
            "buys": int(buys) if buys is not None else None,
            "sells": int(sells) if sells is not None else None,
            "age_hours": age,
            "url": pair.get("url"),
        })
    for pool in market.get("gecko_pools") or []:
        attrs = pool.get("attributes") or {}
        volume_obj = attrs.get("volume_usd") or {}
        created_text = attrs.get("pool_created_at")
        age = None
        if created_text:
            try:
                parsed = str(created_text)
                if parsed.endswith("Z"):
                    parsed = parsed[:-1] + "+00:00"
                created_dt = datetime.fromisoformat(parsed)
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                age = max(0.0, (now_utc() - created_dt.astimezone(timezone.utc)).total_seconds() / 3600)
            except (TypeError, ValueError, OSError):
                pass
        candidates.append({
            "source": "GeckoTerminal",
            "address": attrs.get("address"),
            "dex": attrs.get("dex_id"),
            "liquidity_usd": _number_or_none(attrs.get("reserve_in_usd")),
            "volume_24h": _number_or_none(volume_obj.get("h24")),
            "buys": None, "sells": None, "age_hours": age,
            "url": (f"https://www.geckoterminal.com/solana/pools/{attrs.get('address')}" if attrs.get("address") else None),
        })
    market["market_candidates"] = candidates
    market["market_candidate_count"] = len(candidates)
    return market


def inspect_market_account(address, mint):
    """Inspect a market identifier through Solana RPC without guessing its role.

    A parsed SPL token account can be identified as holding this mint, but it
    is NOT called an AMM vault unless a venue-specific pool-state adapter
    independently proves that relationship.
    """
    if not address:
        return {"address": address, "status": "UNKNOWN", "reason": "Missing address"}
    result = helius_request("getAccountInfo", [
        address,
        {"encoding": "jsonParsed", "commitment": "confirmed"},
    ])
    value = (result or {}).get("value") if isinstance(result, dict) else None
    if not isinstance(value, dict):
        return {"address": address, "status": "UNAVAILABLE", "reason": "RPC account data unavailable"}
    owner_program = value.get("owner")
    data = value.get("data")
    parsed = data.get("parsed") if isinstance(data, dict) else None
    info = (parsed or {}).get("info") if isinstance(parsed, dict) else None
    program_type = (parsed or {}).get("type") if isinstance(parsed, dict) else None
    if isinstance(info, dict) and info.get("mint"):
        same_mint = str(info.get("mint")).lower() == str(mint).lower()
        return {
            "address": address,
            "status": "TOKEN_ACCOUNT_FOR_MINT" if same_mint else "TOKEN_ACCOUNT_OTHER_MINT",
            "owner_program": owner_program,
            "token_authority": info.get("owner"),
            "mint": info.get("mint"),
            "amount": ((info.get("tokenAmount") or {}).get("amount")),
            "program_type": program_type,
            "is_executable": bool(value.get("executable")),
            "vault_verified": False,
            "reason": "SPL token account parsed; AMM vault relationship not yet proven",
        }
    return {
        "address": address,
        "status": "PROGRAM_ACCOUNT" if value.get("executable") or owner_program else "UNCLASSIFIED_ACCOUNT",
        "owner_program": owner_program,
        "is_executable": bool(value.get("executable")),
        "vault_verified": False,
        "reason": "Account owner/program inspected; venue-specific pool layout not decoded",
    }


def get_holder_data(mint, excluded_addresses=None, mint_info=None):
    """Count token-account owners without excluding unverified pool matches.

    Market pair/pool identifiers are not sufficient proof that an account is
    a liquidity vault. Therefore no address is excluded from concentration
    calculations unless a future verified AMM-specific resolver supplies it.
    """
    all_accounts = []
    expected_total = None
    page = 1
    page_limit = 1000
    failed = False
    while page <= 10:
        result = helius_request(
            "getTokenAccounts",
            {"mint": mint, "page": page, "limit": page_limit, "displayOptions": {}},
        )
        if not isinstance(result, dict):
            failed = True
            break
        if page == 1:
            expected_total = _number_or_none(result.get("total"))
        accounts = result.get("token_accounts") or []
        if not isinstance(accounts, list):
            failed = True
            break
        print(f"🔎 Helius holder page {page}: {len(accounts)} accounts")
        all_accounts.extend(a for a in accounts if isinstance(a, dict))
        if len(accounts) < page_limit:
            break
        page += 1

    fetched = len(all_accounts)
    capped = page > 10 and (expected_total is None or expected_total > fetched)
    if expected_total is not None:
        complete = fetched >= expected_total and not failed and not capped
        status = "COMPLETE" if complete else "INCOMPLETE"
    else:
        complete = False
        status = "UNKNOWN — Helius total not supplied"
    if failed:
        status = "INCOMPLETE — one or more pages unavailable"
    if capped:
        status = "INCOMPLETE — pagination cap reached"

    holders = {}
    for account in all_accounts:
        owner = account.get("owner")
        amount = _number_or_none(account.get("amount"))
        if not owner or amount is None or amount <= 0:
            continue
        holders[owner] = holders.get(owner, 0.0) + amount

    sorted_holders = sorted(holders.items(), key=lambda x: x[1], reverse=True)
    mint_supply = None
    if isinstance(mint_info, dict) and mint_info.get("available") is True:
        mint_supply = _number_or_none(mint_info.get("raw_supply"))
    denominator = mint_supply
    denominator_source = "on-chain mint supply" if mint_supply and mint_supply > 0 else None
    if denominator is None and complete:
        denominator = sum(holders.values())
        denominator_source = "sum of fetched token-account balances; mint supply unavailable"

    def pct(amount):
        if denominator is None or denominator <= 0:
            return None
        return amount / denominator * 100

    top1 = pct(sorted_holders[0][1]) if sorted_holders else (0.0 if complete and denominator else None)
    top5 = pct(sum(x[1] for x in sorted_holders[:5])) if sorted_holders else (0.0 if complete and denominator else None)
    top10 = pct(sum(x[1] for x in sorted_holders[:10])) if sorted_holders else (0.0 if complete and denominator else None)

    # All possible pool matches remain visible, but a market-address owner
    # match makes ordinary-holder concentration unverified until AMM metadata
    # proves whether it is a vault/PDA or a normal wallet.
    possible_pool_matches = []
    candidate_address_map = {
        str(x).lower(): str(x) for x in (excluded_addresses or []) if x
    }
    candidate_set = set(candidate_address_map)
    for account in all_accounts:
        owner = str(account.get("owner") or "").lower()
        address = str(account.get("address") or "").lower()
        if owner in candidate_set or address in candidate_set:
            possible_pool_matches.append({
                "token_account": account.get("address"),
                "owner": account.get("owner"),
                "amount": _number_or_none(account.get("amount")),
                "classification": "UNVERIFIED MARKET-ADDRESS MATCH; INCLUDED IN HOLDER METRICS",
            })

    # Inspect candidate market identifiers directly. This is diagnostic only:
    # no balance is excluded until a venue-specific AMM adapter verifies it.
    market_account_checks = []
    for normalized_address in sorted(candidate_set)[:10]:
        # Solana addresses are case-sensitive Base58 strings. Preserve the
        # original spelling for RPC calls; lowercase is only for comparisons.
        address = candidate_address_map[normalized_address]
        market_account_checks.append(inspect_market_account(address, mint))
    if market_account_checks:
        print("\n🔬 MARKET ACCOUNT VERIFICATION")
        for check in market_account_checks:
            print(
                f"• {short_address(check.get('address'))}: {check.get('status')}"
                f" | Program {short_address(check.get('owner_program'))}"
                f" | Vault verified: NO"
            )
            print(f"  Reason: {check.get('reason')}")

    # Cross-check each matching holder token account against its candidate
    # market identifier. This can establish an on-chain authority link, but
    # does not by itself prove that the token account is the AMM's reserve vault.
    market_state_by_address = {
        str(item.get("address") or "").lower(): item
        for item in market_account_checks
    }
    controlled_account_checks = []
    for match in possible_pool_matches[:10]:
        token_account = match.get("token_account")
        market_owner = str(match.get("owner") or "").lower()
        if not token_account or not market_owner:
            continue
        token_check = inspect_market_account(token_account, mint)
        state_check = market_state_by_address.get(market_owner)
        authority_matches = (
            token_check.get("status") == "TOKEN_ACCOUNT_FOR_MINT"
            and str(token_check.get("token_authority") or "").lower() == market_owner
        )
        state_is_program_owned = bool(
            state_check
            and state_check.get("status") == "PROGRAM_ACCOUNT"
            and state_check.get("owner_program")
        )
        if authority_matches and state_is_program_owned:
            relation = "PROGRAM-OWNED MARKET STATE CONTROLS TOKEN ACCOUNT; VAULT ROLE UNVERIFIED"
        elif authority_matches:
            relation = "TOKEN AUTHORITY MATCHES MARKET IDENTIFIER; MARKET STATE TYPE UNVERIFIED"
        else:
            relation = "NO CONFIRMED MARKET-AUTHORITY LINK"
        controlled_account_checks.append({
            "token_account": token_account,
            "market_identifier": match.get("owner"),
            "token_account_check": token_check,
            "market_state_check": state_check,
            "authority_matches": authority_matches,
            "market_state_program_owned": state_is_program_owned,
            "relation": relation,
            "vault_verified": False,
        })

    if controlled_account_checks:
        print("\n🧩 MARKET ↔ TOKEN-ACCOUNT AUTHORITY CHECK")
        for item in controlled_account_checks:
            token_check = item.get("token_account_check") or {}
            print(
                f"• Token account {short_address(item.get('token_account'))}"
                f" | Mint match: {token_check.get('status') == 'TOKEN_ACCOUNT_FOR_MINT'}"
                f" | Authority match: {item.get('authority_matches')}"
            )
            print(f"  Finding: {item.get('relation')}")
            print("  Safety: balance remains included; AMM vault not proven.")

    market_owner_matches = {
        str(item.get("owner") or "").lower()
        for item in possible_pool_matches if item.get("owner")
    }
    top_owner_is_market_candidate = bool(
        sorted_holders and sorted_holders[0][0].lower() in market_owner_matches
    )
    concentration_status = (
        "UNVERIFIED — top token-account owner matches a market identifier"
        if top_owner_is_market_candidate else
        "PROVISIONAL — market-like accounts remain included; no verified AMM vault resolver"
        if possible_pool_matches else
        "PROVISIONAL — no AMM-specific vault verification"
    )

    print(f"🔎 Token accounts fetched: {fetched}")
    print(f"🏊 Possible market-address matches (not excluded): {len(possible_pool_matches)}")
    print(f"📋 Holder data status: {status}")
    print(f"ℹ️ Concentration status: {concentration_status}")
    if possible_pool_matches:
        print("ℹ️ Market-like accounts remain included; concentration must not be interpreted as confirmed individual-wallet ownership.")

    return {
        "market_account_checks": market_account_checks,
        "controlled_account_checks": controlled_account_checks,
        "holders": sorted_holders,
        "holder_count": len(sorted_holders),
        "top1_pct": top1, "top5_pct": top5, "top10_pct": top10,
        "pool_balance_raw": None, "pool_pct": None,
        "pool_accounts": [], "possible_pool_matches": possible_pool_matches,
        "total_supply_raw": denominator, "denominator_source": denominator_source,
        "all_accounts": all_accounts, "data_complete": complete,
        "data_status": status, "expected_accounts": expected_total,
        "fetched_accounts": fetched, "excluded_account_count": 0,
        "pool_classification_status": "UNVERIFIED — no AMM-specific vault verification",
        "concentration_status": concentration_status,
        "concentration_verified": not top_owner_is_market_candidate and not possible_pool_matches,
        "top_owner_is_market_candidate": top_owner_is_market_candidate,
    }


def get_pool_token_accounts(pool_addresses, holder_data):
    """Do not trace origin from an unverified pair-address heuristic."""
    candidates = holder_data.get("possible_pool_matches") or []
    if candidates:
        print("🏊 Pool-origin tracing paused: candidate market-address matches are not verified vaults.")
    return []


def print_market_report(market):
    _previous_print_market_report(market)
    candidates = market.get("market_candidates") or []
    print("\n🔎 DISCOVERED MARKET CANDIDATES")
    if not candidates:
        print("• No DexScreener pair details available for comparison.")
    else:
        for index, item in enumerate(candidates[:10], 1):
            age = item.get("age_hours")
            age_text = f"{age:.1f}h" if age is not None else "age unavailable"
            print(
                f"{index}. {item.get('source')} / {item.get('dex') or 'DEX unknown'}"
                f" | Liquidity {fmt_usd(item.get('liquidity_usd'))}"
                f" | 24h volume {fmt_usd(item.get('volume_24h'))}"
                f" | Txns {('unavailable' if item.get('buys') is None or item.get('sells') is None else item.get('buys') + item.get('sells'))}"
                f" | Age {age_text}"
            )
    print("ℹ️ Candidate markets are compared as separate pools; market age is not token age.")


def get_verdict(score, market):
    """Hard risk gates take precedence over a weighted research score."""
    liquidity = _number_or_none(market.get("liquidity_usd"))
    volume = _number_or_none(market.get("volume_24h"))
    txns = _number_or_none(market.get("total_txns"))
    if liquidity is None:
        return "🔴 LOW PRIORITY — LIQUIDITY UNVERIFIED"
    if liquidity < 1000:
        return "🔴 LOW PRIORITY — CRITICAL LIQUIDITY (<$1K)"
    if volume is not None and txns is not None and volume < 100 and txns < 5:
        return "🔴 LOW PRIORITY — INSUFFICIENT MARKET ACTIVITY"
    if score >= 70:
        return "🟢 WATCHLIST CANDIDATE"
    if score >= 50:
        return "🟡 NEEDS FURTHER RESEARCH"
    return "🔴 LOW PRIORITY — SKIP FOR NOW"


if __name__ == "__main__":
    main()
