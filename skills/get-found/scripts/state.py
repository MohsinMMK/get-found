#!/usr/bin/env python3
"""get-found project state: config, ledger, page inventory, keywords, listings, history.

All state lives in the project's .get-found/ directory (commit it; it holds no secrets).

  state.py init --site https://example.com --type saas [--framework nextjs] [--brand "Acme"] [--blog-prefix /blog/]
  state.py status [--json]                 # every checklist item: status, last checked, next due
  state.py due [--days 0] [--json]         # what needs doing now (drives sync/check runs)
  state.py ingest <audit.json>             # apply an audit report: ledger + page inventory + change detection
  state.py changes [--channel indexnow]    # URLs new/changed/removed since last submitted on a channel
  state.py submitted --channel indexnow --urls U1 U2 | --all-pending
  state.py mark <item> --status pass|warn|fail|manual|n/a [--evidence ".."] [--note ".."]
  state.py keywords [--check]              # keyword map + cannibalization / coverage check
  state.py listings [--json]               # directory/listing tracker
  state.py listing <id> --status live --url https://... [--note ".."]
  state.py log "message"                   # append to history

Use --state DIR to point at a state dir other than ./.get-found
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import VERSION, die, load_json, load_reference, now_iso, parse_date, path_of, save_json, today  # noqa: E402

SITE_TYPES = ["saas", "app", "ai-tool", "local", "ecommerce", "content", "portfolio", "docs", "agency", "nonprofit"]
CHANNELS = ["indexnow", "google", "bing"]


# --------------------------------------------------------------------------- paths

class State:
    def __init__(self, root: str | Path = ".get-found"):
        self.dir = Path(root)

    def p(self, name: str) -> Path:
        return self.dir / name

    def require(self):
        if not self.p("config.json").exists():
            die(f"no get-found state at {self.dir}/ — run `state.py init` first (or pass --state)")
        return self

    # json accessors
    def config(self):
        return load_json(self.p("config.json"), {}) or {}

    def ledger(self):
        return load_json(self.p("ledger.json"), {"items": {}}) or {"items": {}}

    def pages(self):
        return load_json(self.p("pages.json"), {"pages": {}}) or {"pages": {}}

    def keywords(self):
        return load_json(self.p("keywords.json"), {"pages": {}, "backlog": []}) or {"pages": {}, "backlog": []}

    def listings(self):
        return load_json(self.p("listings.json"), {"listings": []}) or {"listings": []}

    def save(self, name, data):
        save_json(self.p(name), data)

    def history(self, event: str, **data):
        self.dir.mkdir(parents=True, exist_ok=True)
        rec = {"at": now_iso(), "event": event, **data}
        with open(self.p("history.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def checklist(state_dir: Path | None):
    return {i["id"]: i for i in load_reference("checklist.json", state_dir).get("items", [])}


def _date(s):
    d = parse_date(s)
    return d.date() if d else None


def next_due(last_checked: str | None, cadence: int) -> str:
    base = _date(last_checked) or today()
    return (base + dt.timedelta(days=cadence)).isoformat()


# --------------------------------------------------------------------------- init

def cmd_init(st: State, a):
    if st.p("config.json").exists() and not a.force:
        die(f"{st.dir}/config.json already exists (use --force to re-initialise; existing ledger is kept)")
    site = a.site.rstrip("/")
    if not urllib.parse.urlsplit(site).scheme:
        site = "https://" + site
    host = urllib.parse.urlsplit(site).hostname or ""
    apex = host[4:] if host.startswith("www.") else host
    types = [t.strip() for raw in a.type for t in raw.split(",") if t.strip()]
    bad = [t for t in types if t not in SITE_TYPES]
    if bad:
        die(f"unknown site type(s) {bad}; choose from {SITE_TYPES}")
    social = {}
    for pair in a.social or []:
        if "=" not in pair:
            die(f"--social expects name=url, got {pair}")
        k, v = pair.split("=", 1)
        social[k.strip().lower()] = v.strip()
    a.type = types
    cfg = st.config() if a.force else {}
    cfg.update({
        "schema": 1,
        "created_with": f"get-found {VERSION}",
        "site_url": site + "/",
        "site_types": a.type,
        "brand": a.brand or cfg.get("brand") or urllib.parse.urlsplit(site).netloc,
        "framework": a.framework or cfg.get("framework") or "unknown",
        "locale": a.locale or cfg.get("locale") or "en",
        "country": (a.country or cfg.get("country") or "").upper(),
        "verticals": a.vertical or cfg.get("verticals") or [],
        "blog": {"path_prefix": a.blog_prefix, "cadence_days": a.blog_cadence},
        "sitemaps": cfg.get("sitemaps") or [],
        "sample_paths": cfg.get("sample_paths") or ["/", a.blog_prefix],
        "google": cfg.get("google") or {"property": f"sc-domain:{apex}", "auth": "env:GOOGLE_APPLICATION_CREDENTIALS or env:GSC_ACCESS_TOKEN"},
        "bing": cfg.get("bing") or {"api_key_env": "BING_WEBMASTER_API_KEY"},
        "indexnow": cfg.get("indexnow") or {"key": None, "key_location": None},
        "social_profiles": {**(cfg.get("social_profiles") or {}), **social},
        "ai_training_policy": a.training or cfg.get("ai_training_policy") or "allow",
        "authors": a.author or cfg.get("authors") or [],
        "directories_target": cfg.get("directories_target") or 10,
        "notes": cfg.get("notes") or "",
    })
    st.save("config.json", cfg)
    cl = checklist(st.dir)
    led = st.ledger()
    for iid, item in cl.items():
        led["items"].setdefault(iid, {"title": item["title"], "status": "todo", "last_checked": None, "last_changed": None,
                                      "next_due": today().isoformat(), "cadence_days": item["cadence_days"], "evidence": "", "notes": ""})
    st.save("ledger.json", led)
    if not st.p("pages.json").exists():
        st.save("pages.json", {"pages": {}, "last_ingest": None})
    if not st.p("keywords.json").exists():
        st.save("keywords.json", {"pages": {}, "backlog": [], "_help": "pages: {path: {primary, secondary[], intent, evidence}}; backlog: [{keyword, intent, status: idea|brief|draft|published, planned_for, url}]"})
    if not st.p("listings.json").exists():
        st.save("listings.json", {"listings": seed_listings(a.type, st.dir, cfg.get("country"), cfg.get("verticals"))})
    if not st.p("rules-notes.md").exists():
        st.p("rules-notes.md").write_text(
            "# Rules notes (live-doc deltas)\n\nWhen a live source contradicts the skill's baseline, record it here: date, source, what changed, what we now do.\n"
            "These notes override the skill's reference files for this project.\n\n", encoding="utf-8")
    gi = st.p(".gitignore")
    if not gi.exists():
        gi.write_text("# get-found: commit the ledger, not caches or per-run audit output\nrules-cache/\nreports/audit-*\n*.tmp\n", encoding="utf-8")
    st.history("init", site=cfg["site_url"], types=a.type)
    print(f"initialised {st.dir}/ for {cfg['site_url']} ({', '.join(a.type)}) — {len(cl)} checklist items, "
          f"{len(st.listings()['listings'])} candidate listings")


def seed_listings(types: list[str], state_dir: Path, country: str | None = None, verticals: list | None = None):
    reg = load_reference("directories.json", state_dir)
    out = []
    for d in reg.get("directories", []):
        fit = d.get("types", ["*"])
        if d.get("countries") and (not country or country.upper() not in d["countries"]):
            continue
        if d.get("verticals") and not set(d["verticals"]) & set(verticals or []):
            continue
        if "*" in fit or set(fit) & set(types):
            out.append({"id": d["id"], "name": d["name"], "submit_url": d.get("url"), "priority": d.get("priority", "nice"),
                        "status": "todo", "listing_url": None, "updated": None, "notes": d.get("notes", "")})
    order = {"core": 0, "high": 1, "nice": 2}
    return sorted(out, key=lambda x: order.get(x["priority"], 3))


# --------------------------------------------------------------------------- status / due

def compute_due(st: State, horizon_days: int = 0):
    led, cfg = st.ledger(), st.config()
    pages = st.pages()["pages"]
    limit = today() + dt.timedelta(days=horizon_days)
    due = []
    for iid, it in led["items"].items():
        if it["status"] == "n/a":
            continue
        reason = None
        if it["status"] in ("todo", "fail"):
            reason = f"status is {it['status']}"
        elif it["status"] == "manual":
            reason = "waiting on a manual step"
        elif _date(it.get("next_due")) and _date(it["next_due"]) <= limit:
            reason = f"re-check due {it['next_due']}"
        if reason:
            due.append({"item": iid, "reason": reason})
    # change-driven triggers
    pend = pending(st, "indexnow") if (cfg.get("indexnow") or {}).get("key") else []
    pend_g = pending(st, "google")
    if pend or pend_g:
        n = len({u for u, _ in pend} | {u for u, _ in pend_g})
        due.append({"item": "sync", "reason": f"{n} new/changed URLs not yet submitted — run sync"})
    # blog cadence
    lp = last_post(st)
    cad = (cfg.get("blog") or {}).get("cadence_days", 7)
    if lp:
        age = (today() - lp).days
        if age >= cad and not any(d["item"] == "blog" for d in due):
            due.append({"item": "blog", "reason": f"last post {lp.isoformat()} ({age} days ago, cadence {cad})"})
    return due


def last_post(st: State):
    cfg = st.config()
    prefix = (cfg.get("blog") or {}).get("path_prefix") or "/blog/"
    best = None
    for url, p in st.pages()["pages"].items():
        path = urllib.parse.urlsplit(url).path
        if not path.startswith(prefix) or path.rstrip("/") == prefix.rstrip("/"):
            continue
        d = _date(p.get("date_published")) or _date(p.get("first_seen"))
        if d and (best is None or d > best):
            best = d
    return best


def cmd_status(st: State, a):
    led, cfg = st.ledger(), st.config()
    cl = checklist(st.dir)
    rows = []
    applicable = [i for i in led["items"].values() if i["status"] != "n/a"]
    score = sum(1.0 if i["status"] == "pass" else 0.5 if i["status"] == "warn" else 0 for i in applicable)
    pct = round(100 * score / max(1, len(applicable)))
    for iid in cl:
        if iid not in led["items"]:
            continue
        it = led["items"][iid]
        rows.append({"item": iid, "reel": cl[iid].get("reel", ""), **{k: it.get(k) for k in ("status", "last_checked", "last_changed", "next_due", "evidence")}})
    lp = last_post(st)
    out = {"site": cfg.get("site_url"), "readiness_pct": pct, "items": rows, "last_post": lp.isoformat() if lp else None,
           "pages_tracked": len(st.pages()["pages"]), "due": compute_due(st)}
    if a.json:
        print(json.dumps(out, indent=2))
        return
    print(f"get-found status — {out['site']}  ·  readiness {pct}%  ·  {out['pages_tracked']} pages tracked  ·  last post: {out['last_post'] or 'none'}")
    print(f"{'item':<17}{'status':<8}{'last checked':<14}{'next due':<12}evidence")
    for r in rows:
        lc = (r["last_checked"] or "-")[:10]
        print(f"{r['item']:<17}{r['status']:<8}{lc:<14}{(r['next_due'] or '-'):<12}{(r['evidence'] or '')[:70]}")
    if out["due"]:
        print("\nDue now:")
        for d in out["due"]:
            print(f"  - {d['item']}: {d['reason']}")


def cmd_due(st: State, a):
    due = compute_due(st, a.days)
    if a.json:
        print(json.dumps(due, indent=2))
    else:
        if not due:
            print("nothing due")
        for d in due:
            print(f"{d['item']}: {d['reason']}")


# --------------------------------------------------------------------------- ingest

def cmd_ingest(st: State, a):
    rep = load_json(Path(a.report))
    if not rep or rep.get("tool") != "get-found-audit":
        die(f"{a.report} is not a get-found audit report")
    now = now_iso(precise=True)
    led = st.ledger()
    # 1) ledger statuses for auditable items
    rank = {"pass": 0, "warn": 1, "fail": 2}
    for iid, res in rep.get("items", {}).items():
        it = led["items"].setdefault(iid, {"status": "todo", "cadence_days": 30})
        prev = it.get("status")
        new_status = res["status"]
        if prev == "manual" and new_status == "pass":
            new_status = "manual"  # audit only covers the on-site half; an off-site step is still open
        it["status"] = new_status
        it["last_checked"] = now
        if prev != res["status"]:
            it["last_changed"] = now
        it["evidence"] = f"audit {rep['generated'][:10]}: {res['errors']} errors, {res['warnings']} warnings" + ("; off-site step pending" if new_status == "manual" else "")
        it["next_due"] = next_due(now, it.get("cadence_days", 30))
    st.save("ledger.json", led)

    # 2) page inventory + change detection
    inv = st.pages()
    pages = inv.setdefault("pages", {})
    seen = set()
    new, changed, removed = [], [], []
    stale_lastmod = []
    sm = rep.get("sitemap_lastmod") or {}
    for p in rep.get("pages", []):
        url = p["url"]
        gone = p.get("status") in (404, 410) or (p.get("redirect_target") and p["redirect_target"].rstrip("/") != url.rstrip("/"))
        if gone:
            if url in pages and not pages[url].get("removed"):
                pages[url]["removed"] = now
                pages[url]["last_changed"] = now
                removed.append(url)
            elif url not in pages and url in sm:
                # listed in the sitemap but dead: engines may hold it — record it so IndexNow is told
                pages[url] = {"first_seen": now, "last_seen": now, "last_changed": now, "removed": now, "submitted": {}}
                removed.append(url)
            continue
        if p.get("status") != 200 or p.get("non_html") or p.get("redirect_target"):
            continue
        url = p["url"]
        seen.add(url)
        rec = pages.get(url)
        snapshot = {k: p.get(k) for k in ("title", "description", "canonical", "noindex", "word_count", "date_published", "date_modified", "jsonld_types")}
        snapshot["og_image"] = (p.get("og") or {}).get("image")
        snapshot["sitemap_lastmod"] = (rep.get("sitemap_lastmod") or {}).get(url)
        if rec is None:
            pages[url] = {**snapshot, "content_hash": p.get("content_hash"), "first_seen": now, "last_seen": now, "last_changed": now, "submitted": {}}
            new.append(url)
        else:
            if rec.get("content_hash") != p.get("content_hash"):
                rec["last_changed"] = now
                changed.append(url)
                if snapshot["sitemap_lastmod"] and snapshot["sitemap_lastmod"] == rec.get("sitemap_lastmod"):
                    stale_lastmod.append(url)
            rec.update(snapshot)
            rec["content_hash"] = p.get("content_hash")
            rec["last_seen"] = now
            rec.pop("removed", None)
    # Pages that simply weren't reached are left alone: removal is only recorded from a real 404/410/redirect.
    # (audit.py re-fetches every known URL from pages.json, so dead pages are always seen.)
    inv["last_ingest"] = now
    st.save("pages.json", inv)
    cfg = st.config()
    if stale_lastmod and "sitemap" in led["items"]:
        sitem = led["items"]["sitemap"]
        if sitem.get("status") == "pass":
            sitem["status"] = "warn"
        sitem["evidence"] += f"; lastmod not updated for {len(stale_lastmod)} changed page(s): {', '.join(path_of(u) for u in stale_lastmod[:5])}"
    if (cfg.get("indexnow") or {}).get("key") and "indexnow" in led["items"]:
        n_pending = len(pending(st, "indexnow"))
        ix = led["items"]["indexnow"]
        if n_pending and ix.get("status") == "pass":
            ix["status"] = "warn"
            ix["evidence"] += f"; {n_pending} URLs pending submission (run sync after deploy)"
    st.save("ledger.json", led)
    lp = last_post(st)
    cad = (cfg.get("blog") or {}).get("cadence_days", 7)
    if lp and "blog" in led["items"]:
        b = led["items"]["blog"]
        age = (today() - lp).days
        new_status = "pass" if age < cad else ("warn" if age < 2 * cad else "fail")
        if b.get("status") != new_status:
            b["last_changed"] = now
        b.update({"status": new_status, "last_checked": now, "evidence": f"last post {lp.isoformat()} ({age} days ago)",
                  "next_due": (lp + dt.timedelta(days=cad)).isoformat()})
        st.save("ledger.json", led)
    st.history("ingest", report=str(a.report), new=len(new), changed=len(changed), removed=len(removed),
               errors=rep["summary"]["errors"], warnings=rep["summary"]["warnings"])
    first_run = len(new) == len(seen) and not changed
    print(f"ingested {a.report}: {len(seen)} pages ({len(new)} new, {len(changed)} changed, {len(removed)} removed)"
          + (" — first inventory" if first_run else ""))
    for label, lst in (("new", new), ("changed", changed), ("removed", removed)):
        for u in lst[:25]:
            print(f"  {label:<8}{path_of(u)}")
        if len(lst) > 25:
            print(f"  … {len(lst) - 25} more {label}")


# --------------------------------------------------------------------------- submissions

def pending(st: State, channel: str):
    """URLs whose last change is newer than their last submission on a channel."""
    out = []
    for url, rec in st.pages()["pages"].items():
        sub = (rec.get("submitted") or {}).get(channel)
        if rec.get("noindex") and not sub:
            continue  # never announced and not meant to be indexed
        lc = rec.get("last_changed")
        if not sub or (_dt_or_min(lc) > _dt_or_min(sub)):
            out.append((url, "removed" if rec.get("removed") else ("new" if not sub else "changed")))
    return out


def _dt_or_min(s):
    d = parse_date(s)
    return d or dt.datetime.min.replace(tzinfo=dt.timezone.utc)


def cmd_changes(st: State, a):
    lst = pending(st, a.channel)
    if a.json:
        print(json.dumps([{"url": u, "kind": k} for u, k in lst], indent=2))
        return
    if not lst:
        print(f"no pending changes for {a.channel}")
    for u, k in lst:
        print(f"{k:<8}{u}")


def mark_submitted(st: State, channel: str, urls: list[str]):
    inv = st.pages()
    now = now_iso(precise=True)
    n = 0
    for u in urls:
        rec = inv["pages"].get(u)
        if rec is None:
            rec = inv["pages"][u] = {"first_seen": now, "last_seen": now, "last_changed": now, "submitted": {}}
        rec.setdefault("submitted", {})[channel] = now
        n += 1
    st.save("pages.json", inv)
    st.history("submitted", channel=channel, count=n)
    return n


def cmd_submitted(st: State, a):
    urls = [u for u, _ in pending(st, a.channel)] if a.all_pending else (a.urls or [])
    n = mark_submitted(st, a.channel, urls)
    print(f"marked {n} URLs as submitted via {a.channel}")


# --------------------------------------------------------------------------- manual marks / log

def cmd_mark(st: State, a):
    led = st.ledger()
    if a.item not in led["items"]:
        die(f"unknown item '{a.item}'. Known: {', '.join(led['items'])}")
    it = led["items"][a.item]
    now = now_iso()
    if it.get("status") != a.status:
        it["last_changed"] = now
    it["status"] = a.status
    it["last_checked"] = now
    if a.evidence is not None:
        it["evidence"] = a.evidence
    if a.note is not None:
        it["notes"] = a.note
    if a.cadence:
        it["cadence_days"] = a.cadence
    it["next_due"] = a.next_due or next_due(now, it.get("cadence_days", 30))
    st.save("ledger.json", led)
    st.history("mark", item=a.item, status=a.status, evidence=a.evidence)
    print(f"{a.item}: {a.status} (next due {it['next_due']})")


def cmd_log(st: State, a):
    st.history("note", message=" ".join(a.message))
    print("logged")


# --------------------------------------------------------------------------- keywords

def cmd_keywords(st: State, a):
    kw = st.keywords()
    pages = st.pages()["pages"]
    mapping = kw.get("pages", {})
    if not a.check:
        for path, k in sorted(mapping.items()):
            print(f"{path:<40} {k.get('primary', ''):<40} {k.get('intent', '')}")
        bl = kw.get("backlog", [])
        if bl:
            print(f"\nbacklog ({len(bl)}):")
            for b in bl:
                print(f"  [{b.get('status', 'idea'):<9}] {b.get('keyword', ''):<45} {b.get('planned_for') or ''} {b.get('url') or ''}")
        return
    problems = []
    seen = {}
    for path, k in mapping.items():
        pk = (k.get("primary") or "").strip().lower()
        if not pk:
            continue
        if pk in seen:
            problems.append(f"cannibalization: '{pk}' is primary for both {seen[pk]} and {path}")
        seen[pk] = path
    for url, rec in pages.items():
        if rec.get("removed") or rec.get("noindex"):
            continue
        path = path_of(url)
        k = mapping.get(path) or mapping.get(path.rstrip("/")) or mapping.get(path + "/")
        if not k or not k.get("primary"):
            problems.append(f"no primary keyword: {path}")
            continue
        words = [w for w in k["primary"].lower().split() if len(w) > 2]
        title = (rec.get("title") or "").lower()
        if words and sum(w in title for w in words) / len(words) < 0.5:
            problems.append(f"keyword not reflected in title: {path} — '{k['primary']}' vs '{rec.get('title')}'")
    for path, k in mapping.items():
        ev = str(k.get("evidence") or "").strip().lower()
        if k.get("primary") and (not ev or "unverified" in ev):
            problems.append(f"no demand evidence for '{k['primary']}' ({path}) — check Search Console/Bing/Keyword Planner before relying on it")
    for b in kw.get("backlog", []):
        bk = (b.get("keyword") or "").strip().lower()
        if bk in seen and b.get("status") != "published":
            problems.append(f"backlog keyword '{bk}' already targeted by {seen[bk]} — refresh that page instead of a new post")
    if a.json:
        print(json.dumps(problems, indent=2))
    else:
        print("\n".join(problems) if problems else "keyword map OK")


# --------------------------------------------------------------------------- listings

def cmd_listings(st: State, a):
    ls = st.listings()["listings"]
    if a.json:
        print(json.dumps(ls, indent=2))
        return
    live = sum(1 for l in ls if l["status"] == "live")
    print(f"{live} live / {len(ls)} tracked listings")
    for l in ls:
        print(f"  [{l['status']:<9}] {l['priority']:<5} {l['name']:<32} {l.get('listing_url') or l.get('submit_url') or ''}")


def cmd_listing(st: State, a):
    data = st.listings()
    for l in data["listings"]:
        if l["id"] == a.id:
            break
    else:
        l = {"id": a.id, "name": a.name or a.id, "submit_url": None, "priority": "nice", "status": "todo", "listing_url": None, "updated": None, "notes": ""}
        data["listings"].append(l)
    l["status"] = a.status
    if a.url:
        l["listing_url"] = a.url
    if a.note:
        l["notes"] = a.note
    l["updated"] = now_iso()
    st.save("listings.json", data)
    live = sum(1 for x in data["listings"] if x["status"] == "live")
    led = st.ledger()
    if "directories" in led["items"]:
        it = led["items"]["directories"]
        it["evidence"] = f"{live} listings live"
        it["last_checked"] = now_iso()
        target = int((st.config().get("directories_target") or 10))
        it["status"] = "pass" if live >= target else "warn" if live else it["status"]
        it["next_due"] = next_due(it["last_checked"], it.get("cadence_days", 90))
        st.save("ledger.json", led)
    st.history("listing", id=a.id, status=a.status, url=a.url)
    print(f"{a.id}: {a.status} ({live} live)")


# --------------------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default=".get-found")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init")
    s.add_argument("--site", required=True)
    s.add_argument("--type", nargs="+", default=["saas"], help=f"one or more of {', '.join(SITE_TYPES)} (space- or comma-separated)")
    s.add_argument("--social", nargs="*", help="profiles as name=url, e.g. linkedin=https://www.linkedin.com/company/acme")
    s.add_argument("--training", choices=["allow", "block"], help="AI-training crawler policy (search/answer bots are always allowed)")
    s.add_argument("--author", nargs="*", help="post author name(s) for bylines")
    s.add_argument("--framework")
    s.add_argument("--brand")
    s.add_argument("--locale")
    s.add_argument("--country", help="ISO country code of the main market, e.g. IN, US")
    s.add_argument("--vertical", nargs="*", help="e.g. healthcare real-estate (adds vertical directories)")
    s.add_argument("--blog-prefix", default="/blog/")
    s.add_argument("--blog-cadence", type=int, default=7)
    s.add_argument("--force", action="store_true")

    s = sub.add_parser("status"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("due"); s.add_argument("--days", type=int, default=0); s.add_argument("--json", action="store_true")
    s = sub.add_parser("ingest"); s.add_argument("report")
    s = sub.add_parser("changes"); s.add_argument("--channel", default="indexnow", choices=CHANNELS); s.add_argument("--json", action="store_true")
    s = sub.add_parser("submitted"); s.add_argument("--channel", required=True, choices=CHANNELS)
    s.add_argument("--urls", nargs="*"); s.add_argument("--all-pending", action="store_true")
    s = sub.add_parser("mark"); s.add_argument("item")
    s.add_argument("--status", required=True, choices=["pass", "warn", "fail", "manual", "todo", "n/a"])
    s.add_argument("--evidence"); s.add_argument("--note"); s.add_argument("--cadence", type=int); s.add_argument("--next-due")
    s = sub.add_parser("keywords"); s.add_argument("--check", action="store_true"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("listings"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("listing"); s.add_argument("id"); s.add_argument("--status", required=True, choices=["todo", "submitted", "live", "rejected", "skipped", "n/a"])
    s.add_argument("--url"); s.add_argument("--note"); s.add_argument("--name")
    s = sub.add_parser("log"); s.add_argument("message", nargs="+")

    a = ap.parse_args()
    st = State(a.state)
    if a.cmd != "init":
        st.require()
    {"init": cmd_init, "status": cmd_status, "due": cmd_due, "ingest": cmd_ingest, "changes": cmd_changes,
     "submitted": cmd_submitted, "mark": cmd_mark, "keywords": cmd_keywords, "listings": cmd_listings,
     "listing": cmd_listing, "log": cmd_log}[a.cmd](st, a)


if __name__ == "__main__":
    main()
