#!/usr/bin/env python3
"""get-found live-docs sync — keeps the skill current without editing the skill.

The skill ships a registry of canonical, official documentation pages (references/sources.json).
Each run, this script re-fetches (in parallel) the ones older than --max-age-days, stores a text
snapshot in .get-found/rules-cache/ (or a user cache dir before `state.py init`), and reports which
pages CHANGED since the last snapshot (with a diff) or FAILED (moved/removed). A change keeps being
reported until `rules.py ack`; a failed source is retried on the next run. Sources marked
"fetch": "browser" block scripts, so they are listed for the agent's browser tool, not fetched.
The agent then reads the changed pages and records any rule change in .get-found/rules-notes.md
(and override JSON files), which take precedence over the skill's baseline.

  rules.py refresh [--max-age-days 7] [--only google-sitemaps openai-bots ...] [--items sitemap ai-bots-allowed]
  rules.py ack <id ...> | --all   # after reading a change: stop reporting it
  rules.py status                 # when each source was last fetched / changed / still unreviewed
  rules.py ai-bots [--all]        # AI user-agents not yet classified in ai-bots.json (+ override); counts unless --all
  rules.py news [--days 45]       # recent posts from official search-engine blogs/changelogs
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (fetch, find_state_dir, load_json, load_reference, now_iso, parse_date, parse_html,  # noqa: E402
                     save_json, sha, user_cache_dir)
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


def _fetch_all(sources, workers=8, abort_after=6):
    """Fetch sources in parallel. Stops queuing new fetches after `abort_after` network errors in a
    row (offline / firewalled), so a dead network costs seconds, not 55 sequential timeouts."""
    results, streak, aborted = {}, 0, False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {}
        queue = list(sources)
        while queue or pending:
            while queue and len(pending) < workers and not aborted:
                s = queue.pop(0)
                pending[pool.submit(fetch, s["url"], timeout=15, max_bytes=3_000_000, retries=0)] = s
            if not pending:
                break
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for fut in done:
                s = pending.pop(fut)
                r = fut.result()
                results[s["id"]] = r
                streak = streak + 1 if r.error else 0
                if streak >= abort_after:
                    aborted = True
            if aborted:
                queue = []
    return results, aborted


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
    changed, failed, browser_only, fresh = [], [], [], 0
    now = now_iso()
    todo = []
    for s in sources:
        m = meta.get(s["id"], {})
        if s.get("fetch") == "browser" and not (a.only and s["id"] in a.only):
            # known to block scripts (403/JS wall): don't burn a request, list it for the agent's browser
            browser_only.append({"id": s["id"], "url": s["url"], "covers": s.get("items", [])})
            continue
        last = parse_date(m.get("fetched"))
        if last and not a.force and (parse_date(now) - last).days < a.max_age_days:
            fresh += 1
            continue
        todo.append(s)
    results, aborted = _fetch_all(todo)
    fetched = len(results)
    for s in todo:
        r = results.get(s["id"])
        if r is None:
            continue  # not attempted (aborted): stays due for the next run
        m = meta.get(s["id"], {})
        if r.error or not r.ok:
            # don't advance `fetched`: a failed source stays due and stays reported until it works again
            m.update({"url": s["url"], "status": r.status or r.error, "failed": now})
            failed.append({"id": s["id"], "url": s["url"], "status": r.status or r.error, "browser": s.get("fetch") == "browser" or r.status in (401, 403),
                           "final_url": r.final_url if r.final_url != s["url"] else None})
            meta[s["id"]] = m
            continue
        m.pop("failed", None)
        text = extract_text(r.text(), r.headers.get("content-type", ""))
        h = sha(text)
        path = cache / f"{s['id']}.txt"
        if m.get("url") and m["url"] != s["url"]:
            m.pop("hash", None)  # source URL was replaced (override/upgrade): re-baseline instead of reporting a diff
            m["rebaselined"] = now
        if m.get("hash") and m["hash"] != h and not path.exists():
            # fingerprint is committed but the text cache isn't (fresh clone / CI): still report the change
            m["changed"] = now
            m["unreviewed"] = {"since": now, "diff": None, "diff_lines": None}
        elif m.get("hash") and m["hash"] != h:
            old = path.read_text(encoding="utf-8").splitlines()
            diff = list(difflib.unified_diff(old, text.splitlines(), f"{s['id']}@{m.get('fetched', '')[:10]}", f"{s['id']}@{now[:10]}", n=1, lineterm=""))
            dpath = cache / f"{s['id']}.diff"
            prev = m.get("unreviewed")
            # several changes before an ack: append, so the first diff isn't lost
            with open(dpath, "a" if prev and prev.get("diff") and dpath.exists() else "w", encoding="utf-8") as fh:
                fh.write("\n".join(diff[:400]) + "\n")
            n = sum(1 for l in diff if l[:1] in "+-" and not l.startswith(("+++", "---")))
            m["changed"] = now
            m["unreviewed"] = {"since": (prev or {}).get("since", now), "diff": str(dpath), "diff_lines": ((prev or {}).get("diff_lines") or 0) + n}
        path.write_text(text, encoding="utf-8")
        if r.final_url and r.final_url.rstrip("/") != s["url"].rstrip("/"):
            m["redirected_to"] = r.final_url
        m.update({"url": s["url"], "fetched": now, "hash": h, "status": r.status, "first_seen": m.get("first_seen") or now})
        meta[s["id"]] = m
    # a change stays reported on every run until `rules.py ack` — re-hashing must not hide it
    by_id = {s["id"]: s for s in reg.get("sources", [])}
    for sid, m in meta.items():
        u = m.get("unreviewed")
        if u and sid in by_id and (not a.only or sid in a.only) and (not a.items or set(by_id[sid].get("items", [])) & set(a.items)):
            changed.append({"id": sid, "url": m.get("url"), "covers": by_id[sid].get("items", []), "since": u.get("since"),
                            "diff": u.get("diff"), "diff_lines": u.get("diff_lines")})
    reported = {f["id"] for f in failed}
    for sid, m in meta.items():
        if m.get("failed") and sid in by_id and sid not in reported and (not a.only or sid in a.only) \
                and (not a.items or set(by_id[sid].get("items", [])) & set(a.items)):
            failed.append({"id": sid, "url": m.get("url"), "status": m.get("status"), "browser": m.get("status") in (401, 403),
                           "final_url": None, "since": m["failed"]})
    save_json(st.p("rules.json"), meta)
    report = {"at": now, "fetched": fetched, "fresh_skipped": fresh, "aborted": aborted, "not_attempted": len(todo) - fetched,
              "changed": changed, "failed": failed, "browser_only": browser_only}
    save_json(st.p("rules-cache") / "last-refresh.json", report)
    st.history("rules-refresh", fetched=fetched, changed=len(changed), failed=len(failed), aborted=aborted)
    led = st.ledger()
    it = led.get("items", {}).get("rules-fresh")
    if it is not None and not a.only and not a.items and st.p("ledger.json").exists():
        it.update({"last_checked": now, "evidence": f"{fetched} fetched, {len(changed)} unreviewed changes, {len(failed)} failed"
                   + (" (aborted: network down)" if aborted else ""),
                   "status": "warn" if (changed or failed or aborted) else "pass"})
        from state import next_due
        it["next_due"] = next_due(now, it.get("cadence_days", 7))
        st.save("ledger.json", led)
    if a.json:
        print(json.dumps(report, indent=2))
        return
    print(f"rules refresh: {fetched} fetched, {fresh} still fresh, {len(changed)} unreviewed changes, {len(failed)} failed"
          + (f", {len(browser_only)} browser-only" if browser_only else ""))
    if aborted:
        print(f"  ABORTED after repeated network errors — {len(todo) - fetched} sources not tried (offline or firewalled?). They stay due.")
    for c in changed:
        where = f"read {c['diff']}" if c["diff"] else f"no local snapshot to diff — open {c['url']}"
        print(f"  CHANGED {c['id']} ({c['diff_lines'] if c['diff_lines'] is not None else '?'} lines, since {str(c['since'])[:10]}) → {where} ; affects {', '.join(c['covers'])}")
    for f in failed:
        if f.get("browser"):
            print(f"  BLOCKED {f['id']} {f['url']} → {f['status']}  (site blocks scripts: read it with your web-fetch/browser tool instead)")
        else:
            print(f"  FAILED  {f['id']} {f['url']} → {f['status']}  (moved? search for the new official URL; add it to .get-found/sources.override.json)")
    if browser_only and a.verbose:
        for b in browser_only:
            print(f"  BROWSER {b['id']} {b['url']}  (blocks scripts; read with your browser tool when {', '.join(b['covers'])} matters)")
    elif browser_only:
        print(f"  {len(browser_only)} browser-only sources skipped (they block scripts): {', '.join(b['id'] for b in browser_only)} — --verbose for URLs")
    if changed or failed:
        print("Next: review each change, record any rule change in .get-found/rules-notes.md, then `rules.py ack <id>` (or --all).")


def cmd_ack(st: State, a):
    meta = load_json(st.p("rules.json"), {}) or {}
    ids = [sid for sid, m in meta.items() if m.get("unreviewed")] if a.all else a.ids
    if not ids:
        print("nothing to acknowledge" if a.all else "give source ids or --all")
        return
    unknown = [i for i in ids if not (meta.get(i) or {}).get("unreviewed")]
    if unknown:
        print(f"error: no unreviewed change for: {', '.join(unknown)}", file=sys.stderr)
        sys.exit(2)
    now = now_iso()
    for i in ids:
        m = meta[i]
        m.pop("unreviewed")
        m["reviewed"] = now
        d = st.p("rules-cache") / f"{i}.diff"
        if d.exists():
            d.unlink()
    save_json(st.p("rules.json"), meta)
    st.history("rules-ack", ids=ids)
    left = sum(1 for m in meta.values() if m.get("unreviewed"))
    print(f"acknowledged {len(ids)} change(s); {left} still unreviewed")


def cmd_status(st: State, a):
    reg = {s["id"]: s for s in load_reference("sources.json", st.dir).get("sources", [])}
    meta = load_json(st.p("rules.json"), {}) or {}
    for sid, s in reg.items():
        m = meta.get(sid, {})
        flag = "UNREVIEWED" if m.get("unreviewed") else ("FAILING" if m.get("failed") else ("browser" if s.get("fetch") == "browser" else ""))
        print(f"{sid:<34} fetched {str(m.get('fetched', 'never'))[:10]:<11} changed {str(m.get('changed', '-'))[:10]:<11} {str(m.get('status', '')):<6} {flag}")


def cmd_ai_bots(st: State, a):
    reg = load_reference("ai-bots.json", st.dir)
    known = {b["token"].lower() for b in reg.get("bots", [])} | {t.lower() for t in reg.get("ignored_tokens", [])}
    r = fetch(COMMUNITY_BOTS, timeout=20)
    if not r.ok:
        print(f"could not fetch community bot list ({r.status or r.error}); check vendor pages listed in sources.json instead")
        return
    try:
        data = json.loads(r.body)
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except ValueError as e:
        print(f"community bot list at {COMMUNITY_BOTS} is not the expected JSON ({e}); check vendor pages listed in sources.json instead")
        return
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
    print("Unlisted bots are allowed by default (a `User-agent: *` allow covers them), so this only matters for (a) new")
    print("search/answer fetchers you should check aren't blocked (robots.txt groups or your CDN), and (b) training crawlers")
    print("the owner wants to opt out of. Nothing to do in a routine check unless the site blocks `*` or uses a bot-blocking CDN.")
    if not a.all:
        print(f"  `rules.py ai-bots --all` lists the {len(relevant)} search/answer-type agents to verify.")
        print(f"  (full list: {st.p('rules-cache') / 'ai-bots-unclassified.json'})")
        return
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
    ap.add_argument("--state", default=None, help="project state dir (default: the nearest .get-found/, else a user cache dir)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("refresh"); s.add_argument("--max-age-days", type=int, default=7); s.add_argument("--only", nargs="*")
    s.add_argument("--items", nargs="*"); s.add_argument("--force", action="store_true"); s.add_argument("--json", action="store_true")
    s.add_argument("--verbose", action="store_true", help="also list the browser-only sources' URLs")
    s = sub.add_parser("ack", help="mark reviewed changes as read"); s.add_argument("ids", nargs="*"); s.add_argument("--all", action="store_true")
    sub.add_parser("status")
    s = sub.add_parser("ai-bots"); s.add_argument("--limit", type=int, default=15)
    s.add_argument("--all", action="store_true", help="list the search/answer-type agents, not just the counts")
    s = sub.add_parser("news"); s.add_argument("--days", type=int, default=45)
    a = ap.parse_args()
    d = find_state_dir(a.state)
    if not (d / "config.json").exists() and not (a.state and Path(a.state).as_posix().rstrip("/") not in (".get-found", "./.get-found")):
        # rules data is project-agnostic: before `state.py init` keep it out of the repo
        d = user_cache_dir()
        print(f"(no .get-found/ here — using {d} for the rules cache)", file=sys.stderr)
    d.mkdir(parents=True, exist_ok=True)
    st = State(d)
    {"refresh": cmd_refresh, "ack": cmd_ack, "status": cmd_status, "ai-bots": cmd_ai_bots, "news": cmd_news}[a.cmd](st, a)


if __name__ == "__main__":
    main()
