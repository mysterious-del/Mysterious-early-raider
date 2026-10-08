import os
import json
import time
import requests
from datetime import datetime, timezone

# ============================================================
# MYSTERIOUS EARLY RADAR
# Quiet / Ground-Zero Scanner
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

DEX_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
DEX_PAIRS_URL = "https://api.dexscreener.com/token-pairs/v1"

MEMORY_FILE = "seen_tokens.json"

# ============================================================
# TARGET RANGE
# ============================================================

MIN_MC = 1000
MAX_MC = 10000

GROUND_ZERO_MAX_MC = 4000

PREFERRED_AGE_HOURS = 6
MAX_AGE_HOURS = 24

# ============================================================
# QUIET ACTIVITY LIMITS
# ============================================================

# $1K-$4K:
# Allow genuinely early projects, but reject obvious activity spikes.

GROUND_ZERO_MAX_VOLUME = 10000
GROUND_ZERO_MAX_TXNS = 150

# $4K-$10K:
# Higher MC projects need to be exceptionally quiet.

HIGHER_MC_MAX_VOLUME = 5000
HIGHER_MC_MAX_TXNS = 100

# Absolute safety ceilings.

MAX_VOLUME_HARD = 100000
MAX_TXNS_HARD = 1500

# Minimum score after hard activity gates.

MIN_SCORE = 45

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


def money(value):
    if value is None:
        return "Unknown"

    value = safe_float(value)

    if value < 1000:
        return f"${value:,.0f}"

    if value < 1_000_000:
        return f"${value / 1000:.1f}K"

    return f"${value / 1_000_000:.2f}M"


def age_hours(timestamp):
    if not timestamp:
        return None

    try:
        created = datetime.fromtimestamp(
            int(timestamp) / 1000,
            tz=timezone.utc
        )

        now = datetime.now(timezone.utc)

        return max(
            0,
            (now - created).total_seconds() / 3600
        )

    except Exception:
        return None


def age_text(hours):
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

    try:
        response = requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": message,
                "disable_web_page_preview": False
            },
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

def latest_profiles():

    try:
        response = requests.get(
            DEX_PROFILES_URL,
            timeout=20
        )

        if not response.ok:
            print(
                "❌ Profile API:",
                response.status_code
            )
            return []

        data = response.json()

        return data if isinstance(data, list) else []

    except Exception as e:
        print("❌ Profile error:", e)
        return []


def token_pairs(chain, address):

    try:
        url = f"{DEX_PAIRS_URL}/{chain}/{address}"

        response = requests.get(
            url,
            timeout=20
        )

        if not response.ok:
            return []

        data = response.json()

        return data if isinstance(data, list) else []

    except Exception as e:
        print(
            f"⚠️ Pair error {address}: {e}"
        )
        return []


def best_pair(pairs):

    if not pairs:
        return None

    return max(
        pairs,
        key=lambda p: safe_float(
            (p.get("liquidity") or {}).get("usd"),
            0
        )
    )


# ============================================================
# ANALYSIS
# ============================================================

def analyze(pair, profile):

    base = pair.get("baseToken") or {}

    name = (
        base.get("name")
        or profile.get("name")
        or "Unknown"
    )

    symbol = (
        base.get("symbol")
        or profile.get("symbol")
        or "UNKNOWN"
    )

    address = (
        base.get("address")
        or profile.get("tokenAddress")
        or ""
    )

    chain = (
        pair.get("chainId")
        or profile.get("chainId")
        or "unknown"
    )

    # --------------------------------------------------------
    # MARKET CAP
    # --------------------------------------------------------

    mc_raw = pair.get("marketCap")

    if mc_raw is None:
        return None, "MC unavailable"

    mc = safe_float(mc_raw, -1)

    if mc <= 0:
        return None, "invalid MC"

    if mc < MIN_MC:
        return None, "below $1K"

    if mc > MAX_MC:
        return None, "above $10K"

    # --------------------------------------------------------
    # AGE
    # --------------------------------------------------------

    age = age_hours(
        pair.get("pairCreatedAt")
    )

    if age is None:
        return None, "age unavailable"

    if age > MAX_AGE_HOURS:
        return None, "older than 24h"

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    volume = safe_float(
        (pair.get("volume") or {}).get("h24"),
        0
    )

    if volume > MAX_VOLUME_HARD:
        return None, "volume too high"

    # --------------------------------------------------------
    # TRANSACTIONS
    # --------------------------------------------------------

    txns = pair.get("txns") or {}
    h24 = txns.get("h24") or {}

    buys = safe_int(h24.get("buys"))
    sells = safe_int(h24.get("sells"))

    transactions = buys + sells

    if transactions > MAX_TXNS_HARD:
        return None, "transactions too high"

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    liquidity_data = pair.get("liquidity") or {}

    liquidity_raw = liquidity_data.get("usd")

    if liquidity_raw is None:
        liquidity = None
    else:
        liquidity = safe_float(
            liquidity_raw,
            0
        )

    liquidity_valid = (
        liquidity is not None
        and liquidity > 0
    )

    # ========================================================
    # ACTIVITY GATES
    # ========================================================

    if mc <= GROUND_ZERO_MAX_MC:

        # $1K-$4K
        # This is our main hunting zone.

        if volume > GROUND_ZERO_MAX_VOLUME:
            return None, "volume above $10K"

        if transactions > GROUND_ZERO_MAX_TXNS:
            return None, "transactions above 150"

    else:

        # $4K-$10K
        # These need to be quieter.

        if volume > HIGHER_MC_MAX_VOLUME:
            return None, "higher MC with too much volume"

        if transactions > HIGHER_MC_MAX_TXNS:
            return None, "higher MC with too many transactions"

    # ========================================================
    # SCORE
    # ========================================================

    score = 0
    reasons = []

    # --------------------------------------------------------
    # MC
    # --------------------------------------------------------

    if mc <= 2000:
        score += 20
        reasons.append("$1K-$2K MC")

    elif mc <= 4000:
        score += 16
        reasons.append("$2K-$4K MC")

    elif mc <= 7000:
        score += 9
        reasons.append("$4K-$7K MC")

    else:
        score += 4
        reasons.append("$7K-$10K MC")

    # --------------------------------------------------------
    # AGE
    # --------------------------------------------------------

    if age <= 1:
        score += 25
        reasons.append("under 1 hour old")

    elif age <= 3:
        score += 22
        reasons.append("very fresh")

    elif age <= 6:
        score += 18
        reasons.append("under 6 hours old")

    elif age <= 12:
        score += 10
        reasons.append("still relatively fresh")

    else:
        score += 3
        reasons.append("under 24 hours old")

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    if volume <= 1000:
        score += 25
        reasons.append("very low volume")

    elif volume <= 5000:
        score += 18
        reasons.append("low volume")

    elif volume <= 10000:
        score += 8
        reasons.append("moderate volume")

    # --------------------------------------------------------
    # TRANSACTIONS
    # --------------------------------------------------------

    if transactions <= 30:
        score += 25
        reasons.append("very few transactions")

    elif transactions <= 100:
        score += 18
        reasons.append("low transactions")

    elif transactions <= 150:
        score += 8
        reasons.append("moderate transactions")

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    if not liquidity_valid:

        # Important:
        # Unknown liquidity is allowed,
        # but it receives ZERO score.

        reasons.append("liquidity unavailable")

    elif liquidity <= 1500:
        score += 12
        reasons.append("very low liquidity")

    elif liquidity <= 5000:
        score += 8
        reasons.append("low liquidity")

    elif liquidity <= 10000:
        score += 4
        reasons.append("moderate liquidity")

    else:
        score += 2
        reasons.append("healthy early liquidity")

    # --------------------------------------------------------
    # MEME SIGNAL
    # --------------------------------------------------------

    description = profile.get(
        "description"
    ) or ""

    searchable = (
        f"{name} {symbol} {description}"
    ).lower()

    if any(
        keyword in searchable
        for keyword in MEME_KEYWORDS
    ):
        score += 8
        reasons.append("meme/project signal")

    # ========================================================
    # FINAL SCORE
    # ========================================================

    if score < MIN_SCORE:
        return None, f"score too low ({score})"

    return {
        "score": max(0, min(score, 100)),
        "name": name,
        "symbol": symbol,
        "address": address,
        "chain": chain,
        "market_cap": mc,
        "age": age,
        "volume": volume,
        "transactions": transactions,
        "liquidity": liquidity,
        "liquidity_valid": liquidity_valid,
        "reasons": reasons,
        "dex_url": pair.get("url"),
    }, None


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def build_message(data):

    mc = data["market_cap"]

    if mc <= 2000:
        level = "🔥 GROUND ZERO"

    elif mc <= 4000:
        level = "🟢 VERY EARLY"

    elif mc <= 7000:
        level = "🟡 EARLY"

    else:
        level = "🟠 EARLY"

    liquidity_text = (
        money(data["liquidity"])
        if data["liquidity_valid"]
        else "Unavailable"
    )

    reasons = "\n".join(
        f"• {x}"
        for x in data["reasons"]
    )

    return f"""
🥷 MYSTERIOUS EARLY RADAR

{level}

🚨 EARLY PROJECT DETECTED

{data["name"]} (${data["symbol"]})

🎯 Radar Score: {data["score"]}/100

💰 Market Cap: {money(data["market_cap"])}
⛓ Chain: {data["chain"]}
⏱ Pair Age: {age_text(data["age"])}

💧 Liquidity: {liquidity_text}
📊 24H Volume: {money(data["volume"])}
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

    print("")
    print("🥷 MYSTERIOUS EARLY RADAR STARTED")
    print("")
    print("🎯 Target: $1K-$10K MC")
    print("🔥 Best zone: $1K-$4K")
    print("⏱ Preferred age: under 6 hours")
    print("📊 Quiet activity strongly preferred")
    print("💧 Unknown liquidity allowed — no score bonus")
    print("")

    seen = load_memory()

    print(
        f"🧠 Previously alerted: {len(seen)}"
    )

    profiles = latest_profiles()

    print(
        f"📡 Profiles received: {len(profiles)}"
    )

    stats = {
        "profiles": len(profiles),
        "pairs": 0,
        "under_2k": 0,
        "2k_4k": 0,
        "4k_7k": 0,
        "7k_10k": 0,
        "above_10k": 0,
        "below_1k": 0,
        "qualified": 0,
        "rejected": 0,
    }

    candidates = []

    # ========================================================
    # SCAN
    # ========================================================

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

        # Only skip projects we've actually alerted before.
        if token_address in seen:
            continue

        pairs = token_pairs(
            chain,
            token_address
        )

        if not pairs:
            stats["rejected"] += 1
            continue

        stats["pairs"] += 1

        pair = best_pair(pairs)

        if not pair:
            stats["rejected"] += 1
            continue

        mc_raw = pair.get("marketCap")

        if mc_raw is not None:

            mc = safe_float(
                mc_raw,
                -1
            )

            if mc < MIN_MC:
                stats["below_1k"] += 1
                stats["rejected"] += 1
                continue

            if mc > MAX_MC:
                stats["above_10k"] += 1
                stats["rejected"] += 1
                continue

            if mc <= 2000:
                stats["under_2k"] += 1

            elif mc <= 4000:
                stats["2k_4k"] += 1

            elif mc <= 7000:
                stats["4k_7k"] += 1

            else:
                stats["7k_10k"] += 1

        result, reason = analyze(
            pair,
            profile
        )

        if not result:

            stats["rejected"] += 1

            print(
                f"⏭️ Rejected "
                f"{token_address[:10]}... "
                f"| {reason}"
            )

            continue

        stats["qualified"] += 1

        candidates.append(
            (
                token_address,
                result
            )
        )

    # ========================================================
    # RANK
    # ========================================================

    candidates.sort(
        key=lambda x: x[1]["score"],
        reverse=True
    )

    # ========================================================
    # LOG
    # ========================================================

    print("")
    print("========== RADAR RESULTS ==========")

    print(
        f"Profiles: {stats['profiles']}"
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
        f"$4K-$7K: {stats['4k_7k']}"
    )

    print(
        f"$7K-$10K: {stats['7k_10k']}"
    )

    print(
        f"Below $1K: {stats['below_1k']}"
    )

    print(
        f"Above $10K: {stats['above_10k']}"
    )

    print(
        f"Qualified: {stats['qualified']}"
    )

    print(
        f"Rejected: {stats['rejected']}"
    )

    print("====================================")

    # ========================================================
    # TELEGRAM
    # ========================================================

    alerts_sent = 0

    for token_address, result in candidates:

        if alerts_sent >= MAX_ALERTS_PER_RUN:
            break

        message = build_message(
            result
        )

        if send_telegram(message):

            print(
                f"🚨 ALERT SENT: "
                f"${result['symbol']} | "
                f"MC={money(result['market_cap'])} | "
                f"Score={result['score']}"
            )

            # Remember only after successful Telegram alert.
            seen.add(token_address)

            alerts_sent += 1

        else:

            print(
                f"❌ ALERT FAILED: "
                f"${result['symbol']}"
            )

        time.sleep(0.5)

    # ========================================================
    # SAVE
    # ========================================================

    print("")
    print(
        f"🚨 Alerts sent: {alerts_sent}"
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

    print("")
    print("✅ RADAR SCAN COMPLETED")


if __name__ == "__main__":
    main()
