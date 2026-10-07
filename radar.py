import os
import requests
from datetime import datetime, timezone

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

DEX_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"

MAX_ALERTS_PER_RUN = 10
MIN_SCORE = 35

CHAIN_NAMES = {
    "solana": "Solana",
    "ethereum": "Ethereum",
    "bsc": "BNB Chain",
    "base": "Base",
    "arbitrum": "Arbitrum",
    "polygon": "Polygon",
    "avax": "Avalanche",
}


def telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": message,
            "disable_web_page_preview": False,
        },
        timeout=20,
    )

    response.raise_for_status()


def get_latest_profiles():
    response = requests.get(
        DEX_PROFILES_URL,
        timeout=20,
    )

    response.raise_for_status()

    data = response.json()

    if isinstance(data, list):
        return data

    return []


def get_token_data(chain, address):
    url = (
        f"https://api.dexscreener.com/"
        f"token-pairs/v1/{chain}/{address}"
    )

    response = requests.get(
        url,
        timeout=20,
    )

    if response.status_code != 200:
        return []

    data = response.json()

    if isinstance(data, list):
        return data

    return []


def calculate_score(profile, pairs):
    score = 0
    reasons = []

    chain = profile.get("chainId", "").lower()
    description = (
        profile.get("description") or ""
    ).lower()

    # New profile signal
    score += 10
    reasons.append("recent token profile")

    # Chain signal
    if chain in ["solana", "base", "bsc"]:
        score += 10
        reasons.append(
            f"{CHAIN_NAMES.get(chain, chain)} project"
        )

    # Description signal
    project_words = [
        "meme",
        "memecoin",
        "community",
        "launch",
        "fair launch",
        "stealth",
        "ai",
        "agent",
        "culture",
        "viral",
    ]

    if any(word in description for word in project_words):
        score += 10
        reasons.append("project/memecoin language")

    if not pairs:
        score += 5
        reasons.append("very early / limited market data")

        return score, reasons

    # Examine available pairs
    best_pair = pairs[0]

    liquidity = (
        best_pair.get("liquidity") or {}
    ).get("usd") or 0

    volume = (
        best_pair.get("volume") or {}
    ).get("h24") or 0

    txns = best_pair.get("txns") or {}
    h24 = txns.get("h24") or {}

    buys = h24.get("buys") or 0
    sells = h24.get("sells") or 0

    pair_created = best_pair.get("pairCreatedAt")

    # Low liquidity = potentially early
    if liquidity and liquidity < 10000:
        score += 20
        reasons.append(
            f"low liquidity (${liquidity:,.0f})"
        )

    elif liquidity and liquidity < 25000:
        score += 10
        reasons.append(
            f"small liquidity (${liquidity:,.0f})"
        )

    # Low volume = low attention
    if volume and volume < 10000:
        score += 10
        reasons.append("low 24h volume")

    # Small transaction count
    total_txns = buys + sells

    if total_txns and total_txns < 100:
        score += 10
        reasons.append("low transaction activity")

    # Very new pair
    if pair_created:
        try:
            created = datetime.fromtimestamp(
                pair_created / 1000,
                tz=timezone.utc,
            )

            age_hours = (
                datetime.now(timezone.utc) - created
            ).total_seconds() / 3600

            if age_hours < 1:
                score += 25
                reasons.append("pair less than 1 hour old")

            elif age_hours < 6:
                score += 20
                reasons.append("pair less than 6 hours old")

            elif age_hours < 24:
                score += 10
                reasons.append("pair less than 24 hours old")

        except Exception:
            pass

    return min(score, 100), reasons


def format_socials(profile):
    links = profile.get("links") or []

    socials = []

    for link in links:
        url = link.get("url")

        if url:
            socials.append(url)

    if not socials:
        return "No social link found"

    return "\n".join(socials[:5])


def build_alert(profile, pairs, score, reasons):
    chain = profile.get("chainId", "unknown")
    address = profile.get("tokenAddress", "unknown")

    chain_name = CHAIN_NAMES.get(
        chain.lower(),
        chain
    )

    description = (
        profile.get("description")
        or "No description available."
    )

    dex_url = profile.get("url")

    socials = format_socials(profile)

    best_pair = pairs[0] if pairs else {}

    liquidity = (
        best_pair.get("liquidity") or {}
    ).get("usd")

    volume = (
        best_pair.get("volume") or {}
    ).get("h24")

    message = f"""
🥷 MYSTERIOUS EARLY RADAR

🔥 EARLY TOKEN DETECTED

⛓ Chain: {chain_name}

🎯 Early Score: {score}/100

🔎 WHY FLAGGED:
{chr(10).join("• " + r for r in reasons)}

🪙 Token:
{address}

💧 Liquidity:
${liquidity:,.0f}
""" if liquidity else f"""
🥷 MYSTERIOUS EARLY RADAR

🔥 EARLY TOKEN DETECTED

⛓ Chain: {chain_name}

🎯 Early Score: {score}/100

🔎 WHY FLAGGED:
{chr(10).join("• " + r for r in reasons)}

🪙 Token:
{address}

💧 Liquidity:
Not available yet
"""

    if volume:
        message += f"""
📊 24H Volume:
${volume:,.0f}
"""

    message += f"""
📝 DESCRIPTION:
{description[:500]}

🌐 SOCIALS:
{socials}

🔗 DEX:
{dex_url or "Not available"}

🧠 ACTION:
Research the project/founder manually before contacting.

⚠️ This is an early-signal alert, not a buy signal.
"""

    return message.strip()


def main():
    print("🥷 Mysterious Early Radar started")

    profiles = get_latest_profiles()

    print(
        f"Found {len(profiles)} latest token profiles"
    )

    alerts_sent = 0

    for profile in profiles:

        if alerts_sent >= MAX_ALERTS_PER_RUN:
            break

        chain = profile.get("chainId")
        address = profile.get("tokenAddress")

        if not chain or not address:
            continue

        pairs = get_token_data(
            chain,
            address
        )

        score, reasons = calculate_score(
            profile,
            pairs
        )

        if score < MIN_SCORE:
            continue

        message = build_alert(
            profile,
            pairs,
            score,
            reasons
        )

        try:
            telegram(message)

            alerts_sent += 1

            print(
                f"🚨 Alert sent: "
                f"{address} | Score {score}"
            )

        except Exception as error:
            print(
                f"Telegram error: {error}"
            )

    print(
        f"✅ Radar scan completed. "
        f"Alerts sent: {alerts_sent}"
    )


if __name__ == "__main__":
    main()
