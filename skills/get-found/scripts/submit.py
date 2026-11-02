#!/usr/bin/env python3
"""get-found submissions: IndexNow, Google Search Console API, Bing Webmaster API.

Credentials come only from environment variables — never put them in files or commit them.
  INDEXNOW_KEY                     (optional; otherwise .get-found/config.json indexnow.key — the key is public by design)
  GOOGLE_APPLICATION_CREDENTIALS   path to a service-account JSON (needs: pip install google-auth requests)
  GSC_ACCESS_TOKEN                 alternatively, a ready OAuth access token with the webmasters scope
  BING_WEBMASTER_API_KEY           API key from Bing Webmaster Tools > Settings > API access

IndexNow
  submit.py indexnow-key [--public-dir apps/web/public]  # create key + key file (default: config public_dir)
  submit.py indexnow --from-state                        # submit every new/changed/removed URL not yet pinged
  submit.py indexnow --urls https://site/a https://site/b [--dry-run]
Google (property = config google.property, e.g. sc-domain:example.com or https://example.com/)
  submit.py google sites | sitemaps | submit-sitemap <url> [--from-state]
  submit.py google inspect <url> [<url> ...]
  submit.py google performance [--days 28] [--dimensions query page] [--limit 1000] [--out file.json]
Bing
  submit.py bing sites | quota | sitemaps | submit-sitemap <url> | queries [--out file.json]
  submit.py bing submit-urls --from-state | --urls ...

Endpoints/quotas are the baseline from references/sources.json; if a call fails with 404/410,
re-check the live docs (rules.py) and set an override in .get-found/endpoints.override.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import secrets
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import die, fetch, find_state_dir, load_reference, now_iso, save_json, site_key  # noqa: E402
from state import State, mark_submitted, pending  # noqa: E402


def endpoints(state_dir):
    return load_reference("endpoints.json", state_dir)


def jpost(url, body, headers=None, method="POST"):
    h = {"Content-Type": "application/json; charset=utf-8"}
    h.update(headers or {})
    r = fetch(url, method=method, data=json.dumps(body).encode() if body is not None else None, headers=h, timeout=60)
    return r


def show(r, label):
    if r.error:
        die(f"{label}: {r.error}")
    try:
        data = json.loads(r.body or b"null")
    except json.JSONDecodeError:
        data = r.text()[:500]
    return r.status, data


# --------------------------------------------------------------------------- IndexNow

FRAMEWORK_MARKERS = ("package.json", "next.config.js", "next.config.mjs", "next.config.ts", "astro.config.mjs", "astro.config.ts",
                     "nuxt.config.ts", "svelte.config.js", "gatsby-config.js", "hugo.toml", "_config.yml", "vite.config.ts",
                     "vite.config.js", "docusaurus.config.js", "eleventy.config.js", ".eleventy.js", "index.html")


def cmd_indexnow_key(st: State, a):
    cfg = st.config()
    repo = st.dir.resolve().parent
    if a.public_dir:
        out_dir = Path(a.public_dir)
    elif cfg.get("public_dir"):
        out_dir = repo / cfg["public_dir"]  # stored relative to the repo root (the dir holding .get-found/)
    else:
        die("which dir is served at the site root? pass --public-dir (e.g. public, static, apps/web/public), "
            "or record it with `state.py init --force --app-dir <app>`")
    if not out_dir.is_dir():
        if not a.create:
            die(f"{out_dir} does not exist — a key file there would never be deployed. Check the app's public/static dir "
                "(monorepos: apps/<app>/public), or pass --create if this really is the right place.")
        out_dir.mkdir(parents=True, exist_ok=True)
    app_dir = out_dir.resolve().parent
    if not any((app_dir / m).exists() for m in FRAMEWORK_MARKERS) and out_dir.resolve() != repo:
        print(f"warning: no package.json or framework config next to {out_dir} — is this the deployed app's public dir?", file=sys.stderr)
    key = (cfg.get("indexnow") or {}).get("key") or secrets.token_hex(16)
    (out_dir / f"{key}.txt").write_text(key, encoding="utf-8")
    cfg.setdefault("indexnow", {})["key"] = key
    cfg["indexnow"]["key_location"] = None
    st.save("config.json", cfg)
    st.history("indexnow-key", file=str(out_dir / f"{key}.txt"))
    print(f"IndexNow key written to {out_dir / (key + '.txt')} and saved in config. Deploy it so it is served at /{key}.txt")


def cmd_indexnow(st: State, a):
    cfg = st.config()
    ep = endpoints(st.dir)["indexnow"]
    key = os.environ.get("INDEXNOW_KEY") or (cfg.get("indexnow") or {}).get("key")
    if not key:
        die("no IndexNow key — run `submit.py indexnow-key --public-dir <your static dir>` first")
    site = a.site or cfg.get("canonical_url") or cfg.get("site_url")
    if not site:
        die("no site: pass --site or set site_url in config")
    want = site_key(urllib.parse.urlsplit(site).hostname or "")
    urls = a.urls or []
    if a.from_state:
        urls = [u for u, _ in pending(st, "indexnow")]
    urls = list(dict.fromkeys(urls))
    # IndexNow takes one host per request and the key must be served on that host. Group by the
    # URLs' real host, accepting www/apex variants of the site (a site set up as example.com that
    # redirects to www.example.com stores www URLs).
    groups: dict[str, list[str]] = {}
    dropped = []
    for u in urls:
        h = (urllib.parse.urlsplit(u).hostname or "").lower()
        (groups.setdefault(h, []) if h and site_key(h) == want else dropped).append(u)
    if dropped:
        print(f"skipping {len(dropped)} URLs not on {want} (e.g. {dropped[0]})", file=sys.stderr)
    if not urls:
        print("nothing to submit to IndexNow")
        return
    if not groups:
        die(f"none of the {len(urls)} URLs are on {want} — check site_url in config (state.py init --force --site ...) or pass --site")
    configured = (cfg.get("indexnow") or {}).get("key_location")
    total, failed = 0, []
    for host, host_urls in groups.items():
        scheme = urllib.parse.urlsplit(host_urls[0]).scheme or "https"
        key_loc = configured if configured and (urllib.parse.urlsplit(configured).hostname or "").lower() == host else f"{scheme}://{host}/{key}.txt"
        kr = fetch(key_loc)
        if not (kr.ok and kr.text().strip() == key):
            msg = f"key file {key_loc} is not live (HTTP {kr.status or kr.error})"
            if a.dry_run:
                print(f"warning: {msg} — deploy it before a real submission", file=sys.stderr)
            else:
                print(f"{msg}. Deploy it first — engines reject submissions otherwise.", file=sys.stderr)
                failed.append(host)
                continue
        for i in range(0, len(host_urls), ep["max_urls_per_request"]):
            batch = host_urls[i:i + ep["max_urls_per_request"]]
            body = {"host": host, "key": key, "keyLocation": key_loc, "urlList": batch}
            if a.dry_run:
                print(json.dumps(body, indent=2)[:2000])
                continue
            r = jpost(a.endpoint or ep["endpoint"], body)
            meaning = ep["responses"].get(str(r.status), "unexpected response")
            print(f"IndexNow {host}: {len(batch)} URLs → HTTP {r.status or r.error}: {meaning}")
            if r.status in (200, 202):
                total += len(batch)
                if a.from_state or st.p("pages.json").exists():
                    mark_submitted(st, "indexnow", batch)
            else:
                die(f"IndexNow rejected the batch: {r.text()[:300]}")
    if total:
        st.history("indexnow", count=total)
        left = len(pending(st, "indexnow"))
        _touch(st, "indexnow", f"{total} URLs submitted {now_iso()[:10]}; {left} pending", force_status="pass" if not left else "warn")
    if failed:
        die(f"nothing submitted for {', '.join(failed)}: key file not live")


# --------------------------------------------------------------------------- Google

GSC_SCOPE = "https://www.googleapis.com/auth/webmasters"


def google_token():
    tok = os.environ.get("GSC_ACCESS_TOKEN")
    if tok:
        return tok
    cred = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if cred:
        try:
            from google.oauth2 import service_account  # type: ignore
            import google.auth.transport.requests as gtr  # type: ignore
        except ImportError:
            die("GOOGLE_APPLICATION_CREDENTIALS is set but google-auth is missing: pip install google-auth requests")
        creds = service_account.Credentials.from_service_account_file(cred, scopes=[GSC_SCOPE])
        creds.refresh(gtr.Request())
        return creds.token
    die("no Google credentials: set GSC_ACCESS_TOKEN or GOOGLE_APPLICATION_CREDENTIALS (service account added as a user on the Search Console property). See references/google.md")


def gcall(method, url, body=None):
    r = jpost(url, body, headers={"Authorization": f"Bearer {google_token()}"}, method=method)
    status, data = show(r, "Google API")
    if status >= 400:
        msg = data.get("error", {}).get("message") if isinstance(data, dict) else data
        die(f"Google API {status}: {msg}")
    return data


def cmd_google(st: State, a):
    cfg = st.config()
    ep = endpoints(st.dir)["google"]
    prop = a.property or (cfg.get("google") or {}).get("property")
    if not prop and a.action != "sites":
        die("no Search Console property in config (google.property)")
    enc = urllib.parse.quote(prop or "", safe="")
    base = ep["webmasters_base"]
    if a.action == "sites":
        print(json.dumps(gcall("GET", f"{base}/sites"), indent=2))
    elif a.action == "sitemaps":
        print(json.dumps(gcall("GET", f"{base}/sites/{enc}/sitemaps"), indent=2))
    elif a.action == "submit-sitemap":
        if not a.args:
            die("give the sitemap URL")
        for sm in a.args:
            gcall("PUT", f"{base}/sites/{enc}/sitemaps/{urllib.parse.quote(sm, safe='')}")
            print(f"submitted sitemap {sm} to {prop}")
        if a.from_state:
            n = mark_submitted(st, "google", [u for u, k in pending(st, "google") if k != "removed"])
            print(f"marked {n} URLs as covered by the Google sitemap submission")
        st.history("google-sitemap", sitemaps=a.args)
        _touch(st, "search-console", f"sitemap submitted {now_iso()[:10]}")
    elif a.action == "inspect":
        results = []
        for u in a.args[: ep["inspect_soft_limit_per_run"]]:
            d = gcall("POST", ep["inspect_url"], {"inspectionUrl": u, "siteUrl": prop, "languageCode": "en-US"})
            idx = (d.get("inspectionResult") or {}).get("indexStatusResult") or {}
            results.append({"url": u, "verdict": idx.get("verdict"), "coverage": idx.get("coverageState"),
                            "last_crawl": idx.get("lastCrawlTime"), "google_canonical": idx.get("googleCanonical"),
                            "robots": idx.get("robotsTxtState"), "indexing": idx.get("indexingState")})
        print(json.dumps(results, indent=2))
    elif a.action == "performance":
        end = dt.date.today() - dt.timedelta(days=2)
        start = end - dt.timedelta(days=a.days - 1)
        body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "dimensions": a.dimensions,
                "rowLimit": a.limit, "dataState": "all"}
        d = gcall("POST", f"{base}/sites/{enc}/searchAnalytics/query", body)
        rows = d.get("rows", [])
        if a.out:
            save_json(Path(a.out), {"property": prop, "start": body["startDate"], "end": body["endDate"], "dimensions": a.dimensions, "rows": rows})
            print(f"{len(rows)} rows → {a.out}")
        else:
            for r in rows[:50]:
                print(f"{' | '.join(r['keys']):<70} clicks {r['clicks']:<6} impr {r['impressions']:<7} pos {r['position']:.1f}")
    else:
        die(f"unknown google action {a.action}")


# --------------------------------------------------------------------------- Bing

def bing_key():
    k = os.environ.get("BING_WEBMASTER_API_KEY")
    if not k:
        die("BING_WEBMASTER_API_KEY is not set (Bing Webmaster Tools > Settings > API access > API key)")
    return k


def bcall(st, method, name, params=None, body=None):
    ep = endpoints(st.dir)["bing"]
    q = {"apikey": bing_key(), **(params or {})}
    url = f"{ep['base']}/{name}?{urllib.parse.urlencode(q)}"
    r = jpost(url, body, method=method) if method == "POST" else fetch(url, headers={"Accept": "application/json"})
    status, data = show(r, f"Bing {name}")
    if status >= 400:
        die(f"Bing {name} → HTTP {status}: {data if isinstance(data, str) else json.dumps(data)[:300]}")
    return data.get("d", data) if isinstance(data, dict) else data


def cmd_bing(st: State, a):
    cfg = st.config()
    site = cfg.get("site_url")
    if a.action == "sites":
        print(json.dumps(bcall(st, "GET", "GetUserSites"), indent=2))
    elif a.action == "quota":
        print(json.dumps(bcall(st, "GET", "GetUrlSubmissionQuota", {"siteUrl": site}), indent=2))
    elif a.action == "sitemaps":
        print(json.dumps(bcall(st, "GET", "GetFeeds", {"siteUrl": site}), indent=2))
    elif a.action == "submit-sitemap":
        for sm in a.args:
            bcall(st, "POST", "SubmitFeed", body={"siteUrl": site, "feedUrl": sm})
            print(f"submitted {sm} to Bing")
        _touch(st, "bing-webmaster", f"sitemap submitted {now_iso()[:10]}")
    elif a.action == "queries":
        d = bcall(st, "GET", "GetQueryStats", {"siteUrl": site})
        if a.out:
            save_json(Path(a.out), {"site": site, "fetched": now_iso(), "rows": d})
            print(f"{len(d) if isinstance(d, list) else '?'} rows → {a.out}")
        else:
            print(json.dumps(d[:50] if isinstance(d, list) else d, indent=2))
    elif a.action == "submit-urls":
        urls = [u for u, k in pending(st, "bing") if k != "removed"] if a.from_state else (a.urls or [])
        if not urls:
            print("nothing to submit to Bing")
            return
        quota = bcall(st, "GET", "GetUrlSubmissionQuota", {"siteUrl": site})
        daily = (quota or {}).get("DailyQuota")
        if daily is not None and daily < len(urls):
            print(f"Bing daily quota {daily} < {len(urls)} URLs; submitting the first {daily}. (IndexNow has no such cap — prefer it.)")
            urls = urls[:daily]
        ep = endpoints(st.dir)["bing"]
        step = ep.get("submit_url_batch_max", 500)
        for i in range(0, len(urls), step):
            batch = urls[i:i + step]
            bcall(st, "POST", ep.get("submit_url_batch_method", "SubmitUrlbatch"), body={"siteUrl": site, "urlList": batch})
            mark_submitted(st, "bing", batch)
        print(f"submitted {len(urls)} URLs to Bing")
    else:
        die(f"unknown bing action {a.action}")


def _touch(st: State, item: str, evidence: str, force_status: str | None = None):
    led = st.ledger()
    it = led["items"].get(item)
    if not it:
        return
    it["last_checked"] = now_iso()
    it["evidence"] = evidence
    new_status = force_status or ("pass" if it["status"] in ("todo", "manual", "fail") else it["status"])
    if new_status != it["status"]:
        it["status"] = new_status
        it["last_changed"] = it["last_checked"]
    d = dt.date.today() + dt.timedelta(days=it.get("cadence_days", 30))
    it["next_due"] = d.isoformat()
    st.save("ledger.json", led)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", help="state dir (default: nearest .get-found/ up to the git root)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("indexnow-key"); s.add_argument("--public-dir", help="dir served at the site root (default: config public_dir, relative to the repo root)")
    s.add_argument("--create", action="store_true", help="create --public-dir if it does not exist")
    s = sub.add_parser("indexnow"); s.add_argument("--urls", nargs="*"); s.add_argument("--from-state", action="store_true")
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--endpoint"); s.add_argument("--site")
    s = sub.add_parser("google"); s.add_argument("action", choices=["sites", "sitemaps", "submit-sitemap", "inspect", "performance"])
    s.add_argument("args", nargs="*"); s.add_argument("--property"); s.add_argument("--from-state", action="store_true")
    s.add_argument("--days", type=int, default=28); s.add_argument("--dimensions", nargs="+", default=["query", "page"])
    s.add_argument("--limit", type=int, default=1000); s.add_argument("--out")
    s = sub.add_parser("bing"); s.add_argument("action", choices=["sites", "quota", "sitemaps", "submit-sitemap", "queries", "submit-urls"])
    s.add_argument("args", nargs="*"); s.add_argument("--urls", nargs="*"); s.add_argument("--from-state", action="store_true"); s.add_argument("--out")
    a = ap.parse_args()
    st = State(find_state_dir(a.state)).require()
    {"indexnow-key": cmd_indexnow_key, "indexnow": cmd_indexnow, "google": cmd_google, "bing": cmd_bing}[a.cmd](st, a)


if __name__ == "__main__":
    main()
