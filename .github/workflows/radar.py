import os
import re
import requests
from datetime import datetime, timezone

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

SEARCH_TERMS = [
    '"launching soon" memecoin',
    '"coming soon" memecoin',
    '"building" memecoin Solana',
    '"stealth launch" crypto',
    '"launch soon" Solana',
    '"fair launch" memecoin',
    '"something is cooking" crypto',
    '"new meme" Solana',
    '"token coming" Solana',
    '"building on Base" memecoin',
]

MAX_FOLLOWERS = 50

PROJECT_WORDS = [
    "memecoin", "meme coin", "token", "coin",
    "solana", "base", "ethereum", "bnb",
    "launch", "building", "community",
    "fair launch", "stealth", "ca soon"
]

PRELAUNCH_WORDS = [
    "building", "launching soon", "coming soon",
    "launch soon", "stealth", "pre-launch",
    "token soon", "fair launch", "something is cooking"
]

LAUNCHED_WORDS = [
    "launched", "launch", "live", "now live",
    "trading", "contract address", "ca:"
]


def telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": message,
            "disable_web_page_preview": False
        },
        timeout=20
    )


def classify(text):
    text = text.lower()

    pre = any(word in text for word in PRELAUNCH_WORDS)
    launched = any(word in text for word in LAUNCHED_WORDS)

    if pre and not launched:
        return "🟢 PRE-LAUNCH"

    if launched:
        return "🔵 RECENTLY LAUNCHED"

    return "🟡 EARLY PROJECT"


def calculate_score(followers, text):
    text = text.lower()
    score = 0

    if followers == 0:
        score += 40
    elif followers <= 10:
        score += 35
    elif followers <= 25:
        score += 30
    elif followers <= 50:
        score += 25

    if any(word in text for word in PROJECT_WORDS):
        score += 20

    if any(word in text for word in PRELAUNCH_WORDS):
        score += 25

    if any(word in text for word in LAUNCHED_WORDS):
        score += 10

    return min(score, 100)


def search_web(query):
    """
    Placeholder discovery layer.

    We deliberately keep discovery separate from scoring.
    Later we can connect this to the strongest available
    X/search data source without rebuilding the radar.
    """

    return []


def process_results(results):
    for item in results:

        username = item.get("username", "")
        followers = int(item.get("followers", 999999))
        text = item.get("text", "")
        url = item.get("url", "")

        if followers > MAX_FOLLOWERS:
            continue

        if not username or not url:
            continue

        score = calculate_score(followers, text)

        if score < 60:
            continue

        status = classify(text)

        if followers == 0:
            priority = "🔥 0-FOLLOWER SPECIAL"
        elif followers <= 10:
            priority = "🔥 EXTREMELY EARLY"
        elif followers <= 25:
            priority = "🟠 VERY EARLY"
        else:
            priority = "🟡 EARLY"

        message = f"""
🥷 MYSTERIOUS EARLY RADAR

{priority}

{status}

👤 @{username}
👥 Followers: {followers}
🎯 Early Score: {score}/100

🔎 Why flagged:
Low-attention crypto/project account with strong early-stage signals.

📝 Signal:
{text[:500]}

🎯 Opportunity:
Check the account and identify the founder/dev before attention grows.

🔗 {url}
"""

        telegram(message.strip())


def main():
    print("🥷 Mysterious Early Radar started")

    for query in SEARCH_TERMS:
        results = search_web(query)
        process_results(results)

    print("Radar scan completed")


if __name__ == "__main__":
    main()
