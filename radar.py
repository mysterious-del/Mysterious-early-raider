import os
import json
import time
import requests
from datetime import datetime, timezone

# ============================================================
# MYSTERIOUS EARLY RADAR
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SEEN_FILE = "seen_tokens.json"

MAX_ALERTS_PER_RUN = 10
MIN_SCORE = 35

DEX_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"


# ============================================================
# MEMORY
# ============================================================

def load_seen():
    try:
        if not os.path.exists(SEEN_FILE):
            return set()

        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            return set()

        return set(data)

    except Exception as e:
        print(f"⚠️ Could not load radar memory: {e}")
        return set()


def save_seen(seen):
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(seen)), f, indent=2)

        print(f"💾 Radar memory saved locally: {len(seen)} tokens")

    except Exception as e:
        print(f"❌ Could not save radar memory: {e}")


# ============================================================
# TELEGRAM
# ============================================================

def telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ BOT_TOKEN or CHAT_ID is missing")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": False
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=20
        )

        if response.status_code == 200:
            return True

        print(
            f"❌ Telegram error {response.status_code}: "
            f"{response.text[:300]}"
        )

    except Exception as e:
        print(f"❌ Telegram request failed: {e}")

    return False


# ============================================================
# DEXSCREENER
# ============================================================

def get_latest_profiles():
    try:
        response = requests.get(
            DEX_PROFILES_URL,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        if not isinstance(data, list):
            return []

        return data

    except Exception as e:
        print(f"❌ Failed to get latest token profiles: {e}")
        return []


def get_token_pairs(chain, address):
    url = f"https://api.dexscreener.com/token-pairs/v1/{chain}/{address}"

    try:
        response = requests.get(
            url,
            timeout=20
        )

        if response.status_code != 200:
            return []

        data = response.json()

        if isinstance(data, list):
            return data

        return []

    except Exception as e:
        print(f"⚠️ Pair lookup failed for {address}: {e}")
        return []


# ============================================================
# SCORING
# ============================================================

def calculate_score(profile, pair):
    score = 0

    chain = str(
        profile.get("chainId")
        or pair.get("chainId")
        or ""
    ).lower()

    address = (
        profile.get("tokenAddress")
        or pair.get("baseToken", {}).get("address")
        or ""
    )

    name = (
        profile.get("name")
        or pair.get("baseToken", {}).get("name")
        or ""
    )

    symbol = (
        profile.get("symbol")
        or pair.get("baseToken", {}).get("symbol")
        or ""
    )

    text = f"{name} {symbol}".lower()

    # --------------------------------------------------------
    # Chain
    # --------------------------------------------------------

    if chain == "solana":
        score += 15

    elif chain in ["base", "bsc", "ethereum"]:
        score += 10

    # --------------------------------------------------------
    # Memecoin / project keywords
    # --------------------------------------------------------

    keywords = [
        "meme",
        "coin",
        "dog",
        "cat",
        "pepe",
        "frog",
        "ai",
        "inu",
        "wojak",
        "chad",
        "moon",
        "pump",
        "based",
        "elon",
        "trump"
    ]

    if any(keyword in text for keyword in keywords):
        score += 15

    # --------------------------------------------------------
    # Liquidity
    # --------------------------------------------------------

    liquidity = pair.get("liquidity") or {}
    liquidity_usd = liquidity.get("usd") or 0

    try:
        liquidity_usd = float(liquidity_usd)
    except:
        liquidity_usd = 0

    if liquidity_usd <= 10000:
        score += 20

    elif liquidity_usd <= 25000:
        score += 10

    # --------------------------------------------------------
    # Volume
    # --------------------------------------------------------

    volume = pair.get("volume") or {}

    volume_24h = volume.get("h24") or 0

    try:
        volume_24h = float(volume_24h)
    except:
        volume_24h = 0

    if volume_24h <= 5000:
        score += 15

    elif volume_24h <= 20000:
        score += 8

    # --------------------------------------------------------
    # Transactions
    # --------------------------------------------------------

    txns = pair.get("txns") or {}
    h24 = txns.get("h24") or {}

    buys = h24.get("buys") or 0
    sells = h24.get("sells") or 0

    try:
        buys = int(buys)
    except:
        buys = 0

    try:
        sells = int(sells)
    except:
        sells = 0

    total_txns = buys + sells

    if total_txns <= 100:
        score += 15

    elif total_txns <= 300:
        score += 8

    # --------------------------------------------------------
    # Pair age
    # --------------------------------------------------------

    pair_created = pair.get("pairCreatedAt")

    if pair_created:
        try:
            created_ms = int(pair_created)

            now_ms = int(
                datetime.now(timezone.utc).timestamp() * 1000
            )

            age_hours = (
                now_ms - created_ms
            ) / 1000 / 60 / 60

            if age_hours <= 1:
                score += 20

            elif age_hours <= 6:
                score += 15

            elif age_hours <= 24:
                score += 10

            elif age_hours <= 72:
                score += 5

        except Exception:
            pass

    return score


# ============================================================
# MESSAGE
# ============================================================

def build_message(profile, pair, score):
    chain = (
        profile.get("chainId")
        or pair.get("chainId")
        or "unknown"
    )

    address = (
        profile.get("tokenAddress")
        or pair.get("baseToken", {}).get("address")
        or "unknown"
    )

    name = (
        profile.get("name")
        or pair.get("baseToken", {}).get("name")
        or "Unknown"
    )

    symbol = (
        profile.get("symbol")
        or pair.get("baseToken", {}).get("symbol")
        or "???"
    )

    description = profile.get("description") or ""

    url = (
        profile.get("url")
        or pair.get("url")
        or f"https://dexscreener.com/{chain}/{address}"
    )

    liquidity = pair.get("liquidity") or {}
    liquidity_usd = liquidity.get("usd") or 0

    volume = pair.get("volume") or {}
    volume_24h = volume.get("h24") or 0

    txns = pair.get("txns") or {}
    h24 = txns.get("h24") or {}

    buys = h24.get("buys") or 0
    sells = h24.get("sells") or 0

    message = f"""
🥷 MYSTERIOUS EARLY RADAR

🚨 EARLY PROJECT DETECTED

Name: {name}
Ticker: ${symbol}
Chain: {chain}

🎯 Radar Score: {score}

💧 Liquidity: ${liquidity_usd:,.0f}
📊 24H Volume: ${volume_24h:,.0f}
🔄 24H Txns: {buys + sells}

🔗 DexScreener:
{url}

📍 Contract:
{address}
""".strip()

    if description:
        short_description = description[:300]

        message += (
            f"\n\n📝 Description:\n"
            f"{short_description}"
        )

    message += (
        "\n\n⚠️ Early radar signal, not a buy signal."
    )

    return message


# ============================================================
# MAIN RADAR
# ============================================================

def main():

    print("🥷 Mysterious Early Radar started")

    seen = load_seen()

    print(
        f"🧠 Loaded radar memory: "
        f"{len(seen)} previously seen tokens"
    )

    profiles = get_latest_profiles()

    print(
        f"Found {len(profiles)} latest token profiles"
    )

    alerts_sent = 0
    candidates = 0

    for profile in profiles:

        if alerts_sent >= MAX_ALERTS_PER_RUN:
            break

        chain = profile.get("chainId")
        address = profile.get("tokenAddress")

        if not chain or not address:
            continue

        token_id = f"{chain}:{address}"

        # ----------------------------------------------------
        # DUPLICATE PROTECTION
        # ----------------------------------------------------

        if token_id in seen:
            continue

        candidates += 1

        # ----------------------------------------------------
        # Get trading pair
        # ----------------------------------------------------

        pairs = get_token_pairs(
            chain,
            address
        )

        if not pairs:
            # Remember that we already processed it
            seen.add(token_id)
            continue

        # Pick first available pair
        pair = pairs[0]

        # ----------------------------------------------------
        # Score
        # ----------------------------------------------------

        score = calculate_score(
            profile,
            pair
        )

        print(
            f"🔎 Candidate: "
            f"{address} | Score {score}"
        )

        # ----------------------------------------------------
        # Low score
        # ----------------------------------------------------

        if score < MIN_SCORE:

            seen.add(token_id)

            continue

        # ----------------------------------------------------
        # Alert
        # ----------------------------------------------------

        message = build_message(
            profile,
            pair,
            score
        )

        sent = telegram(message)

        if sent:

            alerts_sent += 1

            print(
                f"🚨 Alert sent: "
                f"{address} | Score {score}"
            )

            # IMPORTANT:
            # Mark only after successful Telegram alert.
            seen.add(token_id)

        else:

            print(
                f"⚠️ Alert failed: "
                f"{address}"
            )

        # Small delay to avoid hammering APIs
        time.sleep(0.5)

    # --------------------------------------------------------
    # DEBUG MEMORY CHECK
    # --------------------------------------------------------

    print(
        f"📊 New candidates processed: {candidates}"
    )

    print(
        f"📊 Alerts sent this run: {alerts_sent}"
    )

    print(
        f"🧠 DEBUG: seen tokens before save = "
        f"{len(seen)}"
    )

    print(
        f"🧠 DEBUG: seen sample = "
        f"{list(seen)[:10]}"
    )

    # --------------------------------------------------------
    # SAVE MEMORY
    # --------------------------------------------------------

    save_seen(seen)

    print(
        f"✅ Radar scan completed. "
        f"Alerts sent: {alerts_sent}"
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
