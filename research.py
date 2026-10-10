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

    signatures = helius_request(
        "getSignaturesForAddress",
        [
            mint,
            {
                "limit": 100
            }
        ]
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

    keys = (
        message.get(
            "accountKeys"
        )
        or []
    )

    creator = None

    for item in keys:

        if isinstance(
            item,
            dict
        ):

            if item.get(
                "signer"
            ):

                creator = item.get(
                    "pubkey"
                )

                break

    return {
        "creator": creator,
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

    market_cap = market.get("market_cap")
    age_hours = market.get("age_hours")
    volume_raw = market.get("volume_24h")
    txns_raw = market.get("total_txns")

    market_cap = (
        safe_float(market_cap)
        if market_cap is not None
        else None
    )

    volume = (
        safe_float(volume_raw)
        if volume_raw is not None
        else None
    )

    txns = (
        int(txns_raw)
        if txns_raw is not None
        else None
    )

    # --------------------------------------------------------
    # MARKET CAP
    # --------------------------------------------------------

    if market_cap is not None:

        if 1000 <= market_cap <= 2000:
            score += 20

        elif market_cap <= 4000 and market_cap > 2000:
            score += 15

        elif market_cap <= 7000 and market_cap > 4000:
            score += 10

        elif market_cap <= 10000 and market_cap > 7000:
            score += 5

    # --------------------------------------------------------
    # PAIR AGE
    # Newness is context, not proof of quality.
    # --------------------------------------------------------

    if age_hours is not None and age_hours >= 0:

        if age_hours <= 0.5:
            score += 15

        elif age_hours <= 1:
            score += 12

        elif age_hours <= 6:
            score += 8

        elif age_hours <= 24:
            score += 3

    # --------------------------------------------------------
    # TRADING ACTIVITY
    # Low activity earns no points.
    # --------------------------------------------------------

    if volume is not None and volume >= 0:

        if volume >= 10000:
            score += 15

        elif volume >= 3000:
            score += 10

        elif volume >= 500:
            score += 5

    if txns is not None and txns >= 0:

        if txns >= 100:
            score += 10

        elif txns >= 40:
            score += 7

        elif txns >= 15:
            score += 3

    # --------------------------------------------------------
    # HOLDER DISTRIBUTION
    # --------------------------------------------------------

    holder_count = holders.get("holder_count", 0)
    top1 = holders.get("top1_pct", 0)
    top10 = holders.get("top10_pct", 0)

    holder_count = int(
        safe_float(holder_count)
    )

    top1 = safe_float(top1)
    top10 = safe_float(top10)

    if holder_count >= 50:
        score += 10

    elif holder_count >= 20:
        score += 7

    elif holder_count >= 10:
        score += 3

    if holder_count > 0:

        # Concentration in the largest detected holder
        if top1 >= 50:
            score -= 25

        elif top1 >= 30:
            score -= 20

        elif top1 >= 20:
            score -= 12

        elif top1 >= 10:
            score -= 5

        # Concentration among the largest ten holders
        if top10 >= 80:
            score -= 20

        elif top10 >= 60:
            score -= 15

        elif top10 >= 40:
            score -= 8

    else:
        # No detected holders is uncertainty, not proof of safety.
        score -= 10

    # --------------------------------------------------------
    # TOKEN AUTHORITIES
    # --------------------------------------------------------

    if mint_info.get("mint_authority") is None:
        score += 5
    else:
        score -= 10

    if mint_info.get("freeze_authority") is None:
        score += 5
    else:
        score -= 10

    return max(0, min(100, score))
 


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
        market.get(
            "total_txns"
        )
        or 0
    )

    market_cap = safe_float(
        market.get(
            "market_cap"
        )
    )

    volume = safe_float(
        market.get(
            "volume_24h"
        )
    )

    if (
        txns > 1500
        or volume > 100000
        or market_cap > 10000
        or (
            age_hours is not None
            and age_hours > 24
        )
    ):
        return "🔴 IGNORE"

    if score >= 70:
        return "🟢 WATCH"

    if score >= 50:
        return "🟡 CAUTION"

    return "🔴 IGNORE"


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
            "creator_supply_pct": 0,
            "risk": "UNKNOWN",
        }

    balance = get_wallet_token_balance(
        creator,
        mint
    )

    raw_supply = safe_float(
        mint_info.get(
            "raw_supply"
        )
    )

    creator_pct = 0

    if (
        balance is not None
        and raw_supply > 0
    ):

        creator_pct = (
            balance
            / raw_supply
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
        "creator_supply_pct": creator_pct,
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

    if status == "FOUND":

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

    if chain not in SUPPORTED_CHAINS:

        print(
            "❌ Unsupported chain."
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
            "\n❌ Could not retrieve "
            "market data."
        )

        sys.exit(1)

    symbol = str(
        market.get(
            "symbol"
        )
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
        market.get(
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
        creator
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
        holder_data
    )

    print_security_report(
        mint_info
    )

    creator_data = (
        print_creator_report(
            creator,
            mint,
            mint_info
        )
    )

    print_pool_report(
        pool_origin,
        market
    )

    # --------------------------------------------------------
    #     # TOP HOLDER
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
            "ℹ️ No real top holder "
            "to analyze."
        )

        if holder_data.get(
            "pool_pct",
            0
        ) > 0:

            print(
                "🏊 The detected supply "
                "is associated with "
                "the known pool/LP address."
            )

    else:

        top_wallet = (
            real_holders[0][0]
        )

        print(
            f"🐋 Wallet: "
            f"{top_wallet}"
        )

        print(
            f"📊 Supply: "
            f"{holder_data.get('top1_pct', 0):.2f}%"
        )

        decimals = mint_info.get(
            "decimals",
            0
        )

        token_balance = (
            real_holders[0][1]
            / (10 ** decimals)
        )

        print(
            f"🪙 Balance: "
            f"{fmt_number(token_balance)}"
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
            "ℹ️ Creator/top-holder "
            "relationship cannot "
            "be determined."
        )

    else:

        top_wallet = (
            real_holders[0][0]
        )

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
                "🟢 Creator is not "
                "the top real holder."
            )

    # --------------------------------------------------------
    # POSITIVES
    # --------------------------------------------------------

    positives = []

    mc = safe_float(
        market.get(
            "market_cap"
        )
    )

    volume = safe_float(
        market.get(
            "volume_24h"
        )
    )

    txns = int(
        market.get(
            "total_txns"
        )
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

    if (
        pool_origin.get(
            "creator_link"
        )
        is False
    ):

        positives.append(
            "creator not directly "
            "linked to initial pool funding"
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

    elif safe_float(
        liquidity
    ) < 1000:

        warnings.append(
            "very low liquidity"
        )

    if holder_data.get(
        "holder_count",
        0
    ) == 0:

        warnings.append(
            "no real holders yet; "
            "supply is currently "
            "in pool/LP"
        )

    if (
        pool_origin.get(
            "creator_link"
        )
        is True
    ):

        warnings.append(
            "creator directly linked "
            "to initial pool funding"
        )

    if (
        creator_data.get(
            "creator_supply_pct",
            0
        ) >= 20
    ):

        warnings.append(
            "creator holds "
            "significant supply"
        )

    if (
        pool_origin.get(
            "status"
        )
        == "UNKNOWN"
    ):

        warnings.append(
            "pool token origin "
            "could not be verified"
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
    # FINAL VERDICT
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

    if (
        holder_data.get(
            "holder_count",
            0
        )
        == 0
    ):

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

    print(
        "\n🧠 RESEARCH VERDICT"
    )

    print(
        f"🔐 Security: "
        f"{security}"
    )

    print(
        f"👥 Holder Risk: "
        f"{holder_risk}"
    )

    print(
        f"👤 Creator Risk: "
        f"{creator_data.get('risk', 'UNKNOWN')}"
    )

    print(
        "🐋 Top Holder Risk: "
        + (
            "ANALYZED"
            if real_holders
            else "N/A — pool excluded"
        )
    )

    print(
        f"🏊 Pool Origin: "
        f"{pool_origin.get('status', 'UNKNOWN')}"
    )

    print(
        f"🚦 FINAL: "
        f"{verdict}"
    )

    print(
        "\n🥷 RESEARCH COMPLETED"
    )


if __name__ == "__main__":
    main()
