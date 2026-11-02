#!/usr/bin/env python3
"""get-found audit: crawl a site and check every get-found checklist item that can be verified
from the outside. Standard library only.

Usage:
  python3 audit.py https://example.com
  python3 audit.py http://localhost:3000 --max-pages 50          # local dev / preview build
  python3 audit.py https://example.com --state .get-found          # writes report into state dir
  python3 audit.py https://example.com --urls /pricing /blog/new-post   # only these pages

Outputs <state>/reports/audit-<timestamp>.json and .md (or ./get-found-audit.* without --state)
and prints a short summary. Feed the JSON to `state.py ingest` to update the ledger.
Exit code: 0 = no errors, 1 = errors found, 2 = could not run.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import sys
import time
import urllib.parse
import uuid
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (VERSION, die, fetch, image_size, jsonld_types, load_reference, normalize_url,  # noqa: E402
                     now_iso, parse_date, parse_html, path_of, same_site, save_json, sha)
from robots import Robots, bot_matrix, verdict  # noqa: E402

import xml.etree.ElementTree as ET  # noqa: E402

SKIP_EXT = re.compile(r"\.(png|jpe?g|gif|webp|avif|svg|ico|pdf|zip|gz|mp4|mp3|webm|woff2?|ttf|css|js|json|xml|txt|rss|atom)$", re.I)


# --------------------------------------------------------------------------- issues

class Issues:
    def __init__(self):
        self.items = []

    def add(self, item: str, severity: str, code: str, message: str, url: str | None = None):
        self.items.append({"item": item, "severity": severity, "code": code, "message": message, "url": url})


# --------------------------------------------------------------------------- sitemap

def parse_sitemap(url: str, issues: Issues, th: dict, depth: int = 0, seen: set | None = None) -> dict:
    """Fetch a sitemap or sitemap index recursively. Returns {url: lastmod_or_None}."""
    seen = seen if seen is not None else set()
    out: dict[str, str | None] = {}
    if url in seen or depth > 2 or len(seen) > th["sitemap_max_files"]:
        return out
    seen.add(url)
    r = fetch(url, max_bytes=th["sitemap_max_bytes"] + 1)
    if r.error or not r.ok:
        issues.add("sitemap", "error", "sitemap-unreachable", f"Sitemap {url} returned {r.status or r.error}", url)
        return out
    if len(r.body) > th["sitemap_max_bytes"]:
        issues.add("sitemap", "error", "sitemap-too-large", f"Sitemap {url} exceeds {th['sitemap_max_bytes']} bytes uncompressed", url)
    try:
        root = ET.fromstring(r.body)
    except ET.ParseError as e:
        issues.add("sitemap", "error", "sitemap-invalid-xml", f"Sitemap {url} is not valid XML: {e}", url)
        return out
    tag = root.tag.split("}")[-1].lower()
    def find_all(node, name):
        return [c for c in node.iter() if c.tag.split("}")[-1].lower() == name]
    if tag == "sitemapindex":
        for sm in find_all(root, "sitemap"):
            loc = next((c.text.strip() for c in sm if c.tag.split("}")[-1] == "loc" and c.text), None)
            if loc:
                out.update(parse_sitemap(loc, issues, th, depth + 1, seen))
    elif tag == "urlset":
        entries = find_all(root, "url")
        if len(entries) > th["sitemap_max_urls"]:
            issues.add("sitemap", "error", "sitemap-too-many-urls", f"{url} has {len(entries)} URLs (limit {th['sitemap_max_urls']}) — split with a sitemap index", url)
        bad_lastmod = 0
        for e in entries:
            loc = lastmod = None
            for c in e:
                n = c.tag.split("}")[-1]
                if n == "loc" and c.text:
                    loc = c.text.strip()
                elif n == "lastmod" and c.text:
                    lastmod = c.text.strip()
            if not loc:
                continue
            if lastmod and not parse_date(lastmod):
                bad_lastmod += 1
            out[loc] = lastmod
        if bad_lastmod:
            issues.add("sitemap", "warn", "sitemap-bad-lastmod", f"{bad_lastmod} <lastmod> values in {url} are not W3C datetime", url)
    elif tag in ("rss", "feed"):
        for link in find_all(root, "link"):
            href = link.get("href") or (link.text or "").strip()
            if href.startswith("http"):
                out[href] = None
    else:
        issues.add("sitemap", "error", "sitemap-unknown-format", f"{url}: root element <{tag}> is not urlset/sitemapindex/rss/feed", url)
    return out


# --------------------------------------------------------------------------- page checks

def check_og_image(img_url: str, th: dict) -> dict:
    r = fetch(img_url, range_bytes=65536, max_bytes=65536, timeout=15)
    info = {"url": img_url, "status": r.status, "error": r.error}
    if r.error or r.status not in (200, 206):
        return info
    info["content_type"] = r.headers.get("content-type", "")
    total = None
    cr = r.headers.get("content-range", "")
    if "/" in cr:
        try:
            total = int(cr.rsplit("/", 1)[1])
        except ValueError:
            total = None
    if total is None and r.headers.get("content-length") and r.status == 200:
        total = int(r.headers["content-length"])
    info["bytes"] = total
    w, h, fmt = image_size(r.body)
    info.update({"width": w, "height": h, "format": fmt})
    return info


def audit_page(url: str, root: str, th: dict, og_cache: dict) -> dict:
    r = fetch(url)
    page = {"url": url, "status": r.status, "error": r.error, "final_url": r.final_url or url,
            "redirects": [c for c, _ in r.redirects], "elapsed_ms": r.elapsed_ms, "issues": [], "links": []}
    iss = Issues()
    if r.error:
        iss.add("crawlability", "error", "fetch-failed", f"Could not fetch: {r.error}", url)
        page["issues"] = iss.items
        return page
    if r.redirects:
        page["redirect_target"] = r.final_url
        if same_site(r.final_url, root) and r.final_url.rstrip("/") != url.rstrip("/"):
            page["issues"] = iss.items  # the target is queued and audited as its own page
            return page
    ctype = r.headers.get("content-type", "")
    page["content_type"] = ctype
    if not r.ok:
        iss.add("crawlability", "error" if r.status >= 500 else "warn", f"http-{r.status}", f"HTTP {r.status}", url)
        page["issues"] = iss.items
        return page
    if "html" not in ctype.lower():
        page["issues"] = iss.items
        page["non_html"] = True
        return page

    html = r.text()
    p = parse_html(html)
    size = len(r.body)
    page["html_bytes"] = size
    if size > th["html_max_bytes"]:
        iss.add("crawlability", "warn", "html-too-large", f"HTML is {size // 1024} KB — Googlebot only reads the first {th['html_max_bytes'] // 1048576} MB", url)
    head_end = r.body.lower().find(b"</head>")
    if head_end > th["html_head_max_bytes"]:
        iss.add("preview-images", "warn", "head-too-deep", f"</head> ends at byte {head_end // 1024} KB — WhatsApp only reads the first {th['html_head_max_bytes'] // 1024} KB for link previews (move big inline scripts/styles out of <head>)", url)
    base = r.final_url or url
    # ---------- robots / indexability
    robots_meta = ",".join(filter(None, [p.meta("robots") or "", p.meta("googlebot") or ""])).lower()
    x_robots = r.headers.get("x-robots-tag", "").lower()
    noindex = bool(re.search(r"\b(noindex|none)\b", robots_meta + "," + x_robots))
    page["noindex"] = noindex
    page["robots_meta"] = robots_meta
    page["x_robots_tag"] = x_robots
    page["nosnippet"] = "nosnippet" in robots_meta or "nosnippet" in x_robots
    if page["nosnippet"]:
        iss.add("ai-readable", "warn", "nosnippet", "nosnippet set — page cannot be quoted in snippets or Google AI features", url)
    directives = robots_meta + "," + x_robots + "," + (p.meta("bingbot") or "").lower()
    if "noarchive" in directives:
        iss.add("ai-readable", "warn", "noarchive", "noarchive set — Bing excludes the page's content from Copilot answers", url)
    elif "nocache" in directives:
        iss.add("ai-readable", "info", "nocache", "nocache set — Bing/Copilot may only show URL, title and snippet", url)
    m = re.search(r"max-snippet:\s*(-?\d+)", robots_meta)
    if m and m.group(1) == "0":
        iss.add("ai-readable", "warn", "max-snippet-0", "max-snippet:0 blocks snippets and AI feature excerpts", url)
    if "max-image-preview:large" not in robots_meta and "max-image-preview" in robots_meta:
        iss.add("preview-images", "info", "image-preview-limited", "max-image-preview is not 'large' — large image previews (e.g. Discover) are limited", url)

    # ---------- canonical
    canon = p.link_rel("canonical")
    canonical = normalize_url(canon[0].get("href", ""), base) if canon else None
    page["canonical"] = canonical
    if len(canon) > 1:
        iss.add("crawlability", "warn", "multiple-canonicals", f"{len(canon)} canonical tags", url)
    if not canonical and not noindex:
        iss.add("crawlability", "warn", "no-canonical", "No rel=canonical", url)
    elif canonical and canonical.rstrip("/") != (base.split("#")[0]).rstrip("/") and not noindex:
        iss.add("crawlability", "info", "canonical-elsewhere", f"Canonical points elsewhere: {canonical}", url)

    # ---------- title / description
    title = " ".join((p.title or "").split())
    desc = " ".join((p.meta("description") or "").split())
    page["title"], page["title_len"] = title, len(title)
    page["description"], page["description_len"] = desc, len(desc)
    if not title:
        iss.add("titles", "error", "title-missing", "Missing <title>", url)
    else:
        if len(title) < th["title_min"]:
            iss.add("titles", "warn", "title-short", f"Title is {len(title)} chars (<{th['title_min']}): '{title}'", url)
        if len(title) > th["title_max"]:
            iss.add("titles", "warn", "title-long", f"Title is {len(title)} chars (>{th['title_max']}) — may be truncated", url)
        if title.lower() in th["generic_titles"]:
            iss.add("titles", "error", "title-generic", f"Generic placeholder title '{title}'", url)
    if not desc:
        iss.add("titles", "warn", "description-missing", "Missing meta description", url)
    elif len(desc) < th["description_min"] or len(desc) > th["description_max"]:
        iss.add("titles", "info", "description-length", f"Meta description is {len(desc)} chars (aim {th['description_min']}-{th['description_max']})", url)
    h1s = [t for lvl, t in p.headings if lvl == 1]
    page["h1"] = h1s
    if not h1s:
        iss.add("ai-readable", "warn", "h1-missing", "No <h1> in server HTML", url)
    elif len(h1s) > 1:
        iss.add("ai-readable", "info", "h1-multiple", f"{len(h1s)} <h1> elements", url)
    page["lang"] = p.lang
    if not p.lang:
        iss.add("crawlability", "info", "lang-missing", "<html lang> not set", url)
    if not p.meta("viewport"):
        iss.add("crawlability", "warn", "viewport-missing", "No meta viewport (mobile-friendliness)", url)

    # ---------- open graph / twitter
    og = {k: p.meta(f"og:{k}") for k in ("title", "description", "image", "url", "type", "site_name", "image:alt")}
    tw = {k: p.meta(f"twitter:{k}") for k in ("card", "title", "description", "image")}
    page["og"], page["twitter"] = og, tw
    if not og["title"]:
        iss.add("preview-images", "warn", "og-title-missing", "Missing og:title", url)
    if not og["description"]:
        iss.add("preview-images", "info", "og-description-missing", "Missing og:description", url)
    img = og["image"] or tw["image"]
    if not img:
        iss.add("preview-images", "error", "og-image-missing", "No og:image — shared links will have no preview image", url)
    else:
        img_abs = normalize_url(img, base)
        if not img.lower().startswith("http"):
            iss.add("preview-images", "warn", "og-image-relative", "og:image should be an absolute https URL", url)
        if img_abs:
            if img_abs not in og_cache:
                og_cache[img_abs] = check_og_image(img_abs, th)
            info = og_cache[img_abs]
            page["og_image"] = info
            if info.get("error") or info.get("status") not in (200, 206):
                iss.add("preview-images", "error", "og-image-broken", f"og:image not reachable ({info.get('status') or info.get('error')})", url)
            else:
                w, h = info.get("width"), info.get("height")
                if w and h:
                    if w < th["og_min_width"] or h < th["og_min_height"]:
                        iss.add("preview-images", "warn", "og-image-small", f"og:image is {w}x{h} (recommend ≥{th['og_min_width']}x{th['og_min_height']})", url)
                    ratio = w / h if h else 0
                    if not (th["og_ratio_min"] <= ratio <= th["og_ratio_max"]):
                        iss.add("preview-images", "info", "og-image-ratio", f"og:image aspect {ratio:.2f}:1 (≈1.91:1 recommended)", url)
                if info.get("bytes") and info["bytes"] > th["og_max_bytes"]:
                    iss.add("preview-images", "warn", "og-image-heavy", f"og:image is {info['bytes']//1024} KB (some apps, e.g. WhatsApp, drop large images; aim < {th['og_max_bytes']//1024} KB)", url)
    missing_og = [f"og:{k}" for k in ("url", "type", "site_name") if not og[k]]
    if img and not (p.meta("og:image:width") and p.meta("og:image:height")):
        missing_og.append("og:image:width/height")
    if missing_og:
        iss.add("preview-images", "info", "og-incomplete", f"Missing {', '.join(missing_og)} (first share may render without image/brand)", url)
    if img and not og["image:alt"]:
        iss.add("preview-images", "info", "og-image-alt-missing", "No og:image:alt (OG spec asks for it when og:image is set)", url)
    if not tw["card"]:
        iss.add("preview-images", "info", "twitter-card-missing", "No twitter:card (X falls back to OG, but summary_large_image is recommended)", url)

    # ---------- structured data
    objs, errs = p.jsonld()
    types = jsonld_types(objs)
    page["jsonld_types"] = types
    for e in errs:
        iss.add("structured-data", "error", "jsonld-invalid", f"Invalid JSON-LD: {e}", url)
    retired = sorted(set(types) & set(th.get("retired_rich_result_types", [])))
    if retired:
        iss.add("structured-data", "info", "retired-rich-result", f"{', '.join(retired)} markup no longer produces Google rich results (harmless; keep only if the content genuinely is that)", url)
    dp = dm = None
    for o in objs:
        dp = dp or o.get("datePublished")
        dm = dm or o.get("dateModified")
    dp = dp or p.meta("article:published_time")
    dm = dm or p.meta("article:modified_time")
    page["date_published"], page["date_modified"] = dp, dm
    article_like = any(t in types for t in ("Article", "BlogPosting", "NewsArticle", "TechArticle"))
    if article_like and not dp:
        iss.add("structured-data", "warn", "article-no-date", "Article markup without datePublished", url)
    if article_like and not any(o.get("author") for o in objs):
        iss.add("structured-data", "warn", "article-no-author", "Article markup without author", url)
    org = next((o for o in objs if any(t in str(o.get("@type")) for t in ("Organization", "LocalBusiness", "Corporation", "Store", "Clinic"))), None)
    if org and org.get("logo"):
        lg = org["logo"]
        page["org_logo"] = lg.get("url") if isinstance(lg, dict) else (lg if isinstance(lg, str) else None)
    page["same_as"] = sorted({s for o in objs for s in (o.get("sameAs") if isinstance(o.get("sameAs"), list) else [o.get("sameAs")] if o.get("sameAs") else [])})

    # ---------- AI readability (server HTML content)
    text = p.visible_text()
    words = len(text.split())
    page["word_count"] = words
    page["content_hash"] = sha(title + "|" + desc + "|" + text)
    likely_csr = words < th["csr_min_words"] and (bool(p.root_containers) or p.scripts >= 3 or p.has_noscript_warning)
    page["likely_client_rendered"] = likely_csr
    if likely_csr:
        iss.add("ai-readable", "error", "client-rendered",
                f"Only {words} words in server HTML (app shell {p.root_containers or ''}) — AI crawlers that don't run JavaScript see an empty page. Use SSR/SSG/prerendering.", url)
    elif words < th["thin_words"] and not noindex:
        iss.add("ai-readable", "info", "thin-content", f"Only {words} words of text in HTML", url)
    page["images"], page["images_no_alt"] = p.images, p.images_no_alt
    if p.images_no_alt:
        iss.add("ai-readable", "info", "img-alt-missing", f"{p.images_no_alt}/{p.images} <img> without alt", url)

    # ---------- links
    internal = []
    for href in p.anchors:
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        u = normalize_url(href, base)
        if u and same_site(u, root):
            internal.append(u)
    page["links"] = sorted(set(internal))
    page["internal_links"] = len(page["links"])
    if noindex:  # not shown in search — presentation checks don't apply
        iss.items = [i for i in iss.items if i["item"] == "crawlability" and i["code"] not in ("no-canonical", "viewport-missing", "lang-missing")]
    page["issues"] = iss.items
    icons = p.link_rel("icon") + p.link_rel("shortcut") + p.link_rel("apple-touch-icon")
    page["favicon"] = bool(icons)
    page["favicon_svg_only"] = bool(icons) and all(i.get("href", "").lower().split("?")[0].endswith(".svg") or "svg" in i.get("type", "") for i in icons)
    page["verification_meta"] = {k: bool(p.meta(k)) for k in ("google-site-verification", "msvalidate.01", "yandex-verification")}
    return page


# --------------------------------------------------------------------------- site checks

def site_checks(root: str, th: dict, state_dir: Path | None, config: dict, iss: Issues, probe_bots: bool = False) -> dict:
    out = {}
    # robots.txt
    rr = fetch(urllib.parse.urljoin(root, "/robots.txt"))
    robots_info = {"status": rr.status, "error": rr.error, "bytes": len(rr.body)}
    text = rr.text() if rr.ok else ""
    if rr.error:
        iss.add("crawlability", "error", "robots-unreachable", f"robots.txt fetch failed: {rr.error}")
    elif rr.status >= 500:
        iss.add("crawlability", "error", "robots-5xx", f"robots.txt returns {rr.status} — crawlers treat the whole site as disallowed")
    elif rr.status >= 400:
        iss.add("crawlability", "info", "robots-missing", "No robots.txt (crawlers allow everything; add one to declare your sitemap and AI-bot policy)")
    elif "html" in rr.headers.get("content-type", "").lower():
        iss.add("crawlability", "warn", "robots-html", "robots.txt is served as HTML (probably an SPA fallback page)")
        text = ""
    if len(rr.body) > th["robots_max_bytes"]:
        iss.add("crawlability", "warn", "robots-too-big", f"robots.txt larger than {th['robots_max_bytes']} bytes; content past the limit is ignored")
    robots = Robots(text)
    robots_info["sitemaps"] = robots.sitemaps
    robots_info["content_signal"] = [l.strip() for l in text.splitlines() if l.strip().lower().startswith("content-signal")]
    for line in robots_info["content_signal"]:
        prefs = dict(kv.strip().lower().split("=", 1) for kv in line.split(":", 1)[1].split(",") if "=" in kv)
        for key in ("search", "ai-input"):
            if prefs.get(key) == "no":
                iss.add("ai-bots-allowed", "warn", f"content-signal-{key}-no",
                        f"robots.txt Content-Signal says {key}=no — tells AI/search systems not to use the site for {'search results' if key == 'search' else 'AI answers (grounding)'}. Intended?")
    if robots_info["status"] == 200 and robots.allowed("Googlebot", "/")[0] is False:
        iss.add("crawlability", "error", "robots-blocks-all", "robots.txt blocks Googlebot from / — site cannot be indexed")
    if rr.ok and not robots.sitemaps:
        iss.add("sitemap", "warn", "robots-no-sitemap", "robots.txt has no 'Sitemap:' line")
    out["robots"] = robots_info

    # AI + search bot matrix
    reg = load_reference("ai-bots.json", state_dir)
    sample_paths = ["/"] + [p for p in (config.get("sample_paths") or []) if p != "/"][:4]
    rows = bot_matrix(robots, reg.get("bots", []), sample_paths)
    out["bots"] = rows
    policy = (config.get("ai_training_policy") or "allow").lower()
    for r in rows:
        if r["category"] != "training":
            continue
        if policy == "allow" and not r["all_allowed"]:
            iss.add("ai-bots-allowed", "warn", "training-policy-mismatch", f"{r['token']} is blocked but the recorded AI-training policy is 'allow' — update robots.txt or the policy")
        elif policy == "block" and r["all_allowed"] and r["token"] != "__usage_ai-training":
            iss.add("ai-bots-allowed", "warn", "training-policy-mismatch", f"{r['token']} is allowed but the recorded AI-training policy is 'block'")
    for i in verdict(rows):
        item = "preview-images" if i["code"] == "preview-bot-blocked" else ("ai-bots-allowed" if i["token"] not in ("Googlebot", "Bingbot") else "crawlability")
        iss.add(item, i["severity"], i["code"], i["message"])

    # edge/WAF hint: does the homepage answer differently for AI search bot user-agents?
    probes = {}
    base_status = fetch(root).status if probe_bots else None
    for token, ua in ((reg.get("probe_user_agents") or {}).items() if probe_bots else []):
        s = fetch(root, user_agent=ua, timeout=15).status
        probes[token] = s
        if base_status == 200 and s in (401, 403, 429, 503):
            iss.add("ai-bots-allowed", "warn", "edge-block-suspected",
                    f"Homepage returns {s} for {token}'s user-agent but 200 for a browser — a CDN/WAF bot rule (e.g. Cloudflare AI-crawler blocking) may block it even though robots.txt allows it. Verify in your CDN dashboard (spoofed-UA probes can be false positives).")
    if probe_bots:
        out["ua_probes"] = {"baseline": base_status, **probes}

    # llms.txt (optional, informational)
    lr = fetch(urllib.parse.urljoin(root, "/llms.txt"))
    llms = {"status": lr.status}
    if lr.ok and "html" not in lr.headers.get("content-type", "").lower():
        first = next((l for l in lr.text().splitlines() if l.strip()), "")
        llms["h1"] = first.startswith("# ")
        if not llms["h1"]:
            iss.add("ai-readable", "info", "llms-format", "llms.txt should start with '# <Site name>' (llmstxt.org format)")
    else:
        iss.add("ai-readable", "info", "llms-missing", "No /llms.txt (optional; low cost, not used by Google — see references/ai-visibility.md)")
    out["llms_txt"] = llms

    # IndexNow key file
    key = (config.get("indexnow") or {}).get("key")
    if key:
        loc = (config.get("indexnow") or {}).get("key_location") or urllib.parse.urljoin(root, f"/{key}.txt")
        kr = fetch(loc)
        ok = kr.ok and kr.text().strip() == key
        out["indexnow_key"] = {"location": loc, "ok": ok, "status": kr.status}
        if not ok:
            iss.add("indexnow", "error", "indexnow-key-missing", f"IndexNow key file {loc} not found or content mismatch")

    # soft 404
    probe = urllib.parse.urljoin(root, f"/get-found-404-probe-{uuid.uuid4().hex[:8]}")
    sr = fetch(probe)
    out["soft404"] = {"status": sr.status}
    if sr.status == 200:
        iss.add("crawlability", "warn", "soft-404", "Unknown URLs return 200 instead of 404 (soft 404s waste crawl budget and confuse indexing)")

    # https + host consistency
    p = urllib.parse.urlsplit(root)
    if p.scheme == "https" and p.hostname not in ("localhost", "127.0.0.1"):
        hr = fetch(f"http://{p.netloc}/", follow_redirects=False)
        loc = hr.headers.get("location", "")
        out["http_redirect"] = {"status": hr.status, "location": loc}
        if hr.status not in (301, 308) or not loc.startswith("https://"):
            iss.add("crawlability", "warn", "http-not-redirected", f"http:// homepage answers {hr.status or hr.error} instead of a permanent redirect to https")
        alt = p.netloc[4:] if p.netloc.startswith("www.") else "www." + p.netloc
        ar = fetch(f"https://{alt}/", follow_redirects=False)
        if ar.status == 200:
            iss.add("crawlability", "warn", "www-duplicate", f"Both {p.netloc} and {alt} serve 200 — redirect one to the other")
    return out


# --------------------------------------------------------------------------- crawl

def crawl(root: str, seeds: list[str], max_pages: int, workers: int, th: dict, only: bool) -> tuple[dict, bool]:
    pages: dict[str, dict] = {}
    og_cache: dict = {}
    queue = list(dict.fromkeys(seeds))
    queued = set(queue)
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        while queue and len(pages) < max_pages:
            batch = []
            while queue and len(batch) + len(pages) < max_pages and len(batch) < workers * 2:
                batch.append(queue.pop(0))
            for res in pool.map(lambda u: audit_page(u, root, th, og_cache), batch):
                pages[res["url"]] = res
                target = res.get("redirect_target")
                if target and same_site(target, root) and target not in queued and not only:
                    queued.add(target)
                    queue.append(target)
                if only:
                    continue
                for link in res.get("links", []):
                    if link not in queued and not SKIP_EXT.search(urllib.parse.urlsplit(link).path) and "?" not in link:
                        queued.add(link)
                        queue.append(link)
    return pages, bool(queue)


def summarize(pages: dict, site: dict, issues: Issues, sitemap_urls: dict, th: dict) -> None:
    """Cross-page checks: duplicates, sitemap coverage."""
    html_pages = {u: p for u, p in pages.items() if p.get("status") == 200 and not p.get("non_html") and not p.get("redirect_target")}
    indexable = {u: p for u, p in html_pages.items() if not p.get("noindex")}
    for field, item in (("title", "titles"), ("description", "titles")):
        groups = defaultdict(list)
        for u, p in indexable.items():
            if p.get(field):
                groups[p[field].lower()].append(u)
        for val, urls in groups.items():
            if len(urls) > 1:
                issues.add(item, "warn", f"duplicate-{field}", f"{len(urls)} pages share the same {field}: '{val[:70]}' ({', '.join(path_of(x) for x in urls[:5])})")
    if sitemap_urls:
        sm = set(u.rstrip("/") for u in sitemap_urls)
        for u, p in indexable.items():
            canon = (p.get("canonical") or u).rstrip("/")
            if u.rstrip("/") not in sm and canon == u.rstrip("/"):
                issues.add("sitemap", "warn", "missing-from-sitemap", f"Indexable page not in sitemap: {path_of(u)}", u)
        for u, p in pages.items():
            if u.rstrip("/") in sm:
                if p.get("noindex"):
                    issues.add("sitemap", "error", "sitemap-noindex", f"Sitemap lists a noindex page: {path_of(u)}", u)
                elif p.get("status") and p.get("status") != 200:
                    issues.add("sitemap", "error", "sitemap-non-200", f"Sitemap lists a URL returning {p['status']}: {path_of(u)}", u)
                elif p.get("redirect_target"):
                    issues.add("sitemap", "warn", "sitemap-redirect", f"Sitemap lists a redirecting URL: {path_of(u)}", u)
                elif p.get("canonical") and p["canonical"].rstrip("/") != u.rstrip("/"):
                    issues.add("sitemap", "warn", "sitemap-non-canonical", f"Sitemap lists a non-canonical URL: {path_of(u)}", u)
        lastmods = [v for v in sitemap_urls.values() if v]
        if len(sitemap_urls) > 5 and len(set(lastmods)) == 1:
            issues.add("sitemap", "warn", "sitemap-uniform-lastmod", "Every <lastmod> is identical — looks like build time, not real modification dates; search engines ignore untrustworthy lastmod")
        if not lastmods and sitemap_urls:
            issues.add("sitemap", "info", "sitemap-no-lastmod", "Sitemap has no <lastmod>; add accurate dates so engines re-crawl changed pages")
    home = next((p for u, p in html_pages.items() if urllib.parse.urlsplit(u).path in ("", "/")), None)
    if home:
        types = home.get("jsonld_types", [])
        if "Organization" not in types and not any(t in types for t in ("LocalBusiness", "Person", "Corporation", "MedicalOrganization", "Store")) and not any(t.endswith("Business") for t in types):
            issues.add("structured-data", "warn", "org-missing", "Homepage has no Organization/LocalBusiness/Person JSON-LD (brand entity, logo, sameAs)")
        if "WebSite" not in types:
            issues.add("structured-data", "info", "website-missing", "Homepage has no WebSite JSON-LD (helps Google show your site name)")
        if not home.get("same_as"):
            issues.add("social-profiles", "warn", "sameas-missing", "No sameAs links to your social/directory profiles in homepage JSON-LD")
        if not home.get("favicon"):
            issues.add("titles", "warn", "favicon-missing", "No <link rel=icon> on homepage (favicon shows next to your result)")
        elif home.get("favicon_svg_only"):
            issues.add("titles", "warn", "favicon-svg-only", "Only an SVG favicon — Google Search lists PNG/ICO/GIF/JPEG etc. but not SVG; add a PNG (≥48x48) or .ico")
        logo = home.get("org_logo")
        if logo:
            info = check_og_image(normalize_url(logo, home["url"]) or logo, th)
            w, h = info.get("width"), info.get("height")
            if info.get("status") not in (200, 206):
                issues.add("structured-data", "warn", "logo-unreachable", f"Organization logo {logo} not reachable ({info.get('status') or info.get('error')})")
            elif w and h and min(w, h) < 112:
                issues.add("structured-data", "warn", "logo-too-small", f"Organization logo is {w}x{h}; Google needs at least 112x112")
        site["home_same_as"] = home.get("same_as", [])
        site["verification_meta"] = home.get("verification_meta", {})


ITEM_ORDER = ["search-console", "bing-webmaster", "indexnow", "sitemap", "crawlability", "titles", "preview-images",
              "structured-data", "ai-readable", "ai-bots-allowed", "directories", "social-profiles", "keywords", "blog",
              "measurement", "rules-fresh"]
AUDITABLE = {"indexnow", "sitemap", "crawlability", "titles", "preview-images", "structured-data", "ai-readable", "ai-bots-allowed", "social-profiles"}


def rollup(all_issues: list, config: dict) -> dict:
    items = {}
    for item in ITEM_ORDER:
        if item not in AUDITABLE:
            continue
        if item == "indexnow" and not (config.get("indexnow") or {}).get("key"):
            continue
        its = [i for i in all_issues if i["item"] == item]
        sev = Counter(i["severity"] for i in its)
        status = "fail" if sev["error"] else ("warn" if sev["warn"] else "pass")
        items[item] = {"status": status, "errors": sev["error"], "warnings": sev["warn"], "info": sev["info"]}
    return items


def render_md(report: dict) -> str:
    L = [f"# get-found audit — {report['site']}", "", f"Generated {report['generated']} · {report['summary']['pages_crawled']} pages crawled · "
         f"{report['summary']['sitemap_urls']} sitemap URLs", "", "| Item | Status | Errors | Warnings |", "|---|---|---|---|"]
    for k, v in report["items"].items():
        L.append(f"| {k} | {v['status'].upper()} | {v['errors']} | {v['warnings']} |")
    L += ["", "## Issues", ""]
    order = {"error": 0, "warn": 1, "info": 2}
    grouped = defaultdict(list)
    for i in report["issues"]:
        grouped[(i["item"], i["severity"], i["code"])].append(i)
    for (item, sev, code), lst in sorted(grouped.items(), key=lambda kv: (order[kv[0][1]], kv[0][0])):
        L.append(f"- **[{sev}] {item} / {code}** ×{len(lst)} — {lst[0]['message']}")
        urls = [path_of(i["url"]) for i in lst if i.get("url")]
        if urls:
            L.append(f"  - {'page' if len(urls) == 1 else 'pages'}: {', '.join(urls[:12])}{' …' if len(urls) > 12 else ''}")
    L += ["", "## AI & search bot access", "", "| Bot | Vendor | Category | Access |", "|---|---|---|---|"]
    for r in report["site_checks"]["bots"]:
        acc = "allowed" if r["all_allowed"] else ("BLOCKED" if r["all_blocked"] else "partial")
        L.append(f"| {r['token']} | {r['vendor']} | {r['category']} | {acc} |")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("site", nargs="?", help="site origin (default: site_url from .get-found/config.json)")
    ap.add_argument("--state", help="project state dir, e.g. .get-found (reads config, writes reports/)")
    ap.add_argument("--max-pages", type=int, default=150)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--sitemap", action="append", help="sitemap URL(s); default from robots.txt or /sitemap.xml")
    ap.add_argument("--urls", nargs="*", help="audit only these URLs/paths (no crawling)")
    ap.add_argument("--out", help="output path prefix (default <state>/reports/audit-<ts>)")
    ap.add_argument("--probe-bots", action="store_true", help="also request the homepage with AI-bot user-agents to spot CDN/WAF blocks (hints only)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    state_dir = Path(args.state) if args.state else None
    config = {}
    if state_dir:
        from _common import load_json
        config = load_json(state_dir / "config.json", default={}) or {}
    site = args.site or config.get("site_url")
    if not site:
        die("no site URL given and no site_url in config")
    root = normalize_url(site)
    if not root:
        die(f"invalid URL: {site}")
    root = urllib.parse.urlunsplit(urllib.parse.urlsplit(root)._replace(path="/", query=""))
    th = load_reference("thresholds.json", state_dir)

    t0 = time.time()
    iss = Issues()
    home = fetch(root)
    if home.error:
        die(f"cannot reach {root}: {home.error}")
    if home.final_url and home.final_url != root and same_site(home.final_url, root):
        root = urllib.parse.urlunsplit(urllib.parse.urlsplit(home.final_url)._replace(path="/", query=""))

    site_info = site_checks(root, th, state_dir, config, iss, args.probe_bots)
    sitemap_list = args.sitemap or config.get("sitemaps") or site_info["robots"].get("sitemaps") or [urllib.parse.urljoin(root, "/sitemap.xml")]
    sitemap_urls: dict = {}
    for sm in sitemap_list:
        sitemap_urls.update(parse_sitemap(sm, iss, th))
    if not sitemap_urls:
        iss.add("sitemap", "error", "sitemap-missing", f"No sitemap URLs found (tried {', '.join(sitemap_list)})")
    site_info["sitemap"] = {"files": sitemap_list, "url_count": len(sitemap_urls)}
    off_host = [u for u in sitemap_urls if not same_site(u, root)]
    if off_host:
        iss.add("sitemap", "warn", "sitemap-other-host", f"{len(off_host)} sitemap URLs are on another host (e.g. {off_host[0]}) — staging/localhost leak?")

    if args.urls:
        seeds = [normalize_url(u, root) for u in args.urls]
        seeds = [s for s in seeds if s]
        only = True
    else:
        known = []
        if state_dir:
            from _common import load_json
            inv = (load_json(state_dir / "pages.json", default={}) or {}).get("pages", {})
            known = [u for u, rec in inv.items() if not rec.get("removed") and same_site(u, root)]
        seeds = [root] + [u for u in sitemap_urls if same_site(u, root)] + known  # known: re-check pages we tracked before
        only = False
    pages, truncated = crawl(root, seeds, args.max_pages if not args.urls else len(seeds), args.workers, th, only)
    for p in pages.values():
        iss.items.extend(p.get("issues", []))
    summarize(pages, site_info, iss, {u: v for u, v in sitemap_urls.items()} if not args.urls else {}, th)

    report = {
        "tool": "get-found-audit", "version": VERSION, "site": root, "generated": now_iso(),
        "partial": bool(args.urls),
        "truncated": truncated,
        "summary": {"pages_crawled": len(pages), "sitemap_urls": len(sitemap_urls), "seconds": round(time.time() - t0, 1),
                    "errors": sum(1 for i in iss.items if i["severity"] == "error"),
                    "warnings": sum(1 for i in iss.items if i["severity"] == "warn")},
        "items": rollup(iss.items, config),
        "site_checks": site_info,
        "sitemap_lastmod": sitemap_urls,
        "issues": iss.items,
        "pages": [{k: v for k, v in p.items() if k not in ("links", "issues")} for p in pages.values()],
    }
    ts = time.strftime("%Y%m%d-%H%M%S")
    prefix = Path(args.out) if args.out else ((state_dir / "reports" / f"audit-{ts}") if state_dir else Path("get-found-audit"))
    save_json(prefix.with_suffix(".json"), report)
    prefix.with_suffix(".md").write_text(render_md(report), encoding="utf-8")
    if not args.quiet:
        s = report["summary"]
        print(f"get-found audit: {root} — {s['pages_crawled']} pages, {s['sitemap_urls']} sitemap URLs, {s['errors']} errors, {s['warnings']} warnings ({s['seconds']}s)")
        for k, v in report["items"].items():
            print(f"  {v['status'].upper():<5} {k:<16} {v['errors']} errors, {v['warnings']} warnings")
        if truncated:
            print(f"  note: stopped at --max-pages {args.max_pages}; raise it for a full inventory (removed-page detection is skipped)")
        print(f"report: {prefix.with_suffix('.json')}  |  {prefix.with_suffix('.md')}")
    sys.exit(1 if report["summary"]["errors"] else 0)


if __name__ == "__main__":
    main()
