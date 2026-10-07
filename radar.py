import os
import json
import time
import requests
from datetime import datetime, timezone

# ============================================================
# 🥷 MYSTERIOUS EARLY RADAR
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SEEN_FILE = "seen_tokens.json"

MAX_ALERTS_PER_RUN = 10

# Minimum score required before an alert can be sent
MIN_SCORE = 45

# Projects above these levels are considered too hot
HOT_VOLUME = 100_000
HOT_TXNS = 1_500

DEX_PROFILES_URL = (
    "https://api.dexscreener.com/token-profiles/latest/v1"
)


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
        print(f"⚠️ Could not load memory: {e}")
        return set()


def save_seen(seen):
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(seen)), f, indent=2)

        print(f"💾 Memory saved: {len(seen)} tokens")

    except Exception as e:
        print(f"❌ Could not save memory: {e}")


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
            f"❌ Telegram error "
            f"{response.status_code}: "
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

        if isinstance(data, list):
            return data

        return []

    except Exception as e:
        print(f"❌ Profile request failed: {e}")
        return []


def get_token_pairs(chain, address):
    url = (
        f"https://api.dexscreener.com/"
        f"token-pairs/v1/{chain}/{address}"
    )

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
        print(
            f"⚠️ Pair lookup failed "
            f"for {address}: {e}"
        )

        return []


# ============================================================
# HELPERS
# ============================================================

def safe_float(value, default=0):
    try:
        return float(value)
    except Exception:
        return default


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def get_pair_age_hours(pair):
    pair_created = pair.get("pairCreatedAt")

    if not pair_created:
        return None

    try:
        created_ms = int(pair_created)

        now_ms = int(
            datetime.now(timezone.utc).timestamp() * 1000
        )

        return max(
            0,
            (now_ms - created_ms) / 1000 / 60 / 60
        )

    except Exception:
        return None


# ============================================================
# ATTENTION LEVEL
# ============================================================

def get_attention_level(volume, transactions, age_hours):

    # Very high activity = already crowded
    if volume >= HOT_VOLUME or transactions >= HOT_TXNS:
        return "HOT", "🔴"

    # Strong activity but not yet extreme
    if volume >= 25_000 or transactions >= 500:
        return "WARM", "🟡"

    # Low activity = early territory
    return "EARLY", "🟢"


# ============================================================
# SCORING
# ============================================================

def calculate_score(profile, pair):

    score = 0
    reasons = []

    chain = str(
        profile.get("chainId")
        or pair.get("chainId")
        or ""
    ).lower()

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

    description = (
        profile.get("description")
        or ""
    )

    text = (
        f"{name} "
        f"{symbol} "
        f"{description}"
    ).lower()

    # --------------------------------------------------------
    # CHAIN
    # --------------------------------------------------------

    if chain == "solana":
        score += 15
        reasons.append("Solana early-market signal")

    elif chain == "base":
        score += 12
        reasons.append("Base early-market signal")

    elif chain in ["bsc", "ethereum"]:
        score += 8

    # --------------------------------------------------------
    # MEME / NARRATIVE
    # --------------------------------------------------------

    meme_words = [
        "meme",
        "memecoin",
        "coin",
        "dog",
        "doge",
        "cat",
        "pepe",
        "frog",
        "inu",
        "shib",
        "wojak",
        "chad",
        "ai",
        "elon",
        "trump",
        "based",
        "degen",
        "ape",
        "moon",
        "pump"
    ]

    matched_words = [
        word for word in meme_words
        if word in text
    ]

    if matched_words:
        score += 15
        reasons.append("Meme/narrative detected")

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    liquidity = pair.get("liquidity") or {}

    liquidity_usd = safe_float(
        liquidity.get("usd")
    )

    if 0 < liquidity_usd <= 5_000:
        score += 20
        reasons.append("Very low liquidity")

    elif liquidity_usd <= 10_000:
        score += 15
        reasons.append("Low liquidity")

    elif liquidity_usd <= 25_000:
        score += 8
        reasons.append("Early liquidity")

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    volume = pair.get("volume") or {}

    volume_24h = safe_float(
        volume.get("h24")
    )

    if volume_24h <= 2_500:
        score += 15
        reasons.append("Very low trading volume")

    elif volume_24h <= 10_000:
        score += 10
        reasons.append("Low trading volume")

    elif volume_24h <= 25_000:
        score += 5

    # --------------------------------------------------------
    # TRANSACTIONS
    # --------------------------------------------------------

    txns = pair.get("txns") or {}
    h24 = txns.get("h24") or {}

    buys = safe_int(h24.get("buys"))
    sells = safe_int(h24.get("sells"))

    total_txns = buys + sells

    if total_txns <= 50:
        score += 15
        reasons.append("Very low transaction activity")

    elif total_txns <= 150:
        score += 10
        reasons.append("Low transaction activity")

    elif total_txns <= 300:
        score += 5

    # --------------------------------------------------------
    # AGE
    # --------------------------------------------------------

    age_hours = get_pair_age_hours(pair)

    if age_hours is not None:

        if age_hours <= 1:
            score += 25
            reasons.append("Extremely fresh launch")

        elif age_hours <= 3:
            score += 20
            reasons.append("Very fresh launch")

        elif age_hours <= 6:
            score += 15
            reasons.append("Fresh launch")

        elif age_hours <= 24:
            score += 10
            reasons.append("Less than 24 hours old")

        elif age_hours <= 72:
            score += 5

    # --------------------------------------------------------
    # PROJECT LINKS
    # --------------------------------------------------------

    if profile.get("description"):
        score += 3
        reasons.append("Project description found")

    if profile.get("url"):
        score += 2
        reasons.append("Project link found")

    return score, reasons


# ============================================================
# ALERT MESSAGE
# ============================================================

def build_message(
    profile,
    pair,
    score,
    reasons,
    attention,
    icon,
    stats
):

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

    url = (
        profile.get("url")
        or pair.get("url")
        or f"https://dexscreener.com/"
           f"{chain}/{address}"
    )

    liquidity = stats["liquidity"]
    volume = stats["volume"]
    buys = stats["buys"]
    sells = stats["sells"]
    age_hours = stats["age_hours"]

    if age_hours is None:
        age_text = "Unknown"

    elif age_hours < 1:
        age_text = f"{age_hours * 60:.0f} minutes"

    elif age_hours < 24:
        age_text = f"{age_hours:.1f} hours"

    else:
        age_text = f"{age_hours / 24:.1f} days"

    reason_lines = "\n".join(
        f"• {reason}"
        for reason in reasons[:6]
    )

    message = f"""
🥷 MYSTERIOUS EARLY RADAR

{icon} {attention} ATTENTION

🚨 EARLY PROJECT DETECTED

{name} (${symbol})

🎯 Radar Score: {score}/100
⛓ Chain: {chain}
⏱ Pair Age: {age_text}

💧 Liquidity: ${liquidity:,.0f}
📊 24H Volume: ${volume:,.0f}
🔄 24H Txns: {buys + sells}

WHY IT WAS FLAGGED
{reason_lines}

🔗 DexScreener:
{url}

📍 Contract:
{address}

⚠️ Radar signal only. Do your own research.
""".strip()

    return message


# ============================================================
# MAIN
# ============================================================

def main():

    print("🥷 Mysterious Early Radar started")

    seen = load_seen()

    print(
        f"🧠 Loaded memory: "
        f"{len(seen)} previously seen tokens"
    )

    profiles = get_latest_profiles()

    print(
        f"Found {len(profiles)} latest token profiles"
    )

    alerts_sent = 0
    candidates = 0
    skipped_seen = 0
    skipped_hot = 0

    for profile in profiles:

        # ----------------------------------------------------
        # MAX ALERT LIMIT
        # ----------------------------------------------------

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
            skipped_seen += 1
            continue

        candidates += 1

        # ----------------------------------------------------
        # GET PAIRS
        # ----------------------------------------------------

        pairs = get_token_pairs(
            chain,
            address
        )

        if not pairs:
            seen.add(token_id)
            continue

        pair = pairs[0]

        # ----------------------------------------------------
        # BASIC STATS
        # ----------------------------------------------------

        liquidity = pair.get("liquidity") or {}
        liquidity_usd = safe_float(
            liquidity.get("usd")
        )

        volume_data = pair.get("volume") or {}
        volume_24h = safe_float(
            volume_data.get("h24")
        )

        txns = pair.get("txns") or {}
        h24 = txns.get("h24") or {}

        buys = safe_int(
            h24.get("buys")
        )

        sells = safe_int(
            h24.get("sells")
        )

        total_txns = buys + sells

        age_hours = get_pair_age_hours(pair)

        # ----------------------------------------------------
        # ATTENTION LEVEL
        # ----------------------------------------------------

        attention, icon = get_attention_level(
            volume_24h,
            total_txns,
            age_hours
        )

        print(
            f"🔎 Candidate: {address} | "
            f"Attention: {attention} | "
            f"Volume: ${volume_24h:,.0f} | "
            f"Txns: {total_txns}"
        )

        # ----------------------------------------------------
        # HOT FILTER
        # ----------------------------------------------------

        if attention == "HOT":

            print(
                f"🔥 Too hot: {address} | "
                f"Volume ${volume_24h:,.0f} | "
                f"Txns {total_txns}"
            )

            skipped_hot += 1

            # Remember it so it isn't repeatedly inspected
            seen.add(token_id)

            continue

        # ----------------------------------------------------
        # SCORE
        # ----------------------------------------------------

        score, reasons = calculate_score(
            profile,
            pair
        )

        print(
            f"🎯 Score: {score} | "
            f"{address}"
        )

        # ----------------------------------------------------
        # LOW SCORE
        # ----------------------------------------------------

        if score < MIN_SCORE:

            print(
                f"⏭️ Low score: "
                f"{address} | {score}"
            )

            seen.add(token_id)

            continue

        # ----------------------------------------------------
        # BUILD ALERT
        # ----------------------------------------------------

        stats = {
            "liquidity": liquidity_usd,
            "volume": volume_24h,
            "buys": buys,
            "sells": sells,
            "age_hours": age_hours
        }

        message = build_message(
            profile,
            pair,
            score,
            reasons,
            attention,
            icon,
            stats
        )

        # ----------------------------------------------------
        # SEND
        # ----------------------------------------------------

        sent = telegram(message)

        if sent:

            alerts_sent += 1

            print(
                f"🚨 Alert sent: "
                f"{address} | "
                f"Score {score} | "
                f"{attention}"
            )

            # Only mark after successful Telegram alert
            seen.add(token_id)

        else:

            print(
                f"⚠️ Alert failed: "
                f"{address}"
            )

        time.sleep(0.5)

    # ========================================================
    # DEBUG / SUMMARY
    # ========================================================

    print("")
    print("===== RADAR SUMMARY =====")

    print(
        f"📊 Candidates processed: "
        f"{candidates}"
    )

    print(
        f"⏭️ Previously seen: "
        f"{skipped_seen}"
    )

    print(
        f"🔥 Too hot skipped: "
        f"{skipped_hot}"
    )

    print(
        f"🚨 Alerts sent: "
        f"{alerts_sent}"
    )

    print(
        f"🧠 DEBUG: seen tokens before save = "
        f"{len(seen)}"
    )

    print(
        f"🧠 DEBUG: seen sample = "
        f"{list(seen)[:10]}"
    )

    print("=========================")

    # ========================================================
    # SAVE MEMORY
    # ========================================================

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
