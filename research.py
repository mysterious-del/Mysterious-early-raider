import os
import sys
import requests
from collections import defaultdict
from datetime import datetime, timezone


DEX_BASE = "https://api.dexscreener.com"
HELIUS_API = "https://mainnet.helius-rpc.com/"

MAX_HOLDER_PAGES = 10


def money(value):
    if value is None:
        return "Unavailable"

    try:
        value = float(value)

        if value >= 1_000_000:
            return f"${value / 1_000_000:.2f}M"
        if value >= 1_000:
            return f"${value / 1_000:.1f}K"

        return f"${value:.2f}"
    except:
        return "Unavailable"


def number(value):
    if value is None:
        return "Unavailable"

    try:
        return f"{int(value):,}"
    except:
        return "Unavailable"


def percentage(value):
    if value is None:
        return "Unavailable"

    return f"{value:.1f}%"


def age_minutes(created_at):
    if not created_at:
        return None

    try:
        created = datetime.fromtimestamp(
            int(created_at) / 1000,
            tz=timezone.utc
        )

        now = datetime.now(timezone.utc)

        return int(
            (now - created).total_seconds() / 60
        )

    except:
        return None


# ============================================================
# DEXSCREENER
# ============================================================

def get_pairs(chain, address):

    url = (
        f"{DEX_BASE}/token-pairs/v1/"
        f"{chain}/{address}"
    )

    response = requests.get(
        url,
        timeout=15,
        headers={
            "Accept": "application/json"
        }
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# HELIUS RPC
# ============================================================

def helius_request(method, params):

    api_key = os.getenv(
        "HELIUS_API_KEY"
    )

    if not api_key:

        return {
            "ok": False,
            "error": (
                "HELIUS_API_KEY not configured"
            )
        }

    url = (
        f"{HELIUS_API}"
        f"?api-key={api_key}"
    )

    payload = {
        "jsonrpc": "2.0",
        "id": "mysterious-research",
        "method": method,
        "params": params
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=25,
            headers={
                "Content-Type":
                    "application/json"
            }
        )

        response.raise_for_status()

        data = response.json()

        if data.get("error"):

            return {
                "ok": False,
                "error": str(
                    data["error"]
                )
            }

        return {
            "ok": True,
            "result": data.get(
                "result"
            )
        }

    except Exception as e:

        return {
            "ok": False,
            "error": str(e)
        }


# ============================================================
# HOLDER DISTRIBUTION
# ============================================================

def get_holder_data(mint):

    balances_by_owner = defaultdict(int)

    page = 1
    total_accounts = 0

    try:

        while page <= MAX_HOLDER_PAGES:

            result = helius_request(
                "getTokenAccounts",
                {
                    "mint": mint,
                    "page": page,
                    "limit": 1000,
                    "displayOptions": {}
                }
            )

            if not result["ok"]:

                return {
                    "available": False,
                    "error": result["error"]
                }

            response = result["result"] or {}

            accounts = (
                response.get(
                    "token_accounts"
                ) or []
            )

            if not accounts:
                break

            for account in accounts:

                owner = account.get(
                    "owner"
                )

                amount = account.get(
                    "amount",
                    0
                )

                if not owner:
                    continue

                try:
                    amount = int(amount)
                except:
                    amount = 0

                if amount > 0:

                    balances_by_owner[
                        owner
                    ] += amount

            total_accounts += len(
                accounts
            )

            if len(accounts) < 1000:
                break

            page += 1

        if not balances_by_owner:

            return {
                "available": True,
                "holders": 0,
                "top1": None,
                "top5": None,
                "top10": None
            }

        sorted_holders = sorted(
            balances_by_owner.items(),
            key=lambda x: x[1],
            reverse=True
        )

        total_supply_held = sum(
            balances_by_owner.values()
        )

        if total_supply_held <= 0:

            return {
                "available": True,
                "holders": 0,
                "top1": None,
                "top5": None,
                "top10": None
            }

        def concentration(count):

            amount = sum(
                balance
                for _, balance
                in sorted_holders[:count]
            )

            return (
                amount /
                total_supply_held
            ) * 100

        return {
            "available": True,
            "holders": len(
                sorted_holders
            ),
            "token_accounts":
                total_accounts,
            "top1":
                concentration(1),
            "top5":
                concentration(5),
            "top10":
                concentration(10)
        }

    except Exception as e:

        return {
            "available": False,
            "error": str(e)
        }


# ============================================================
# TOKEN SECURITY / AUTHORITIES
# ============================================================

def get_token_security(mint):

    result = helius_request(
        "getAsset",
        {
            "id": mint,
            "displayOptions": {
                "showFungible": True
            }
        }
    )

    if not result["ok"]:

        return {
            "available": False,
            "error": result["error"]
        }

    asset = result["result"] or {}

    token_info = (
        asset.get("token_info")
        or {}
    )

    content = (
        asset.get("content")
        or {}
    )

    metadata = (
        content.get("metadata")
        or {}
    )

    return {
        "available": True,

        "name":
            metadata.get("name"),

        "symbol":
            metadata.get("symbol"),

        "decimals":
            token_info.get(
                "decimals"
            ),

        "supply":
            token_info.get(
                "supply"
            ),

        "token_program":
            token_info.get(
                "token_program"
            ),

        "interface":
            asset.get(
                "interface"
            ),

        "ownership":
            asset.get(
                "ownership"
            ),

        "authorities":
            asset.get(
                "authorities"
            ),

        "creators":
            asset.get(
                "creators"
            )
    }


# ============================================================
# ANALYSIS
# ============================================================

def analyze_pair(
    pair,
    holder_data=None,
    security_data=None
):

    liquidity = (
        pair.get("liquidity") or {}
    ).get("usd")

    volume = (
        pair.get("volume") or {}
    ).get("h24")

    txns = pair.get("txns") or {}

    h24 = (
        txns.get("h24") or {}
    )

    buys = h24.get(
        "buys",
        0
    ) or 0

    sells = h24.get(
        "sells",
        0
    ) or 0

    market_cap = pair.get(
        "marketCap"
    )

    if market_cap is None:

        market_cap = pair.get(
            "fdv"
        )

    age = age_minutes(
        pair.get(
            "pairCreatedAt"
        )
    )

    score = 0

    warnings = []

    positives = []

    # ========================================================
    # MARKET CAP
    # ========================================================

    if market_cap is not None:

        if 1_000 <= market_cap <= 4_000:

            score += 25

            positives.append(
                "very early MC"
            )

        elif 4_000 < market_cap <= 10_000:

            score += 15

            positives.append(
                "early MC"
            )

        elif market_cap > 10_000:

            warnings.append(
                "MC above radar target"
            )

    # ========================================================
    # AGE
    # ========================================================

    if age is not None:

        if age <= 30:

            score += 25

            positives.append(
                "extremely fresh pair"
            )

        elif age <= 60:

            score += 20

            positives.append(
                "under 1 hour old"
            )

        elif age <= 360:

            score += 10

            positives.append(
                "under 6 hours old"
            )

        elif age > 1440:

            warnings.append(
                "pair older than 24 hours"
            )

    # ========================================================
    # VOLUME
    # ========================================================

    if volume is not None:

        if volume <= 500:

            score += 20

            positives.append(
                "very low volume"
            )

        elif volume <= 3_000:

            score += 12

            positives.append(
                "low volume"
            )

        elif volume > 10_000:

            warnings.append(
                "high volume"
            )

    # ========================================================
    # TRANSACTIONS
    # ========================================================

    total_txns = buys + sells

    if total_txns <= 20:

        score += 15

        positives.append(
            "very low transaction count"
        )

    elif total_txns <= 60:

        score += 10

        positives.append(
            "low transaction count"
        )

    elif total_txns > 150:

        warnings.append(
            "high transaction activity"
        )

    # ========================================================
    # LIQUIDITY
    # ========================================================

    if liquidity is None or liquidity == 0:

        warnings.append(
            "liquidity unavailable"
        )

    elif liquidity < 1_000:

        warnings.append(
            "very thin liquidity"
        )

    elif liquidity < 5_000:

        positives.append(
            "low liquidity"
        )

    else:

        positives.append(
            "liquidity available"
        )

    # ========================================================
    # BUY / SELL
    # ========================================================

    if total_txns > 0:

        buy_ratio = (
            buys / total_txns
        )

        if buy_ratio >= 0.70:

            positives.append(
                "buy-heavy activity"
            )

        elif buy_ratio <= 0.30:

            warnings.append(
                "sell-heavy activity"
            )

    # ========================================================
    # HOLDER ANALYSIS
    # ========================================================

    holder_penalty = 0

    if (
        holder_data
        and holder_data.get(
            "available"
        )
    ):

        holders = holder_data.get(
            "holders",
            0
        )

        top1 = holder_data.get(
            "top1"
        )

        top10 = holder_data.get(
            "top10"
        )

        if holders >= 100:

            positives.append(
                "100+ holders"
            )

        elif holders >= 50:

            positives.append(
                "50+ holders"
            )

        elif holders < 20:

            warnings.append(
                "very few holders"
            )

            holder_penalty += 10

        if top1 is not None:

            if top1 >= 30:

                warnings.append(
                    "top holder concentration "
                    "above 30%"
                )

                holder_penalty += 20

            elif top1 >= 20:

                warnings.append(
                    "top holder concentration "
                    "above 20%"
                )

                holder_penalty += 10

            elif top1 >= 10:

                warnings.append(
                    "top holder concentration "
                    "above 10%"
                )

                holder_penalty += 5

        if top10 is not None:

            if top10 >= 60:

                warnings.append(
                    "top 10 concentration "
                    "above 60%"
                )

                holder_penalty += 20

            elif top10 >= 40:

                warnings.append(
                    "top 10 concentration "
                    "above 40%"
                )

                holder_penalty += 10

    elif (
        holder_data
        and not holder_data.get(
            "available"
        )
    ):

        warnings.append(
            "holder data unavailable"
        )

    # ========================================================
    # SECURITY / AUTHORITIES
    # ========================================================

    security_penalty = 0

    if (
        security_data
        and security_data.get(
            "available"
        )
    ):

        interface = security_data.get(
            "interface"
        )

        token_program = (
            security_data.get(
                "token_program"
            )
        )

        if interface:

            positives.append(
                f"token interface: {interface}"
            )

        if token_program:

            positives.append(
                "token program identified"
            )

        authorities = (
            security_data.get(
                "authorities"
            )
        )

        if authorities:

            warnings.append(
                "token authority data requires review"
            )

        else:

            positives.append(
                "no token authority record returned"
            )

    else:

        warnings.append(
            "token security data unavailable"
        )

    # ========================================================
    # APPLY PENALTIES
    # ========================================================

    score -= holder_penalty

    score -= security_penalty

    score = max(
        0,
        min(
            score,
            100
        )
    )

    # ========================================================
    # SERIOUS WARNINGS
    # ========================================================

    serious_warning = any(

        phrase in warning.lower()

        for warning in warnings

        for phrase in [

            "high volume",

            "high transaction",

            "older than",

            "above radar target"

        ]
    )

    # ========================================================
    # VERDICT
    # ========================================================

    if (
        score >= 70
        and not serious_warning
    ):

        verdict = "🟢 WATCH"

    elif score >= 50:

        verdict = "🟡 CAUTION"

    else:

        verdict = "🔴 IGNORE"

    return {

        "score":
            score,

        "verdict":
            verdict,

        "market_cap":
            market_cap,

        "liquidity":
            liquidity,

        "volume":
            volume,

        "buys":
            buys,

        "sells":
            sells,

        "total_txns":
            total_txns,

        "age":
            age,

        "positives":
            positives,

        "warnings":
            warnings,

        "holder_data":
            holder_data,

        "security_data":
            security_data,

        "pair":
            pair
    }


# ============================================================
# MAIN
# ============================================================

def main():

    if len(sys.argv) < 3:

        print(
            "Usage:"
        )

        print(
            "python research.py "
            "<chain> <contract>"
        )

        sys.exit(1)

    chain = sys.argv[1].lower()

    address = sys.argv[2].strip()

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
        f"📍 Contract: {address}"
    )

    print()

    # ========================================================
    # DEX DATA
    # ========================================================

    try:

        pairs = get_pairs(
            chain,
            address
        )

    except Exception as e:

        print(
            "❌ Could not retrieve "
            "token data."
        )

        print(
            f"Error: {e}"
        )

        sys.exit(1)

    if not pairs:

        print(
            "❌ No trading pair found."
        )

        sys.exit(1)

    # ========================================================
    # BEST PAIR
    # ========================================================

    def liquidity_value(pair):

        try:

            return float(

                (
                    pair.get(
                        "liquidity"
                    ) or {}
                ).get(
                    "usd"
                ) or 0
            )

        except:

            return 0

    pair = sorted(
        pairs,
        key=liquidity_value,
        reverse=True
    )[0]

    base = (
        pair.get(
            "baseToken"
        ) or {}
    )

    name = (
        base.get(
            "name"
        )
        or "Unknown"
    )

    symbol = str(
        base.get(
            "symbol"
        )
        or "UNKNOWN"
    ).lstrip("$")

    print(
        f"🪙 {name} (${symbol})"
    )

    print()

    # ========================================================
    # HOLDER DATA
    # ========================================================

    print(
        "🔎 Checking holder distribution..."
    )

    if chain == "solana":

        holder_data = get_holder_data(
            address
        )

    else:

        holder_data = {

            "available":
                False,

            "error":
                "Holder analysis currently "
                "supports Solana only"

        }

    # ========================================================
    # TOKEN SECURITY
    # ========================================================

    print(
        "🔐 Checking token security..."
    )

    if chain == "solana":

        security_data = (
            get_token_security(
                address
            )
        )

    else:

        security_data = {

            "available":
                False,

            "error":
                "Token security analysis "
                "currently supports "
                "Solana only"

        }

    # ========================================================
    # ANALYZE
    # ========================================================

    result = analyze_pair(

        pair,

        holder_data,

        security_data

    )

    # ========================================================
    # SUMMARY
    # ========================================================

    print()

    print(
        f"🎯 Research Score: "
        f"{result['score']}/100"
    )

    print(
        f"🚦 Verdict: "
        f"{result['verdict']}"
    )

    print()

    print(
        f"💰 Market Cap: "
        f"{money(result['market_cap'])}"
    )

    print(
        f"💧 Liquidity: "
        f"{money(result['liquidity'])}"
    )

    if result["age"] is not None:

        print(
            f"⏱ Pair Age: "
            f"{result['age']} minutes"
        )

    else:

        print(
            "⏱ Pair Age: Unavailable"
        )

    print(
        f"📊 24H Volume: "
        f"{money(result['volume'])}"
    )

    print(
        f"🟢 Buys: "
        f"{number(result['buys'])}"
    )

    print(
        f"🔴 Sells: "
        f"{number(result['sells'])}"
    )

    print(
        f"🔄 Total Txns: "
        f"{number(result['total_txns'])}"
    )

    # ========================================================
    # HOLDER OUTPUT
    # ========================================================

    print()

    print(
        "👥 HOLDER DISTRIBUTION"
    )

    if (
        holder_data
        and holder_data.get(
            "available"
        )
    ):

        print(
            f"👤 Holders: "
            f"{number(holder_data.get('holders'))}"
        )

        print(
            f"🥇 Top 1 Holder: "
            f"{percentage(holder_data.get('top1'))}"
        )

        print(
            f"🏆 Top 5 Holders: "
            f"{percentage(holder_data.get('top5'))}"
        )

        print(
            f"📊 Top 10 Holders: "
            f"{percentage(holder_data.get('top10'))}"
        )

    else:

        print(
            "❌ Holder data unavailable"
        )

    # ========================================================
    # SECURITY OUTPUT
    # ========================================================

    print()

    print(
        "🔐 TOKEN SECURITY"
    )

    if (
        security_data
        and security_data.get(
            "available"
        )
    ):

        interface = (
            security_data.get(
                "interface"
            )
        )

        token_program = (
            security_data.get(
                "token_program"
            )
        )

        decimals = (
            security_data.get(
                "decimals"
            )
        )

        supply = (
            security_data.get(
                "supply"
            )
        )

        print(
            f"🧩 Interface: "
            f"{interface or 'Unavailable'}"
        )

        print(
            f"⚙️ Token Program: "
            f"{token_program or 'Unavailable'}"
        )

        print(
            f"🔢 Decimals: "
            f"{decimals if decimals is not None else 'Unavailable'}"
        )

        print(
            f"📦 Supply: "
            f"{number(supply)}"
        )

        authorities = (
            security_data.get(
                "authorities"
            )
        )

        if authorities:

            print(
                "⚠️ Authorities: "
                "Present in asset data"
            )

        else:

            print(
                "✅ Authorities: "
                "None returned"
            )

    else:

        print(
            "❌ Token security data unavailable"
        )

        if security_data:

            print(
                f"Reason: "
                f"{security_data.get('error')}"
            )

    # ========================================================
    # POSITIVES
    # ========================================================

    print()

    print(
        "✅ POSITIVE SIGNALS"
    )

    if result["positives"]:

        for item in result["positives"]:

            print(
                f"• {item}"
            )

    else:

        print(
            "• None"
        )

    # ========================================================
    # WARNINGS
    # ========================================================

    print()

    print(
        "🚩 WARNINGS"
    )

    if result["warnings"]:

        for item in result["warnings"]:

            print(
                f"• {item}"
            )

    else:

        print(
            "• None"
        )

    # ========================================================
    # DEXSCREENER
    # ========================================================

    print()

    dex_url = pair.get(
        "url"
    )

    if dex_url:

        print(
            "🔗 DexScreener:"
        )

        print(
            dex_url
        )

    print()

    print(
        "⚠️ Research signal only. "
        "Do your own research."
    )

    print()


if __name__ == "__main__":
    main()
