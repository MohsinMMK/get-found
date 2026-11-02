#!/usr/bin/env python3
"""get-found project state: config, ledger, page inventory, keywords, listings, history.

All state lives in the project's .get-found/ directory (commit it; it holds no secrets).

  state.py init --site https://example.com --type saas [--framework nextjs] [--brand "Acme"] [--blog-prefix /blog/]
               [--app-dir apps/web] [--public-dir apps/web/public]   # monorepos: which app is the website
  state.py detect [--json]                 # framework / monorepo app detection (read-only)
  state.py status [--json]                 # every checklist item: status, last checked, next due
  state.py due [--days 0] [--json]         # what needs doing now (drives sync/check runs)
  state.py ingest <audit.json> [--force]   # apply an audit report: ledger + page inventory + change detection
  state.py changes [--channel indexnow]    # URLs new/changed/removed since last submitted on a channel
  state.py submitted --channel indexnow --urls U1 U2 | --all-pending
  state.py mark <item> --status pass|warn|fail|manual|n/a [--evidence ".."] [--note ".."]
  state.py keywords [--check]              # keyword map + cannibalization / coverage check
  state.py listings [--json]               # directory/listing tracker
  state.py listing <id> --status live --url https://... [--note ".."]
  state.py log "message"                   # append to history

Without --state (or with the bare default .get-found) the state dir is found by walking up from
the cwd to the git root, so commands work from apps/<app>/ in a monorepo; init creates it at the
git root.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import os
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (VERSION, die, find_state_dir, load_json, load_reference, now_iso, parse_date,  # noqa: E402
                     path_of, save_json, site_key, today)

ARTICLE_TYPES = {"Article", "BlogPosting", "NewsArticle", "TechArticle"}
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
    cfg = st.config() if a.force else {}  # --force keeps every setting not given again on the command line
    if not (a.site or cfg.get("site_url")):
        die("--site is required")
    site = (a.site or cfg["site_url"]).rstrip("/")
    if not urllib.parse.urlsplit(site).scheme:
        site = "https://" + site
    host = urllib.parse.urlsplit(site).hostname or ""
    apex = host[4:] if host.startswith("www.") else host
    types = [t.strip() for raw in (a.type or cfg.get("site_types") or ["saas"]) for t in raw.split(",") if t.strip()]
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
    old_blog = cfg.get("blog") or {}
    prefixes = [p for p in (a.blog_prefix or []) if p]
    blog_prefix = (prefixes[0] if len(prefixes) == 1 else prefixes) if prefixes else (old_blog.get("path_prefix") or "/blog/")
    app = resolve_app(st, a, cfg)
    if app.get("framework") and not a.framework and cfg.get("framework") in (None, "unknown"):
        a.framework = app["framework"]
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
        "blog": {"path_prefix": blog_prefix, "cadence_days": a.blog_cadence or old_blog.get("cadence_days") or 7},
        "sitemaps": cfg.get("sitemaps") or [],
        # sample a blog path for the bot matrix only when the user told us where the blog lives
        "sample_paths": cfg.get("sample_paths") or ["/"] + prefixes,
        "app_dir": app.get("app_dir"),
        "public_dir": app.get("public_dir"),
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
    if cfg.get("app_dir") not in (None, "."):
        print(f"  website app: {cfg['app_dir']} (public dir {cfg.get('public_dir')})")


# --------------------------------------------------------------------------- framework / monorepo detection

FRAMEWORK_FILES = [
    ("nextjs", ("next.config.js", "next.config.mjs", "next.config.ts", "next.config.cjs"), "public"),
    ("astro", ("astro.config.mjs", "astro.config.ts", "astro.config.js", "astro.config.cjs"), "public"),
    ("nuxt", ("nuxt.config.ts", "nuxt.config.js", "nuxt.config.mjs"), "public"),
    ("sveltekit", ("svelte.config.js", "svelte.config.ts"), "static"),
    ("react-router", ("react-router.config.ts", "react-router.config.js", "remix.config.js"), "public"),
    ("gatsby", ("gatsby-config.js", "gatsby-config.ts", "gatsby-config.mjs"), "static"),
    ("docusaurus", ("docusaurus.config.js", "docusaurus.config.ts"), "static"),
    ("eleventy", (".eleventy.js", "eleventy.config.js", "eleventy.config.mjs", "eleventy.config.cjs"), "."),
    ("hugo", ("hugo.toml", "hugo.yaml", "hugo.json"), "static"),
    ("vite", ("vite.config.ts", "vite.config.js", "vite.config.mjs"), "public"),
]
PKG_FRAMEWORKS = {"next": "nextjs", "astro": "astro", "nuxt": "nuxt", "@sveltejs/kit": "sveltekit", "@remix-run/react": "react-router",
                  "@react-router/dev": "react-router", "gatsby": "gatsby", "@docusaurus/core": "docusaurus", "vite": "vite"}
INTERNAL_HINT = ("admin", "ops", "dashboard", "internal", "studio", "backoffice", "console", "cms", "storybook")


def _framework_of(d: Path) -> tuple[str, str] | None:
    spa = (d / "index.html").exists()  # vite also builds libraries; only an index.html makes it a site
    for name, files, public in FRAMEWORK_FILES:
        if any((d / f).exists() for f in files) and (name != "vite" or spa):
            return name, public
    pkg = load_json(d / "package.json", default=None) if (d / "package.json").exists() else None
    if isinstance(pkg, dict):
        deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
        for dep, name in PKG_FRAMEWORKS.items():
            if dep in deps and (name != "vite" or spa):
                return name, next(p for n, _, p in FRAMEWORK_FILES if n == name)
    return None


def _workspace_globs(root: Path) -> list[str]:
    globs: list[str] = []
    pkg = load_json(root / "package.json", default=None) if (root / "package.json").exists() else None
    if isinstance(pkg, dict):
        ws = pkg.get("workspaces")
        globs += ws if isinstance(ws, list) else (ws or {}).get("packages", []) if isinstance(ws, dict) else []
    pw = root / "pnpm-workspace.yaml"
    if pw.exists():
        in_pk = False
        for line in pw.read_text(encoding="utf-8", errors="replace").splitlines():
            if re.match(r"^packages\s*:", line):
                in_pk = True
                continue
            if in_pk:
                m = re.match(r"^\s*-\s*['\"]?([^'\"#]+?)['\"]?\s*(#.*)?$", line)
                if m:
                    globs.append(m.group(1).strip())
                elif line.strip() and not line.startswith((" ", "\t")):
                    in_pk = False
    if not globs and any((root / f).exists() for f in ("turbo.json", "nx.json", "lerna.json")):
        globs = ["apps/*", "packages/*"]
    if not globs and (root / "apps").is_dir():
        globs = ["apps/*"]
    return [g for g in globs if not g.startswith("!")]


def detect_apps(root: Path) -> dict:
    """Find the deployable web app(s): the repo root itself, or workspace packages with a framework."""
    root = root.resolve()
    globs = _workspace_globs(root)
    apps = []
    seen = set()
    for g in globs:
        for d in sorted(root.glob(g.rstrip("/"))):
            if not d.is_dir() or "node_modules" in d.parts or d in seen:
                continue
            seen.add(d)
            fw = _framework_of(d)
            if fw:
                rel = d.relative_to(root).as_posix()
                apps.append({"app_dir": rel, "framework": fw[0], "public_dir": (Path(rel) / fw[1]).as_posix() if fw[1] != "." else rel,
                             "looks_internal": any(h in d.name.lower() for h in INTERNAL_HINT)})
    root_fw = _framework_of(root)
    return {"root": str(root), "monorepo": bool(globs), "workspace_globs": globs, "apps": apps,
            "root_app": {"app_dir": ".", "framework": root_fw[0], "public_dir": root_fw[1]} if root_fw else None}


def resolve_app(st: State, a, cfg: dict) -> dict:
    """Pick the website app: --app-dir wins, then the stored one, then detection (ask when ambiguous)."""
    root = st.dir.resolve().parent
    if a.app_dir:
        d = (root / a.app_dir).resolve() if not Path(a.app_dir).is_absolute() else Path(a.app_dir)
        if not d.is_dir():
            die(f"--app-dir {a.app_dir} is not a directory under {root}")
        rel = os.path.relpath(d, root)
        fw = _framework_of(d)
        pub = a.public_dir or (os.path.normpath(os.path.join(rel, fw[1])) if fw else os.path.join(rel, "public"))
        return {"app_dir": Path(rel).as_posix(), "public_dir": Path(pub).as_posix(), "framework": fw[0] if fw else None}
    if cfg.get("app_dir"):
        return {"app_dir": cfg["app_dir"], "public_dir": a.public_dir or cfg.get("public_dir")}
    det = detect_apps(root)
    apps = det["apps"]
    if len(apps) > 1:
        lines = "\n".join(f"  --app-dir {x['app_dir']:<28} ({x['framework']}{', looks internal/admin' if x['looks_internal'] else ''})" for x in apps)
        die(f"monorepo with {len(apps)} web apps — say which one is the public website:\n{lines}\n"
            "Re-run init with --app-dir <dir> (keep the one .get-found/ at the repo root; audit only the public app).")
    pick = apps[0] if apps else det["root_app"]
    if not pick:
        return {"app_dir": ".", "public_dir": a.public_dir}
    return {"app_dir": pick["app_dir"], "public_dir": a.public_dir or pick["public_dir"], "framework": pick["framework"]}


def cmd_detect(st: State, a):
    det = detect_apps(st.dir.resolve().parent)
    if a.json:
        print(json.dumps(det, indent=2))
        return
    print(f"repo root: {det['root']}{' (monorepo: ' + ', '.join(det['workspace_globs']) + ')' if det['monorepo'] else ''}")
    if det["root_app"]:
        print(f"  .  {det['root_app']['framework']}  public dir: {det['root_app']['public_dir']}")
    for x in det["apps"]:
        print(f"  {x['app_dir']}  {x['framework']}  public dir: {x['public_dir']}{'  (looks internal/admin)' if x['looks_internal'] else ''}")
    if not det["apps"] and not det["root_app"]:
        print("  no framework config found — see references/frameworks.md §Detect")


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
    if pend:
        due.append({"item": "sync", "reason": f"{len(pend)} new/changed URLs not yet sent to IndexNow — run sync (submit.py indexnow --from-state)"})
    # Google has no submit API for ordinary pages: only count it once the project tracks the channel
    # (API credentials present, or the google channel was marked submitted before), and say how to clear it.
    if google_tracked(cfg, pages):
        pend_g = pending(st, "google")
        if pend_g:
            due.append({"item": "sync", "reason": f"{len(pend_g)} new/changed URLs not yet submitted to Google — resubmit the sitemap "
                                                  "(submit.py google submit-sitemap) or use URL Inspection → Request indexing, then "
                                                  "`state.py submitted --channel google --all-pending`"})
    # blog cadence
    lp = last_post(st)
    cad = (cfg.get("blog") or {}).get("cadence_days", 7)
    if lp:
        age = (today() - lp).days
        if age >= cad and not any(d["item"] == "blog" for d in due):
            due.append({"item": "blog", "reason": f"last post {lp.isoformat()} ({age} days ago, cadence {cad})"})
    return due


def google_tracked(cfg: dict, pages: dict) -> bool:
    if any(os.environ.get(k) for k in ("GOOGLE_APPLICATION_CREDENTIALS", "GSC_ACCESS_TOKEN")):
        return True
    return any((rec.get("submitted") or {}).get("google") for rec in pages.values())


def blog_prefixes(cfg: dict) -> list[str]:
    pfx = (cfg.get("blog") or {}).get("path_prefix") or "/blog/"
    return [p for p in ([pfx] if isinstance(pfx, str) else pfx) if p]


def last_post(st: State):
    """Newest post date: pages under a blog prefix, or any non-home page marked up as an Article."""
    cfg = st.config()
    prefixes = blog_prefixes(cfg)
    best = None
    for url, p in st.pages()["pages"].items():
        path = urllib.parse.urlsplit(url).path
        in_blog = any(path.startswith(px) and path.rstrip("/") != px.rstrip("/") for px in prefixes)
        is_article = path not in ("", "/") and bool(set(p.get("jsonld_types") or []) & ARTICLE_TYPES)
        if not (in_blog or is_article):
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
    cfg = st.config()
    # 0) is this report about the site this ledger tracks? (localhost/preview audits must not leak in)
    rep_host = urllib.parse.urlsplit(rep.get("site") or "").hostname or ""
    cfg_host = urllib.parse.urlsplit(cfg.get("site_url") or "").hostname or ""
    if rep_host and cfg_host and site_key(rep_host) != site_key(cfg_host):
        if not a.force:
            die(f"{a.report} is an audit of {rep_host}, but this ledger tracks {cfg_host} — not ingesting. "
                "For local/preview checks run audit.py without --state; if the site really moved, "
                "run `state.py init --force --site <new url>` (or pass --force to ingest anyway).")
        print(f"warning: ingesting a report for {rep_host} into the {cfg_host} ledger (--force)", file=sys.stderr)
    elif rep_host and rep_host != cfg_host and rep.get("site"):
        if cfg.get("canonical_url") != rep["site"]:
            # e.g. config says example.com but it redirects to www.example.com: remember where pages really live
            cfg["canonical_url"] = rep["site"]
            st.save("config.json", cfg)
            print(f"note: {cfg_host} redirects to {rep_host}; recorded canonical_url {rep['site']} (IndexNow submits for that host)")
    partial = bool(rep.get("partial") or rep.get("truncated"))
    led = st.ledger()
    # 1) ledger statuses for auditable items. A partial (--urls) or truncated audit skipped the
    # cross-page checks, so it may only make an item worse, never better.
    rank = {"pass": 0, "warn": 1, "fail": 2}
    for iid, res in rep.get("items", {}).items():
        it = led["items"].setdefault(iid, {"status": "todo", "cadence_days": 30})
        prev = it.get("status")
        if prev == "n/a":
            continue
        if partial and rank.get(res["status"], 0) <= rank.get(prev, 0):
            continue
        offsite = prev == "manual" or bool(it.get("manual_pending"))
        new_status = res["status"]
        if offsite:
            it["manual_pending"] = True  # an off-site step is still open, whatever the on-site half says
            if new_status == "pass":
                new_status = "manual"
        it["status"] = new_status
        it["last_checked"] = now
        if prev != new_status:
            it["last_changed"] = now
        it["evidence"] = (f"{'partial ' if partial else ''}audit {rep['generated'][:10]}: {res['errors']} errors, {res['warnings']} warnings"
                          + ("; off-site step pending" if offsite else ""))
        it["next_due"] = next_due(now, it.get("cadence_days", 30))
    # Search Console verified by DNS TXT or meta tag: the off-site half is likely done, confirm it
    sc = rep.get("site_checks") or {}
    gsc = led["items"].get("search-console")
    via = "DNS TXT" if (sc.get("dns_verification") or {}).get("google") else (
        "meta tag" if (sc.get("verification_meta") or {}).get("google-site-verification") else None)
    if gsc and via and gsc.get("status") == "todo":
        gsc.update({"status": "manual", "last_checked": now, "last_changed": now, "manual_pending": True,
                    "evidence": f"google-site-verification {via} found — confirm the property shows as verified in Search Console "
                                "and the sitemap is submitted, then mark pass"})
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
          + (" — first inventory" if first_run else "")
          + (" — partial audit: checklist items only updated where they got worse" if partial else ""))
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
    if a.status == "manual":
        it["manual_pending"] = True
    else:
        it.pop("manual_pending", None)  # an explicit mark settles the off-site step
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
    ap.add_argument("--state", help="state dir (default: nearest .get-found/ up to the git root)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init")
    s.add_argument("--site", help="site URL (required on first init)")
    s.add_argument("--type", nargs="+", help=f"one or more of {', '.join(SITE_TYPES)} (space- or comma-separated; default saas)")
    s.add_argument("--social", nargs="*", help="profiles as name=url, e.g. linkedin=https://www.linkedin.com/company/acme")
    s.add_argument("--training", choices=["allow", "block"], help="AI-training crawler policy (search/answer bots are always allowed)")
    s.add_argument("--author", nargs="*", help="post author name(s) for bylines")
    s.add_argument("--framework")
    s.add_argument("--brand")
    s.add_argument("--locale")
    s.add_argument("--country", help="ISO country code of the main market, e.g. IN, US")
    s.add_argument("--vertical", nargs="*", help="e.g. healthcare real-estate (adds vertical directories)")
    s.add_argument("--blog-prefix", nargs="*", help="blog path prefix(es), e.g. /blog/ /journal/ (default /blog/; posts are also found via Article JSON-LD)")
    s.add_argument("--blog-cadence", type=int, help="days between posts (default 7)")
    s.add_argument("--app-dir", help="monorepo: the website app's dir relative to the repo root, e.g. apps/storefront")
    s.add_argument("--public-dir", help="dir served at the site root (default: detected, e.g. apps/storefront/public)")
    s.add_argument("--force", action="store_true", help="re-initialise; keeps the ledger and every setting not given again")
    s = sub.add_parser("detect"); s.add_argument("--json", action="store_true")

    s = sub.add_parser("status"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("due"); s.add_argument("--days", type=int, default=0); s.add_argument("--json", action="store_true")
    s = sub.add_parser("ingest"); s.add_argument("report")
    s.add_argument("--force", action="store_true", help="ingest even if the report's host differs from config site_url")
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
    st = State(find_state_dir(a.state, for_init=a.cmd in ("init", "detect")))
    if a.cmd not in ("init", "detect"):
        st.require()
    {"init": cmd_init, "detect": cmd_detect, "status": cmd_status, "due": cmd_due, "ingest": cmd_ingest, "changes": cmd_changes,
     "submitted": cmd_submitted, "mark": cmd_mark, "keywords": cmd_keywords, "listings": cmd_listings,
     "listing": cmd_listing, "log": cmd_log}[a.cmd](st, a)


if __name__ == "__main__":
    main()
