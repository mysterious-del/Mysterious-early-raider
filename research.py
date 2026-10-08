import sys
import requests
from datetime import datetime, timezone

DEX_BASE = "https://api.dexscreener.com"


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


def age_minutes(created_at):
    if not created_at:
        return None

    try:
        created = datetime.fromtimestamp(
            int(created_at) / 1000,
            tz=timezone.utc
        )

        now = datetime.now(timezone.utc)

        return int((now - created).total_seconds() / 60)

    except:
        return None


def get_pairs(chain, address):
    url = f"{DEX_BASE}/token-pairs/v1/{chain}/{address}"

    response = requests.get(
        url,
        timeout=15,
        headers={"Accept": "application/json"}
    )

    response.raise_for_status()

    return response.json()


def analyze_pair(pair):
    liquidity = (pair.get("liquidity") or {}).get("usd")
    volume = (pair.get("volume") or {}).get("h24")

    txns = pair.get("txns") or {}
    h24 = txns.get("h24") or {}

    buys = h24.get("buys", 0) or 0
    sells = h24.get("sells", 0) or 0

    market_cap = pair.get("marketCap")

    if market_cap is None:
        market_cap = pair.get("fdv")

    age = age_minutes(pair.get("pairCreatedAt"))

    score = 0
    warnings = []
    positives = []

    # MARKET CAP
    if market_cap is not None:
        if 1_000 <= market_cap <= 4_000:
            score += 25
            positives.append("very early MC")
        elif 4_000 < market_cap <= 10_000:
            score += 15
            positives.append("early MC")
        elif market_cap > 10_000:
            warnings.append("MC above radar target")

    # AGE
    if age is not None:
        if age <= 30:
            score += 25
            positives.append("extremely fresh pair")
        elif age <= 60:
            score += 20
            positives.append("under 1 hour old")
        elif age <= 360:
            score += 10
            positives.append("under 6 hours old")
        elif age > 1440:
            warnings.append("pair older than 24 hours")

    # VOLUME
    if volume is not None:
        if volume <= 500:
            score += 20
            positives.append("very low volume")
        elif volume <= 3_000:
            score += 12
            positives.append("low volume")
        elif volume > 10_000:
            warnings.append("high volume")

    # TRANSACTIONS
    total_txns = buys + sells

    if total_txns <= 20:
        score += 15
        positives.append("very low transaction count")
    elif total_txns <= 60:
        score += 10
        positives.append("low transaction count")
    elif total_txns > 150:
        warnings.append("high transaction activity")

    # LIQUIDITY
    if liquidity is None or liquidity == 0:
        warnings.append("liquidity unavailable")
    elif liquidity < 1_000:
        warnings.append("very thin liquidity")
    elif liquidity < 5_000:
        positives.append("low liquidity")
    else:
        positives.append("liquidity available")

    # BUY / SELL BALANCE
    if total_txns > 0:
        buy_ratio = buys / total_txns

        if buy_ratio >= 0.70:
            positives.append("buy-heavy activity")
        elif buy_ratio <= 0.30:
            warnings.append("sell-heavy activity")

    # VERDICT
    if score >= 70 and not any(
        "high" in warning.lower()
        or "older" in warning.lower()
        for warning in warnings
    ):
        verdict = "🟢 WATCH"
    elif score >= 50:
        verdict = "🟡 CAUTION"
    else:
        verdict = "🔴 IGNORE"

    return {
        "score": min(score, 100),
        "verdict": verdict,
        "market_cap": market_cap,
        "liquidity": liquidity,
        "volume": volume,
        "buys": buys,
        "sells": sells,
        "total_txns": total_txns,
        "age": age,
        "positives": positives,
        "warnings": warnings,
        "pair": pair,
    }


def main():
    if len(sys.argv) < 3:
        print("Usage:")
        print("python research.py <chain> <contract>")
        print()
        print("Example:")
        print("python research.py solana CONTRACT_ADDRESS")
        sys.exit(1)

    chain = sys.argv[1].lower()
    address = sys.argv[2].strip()

    print()
    print("🥷 MYSTERIOUS TOKEN CHECKER")
    print("============================")
    print(f"⛓ Chain: {chain}")
    print(f"📍 Contract: {address}")
    print()

    try:
        pairs = get_pairs(chain, address)
    except Exception as e:
        print("❌ Could not retrieve token data.")
        print(f"Error: {e}")
        sys.exit(1)

    if not pairs:
        print("❌ No trading pair found.")
        sys.exit(1)

    # Prefer the pair with the highest liquidity.
    def liquidity_value(pair):
        try:
            return float((pair.get("liquidity") or {}).get("usd") or 0)
        except:
            return 0

    pair = sorted(
        pairs,
        key=liquidity_value,
        reverse=True
    )[0]

    result = analyze_pair(pair)

    base = pair.get("baseToken") or {}

    name = base.get("name") or "Unknown"
    symbol = str(base.get("symbol") or "UNKNOWN").lstrip("$")

    print(f"🪙 {name} (${symbol})")
    print()

    print(f"🎯 Research Score: {result['score']}/100")
    print(f"🚦 Verdict: {result['verdict']}")
    print()

    print(f"💰 Market Cap: {money(result['market_cap'])}")
    print(f"💧 Liquidity: {money(result['liquidity'])}")

    if result["age"] is not None:
        print(f"⏱ Pair Age: {result['age']} minutes")
    else:
        print("⏱ Pair Age: Unavailable")

    print(f"📊 24H Volume: {money(result['volume'])}")
    print(f"🟢 Buys: {number(result['buys'])}")
    print(f"🔴 Sells: {number(result['sells'])}")
    print(f"🔄 Total Txns: {number(result['total_txns'])}")

    print()
    print("✅ POSITIVE SIGNALS")

    if result["positives"]:
        for item in result["positives"]:
            print(f"• {item}")
    else:
        print("• None")

    print()
    print("🚩 WARNINGS")

    if result["warnings"]:
        for item in result["warnings"]:
            print(f"• {item}")
    else:
        print("• None")

    print()

    dex_url = pair.get("url")

    if dex_url:
        print("🔗 DexScreener:")
        print(dex_url)

    print()
    print("⚠️ Research signal only. Do your own research.")
    print()


if __name__ == "__main__":
    main()
