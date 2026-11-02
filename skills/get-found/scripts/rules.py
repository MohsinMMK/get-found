#!/usr/bin/env python3
"""get-found live-docs sync — keeps the skill current without editing the skill.

The skill ships a registry of canonical, official documentation pages (references/sources.json).
Each run, this script re-fetches the ones older than --max-age-days, stores a text snapshot in
.get-found/rules-cache/, and reports which pages CHANGED since the last snapshot (with a diff) or
FAILED (moved/removed). The agent then reads the changed pages and records any rule change in
.get-found/rules-notes.md (and override JSON files), which take precedence over the skill's baseline.

  rules.py refresh [--max-age-days 7] [--only google-sitemaps openai-bots ...] [--items sitemap ai-bots-allowed]
  rules.py status                 # when each source was last fetched / changed
  rules.py ai-bots                # find AI user-agents not yet classified in ai-bots.json (+ override)
  rules.py news [--days 45]       # recent posts from official search-engine blogs/changelogs
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import fetch, load_json, load_reference, now_iso, parse_date, parse_html, save_json, sha  # noqa: E402
from state import State  # noqa: E402

COMMUNITY_BOTS = "https://raw.githubusercontent.com/ai-robots-txt/ai.robots.txt/main/robots.json"


def extract_text(body: str, ctype: str) -> str:
    if "html" in ctype or body.lstrip().startswith("<"):
        p = parse_html(body)
        lines = [" ".join(t.split()) for t in p.text_parts]
        text = "\n".join(l for l in lines if l)
        # one sentence-ish per line keeps diffs readable
        return re.sub(r"(?<=[.!?])\s+(?=[A-Z])", "\n", text)
    return body


def cmd_refresh(st: State, a):
    reg = load_reference("sources.json", st.dir)
    sources = reg.get("sources", [])
    if a.only:
        unknown = sorted(set(a.only) - {s["id"] for s in sources})
        if unknown:
            print(f"error: unknown source id(s): {', '.join(unknown)}. Valid ids: {', '.join(s['id'] for s in sources)}", file=sys.stderr)
            sys.exit(2)
        sources = [s for s in sources if s["id"] in a.only]
    if a.items:
        sources = [s for s in sources if set(s.get("items", [])) & set(a.items)]
    meta = load_json(st.p("rules.json"), {}) or {}
    cache = st.p("rules-cache")
    cache.mkdir(parents=True, exist_ok=True)
    changed, failed, fresh, fetched = [], [], 0, 0
    now = now_iso()
    for s in sources:
        m = meta.get(s["id"], {})
        last = parse_date(m.get("fetched"))
        if last and not a.force and (parse_date(now) - last).days < a.max_age_days:
            fresh += 1
            continue
        r = fetch(s["url"], timeout=25, max_bytes=3_000_000)
        fetched += 1
        if r.error or not r.ok:
            m.update({"url": s["url"], "fetched": now, "status": r.status or r.error})
            failed.append({"id": s["id"], "url": s["url"], "status": r.status or r.error, "browser": s.get("fetch") == "browser" or r.status in (401, 403),
                           "final_url": r.final_url if r.final_url != s["url"] else None})
            meta[s["id"]] = m
            continue
        text = extract_text(r.text(), r.headers.get("content-type", ""))
        h = sha(text)
        path = cache / f"{s['id']}.txt"
        if m.get("url") and m["url"] != s["url"]:
            m.pop("hash", None)  # source URL was replaced (override/upgrade): re-baseline instead of reporting a diff
            m["rebaselined"] = now
        if m.get("hash") and m["hash"] != h and not path.exists():
            # fingerprint is committed but the text cache isn't (fresh clone / CI): still report the change
            changed.append({"id": s["id"], "url": s["url"], "covers": s.get("items", []), "diff": None, "diff_lines": None})
            m["changed"] = now
        elif m.get("hash") and m["hash"] != h:
            old = path.read_text(encoding="utf-8").splitlines()
            diff = list(difflib.unified_diff(old, text.splitlines(), f"{s['id']}@{m.get('fetched', '')[:10]}", f"{s['id']}@{now[:10]}", n=1, lineterm=""))
            (cache / f"{s['id']}.diff").write_text("\n".join(diff[:400]) + "\n", encoding="utf-8")
            changed.append({"id": s["id"], "url": s["url"], "covers": s.get("items", []), "diff": str(cache / f"{s['id']}.diff"),
                            "diff_lines": sum(1 for l in diff if l[:1] in "+-" and not l.startswith(("+++", "---")))})
            m["changed"] = now
        path.write_text(text, encoding="utf-8")
        if r.final_url and r.final_url.rstrip("/") != s["url"].rstrip("/"):
            m["redirected_to"] = r.final_url
        m.update({"url": s["url"], "fetched": now, "hash": h, "status": r.status, "first_seen": m.get("first_seen") or now})
        meta[s["id"]] = m
    save_json(st.p("rules.json"), meta)
    report = {"at": now, "fetched": fetched, "fresh_skipped": fresh, "changed": changed, "failed": failed}
    save_json(st.p("rules-cache") / "last-refresh.json", report)
    st.history("rules-refresh", fetched=fetched, changed=len(changed), failed=len(failed))
    led = st.ledger()
    it = led.get("items", {}).get("rules-fresh")
    if it is not None and not a.only and not a.items:
        it.update({"last_checked": now, "evidence": f"{fetched} fetched, {len(changed)} changed, {len(failed)} failed",
                   "status": "warn" if (changed or failed) else "pass"})
        from state import next_due
        it["next_due"] = next_due(now, it.get("cadence_days", 7))
        st.save("ledger.json", led)
    if a.json:
        print(json.dumps(report, indent=2))
        return
    print(f"rules refresh: {fetched} fetched, {fresh} still fresh, {len(changed)} changed, {len(failed)} failed")
    for c in changed:
        where = f"read {c['diff']}" if c["diff"] else f"no local snapshot to diff — open {c['url']}"
        print(f"  CHANGED {c['id']} ({c['diff_lines'] if c['diff_lines'] is not None else '?'} lines) → {where} ; affects {', '.join(c['covers'])}")
    for f in failed:
        if f.get("browser"):
            print(f"  BLOCKED {f['id']} {f['url']} → {f['status']}  (site blocks scripts: read it with your web-fetch/browser tool instead)")
        else:
            print(f"  FAILED  {f['id']} {f['url']} → {f['status']}  (moved? search for the new official URL; add it to .get-found/sources.override.json)")
    if changed or failed:
        print("Next: review each change, then record any rule change in .get-found/rules-notes.md and mark rules-fresh pass.")


def cmd_status(st: State, a):
    reg = {s["id"]: s for s in load_reference("sources.json", st.dir).get("sources", [])}
    meta = load_json(st.p("rules.json"), {}) or {}
    for sid, s in reg.items():
        m = meta.get(sid, {})
        print(f"{sid:<34} fetched {str(m.get('fetched', 'never'))[:10]:<11} changed {str(m.get('changed', '-'))[:10]:<11} {m.get('status', '')}")


def cmd_ai_bots(st: State, a):
    reg = load_reference("ai-bots.json", st.dir)
    known = {b["token"].lower() for b in reg.get("bots", [])} | {t.lower() for t in reg.get("ignored_tokens", [])}
    r = fetch(COMMUNITY_BOTS, timeout=20)
    if not r.ok:
        print(f"could not fetch community bot list ({r.status or r.error}); check vendor pages listed in sources.json instead")
        return
    data = json.loads(r.body)
    unknown = {k: v for k, v in data.items() if k.lower() not in known}

    def rank(item):
        fn = str(item[1].get("function", "")).lower()
        if re.search(r"\bsearch\b", fn):  # not "research"
            return 0
        if any(w in fn for w in ("assistant", "answer", "user-initiated", "user prompts")):
            return 1
        return 2
    unknown = dict(sorted(unknown.items(), key=rank))
    relevant = {k: v for k, v in unknown.items() if rank((k, v)) < 2}
    save_json(st.p("rules-cache") / "ai-bots-unclassified.json", unknown)
    st.history("ai-bots-check", unknown=len(unknown))
    print(f"{len(data)} agents in community list; {len(unknown)} not classified in ai-bots.json (+override); {len(relevant)} look like search/answer/agent fetchers.")
    print("Unlisted bots are allowed by default, so this only matters for (a) new search/answer fetchers you should explicitly")
    print("check aren't blocked (by robots.txt groups or your CDN), and (b) training crawlers the owner wants to opt out of.")
    print("Search/answer-type agents are listed first.")
    print("Verify each against the vendor's own documentation before adding it to .get-found/ai-bots.override.json:\n")
    for k, v in list(relevant.items())[: a.limit]:
        operator = re.sub(r"[\[\]()]", " ", str(v.get("operator", "")))[:50]
        print(f"  {k:<32} {str(v.get('function', ''))[:40]:<40} {operator}")
    print(f"  (full list incl. {len(unknown) - len(relevant)} training/scraper agents: {st.p('rules-cache') / 'ai-bots-unclassified.json'})")


def cmd_news(st: State, a):
    reg = load_reference("sources.json", st.dir)
    since = parse_date(now_iso())
    import datetime as dt
    since = since - dt.timedelta(days=a.days)
    for feed in reg.get("feeds", []):
        r = fetch(feed["url"], timeout=20)
        if not r.ok:
            print(f"[{feed['id']}] unavailable ({r.status or r.error}) — open {feed.get('html', feed['url'])}")
            continue
        try:
            root = ET.fromstring(r.body)
        except ET.ParseError:
            print(f"[{feed['id']}] not a valid feed")
            continue
        items = []
        for node in root.iter():
            tag = node.tag.split("}")[-1]
            if tag not in ("item", "entry"):
                continue
            fields = {c.tag.split("}")[-1]: (c.text or "").strip() or c.get("href", "") for c in node}
            date = parse_date(fields.get("pubDate") or fields.get("updated") or fields.get("published") or "")
            if date is None:
                try:
                    from email.utils import parsedate_to_datetime
                    date = parsedate_to_datetime(fields.get("pubDate", ""))
                except Exception:
                    date = None
            if date and date >= since:
                items.append((date, fields.get("title", ""), fields.get("link", "")))
        print(f"[{feed['id']}] {len(items)} posts in the last {a.days} days")
        for d, t, l in sorted(items, reverse=True)[:15]:
            print(f"  {d.date()}  {t}  {l}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default=".get-found")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("refresh"); s.add_argument("--max-age-days", type=int, default=7); s.add_argument("--only", nargs="*")
    s.add_argument("--items", nargs="*"); s.add_argument("--force", action="store_true"); s.add_argument("--json", action="store_true")
    sub.add_parser("status")
    s = sub.add_parser("ai-bots"); s.add_argument("--limit", type=int, default=15)
    s = sub.add_parser("news"); s.add_argument("--days", type=int, default=45)
    a = ap.parse_args()
    st = State(a.state)
    st.dir.mkdir(parents=True, exist_ok=True)  # rules data is project-agnostic: works before `state.py init`
    {"refresh": cmd_refresh, "status": cmd_status, "ai-bots": cmd_ai_bots, "news": cmd_news}[a.cmd](st, a)


if __name__ == "__main__":
    main()
