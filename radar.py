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

DEX_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
DEX_PAIRS_URL = "https://api.dexscreener.com/token-pairs/v1"

MEMORY_FILE = "seen_tokens.json"

# ============================================================
# RADAR SETTINGS
# ============================================================

# Main target starts around $1K.
# $1K-$2K gets the strongest priority.
MIN_TARGET_MC = 1000

# We still allow projects above $4K if they are genuinely early.
SOFT_EARLY_MC = 4000

# Normal upper boundary for the early radar.
MAX_EARLY_MC = 10000

# Freshness
MAX_AGE_HOURS = 24

# Activity is used for scoring, not as an automatic rejection
MAX_VOLUME_HARD = 100000
MAX_TXNS_HARD = 1500

# Maximum Telegram alerts per run
MAX_ALERTS_PER_RUN = 10

# ============================================================
# KEYWORDS
# ============================================================

MEME_KEYWORDS = [
    "meme",
    "memecoin",
    "dog",
    "cat",
    "pepe",
    "frog",
    "inu",
    "chad",
    "wojak",
    "anime",
    "ai",
    "cult",
    "community",
    "coin",
    "token",
    "degen",
    "based",
    "trump",
    "elon",
]

# ============================================================
# MEMORY
# ============================================================

def load_memory():
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return set(str(x) for x in data)

        return set()

    except Exception:
        return set()


def save_memory(seen):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen)), f, indent=2)


# ============================================================
# HELPERS
# ============================================================

def safe_float(value, default=0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value, default=0):
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def format_money(value):
    if value is None:
        return "Unknown"

    value = safe_float(value)

    if value < 1000:
        return f"${value:,.0f}"

    if value < 1_000_000:
        return f"${value / 1000:.1f}K"

    return f"${value / 1_000_000:.2f}M"


def calculate_age_hours(pair_created_at):
    if not pair_created_at:
        return None

    try:
        created_ms = int(pair_created_at)

        created = datetime.fromtimestamp(
            created_ms / 1000,
            tz=timezone.utc
        )

        now = datetime.now(timezone.utc)

        return max(
            0,
            (now - created).total_seconds() / 3600
        )

    except Exception:
        return None


def format_age(hours):
    if hours is None:
        return "Unknown"

    minutes = int(hours * 60)

    if minutes < 60:
        return f"{minutes} minutes"

    return f"{hours:.1f} hours"


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not BOT_TOKEN or not CHAT_ID:
        print("❌ BOT_TOKEN or CHAT_ID missing")
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

        if response.ok:
            return True

        print(
            "❌ Telegram error:",
            response.status_code,
            response.text[:500]
        )

    except Exception as e:
        print("❌ Telegram exception:", e)

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

        if not response.ok:
            print(
                "❌ DEX profile error:",
                response.status_code
            )
            return []

        data = response.json()

        if isinstance(data, list):
            return data

        return []

    except Exception as e:
        print("❌ Profile request failed:", e)
        return []


def get_pairs(chain, address):

    try:
        url = f"{DEX_PAIRS_URL}/{chain}/{address}"

        response = requests.get(
            url,
            timeout=20
        )

        if not response.ok:
            return []

        data = response.json()

        if isinstance(data, list):
            return data

        return []

    except Exception as e:
        print(
            f"⚠️ Pair lookup failed: {address} | {e}"
        )
        return []


def choose_best_pair(pairs):

    if not pairs:
        return None

    return max(
        pairs,
        key=lambda p: safe_float(
            (p.get("liquidity") or {}).get("usd")
        )
    )


# ============================================================
# ANALYZE TOKEN
# ============================================================

def analyze_token(pair, profile):

    base_token = pair.get("baseToken") or {}

    name = (
        base_token.get("name")
        or profile.get("name")
        or "Unknown"
    )

    symbol = (
        base_token.get("symbol")
        or profile.get("symbol")
        or "UNKNOWN"
    )

    address = (
        base_token.get("address")
        or profile.get("tokenAddress")
        or ""
    )

    chain = (
        pair.get("chainId")
        or profile.get("chainId")
        or "unknown"
    )

    market_cap_raw = pair.get("marketCap")

    if market_cap_raw is not None:
        market_cap = safe_float(
            market_cap_raw,
            default=-1
        )
    else:
        market_cap = None

    liquidity = safe_float(
        (pair.get("liquidity") or {}).get("usd")
    )

    volume = safe_float(
        (pair.get("volume") or {}).get("h24")
    )

    txns_data = pair.get("txns") or {}
    h24 = txns_data.get("h24") or {}

    buys = safe_int(h24.get("buys"))
    sells = safe_int(h24.get("sells"))

    transactions = buys + sells

    age = calculate_age_hours(
        pair.get("pairCreatedAt")
    )

    if age is None:
        return None, "no age data"

    if age > MAX_AGE_HOURS:
        return None, "older than 24 hours"

    if volume > MAX_VOLUME_HARD:
        return None, "volume too high"

    if transactions > MAX_TXNS_HARD:
        return None, "transaction activity too high"

    # --------------------------------------------------------
    # MARKET CAP
    # --------------------------------------------------------

    if market_cap is not None:

        if market_cap <= 0:
            market_cap = None

        elif market_cap > MAX_EARLY_MC:
            return None, "above $10K MC"

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = 0
    reasons = []

    # --------------------------------------------------------
    # MARKET CAP SCORE
    # --------------------------------------------------------

    if market_cap is not None:

        if market_cap < 1000:
            score += 20
            reasons.append("sub-$1K MC")

        elif market_cap <= 2000:
            score += 40
            reasons.append("$1K-$2K MC")

        elif market_cap <= 4000:
            score += 32
            reasons.append("$2K-$4K MC")

        elif market_cap <= 7000:
            score += 22
            reasons.append("$4K-$7K MC")

        elif market_cap <= 10000:
            score += 12
            reasons.append("$7K-$10K MC")

    else:
        # MC unavailable.
        # We DO NOT pretend this means $0.
        score += 15
        reasons.append("MC not available yet")

    # --------------------------------------------------------
    # AGE SCORE
    # --------------------------------------------------------

    if age <= 1:
        score += 35
        reasons.append("extremely fresh")

    elif age <= 3:
        score += 30
        reasons.append("very fresh")

    elif age <= 6:
        score += 25
        reasons.append("fresh launch")

    elif age <= 12:
        score += 15
        reasons.append("still early")

    else:
        score += 5

    # --------------------------------------------------------
    # VOLUME SCORE
    # --------------------------------------------------------

    if volume <= 500:
        score += 25
        reasons.append("almost zero volume")

    elif volume <= 2500:
        score += 20
        reasons.append("very low volume")

    elif volume <= 5000:
        score += 15
        reasons.append("low volume")

    elif volume <= 10000:
        score += 8
        reasons.append("moderate early volume")

    # --------------------------------------------------------
    # TRANSACTION SCORE
    # --------------------------------------------------------

    if transactions <= 20:
        score += 25
        reasons.append("almost no transactions")

    elif transactions <= 50:
        score += 20
        reasons.append("very few transactions")

    elif transactions <= 100:
        score += 15
        reasons.append("low transactions")

    elif transactions <= 300:
        score += 8
        reasons.append("limited transactions")

    # --------------------------------------------------------
    # LIQUIDITY SCORE
    # --------------------------------------------------------

    if liquidity <= 1000:
        score += 20
        reasons.append("ultra-low liquidity")

    elif liquidity <= 3000:
        score += 15
        reasons.append("very low liquidity")

    elif liquidity <= 7000:
        score += 10
        reasons.append("low liquidity")

    elif liquidity <= 15000:
        score += 5

    # --------------------------------------------------------
    # MEME / PROJECT SIGNAL
    # --------------------------------------------------------

    description = profile.get("description") or ""

    searchable = (
        f"{name} {symbol} {description}"
    ).lower()

    if any(
        keyword in searchable
        for keyword in MEME_KEYWORDS
    ):
        score += 10
        reasons.append("meme/project signal")

    # --------------------------------------------------------
    # FINAL QUALIFICATION
    # --------------------------------------------------------

    # We want genuine early projects.
    # Score 45+ is enough now.
    if score < 45:
        return None, "score below 45"

    return {
        "score": min(score, 100),
        "name": name,
        "symbol": symbol,
        "address": address,
        "chain": chain,
        "market_cap": market_cap,
        "liquidity": liquidity,
        "volume": volume,
        "transactions": transactions,
        "age": age,
        "reasons": reasons,
        "dex_url": pair.get("url"),
    }, None


# ============================================================
# ALERT MESSAGE
# ============================================================

def build_alert(data):

    mc = data["market_cap"]

    if mc is None:
        mc_text = "Not available"
    else:
        mc_text = format_money(mc)

    if mc is not None and mc <= 2000:
        attention = "🔥 GROUND ZERO"

    elif mc is not None and mc <= 4000:
        attention = "🟢 VERY EARLY"

    elif mc is not None and mc <= 10000:
        attention = "🟡 EARLY"

    else:
        attention = "🟣 EARLY / MC UNKNOWN"

    reasons = "\n".join(
        f"• {reason}"
        for reason in data["reasons"]
    )

    return f"""
🥷 MYSTERIOUS EARLY RADAR

{attention}

🚨 EARLY PROJECT DETECTED

{data["name"]} (${data["symbol"]})

🎯 Radar Score: {data["score"]}/100

💰 Market Cap: {mc_text}
⛓ Chain: {data["chain"]}
⏱ Pair Age: {format_age(data["age"])}

💧 Liquidity: {format_money(data["liquidity"])}
📊 24H Volume: {format_money(data["volume"])}
🔄 24H Txns: {data["transactions"]}

WHY IT WAS FLAGGED
{reasons}

🔗 DexScreener:
{data["dex_url"] or "Unavailable"}

📍 Contract:
{data["address"]}

⚠️ Radar signal only. Do your own research.
""".strip()


# ============================================================
# MAIN
# ============================================================

def main():

    print("🥷 MYSTERIOUS EARLY RADAR STARTED")

    print("🎯 Main target: $1K+ MC")
    print("🔥 Highest priority: $1K-$2K")
    print("🟢 Strong priority: $2K-$4K")
    print("🟡 Still eligible: $4K-$10K")
    print("⏱ Maximum age: 24 hours")

    seen = load_memory()

    print(
        f"🧠 Previously alerted: {len(seen)}"
    )

    profiles = get_latest_profiles()

    print(
        f"📡 Profiles received: {len(profiles)}"
    )

    if not profiles:
        print("⚠️ No profiles returned.")
        save_memory(seen)
        return

    stats = {
        "profiles": len(profiles),
        "pairs": 0,
        "under_2k": 0,
        "2k_4k": 0,
        "4k_10k": 0,
        "above_10k": 0,
        "no_mc": 0,
        "qualified": 0,
        "rejected": 0,
    }

    candidates = []

    # --------------------------------------------------------
    # SCAN
    # --------------------------------------------------------

    for profile in profiles:

        token_address = profile.get(
            "tokenAddress"
        )

        chain = profile.get(
            "chainId"
        )

        if not token_address or not chain:
            stats["rejected"] += 1
            continue

        # Already alerted?
        if token_address in seen:
            continue

        pairs = get_pairs(
            chain,
            token_address
        )

        if not pairs:
            stats["rejected"] += 1
            continue

        stats["pairs"] += 1

        pair = choose_best_pair(pairs)

        if not pair:
            stats["rejected"] += 1
            continue

        market_cap_raw = pair.get("marketCap")

        if market_cap_raw is None:
            stats["no_mc"] += 1

        else:
            mc = safe_float(
                market_cap_raw,
                default=0
            )

            if mc > 10000:
                stats["above_10k"] += 1
                stats["rejected"] += 1
                continue

            if mc <= 2000:
                stats["under_2k"] += 1

            elif mc <= 4000:
                stats["2k_4k"] += 1

            else:
                stats["4k_10k"] += 1

        result, rejection_reason = analyze_token(
            pair,
            profile
        )

        if not result:

            stats["rejected"] += 1

            print(
                f"⏭️ Rejected "
                f"{token_address[:10]}... "
                f"| {rejection_reason}"
            )

            continue

        stats["qualified"] += 1

        candidates.append(
            (
                token_address,
                result
            )
        )

    # --------------------------------------------------------
    # RANK CANDIDATES
    # --------------------------------------------------------

    candidates.sort(
        key=lambda item: item[1]["score"],
        reverse=True
    )

    print("")
    print("===== RADAR SCAN RESULTS =====")

    print(
        f"Profiles received: {stats['profiles']}"
    )

    print(
        f"Pairs found: {stats['pairs']}"
    )

    print(
        f"$1K-$2K: {stats['under_2k']}"
    )

    print(
        f"$2K-$4K: {stats['2k_4k']}"
    )

    print(
        f"$4K-$10K: {stats['4k_10k']}"
    )

    print(
        f"MC unavailable: {stats['no_mc']}"
    )

    print(
        f"Qualified: {stats['qualified']}"
    )

    print(
        f"Rejected: {stats['rejected']}"
    )

    print("==============================")

    # --------------------------------------------------------
    # SEND ALERTS
    # --------------------------------------------------------

    alerts_sent = 0

    for token_address, result in candidates:

        if alerts_sent >= MAX_ALERTS_PER_RUN:
            break

        message = build_alert(result)

        success = send_telegram(message)

        if success:

            print(
                f"🚨 ALERT SENT: "
                f"${result['symbol']} "
                f"| MC={format_money(result['market_cap'])} "
                f"| Score={result['score']}"
            )

            # Only remember projects AFTER
            # Telegram successfully receives them.
            seen.add(token_address)

            alerts_sent += 1

        else:

            print(
                f"❌ ALERT FAILED: "
                f"${result['symbol']}"
            )

        time.sleep(0.5)

    # --------------------------------------------------------
    # SAVE MEMORY
    # --------------------------------------------------------

    print("")
    print(
        f"🚨 Alerts sent this run: {alerts_sent}"
    )

    print(
        f"🧠 Memory before save: {len(seen)}"
    )

    save_memory(seen)

    print(
        f"🧠 Memory after save: {len(seen)}"
    )

    print(
        f"DEBUG: seen sample = {list(seen)[:5]}"
    )

    print("✅ RADAR SCAN COMPLETED")


if __name__ == "__main__":
    main()
