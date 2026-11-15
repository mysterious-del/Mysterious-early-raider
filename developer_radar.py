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
    'site:x.com/ "Solana" ("launching soon" OR "coming soon" OR "pre-launch")',
    'site:x.com/ "building on Solana" OR "Solana project"',
    'site:x.com/ "Solana" ("community manager" OR "looking for moderators" OR "hiring")',
    'site:x.com/ "new project" "Solana" -airdrop',
]
TERMS = {
    "pre-launch": ["pre-launch", "prelaunch", "launching soon", "coming soon", "before launch", "token launch", "fair launch"],
    "building": ["building on solana", "solana builder", "new solana project", "shipping on solana", "developing on solana"],
    "team need": ["community manager", "looking for moderators", "hiring", "ambassador", "community team", "growth partner"],
    "project": ["introducing", "our project", "our dapp", "our app", "protocol", "web3 startup"],
}
NOISE = ["airdrop claim", "free mint", "giveaway", "100x", "signal group", "buy now"]

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

def get(url, params=None):
    try:
        r = S.get(url, params=params, timeout=18)
        if r.status_code == 429:
            print("Rate limited:", url); return None
        r.raise_for_status()
        return r
    except requests.RequestException as e:
        print("Request failed:", e); return None

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
    elif source == "GitHub": points += 5
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
        r = get("https://www.bing.com/search", {"q": q, "format": "rss"})
        if not r: continue
        try:
            root = ET.fromstring(r.text)
            for item in root.findall(".//item"):
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
    endpoint = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
    for q in ['"building on Solana"', '"Solana" "launching soon"', '"Solana project" "coming soon"', '"community manager" Solana']:
        r = get(endpoint, {"q": q, "limit": 30, "sort": "latest"})
        if not r: continue
        try:
            for post in r.json().get("posts", []):
                rec, author = post.get("record", {}), post.get("author", {})
                text = rec.get("text", "")
                created = dateval(rec.get("createdAt"))
                if created and created < NOW() - timedelta(days=LOOKBACK): continue
                points, why = score(text, "Bluesky", created)
                if points < MIN_SCORE: continue
                handle = author.get("handle")
                uri = post.get("uri", "")
                rkey = uri.rsplit("/", 1)[-1]
                url = f"https://bsky.app/profile/{handle}/post/{rkey}" if handle else ""
                results.append({
                    "source": "Bluesky", "handle": "@" + handle if handle else None, "url": url,
                    "title": text[:250], "evidence": text[:500],
                    "published_at": created.isoformat() if created else None,
                    "followers": author.get("followersCount"),
                    "engagement": {k: post.get(k) for k in ["likeCount","repostCount","replyCount"]},
                    "score": points, "reasons": why,
                    "verification": "Bluesky account, not confirmed as an X account or project founder."
                })
        except (ValueError, TypeError): print("Bluesky response parse error")
        time.sleep(.5)
    return results

def github_solana():
    results = []
    since = (NOW() - timedelta(days=LOOKBACK)).strftime("%Y-%m-%d")
    for q in [f"solana created:>={since} stars:0..20", f"solana blockchain created:>={since} stars:0..20"]:
        try:
            r = S.get("https://api.github.com/search/repositories",
                      params={"q": q, "sort": "created", "order": "desc", "per_page": 25},
                      headers={"Accept":"application/vnd.github+json",
                               **({"Authorization":"Bearer "+os.environ["GITHUB_TOKEN"]} if os.getenv("GITHUB_TOKEN") else {})},
                      timeout=18)
            if r.status_code == 403: print("GitHub search rate limit reached"); continue
            r.raise_for_status()
            for repo in r.json().get("items", []):
                created = dateval(repo.get("created_at"))
                text = (repo.get("full_name","") + " " + (repo.get("description") or "") + " Solana")
                points, why = score(text, "GitHub", created)
                if points < MIN_SCORE: continue
                owner = (repo.get("owner") or {}).get("login")
                results.append({
                    "source":"GitHub", "handle":"@"+owner if owner else None,
                    "url":repo.get("html_url"), "title":repo.get("full_name"),
                    "evidence":(repo.get("description") or "")[:500],
                    "published_at":created.isoformat() if created else None,
                    "followers":None, "engagement":{"stars":repo.get("stargazers_count")},
                    "score":points, "reasons":why,
                    "verification":"Repository owner is only a candidate, not a confirmed founder."
                })
        except (requests.RequestException, ValueError) as e: print("GitHub search failed:", e)
        time.sleep(.7)
    return results

def alert(lead):
    reasons = "\n".join("• " + x for x in lead["reasons"][:5])
    msg = (
        "🥷 MYSTERIOUS EARLY RADAR — SOLANA LEAD\n\n"
        f"🎯 Score: {lead['score']}/100\\n👤 Account/owner: {lead.get('handle') or 'Unknown'}\\n"
        f"🔎 Source: {lead['source']}\\n📝 Signal: {lead['title']}\\n"
        f"👥 Followers: {lead.get('followers') if lead.get('followers') is not None else 'Unavailable'}\\n"
        f"📊 Engagement: {lead.get('engagement') or 'Unavailable'}\\n"
        f"🕒 Published: {lead.get('published_at') or 'Unknown'}\\n\\n"
        f"Why flagged:\\n{reasons}\\n\\n🔗 {lead.get('url')}\\n\\n⚠️ {lead['verification']}"
    )
    if not BOT or not CHAT:
        print(msg); return
    try:
        r = S.post(f"https://api.telegram.org/bot{BOT}/sendMessage",
                   json={"chat_id":CHAT,"text":msg,"disable_web_page_preview":True}, timeout=18)
        r.raise_for_status()
    except requests.RequestException as e: print("Telegram delivery failed:", e)

def main():
    print("Mysterious Early Radar — Solana developer discovery")
    seen, history = load(SEEN, {}), load(LEADS, [])
    if not isinstance(seen, dict): seen = {}
    if not isinstance(history, list): history = []
    candidates = []
    for fn in [bing_x, bluesky, github_solana]:
        try:
            batch = fn(); print(fn.__name__, "candidates:", len(batch)); candidates.extend(batch)
        except Exception as e: print("Source failed:", fn.__name__, e)
    unique = {}
    for item in candidates:
        raw = f"{item['source']}|{item.get('url')}|{item.get('title')}"
        item["id"] = hashlib.sha256(raw.encode()).hexdigest()[:24]
        if item["id"] not in unique or item["score"] > unique[item["id"]]["score"]:
            unique[item["id"]] = item
    ranked = sorted(unique.values(), key=lambda x:x["score"], reverse=True)
    fresh = [x for x in ranked if x["id"] not in seen]
    for item in fresh:
        item["first_seen_at"] = NOW().isoformat()
        item["pipeline_status"] = "DISCOVERED"
        if len([x for x in fresh[:fresh.index(item)+1]]) <= MAX_ALERTS:
            alert(item)
        seen[item["id"]] = {"url":item.get("url"),"source":item["source"],
                            "first_seen_at":item["first_seen_at"],"score":item["score"]}
    history.extend(fresh)
    save(SEEN, dict(list(seen.items())[-15000:]))
    save(LEADS, history[-2000:])
    print("New leads:", len(fresh), "| alerts attempted:", min(len(fresh), MAX_ALERTS))

if __name__ == "__main__":
    main()
