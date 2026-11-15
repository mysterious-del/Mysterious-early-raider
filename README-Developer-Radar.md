# Mysterious Early Radar — Solana Pre-Launch Discovery

This is a separate module. It does not replace `radar.py`, `research.py`, or their workflow.

## Sources
- Bing RSS search for search-indexed public X pages matching Solana launch/building queries.
- Bluesky public search API for additional Solana builder signals.
- GitHub public repository search for recently created Solana projects.

## Important limitations
This is a free, best-effort discovery system, **not an X API replacement**. It cannot search all X posts, inspect every X profile, reliably find zero-post accounts, or guarantee follower counts/engagement. Search indexing can be delayed or incomplete. Bluesky/GitHub leads are not automatically confirmed X accounts or founders. Verify leads manually before outreach.

No account passwords, session cookies, or access-control bypasses are used.

## Install
1. Add `developer_radar.py` to the repository root.
2. Add `.github/workflows/Developer-Radar.yml`.
3. Confirm repository Actions secrets `BOT_TOKEN` and `CHAT_ID` exist.
4. Commit/push the files.
5. Open Actions → Mysterious Developer Radar → Run workflow to test.

It runs twice hourly at minutes 17 and 47. It creates/updates `seen_accounts.json` and `leads.json`.

Tune workflow environment values: `LOOKBACK_DAYS` (14), `MIN_LEAD_SCORE` (35), `MAX_ALERTS_PER_RUN` (8).
