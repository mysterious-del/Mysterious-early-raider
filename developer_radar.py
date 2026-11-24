import os, re, json, time, hashlib, requests
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

ROOT = Path(__file__).resolve().parent
SEEN = ROOT / "seen_accounts.json"
LEADS = ROOT / "leads.json"
BOT = os.getenv("BOT_TOKEN", "")
CHAT = os.getenv("CHAT_ID", "")
LOOKBACK = int(os.getenv("LOOKBACK_DAYS", "14"))
MIN_SCORE = int(os.getenv("MIN_LEAD_SCORE", "35"))
MAX_ALERTS = int(os.getenv("MAX_ALERTS_PER_RUN", "8"))
S = requests.Session()
S.headers.update({"User-Agent": "MysteriousEarlyRadar/1.0 (public research)", "Accept": "*/*"})
NOW = lambda: datetime.now(timezone.utc)

QUERIES = [
    'site:x.com/ "building on Solana"',
    'site:x.com/ "Solana" "launching soon"',
    'site:x.com/ "Solana" "coming soon" project',
    'site:x.com/ "Solana" "community manager"',
    'site:x.com/ "Solana" "looking for moderators"',
    'site:x.com/ "Solana" "new project" -airdrop',
]
TERMS = {
    "pre-launch": ["pre-launch", "prelaunch", "launching soon", "coming soon", "before launch", "token launch", "fair launch"],
    "building": ["building on solana", "solana builder", "new solana project", "shipping on solana", "developing on solana"],
    "team need": ["community manager", "looking for moderators", "hiring", "ambassador", "community team", "growth partner"],
    "project": ["introducing", "our project", "our dapp", "our app", "protocol", "web3 startup"],
}
NOISE = [
    "airdrop claim", "free mint", "giveaway", "100x", "signal group", "buy now",
    "tutorial", "beginner guide", "course", "lecture notes", "study notes",
    "awesome list", "awesome-solana", "cheat sheet", "interview questions",
    "sniper bot", "bundler bot", "trading bot", "memecoin screener",
    "whale tracker", "rug checker", "pump fun bot", "download", "crack",
    "solana documentation", "validator setup guide", "rpc benchmark"
]
PROJECT_SIGNALS = [
    "launching", "launch soon", "testnet", "mainnet", "dapp", "protocol",
    "platform", "app", "wallet", "marketplace", "game", "payments",
    "payment", "defi", "dex", "exchange", "staking", "lending",
    "bridge", "infrastructure", "sdk", "dashboard", "beta", "waitlist",
    "early access", "users", "customers", "product"
]

def load(path, default):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return default

def save(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)

def dateval(value):
    if not value: return None
    try:
        d = parsedate_to_datetime(value)
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except Exception:
        try: return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception: return None

def get(url, params=None, headers=None):
    for attempt in range(3):
        try:
            r = S.get(url, params=params, headers=headers, timeout=20)
            if r.status_code == 429:
                wait = 2 ** attempt
                print(f"Rate limited by {url}; retrying in {wait}s")
                time.sleep(wait)
                continue
            if r.status_code in (401, 403):
                print(f"Access denied ({r.status_code}) by {r.url}")
                return None
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            print(f"Request failed (attempt {attempt + 1}/3): {e}")
            if attempt < 2:
                time.sleep(1 + attempt)
    return None

def score(text, source, published):
    t = text.lower()
    points, why = 0, []
    if any(x in t for x in ["solana", "$sol", "sol chain"]):
        points += 20; why.append("Solana relevance")
    weights = {"pre-launch": 22, "building": 18, "team need": 16, "project": 8}
    for group, words in TERMS.items():
        hits = [w for w in words if w in t]
        if hits:
            points += min(weights[group], 8 + 4 * len(hits))
            why.append(group + ": " + ", ".join(hits[:3]))
    if published:
        age = NOW() - published
        if age <= timedelta(hours=24): points += 12; why.append("Published within 24 hours")
        elif age <= timedelta(days=3): points += 8; why.append("Published within 3 days")
        elif age <= timedelta(days=7): points += 4; why.append("Published within 7 days")
    if source == "X search index": points += 8
    elif source == "Bluesky": points += 4
    elif source == "GitHub": points += 10
    if any(x in t for x in NOISE): points -= 18; why.append("Promotional/noise penalty")
    return max(0, min(100, points)), why

def x_handle(url, text):
    m = re.search(r"(?:https?://)?(?:www\.)?x\.com/([A-Za-z0-9_]{1,15})", url, re.I)
    if m and m.group(1).lower() not in {"search","hashtag","home","i","intent","share"}:
        return "@" + m.group(1)
    m = re.search(r"@([A-Za-z0-9_]{1,15})", text)
    return "@" + m.group(1) if m else None

def bing_x():
    results = []
    for q in QUERIES:
        r = get("https://www.bing.com/search", {"q": q, "format": "rss"},
                headers={"Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8"})
        if not r: continue
        try:
            root = ET.fromstring(r.text)
            items = root.findall(".//item")
            if not items:
                print("Bing RSS returned no items for query:", q)
            for item in items:
                title = re.sub("<[^>]+>", " ", item.findtext("title", "")).strip()
                url = (item.findtext("link", "") or "").strip()
                desc = re.sub("<[^>]+>", " ", item.findtext("description", "")).strip()
                published = dateval(item.findtext("pubDate"))
                if "x.com/" not in url.lower(): continue
                if published and published < NOW() - timedelta(days=LOOKBACK): continue
                points, why = score(title + " " + desc, "X search index", published)
                if points < MIN_SCORE: continue
                results.append({
                    "source": "X search index", "handle": x_handle(url, title + " " + desc),
                    "url": url, "title": title[:250], "evidence": desc[:500],
                    "published_at": published.isoformat() if published else None,
                    "followers": None, "engagement": None, "score": points, "reasons": why,
                    "verification": "Indexed public X result. Confirm profile/post manually."
                })
        except ET.ParseError: print("Bing RSS parse error")
        time.sleep(.7)
    return results

def bluesky():
    results = []
    queries = ['"building on Solana"', '"Solana" "launching soon"',
               '"Solana project" "coming soon"', '"community manager" Solana']
    endpoints = [
        "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts",
        "https://bsky.social/xrpc/app.bsky.feed.searchPosts",
    ]
    blocked = set()
    for q in queries:
        response = None
        for endpoint in endpoints:
            if endpoint in blocked:
                continue
            response = get(endpoint, {"q": q, "limit": 30, "sort": "latest"},
                           headers={"Accept": "application/json"})
            if response:
                break
            blocked.add(endpoint)
        if not response:
            continue
        try:
            for post in response.json().get("posts", []):
                rec, author = post.get("record", {}), post.get("author", {})
                text = rec.get("text", "")
                created = dateval(rec.get("createdAt"))
                if created and created < NOW() - timedelta(days=LOOKBACK):
                    continue
                points, why = score(text, "Bluesky", created)
                if points < MIN_SCORE:
                    continue
                handle = author.get("handle")
                uri = post.get("uri", "")
                rkey = uri.rsplit("/", 1)[-1]
                url = f"https://bsky.app/profile/{handle}/post/{rkey}" if handle else ""
                results.append({
                    "source": "Bluesky", "handle": "@" + handle if handle else None,
                    "url": url, "title": text[:250], "evidence": text[:500],
                    "published_at": created.isoformat() if created else None,
                    "followers": author.get("followersCount"),
                    "engagement": {k: post.get(k) for k in ["likeCount", "repostCount", "replyCount"]},
                    "score": points, "reasons": why,
                    "verification": "Bluesky account, not confirmed as an X account or project founder."
                })
        except (ValueError, TypeError, AttributeError) as e:
            print("Bluesky response parse error:", e)
        time.sleep(.5)
    if not results and blocked:
        print("Bluesky search unavailable from this runner; continuing with other sources.")
    return results

def github_solana():
    """Find recent Solana product repositories, not every repo mentioning Solana."""
    results = []
    since = (NOW() - timedelta(days=LOOKBACK)).strftime("%Y-%m-%d")
    queries = [
        f'solana (launch OR testnet OR mainnet OR dapp OR protocol OR platform) created:>={since} fork:false',
        f'solana (wallet OR marketplace OR game OR payments OR defi OR exchange) created:>={since} fork:false',
        f'solana (app OR product OR beta OR waitlist OR infrastructure) pushed:>={since} fork:false',
    ]
    token = os.getenv("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    collected = {}
    for q in queries:
        try:
            r = S.get(
                "https://api.github.com/search/repositories",
                params={"q": q, "sort": "updated", "order": "desc", "per_page": 30},
                headers=headers, timeout=20
            )
            if r.status_code == 403:
                print("GitHub search rate limit or access restriction; skipping this query")
                time.sleep(2)
                continue
            r.raise_for_status()
            for repo in r.json().get("items", []):
                url = repo.get("html_url")
                if url:
                    collected[url] = repo
        except (requests.RequestException, ValueError) as e:
            print("GitHub search failed:", e)
        time.sleep(1.2)

    for repo in collected.values():
    url = repo.get("html_url", "")
    if not url:
        continue

    name = repo.get("full_name", "")
        description = repo.get("description") or ""
        topics = " ".join(repo.get("topics") or [])
        combined = f"{name} {description} {topics}".lower()
        if repo.get("fork") or repo.get("archived"):
            continue
        if any(term in combined for term in NOISE):
            continue
        # Require a real product/build signal; a repo merely named "solana" is not enough.
        signals = [term for term in PROJECT_SIGNALS if term in combined]
        if not description.strip() and not topics:
            continue
        if not signals:
            continue
        created = dateval(repo.get("created_at"))
        pushed = dateval(repo.get("pushed_at"))
        if pushed and pushed < NOW() - timedelta(days=LOOKBACK):
            continue
        if created and created < NOW() - timedelta(days=max(LOOKBACK, 30)) and not pushed:
            continue
        points, why = score(combined + " Solana", "GitHub", pushed or created)
        points += min(12, 4 * len(signals))
        why.append("Product/build signals: " + ", ".join(signals[:4]))
        if pushed and NOW() - pushed <= timedelta(days=3):
            points += 8
            why.append("Repository updated within 3 days")
        elif pushed and NOW() - pushed <= timedelta(days=7):
            points += 4
            why.append("Repository updated within 7 days")
        if created and NOW() - created <= timedelta(days=14):
            points += 4
            why.append("Recently created repository")
        if repo.get("stargazers_count", 0) <= 5:
            points += 2
            why.append("Low-star early-stage repository")
        points = min(100, points)
        if points < MIN_SCORE:
            continue
        owner = (repo.get("owner") or {}).get("login")
        results.append({
            "source": "GitHub", "handle": "@" + owner if owner else None,
            "url": url, "title": name,
            "evidence": description[:500],
            "published_at": (pushed or created).isoformat() if (pushed or created) else None,
            "followers": None,
            "engagement": {"stars": repo.get("stargazers_count"), "forks": repo.get("forks_count")},
            "score": points, "reasons": why,
            "verification": "Potential Solana product repository. Owner is not confirmed as founder; check README, commits and profile before outreach."
        })
    return results

def alert(lead):
    reasons = "\n".join("â¢ " + x for x in lead.get("reasons", [])[:5])
    msg = (
        "ð¥· MYSTERIOUS EARLY RADAR â SOLANA LEAD\n\n"
        f"ð¯ Score: {lead.get('score', 0)}/100\n"
        f"ð¤ Account/owner: {lead.get('handle') or 'Unknown'}\n"
        f"ð Source: {lead.get('source', 'Unknown')}\n"
        f"ð Signal: {lead.get('title', 'Untitled')}\n"
        f"ð¥ Followers: {lead.get('followers') if lead.get('followers') is not None else 'Unavailable'}\n"
        f"ð Engagement: {lead.get('engagement') or 'Unavailable'}\n"
        f"ð Published/updated: {lead.get('published_at') or 'Unknown'}\n\n"
        f"Why flagged:\n{reasons}\n\n"
        f"ð {lead.get('url', '')}\n\n"
        f"â ï¸ {lead.get('verification', 'Verify manually before outreach.')}"
    )
    if not BOT or not CHAT:
        print("Telegram credentials missing; lead remains pending:", lead.get("title"))
        return False
    try:
        r = S.post(
            f"https://api.telegram.org/bot{BOT}/sendMessage",
            json={"chat_id": CHAT, "text": msg, "disable_web_page_preview": True},
            timeout=20
        )
        r.raise_for_status()
        payload = r.json()
        if payload.get("ok") is True:
            return True
        print("Telegram rejected message:", payload)
    except (requests.RequestException, ValueError) as e:
        print("Telegram delivery failed:", e)
    return False

def main():
    print("Mysterious Early Radar â Solana developer discovery")
    seen, history = load(SEEN, {}), load(LEADS, [])
    if not isinstance(seen, dict):
        seen = {}
    if not isinstance(history, list):
        history = []

    candidates = []
    for fn in [bing_x, bluesky, github_solana]:
        try:
            batch = fn()
            print(fn.__name__, "candidates:", len(batch))
            candidates.extend(batch)
        except Exception as e:
            print("Source failed:", fn.__name__, e)

    unique = {}
    for item in candidates:
        # Keep the original ID recipe so existing seen_accounts.json entries remain compatible.
        raw = f"{item['source']}|{item.get('url')}|{item.get('title')}"
        item["id"] = hashlib.sha256(raw.encode()).hexdigest()[:24]
        if item["id"] not in unique or item["score"] > unique[item["id"]]["score"]:
            unique[item["id"]] = item

    ranked = sorted(unique.values(), key=lambda x: x["score"], reverse=True)
    fresh = [x for x in ranked if x["id"] not in seen]
    now_text = NOW().isoformat()
    history_by_id = {x.get("id"): x for x in history if isinstance(x, dict) and x.get("id")}

    for item in fresh:
        item["first_seen_at"] = now_text
        item["pipeline_status"] = "PENDING_ALERT"
        item["delivery_state"] = "PENDING"
        history_by_id[item["id"]] = item
        seen[item["id"]] = {
            "url": item.get("url"), "source": item["source"],
            "first_seen_at": now_text, "score": item["score"],
            "delivery_state": "PENDING"
        }

    # Retry only leads discovered by this version and not yet confirmed by Telegram.
    pending = sorted(
        [x for x in history_by_id.values() if x.get("delivery_state") == "PENDING"],
        key=lambda x: x.get("score", 0), reverse=True
    )
    sent = 0
    for item in pending[:MAX_ALERTS]:
        seen.setdefault(item["id"], {
            "url": item.get("url"), "source": item.get("source"),
            "first_seen_at": item.get("first_seen_at", now_text),
            "score": item.get("score", 0), "delivery_state": "PENDING"
        })
        if alert(item):
            sent += 1
            item["delivery_state"] = "SENT"
            item["pipeline_status"] = "ALERTED"
            item["alerted_at"] = NOW().isoformat()
            seen[item["id"]]["delivery_state"] = "SENT"
            seen[item["id"]]["alerted_at"] = item["alerted_at"]
        else:
            print("Lead retained for retry:", item.get("title"))

    # Keep existing history and all pending leads; cap only old completed records.
    history = list(history_by_id.values())
    pending_ids = {x.get("id") for x in history if x.get("delivery_state") == "PENDING"}
    completed = [x for x in history if x.get("id") not in pending_ids]
    history = completed[-max(0, 2000 - len(pending_ids)):] + [x for x in history if x.get("id") in pending_ids]
    save(SEEN, dict(list(seen.items())[-15000:]))
    save(LEADS, history)

    by_source = {}
    for item in ranked:
        by_source[item["source"]] = by_source.get(item["source"], 0) + 1
    print("Ranked candidates by source:", by_source)
    print("New leads:", len(fresh), "| pending alerts:", len(pending), "| confirmed Telegram sends:", sent)
    print("Reminder: GitHub owners and social accounts are prospects to verify, not confirmed project founders.")

if __name__ == "__main__":
    main()
