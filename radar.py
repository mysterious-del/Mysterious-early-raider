import os
import json
import time
import requests
from datetime import datetime, timezone

# ============================================================
# MYSTERIOUS EARLY RADAR
# Ground-zero scanner: $0-$4K MC, tiny volume, tiny audience
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

DEX_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
DEX_PAIRS_URL = "https://api.dexscreener.com/token-pairs/v1"

MEMORY_FILE = "seen_tokens.json"

# ------------------------------------------------------------
# HARD LIMITS
# ------------------------------------------------------------

MAX_MARKET_CAP = 4000

# Ground-zero preferences
MAX_VOLUME = 5000
MAX_TXNS = 150
MAX_LIQUIDITY = 10000
MAX_AGE_HOURS = 6

# Extremely fresh projects get priority
VERY_EARLY_HOURS = 1

# Maximum alerts per GitHub Actions run
MAX_ALERTS_PER_RUN = 10

# ------------------------------------------------------------
# KEYWORDS
# ------------------------------------------------------------

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
    "elon",
    "trump",
    "anime",
    "ai",
    "frog",
    "cult",
    "community",
    "coin",
    "token",
    "sol",
    "based",
    "degen",
]

# ------------------------------------------------------------
# BASIC HELPERS
# ------------------------------------------------------------

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


def age_hours(pair_created_at):
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


def telegram_send(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ Missing BOT_TOKEN or CHAT_ID")
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


# ------------------------------------------------------------
# DEXSCREENER
# ------------------------------------------------------------

def get_latest_profiles():
    try:
        response = requests.get(
            DEX_PROFILES_URL,
            timeout=20
        )

        if not response.ok:
            print(
                "❌ Profile API error:",
                response.status_code
            )
            return []

        data = response.json()

        if not isinstance(data, list):
            return []

        return data

    except Exception as e:
        print("❌ Profile fetch error:", e)
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
            f"⚠️ Pair lookup failed for {address}: {e}"
        )
        return []


# ------------------------------------------------------------
# PROJECT DATA
# ------------------------------------------------------------

def get_pair_data(profile):
    chain = profile.get("chainId")
    token_address = profile.get("tokenAddress")

    if not chain or not token_address:
        return None

    pairs = get_pairs(
        chain,
        token_address
    )

    if not pairs:
        return None

    # Pick the pair with the most useful liquidity.
    pairs = sorted(
        pairs,
        key=lambda p: safe_float(
            (p.get("liquidity") or {}).get("usd")
        ),
        reverse=True
    )

    return pairs[0]


def get_token_name(pair, profile):
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

    return name, symbol, address


# ------------------------------------------------------------
# GROUND-ZERO FILTER
# ------------------------------------------------------------

def evaluate_ground_zero(pair, profile):
    liquidity = safe_float(
        (pair.get("liquidity") or {}).get("usd")
    )

    volume = safe_float(
        (pair.get("volume") or {}).get("h24")
    )

    txns_data = pair.get("txns") or {}
    h24_txns = txns_data.get("h24") or {}

    buys = safe_int(h24_txns.get("buys"))
    sells = safe_int(h24_txns.get("sells"))

    txns = buys + sells

    market_cap_raw = pair.get("marketCap")

    if market_cap_raw is not None:
        market_cap = safe_float(
            market_cap_raw,
            default=-1
        )
    else:
        market_cap = None

    age = age_hours(
        pair.get("pairCreatedAt")
    )

    reasons = []
    score = 0

    # --------------------------------------------------------
    # HARD MARKET CAP RULE
    # --------------------------------------------------------

    if market_cap is not None:

        if market_cap <= 0:
            # Unknown/zero MC is not automatically treated
            # as $0. We use the strict fallback below.
            market_cap_known = False

        elif market_cap > MAX_MARKET_CAP:
            return None

        else:
            market_cap_known = True

            if market_cap <= 1000:
                score += 35
                reasons.append("MC under $1K")

            elif market_cap <= 2000:
                score += 30
                reasons.append("MC under $2K")

            elif market_cap <= 3000:
                score += 25
                reasons.append("MC under $3K")

            else:
                score += 20
                reasons.append("MC under $4K")

    else:
        market_cap_known = False

    # --------------------------------------------------------
    # AGE
    # --------------------------------------------------------

    if age is None:
        return None

    if age > MAX_AGE_HOURS:
        return None

    if age <= VERY_EARLY_HOURS:
        score += 30
        reasons.append("extremely fresh")

    elif age <= 3:
        score += 25
        reasons.append("very fresh")

    else:
        score += 15
        reasons.append("fresh launch")

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    if volume > MAX_VOLUME:
        return None

    if volume <= 500:
        score += 20
        reasons.append("almost zero volume")

    elif volume <= 1500:
        score += 15
        reasons.append("very low volume")

    elif volume <= 3000:
        score += 10
        reasons.append("low volume")

    else:
        score += 5
        reasons.append("limited volume")

    # --------------------------------------------------------
    # TRANSACTIONS
    # --------------------------------------------------------

    if txns > MAX_TXNS:
        return None

    if txns <= 20:
        score += 20
        reasons.append("almost no transactions")

    elif txns <= 50:
        score += 15
        reasons.append("very few transactions")

    elif txns <= 100:
        score += 10
        reasons.append("low transaction count")

    else:
        score += 5

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    if liquidity <= 0:
        return None

    if liquidity > MAX_LIQUIDITY:
        return None

    if liquidity <= 1000:
        score += 20
        reasons.append("ultra-low liquidity")

    elif liquidity <= 3000:
        score += 15
        reasons.append("very low liquidity")

    elif liquidity <= 5000:
        score += 10
        reasons.append("low liquidity")

    else:
        score += 5

    # --------------------------------------------------------
    # NAME / DESCRIPTION SIGNAL
    # --------------------------------------------------------

    name, symbol, address = get_token_name(
        pair,
        profile
    )

    searchable = " ".join([
        str(name),
        str(symbol),
        str(profile.get("description") or "")
    ]).lower()

    if any(
        keyword in searchable
        for keyword in MEME_KEYWORDS
    ):
        score += 10
        reasons.append("meme/project signal")

    # --------------------------------------------------------
    # STRICT FALLBACK WHEN MC IS NOT AVAILABLE
    # --------------------------------------------------------

    # IMPORTANT:
    # We NEVER pretend that missing MC = $0.
    #
    # If DEX Screener doesn't provide MC, the project can
    # still qualify only if EVERYTHING ELSE is extremely tiny.

    if not market_cap_known:

        if liquidity > 5000:
            return None

        if volume > 2500:
            return None

        if txns > 75:
            return None

        if age > 3:
            return None

        reasons.append("MC not available — ultra-early fallback")

    # --------------------------------------------------------
    # FINAL SCORE REQUIREMENT
    # --------------------------------------------------------

    if score < 60:
        return None

    return {
        "score": min(score, 100),
        "market_cap": market_cap,
        "market_cap_known": market_cap_known,
        "liquidity": liquidity,
        "volume": volume,
        "txns": txns,
        "age": age,
        "name": name,
        "symbol": symbol,
        "address": address,
        "chain": pair.get("chainId") or profile.get("chainId"),
        "dex_url": pair.get("url"),
        "reasons": reasons,
        "profile": profile
    }


# ------------------------------------------------------------
# ALERT
# ------------------------------------------------------------

def build_alert(data):
    if data["market_cap_known"]:
        mc_text = format_money(
            data["market_cap"]
        )
    else:
        mc_text = "Not available yet"

    if data["age"] <= 1:
        attention = "🔴 GROUND ZERO"
    elif data["age"] <= 3:
        attention = "🟠 VERY EARLY"
    else:
        attention = "🟡 EARLY"

    reasons = "\n".join(
        f"• {reason}"
        for reason in data["reasons"]
    )

    message = f"""
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
🔄 24H Txns: {data["txns"]}

WHY IT WAS FLAGGED
{reasons}

🔗 DexScreener:
{data["dex_url"] or "Unavailable"}

📍 Contract:
{data["address"]}

⚠️ Radar signal only. Do your own research.
""".strip()

    return message


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------

def main():

    print("🥷 Mysterious Early Radar started")
    print(
        f"🎯 Ground-zero ceiling: ${MAX_MARKET_CAP:,} MC"
    )
    print(
        f"📊 Max volume: ${MAX_VOLUME:,}"
    )
    print(
        f"🔄 Max transactions: {MAX_TXNS}"
    )
    print(
        f"💧 Max liquidity: ${MAX_LIQUIDITY:,}"
    )
    print(
        f"⏱ Max age: {MAX_AGE_HOURS} hours"
    )

    seen = load_memory()

    print(
        f"🧠 Existing alerted projects: {len(seen)}"
    )

    profiles = get_latest_profiles()

    print(
        f"Found {len(profiles)} latest token profiles"
    )

    alerts_sent = 0

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # We ONLY put projects into memory AFTER an alert.
    #
    # This means a project that is too big today can become
    # eligible later and still be discovered.
    # --------------------------------------------------------

    for profile in profiles:

        if alerts_sent >= MAX_ALERTS_PER_RUN:
            break

        token_address = profile.get(
            "tokenAddress"
        )

        if not token_address:
            continue

        # Already alerted?
        if token_address in seen:
            continue

        pair = get_pair_data(profile)

        if not pair:
            # No pair yet.
            #
            # DEX Screener cannot reliably give us a
            # pre-launch project here, so we don't fake
            # an alert.
            continue

        result = evaluate_ground_zero(
            pair,
            profile
        )

        if not result:
            # DO NOT add to memory.
            #
            # This allows it to become eligible later.
            continue

        message = build_alert(result)

        if telegram_send(message):

            print(
                f"🚨 Ground-zero alert sent: "
                f"{result['symbol']} | "
                f"MC={result['market_cap']} | "
                f"Score={result['score']}"
            )

            seen.add(token_address)
            alerts_sent += 1

        else:
            print(
                f"❌ Alert failed: "
                f"{result['symbol']}"
            )

        time.sleep(0.5)

    # --------------------------------------------------------
    # SAVE MEMORY
    # --------------------------------------------------------

    print(
        f"DEBUG: seen tokens before save = {len(seen)}"
    )

    save_memory(seen)

    print(
        f"DEBUG: seen sample = "
        f"{list(seen)[:5]}"
    )

    print(
        f"✅ Radar scan completed. "
        f"Ground-zero alerts sent: {alerts_sent}"
    )


if __name__ == "__main__":
    main()
