"""Tests for get-found scripts. Run: python3 -m unittest discover -s tests -v
Serves tests/fixtures/site on a local port (no internet needed)."""
import functools
import http.server
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "get-found" / "scripts"
TEMPLATES = ROOT / "skills" / "get-found" / "assets" / "templates"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "site"
sys.path.insert(0, str(SCRIPTS))

from _common import image_size, parse_html  # noqa: E402
from robots import Robots, bot_matrix, verdict  # noqa: E402


def run(*args, cwd, env=None):
    import os
    full = {k: v for k, v in os.environ.items() if k not in ("GOOGLE_APPLICATION_CREDENTIALS", "GSC_ACCESS_TOKEN")}
    return subprocess.run([sys.executable, *map(str, args)], cwd=cwd, capture_output=True, text=True, timeout=120,
                          env={**full, **(env or {})})


class Site:
    """Copy the fixture, point its absolute URLs at a free local port, serve it."""

    def __init__(self, mutate=None, status=None):
        self.dir = Path(tempfile.mkdtemp(prefix="gf-site-"))
        shutil.copytree(FIXTURE, self.dir, dirs_exist_ok=True)
        forced = status or {}  # path -> HTTP status to return instead of the file

        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path in forced:
                    self.send_error(forced[self.path])
                    return
                super().do_GET()
        handler = functools.partial(Quiet, directory=str(self.dir))
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.rewrite()
        if mutate:
            mutate(self)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def rewrite(self):
        for f in self.dir.rglob("*"):
            if f.suffix in (".html", ".xml", ".txt"):
                f.write_text(f.read_text(encoding="utf-8").replace("http://127.0.0.1:8765", self.base), encoding="utf-8")

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)


class RobotsTests(unittest.TestCase):
    def test_longest_match_and_allow_tie(self):
        r = Robots("User-agent: *\nDisallow: /private\nAllow: /private/ok\nDisallow: /x$\n")
        self.assertFalse(r.allowed("anybot", "/private/secret")[0])
        self.assertTrue(r.allowed("anybot", "/private/ok/page")[0])
        self.assertFalse(r.allowed("anybot", "/x")[0])
        self.assertTrue(r.allowed("anybot", "/x/y")[0])
        tie = Robots("User-agent: *\nDisallow: /a\nAllow: /a\n")
        self.assertTrue(tie.allowed("bot", "/a")[0])

    def test_specific_group_overrides_star_and_groups_merge(self):
        r = Robots("User-agent: *\nDisallow: /\n\nUser-agent: OAI-SearchBot\nUser-agent: PerplexityBot\nAllow: /\n\nUser-agent: oai-searchbot\nDisallow: /tmp/\n")
        self.assertTrue(r.allowed("OAI-SearchBot", "/blog/")[0])
        self.assertFalse(r.allowed("OAI-SearchBot", "/tmp/a")[0])
        self.assertTrue(r.allowed("PerplexityBot", "/")[0])
        self.assertFalse(r.allowed("GPTBot", "/")[0])
        self.assertTrue(r.allowed("GPTBot", "/robots.txt")[0])

    def test_wildcards(self):
        r = Robots("User-agent: *\nDisallow: /*.pdf$\nDisallow: /*?session=\n")
        self.assertFalse(r.allowed("b", "/docs/file.pdf")[0])
        self.assertTrue(r.allowed("b", "/docs/file.pdf.html")[0])
        self.assertFalse(r.allowed("b", "/page?session=1")[0])

    def test_templates_keep_search_and_previews_open(self):
        reg = json.loads((ROOT / "skills/get-found/references/ai-bots.json").read_text())
        for name, training_blocked in (("robots-allow-all.txt", False), ("robots-block-training.txt", True)):
            rows = bot_matrix(Robots((TEMPLATES / name).read_text()), reg["bots"], ["/", "/blog/post"])
            for row in rows:
                if row["category"] == "training":
                    self.assertEqual(row["all_blocked"], training_blocked, f"{name}: {row['token']}")
                else:
                    self.assertTrue(row["all_allowed"], f"{name} blocks {row['token']}")
            self.assertFalse([i for i in verdict(rows) if i["severity"] == "error"], name)


class ImageTests(unittest.TestCase):
    def test_png_size(self):
        w, h, fmt = image_size((FIXTURE / "og.png").read_bytes()[:64])
        self.assertEqual((w, h, fmt), (1200, 630, "png"))


class AuditAndStateTests(unittest.TestCase):
    def setUp(self):
        self.proj = Path(tempfile.mkdtemp(prefix="gf-proj-"))

    def tearDown(self):
        shutil.rmtree(self.proj, ignore_errors=True)

    def audit(self, site):
        res = run(SCRIPTS / "audit.py", "--state", ".get-found", "--quiet", cwd=self.proj)
        self.assertIn(res.returncode, (0, 1), res.stderr)
        report = sorted((self.proj / ".get-found" / "reports").glob("audit-*.json"))[-1]
        return report, json.loads(report.read_text())

    def test_full_lifecycle(self):
        site = Site()
        try:
            r = run(SCRIPTS / "state.py", "init", "--site", site.base, "--type", "saas", "local", "--country", "IN",
                    "--vertical", "healthcare", "--brand", "Acme Health", cwd=self.proj)
            self.assertEqual(r.returncode, 0, r.stderr)
            listings = json.loads((self.proj / ".get-found/listings.json").read_text())["listings"]
            ids = {l["id"] for l in listings}
            self.assertIn("practo", ids)
            self.assertIn("justdial", ids)
            self.assertNotIn("99acres", ids)      # real-estate vertical not selected
            self.assertNotIn("yelp", ids)         # not an IN directory

            path, rep = self.audit(site)
            codes = {(i["item"], i["code"]) for i in rep["issues"]}
            for expected in [("ai-readable", "client-rendered"), ("sitemap", "sitemap-noindex"), ("sitemap", "sitemap-non-200"),
                             ("ai-bots-allowed", "bot-blocked"), ("titles", "title-generic"), ("preview-images", "og-image-missing")]:
                self.assertIn(expected, codes)
            blocked = [i for i in rep["issues"] if i["code"] == "bot-blocked"]
            self.assertTrue(all("PerplexityBot" in i["message"] for i in blocked))
            noindex_issues = [i for i in rep["issues"] if (i.get("url") or "").endswith("/hidden.html") and i["item"] != "sitemap"]
            self.assertTrue(all(i["item"] == "preview-images" and i["severity"] == "info" for i in noindex_issues),
                            "noindex pages get no search-presentation issues; link-preview ones only as info")
            self.assertIn("og-image-missing", {i["code"] for i in noindex_issues})
            self.assertEqual(rep["items"]["structured-data"]["status"], "pass")

            r = run(SCRIPTS / "state.py", "ingest", path, cwd=self.proj)
            self.assertIn("5 new", r.stdout)
            ledger = json.loads((self.proj / ".get-found/ledger.json").read_text())["items"]
            self.assertEqual(ledger["ai-readable"]["status"], "fail")
            pend = json.loads(run(SCRIPTS / "state.py", "changes", "--json", cwd=self.proj).stdout)
            self.assertFalse(any(p["url"].endswith("/hidden.html") for p in pend), "noindex page should not be queued")
            run(SCRIPTS / "state.py", "submitted", "--channel", "indexnow", "--all-pending", cwd=self.proj)
            self.assertEqual(json.loads(run(SCRIPTS / "state.py", "changes", "--json", cwd=self.proj).stdout), [])
        finally:
            site.close()

        def mutate(s):
            post = s.dir / "blog" / "first-post.html"
            post.write_text(post.read_text().replace("seven tactics", "eight tactics"))
            (s.dir / "about.html").unlink()
            new = post.read_text().replace("first-post", "second-post").replace("2026-10-20", "2026-11-01")
            (s.dir / "blog" / "second-post.html").write_text(new)
            sm = s.dir / "sitemap.xml"
            sm.write_text(sm.read_text().replace("</urlset>", f"<url><loc>{s.base}/blog/second-post.html</loc></url></urlset>"))

        # Same origin is needed for change detection, so rebind the new site to the old config URL.
        site2 = Site(mutate=mutate)
        try:
            cfg_path = self.proj / ".get-found/config.json"
            cfg = json.loads(cfg_path.read_text())
            old_base = cfg["site_url"].rstrip("/")
            pages_path = self.proj / ".get-found/pages.json"
            pages_path.write_text(pages_path.read_text().replace(old_base, site2.base))
            cfg["site_url"] = site2.base + "/"
            cfg_path.write_text(json.dumps(cfg))
            path, rep = self.audit(site2)
            out = run(SCRIPTS / "state.py", "ingest", path, cwd=self.proj).stdout
            self.assertIn("1 new", out)
            self.assertIn("1 changed", out)
            self.assertIn("1 removed", out)
            pend = {p["url"].rsplit("/", 1)[-1]: p["kind"] for p in json.loads(run(SCRIPTS / "state.py", "changes", "--json", cwd=self.proj).stdout)}
            self.assertEqual(pend, {"about.html": "removed", "first-post.html": "changed", "second-post.html": "new"})
            ledger = json.loads((self.proj / ".get-found/ledger.json").read_text())["items"]
            self.assertIn("2026-11-01", ledger["blog"]["evidence"])
        finally:
            site2.close()

    def test_init_flags_and_manual_status(self):
        r = run(SCRIPTS / "state.py", "init", "--site", "https://www.example.com", "--type", "saas,local", "--training", "block",
                "--social", "linkedin=https://www.linkedin.com/company/x", "--author", "Dr. Meera Rao", cwd=self.proj)
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads((self.proj / ".get-found/config.json").read_text())
        self.assertEqual(cfg["google"]["property"], "sc-domain:example.com")
        self.assertEqual(cfg["site_types"], ["saas", "local"])
        self.assertEqual(cfg["ai_training_policy"], "block")
        self.assertEqual(cfg["social_profiles"]["linkedin"], "https://www.linkedin.com/company/x")
        self.assertEqual(cfg["authors"], ["Dr. Meera Rao"])
        bad = run(SCRIPTS / "state.py", "--state", "other", "init", "--site", "x.com", "--type", "blog", cwd=self.proj)
        self.assertNotEqual(bad.returncode, 0)

        site = Site()
        try:
            cfg["site_url"] = site.base + "/"
            cfg["ai_training_policy"] = "allow"
            (self.proj / ".get-found/config.json").write_text(json.dumps(cfg))
            run(SCRIPTS / "state.py", "mark", "social-profiles", "--status", "manual", "--evidence", "waiting for Instagram", cwd=self.proj)
            path, rep = self.audit(site)
            self.assertIn(("ai-bots-allowed", "training-policy-mismatch"), {(i["item"], i["code"]) for i in rep["issues"]})
            run(SCRIPTS / "state.py", "ingest", path, cwd=self.proj)
            led = json.loads((self.proj / ".get-found/ledger.json").read_text())["items"]
            self.assertEqual(led["social-profiles"]["status"], "manual", "audit must not close an open manual step")
            # second audit: nothing removed just because a page wasn't linked
            path, _ = self.audit(site)
            out = run(SCRIPTS / "state.py", "ingest", path, cwd=self.proj).stdout
            self.assertIn("0 removed", out)
        finally:
            site.close()

    def test_root_without_slash_is_one_page(self):
        def mutate(s):
            sm = s.dir / "sitemap.xml"
            sm.write_text(sm.read_text().replace(f"<loc>{s.base}/</loc>", f"<loc>{s.base}</loc>"))
            about = s.dir / "about.html"
            about.write_text(about.read_text().replace('<a href="/">Home</a>', '<a href="/">Home</a> <a href="/missing.html">Old link</a>'))
        site = Site(mutate=mutate)
        try:
            run(SCRIPTS / "state.py", "init", "--site", site.base, "--type", "saas", cwd=self.proj)
            _, rep = self.audit(site)
            self.assertNotIn("duplicate-title", {i["code"] for i in rep["issues"]})
            roots = [p["url"] for p in rep["pages"] if p["url"].rstrip("/") == site.base]
            self.assertEqual(len(roots), 1, roots)
            missing = [i for i in rep["issues"] if i["code"] == "http-404" and (i.get("url") or "").endswith("/missing.html")]
            self.assertEqual(len(missing), 1)
            self.assertIn("linked from /about.html", missing[0]["message"])
        finally:
            site.close()

    def test_keyword_check(self):
        site = Site()
        try:
            run(SCRIPTS / "state.py", "init", "--site", site.base, "--type", "saas", cwd=self.proj)
            path, _ = self.audit(site)
            run(SCRIPTS / "state.py", "ingest", path, cwd=self.proj)
            kw = {"pages": {"/": {"primary": "clinic booking software"}, "/about.html": {"primary": "clinic booking software"}}, "backlog": []}
            (self.proj / ".get-found/keywords.json").write_text(json.dumps(kw))
            out = run(SCRIPTS / "state.py", "keywords", "--check", cwd=self.proj).stdout
            self.assertIn("cannibalization", out)
            self.assertIn("no primary keyword: /blog/first-post.html", out)
            self.assertIn("keyword not reflected in title: /about.html", out)
        finally:
            site.close()


class ParserEdgeTests(unittest.TestCase):
    def test_robots_bom_is_ignored(self):
        r = Robots("﻿User-agent: *\nDisallow: /private/\n")
        self.assertFalse(r.allowed("Googlebot", "/private/x")[0], "BOM must not hide the first User-agent line")

    def test_head_without_closing_tag(self):
        p = parse_html('<html><head><title>Real title</title><meta name="description" content="d">'
                       '<link rel="canonical" href="https://x.test/"><body><h1>Hi</h1><p>text</p></body></html>')
        self.assertEqual(p.title, "Real title")
        self.assertEqual(p.meta("description"), "d")
        self.assertFalse(p.title_in_body)

    def test_svg_title_is_not_the_page_title(self):
        p = parse_html('<html><head><title>Real</title></head><body><svg><title>Icon</title></svg><p>x</p></body></html>')
        self.assertEqual(p.title, "Real")
        p = parse_html('<html><head></head><body><svg><title>Icon</title></svg></body></html>')
        self.assertFalse(p.title)

    def test_repeated_robots_meta(self):
        p = parse_html('<head><meta name="robots" content="max-image-preview:large"><meta name="robots" content="noindex"></head>')
        self.assertEqual(p.meta_all("robots"), ["max-image-preview:large", "noindex"])


class SiteEdgeTests(unittest.TestCase):
    def setUp(self):
        self.proj = Path(tempfile.mkdtemp(prefix="gf-proj-"))

    def tearDown(self):
        shutil.rmtree(self.proj, ignore_errors=True)

    def audit(self, *extra, site=None):
        res = run(SCRIPTS / "audit.py", *([site] if site else []), "--state", ".get-found", "--quiet", *extra, cwd=self.proj)
        self.assertIn(res.returncode, (0, 1), res.stderr)
        report = sorted((self.proj / ".get-found" / "reports").glob("audit-*.json"))[-1]
        return report, json.loads(report.read_text())

    def test_robots_5xx_means_disallow_all(self):
        site = Site(status={"/robots.txt": 503})
        try:
            run(SCRIPTS / "state.py", "init", "--site", site.base, "--type", "saas", cwd=self.proj)
            _, rep = self.audit()
            codes = {i["code"] for i in rep["issues"]}
            self.assertIn("robots-5xx", codes)
            blocked = [i for i in rep["issues"] if i["code"] == "bot-blocked" and "Googlebot" in i["message"]]
            self.assertTrue(blocked and blocked[0]["severity"] == "error")
            self.assertNotIn("training-policy-mismatch", codes, "assumed disallow-all is not an owner's training choice")
            self.assertTrue(len(rep["pages"]) > 1, "the pages are still audited")
        finally:
            site.close()
        site = Site(status={"/robots.txt": 404})
        try:
            cfg = json.loads((self.proj / ".get-found/config.json").read_text())
            cfg["site_url"] = site.base + "/"
            (self.proj / ".get-found/config.json").write_text(json.dumps(cfg))
            _, rep = self.audit()
            self.assertIn("robots-missing", {i["code"] for i in rep["issues"]})
            self.assertFalse([i for i in rep["issues"] if i["code"] == "bot-blocked"], "4xx robots.txt means allow all")
        finally:
            site.close()

    def test_site_patterns(self):
        """robots-disallowed links, intentional noindex, lastmod vs dateModified, dict sameAs."""
        def mutate(s):
            rb = s.dir / "robots.txt"
            rb.write_text(rb.read_text().replace("Allow: /\n", "Allow: /\nDisallow: /private.html\n", 1), encoding="utf-8")
            (s.dir / "private.html").write_text("<html><head><title>Private</title></head><body>x</body></html>")
            (s.dir / "products").mkdir()
            links = []
            for n in "abc":
                (s.dir / "products" / f"{n}.html").write_text(
                    f'<html lang="en"><head><title>Product {n} | Acme</title><meta name="robots" content="noindex"></head><body><h1>{n}</h1></body></html>')
                links.append(f'<a href="/products/{n}.html">{n}</a>')
            idx = s.dir / "index.html"
            idx.write_text(idx.read_text().replace("</header>", " ".join(links) + ' <a href="/private.html">Private</a></header>')
                           .replace('"sameAs":["https://www.linkedin.com/company/acme-health"]',
                                    '"sameAs":[{"@id":"https://example.org/x"},"https://www.linkedin.com/company/acme-health",""]'))
            post = s.dir / "blog" / "first-post.html"
            post.write_text(post.read_text().replace('"dateModified":"2026-10-20T09:00:00+05:30"', '"dateModified":"2026-10-28T09:00:00+05:30"'))
        site = Site(mutate=mutate)
        try:
            run(SCRIPTS / "state.py", "init", "--site", site.base, "--type", "ecommerce", cwd=self.proj)
            _, rep = self.audit()
            codes = {i["code"]: i for i in rep["issues"]}
            self.assertIn("robots-disallowed-linked", codes)
            self.assertFalse([p for p in rep["pages"] if p["url"].endswith("/private.html")], "disallowed URL must not be crawled")
            self.assertIn("commercial-pages-noindex", codes)
            self.assertIn("lastmod-datemodified-mismatch", codes)
            self.assertIn("/blog/first-post.html", codes["lastmod-datemodified-mismatch"]["message"])
            home = next(p for p in rep["pages"] if p["url"].rstrip("/") == site.base)
            self.assertEqual(home.get("same_as"), ["https://www.linkedin.com/company/acme-health"])

            cfg = json.loads((self.proj / ".get-found/config.json").read_text())
            cfg["intentional_noindex"] = [{"path": "/products/", "reason": "pre-launch"}]
            (self.proj / ".get-found/config.json").write_text(json.dumps(cfg))
            _, rep = self.audit()
            codes = {i["code"] for i in rep["issues"]}
            self.assertNotIn("commercial-pages-noindex", codes)
            self.assertIn("commercial-pages-noindex-intended", codes)
            _, rep = self.audit("--ignore-robots")
            self.assertTrue([p for p in rep["pages"] if p["url"].endswith("/private.html")])
        finally:
            site.close()


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.proj = Path(tempfile.mkdtemp(prefix="gf-proj-"))

    def tearDown(self):
        shutil.rmtree(self.proj, ignore_errors=True)

    def state(self, *args, env=None):
        return run(SCRIPTS / "state.py", *args, cwd=self.proj, env=env)

    def ledger(self):
        return json.loads((self.proj / ".get-found/ledger.json").read_text())["items"]

    def audit(self, *extra):
        res = run(SCRIPTS / "audit.py", *extra, "--state", ".get-found", "--quiet", cwd=self.proj)
        self.assertIn(res.returncode, (0, 1), res.stderr)
        return sorted((self.proj / ".get-found" / "reports").glob("audit-*.json"))[-1]

    def test_partial_ingest_never_improves_site_wide_items(self):
        site = Site()
        try:
            self.state("init", "--site", site.base, "--type", "saas")
            self.state("ingest", self.audit())
            before = self.ledger()
            self.assertEqual(before["ai-readable"]["status"], "fail")
            path = self.audit("--urls", "/about.html")
            rep = json.loads(path.read_text())
            self.assertTrue(rep.get("partial"))
            out = self.state("ingest", path).stdout
            self.assertIn("partial", out)
            after = self.ledger()
            self.assertEqual(after["ai-readable"]["status"], "fail", "a page subset must not mark a site-wide failure fixed")
            for k, v in before.items():
                rank = {"todo": 0, "pass": 1, "manual": 2, "warn": 3, "fail": 4}
                if v["status"] in rank and after[k]["status"] in rank and v["status"] != after[k]["status"]:
                    self.assertGreater(rank[after[k]["status"]], rank[v["status"]], f"{k}: {v['status']} -> {after[k]['status']}")
        finally:
            site.close()

    def test_ingest_refuses_other_host_and_records_canonical(self):
        site = Site()
        try:
            self.state("init", "--site", "https://www.example.com", "--type", "saas")
            path = self.audit(site.base)
            r = self.state("ingest", path)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("example.com", r.stderr)
            self.assertFalse((self.proj / ".get-found/pages.json").exists() and
                             json.loads((self.proj / ".get-found/pages.json").read_text()).get("pages"))
            r = self.state("ingest", path, "--force")
            self.assertEqual(r.returncode, 0, r.stderr)
            # www vs apex of the same site: accepted, and the served host is remembered for IndexNow
            cfg = json.loads((self.proj / ".get-found/config.json").read_text())
            cfg["site_url"] = site.base.replace("127.0.0.1", "www.127.0.0.1") + "/"
            (self.proj / ".get-found/config.json").write_text(json.dumps(cfg))
            r = self.state("ingest", path)
            self.assertEqual(r.returncode, 0, r.stderr)
            cfg = json.loads((self.proj / ".get-found/config.json").read_text())
            self.assertEqual(cfg.get("canonical_url", "").rstrip("/"), site.base)
        finally:
            site.close()

    def test_google_sync_only_when_tracked(self):
        site = Site()
        try:
            self.state("init", "--site", site.base, "--type", "saas")
            self.state("ingest", self.audit())
            due = lambda env=None: json.loads(self.state("due", "--json", env=env).stdout)
            reasons = " ".join(d["reason"] for d in due())
            self.assertNotIn("Google", reasons, "no Google credentials and never submitted: don't nag")
            reasons = " ".join(d["reason"] for d in due({"GSC_ACCESS_TOKEN": "x"}))
            self.assertIn("--channel google --all-pending", reasons)
        finally:
            site.close()

    def test_init_force_keeps_settings_and_manual_flag(self):
        r = self.state("init", "--site", "https://example.com", "--type", "content", "--blog-prefix", "/articles/", "--blog-cadence", "14")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.state("init", "--force", "--site", "https://www.example.com")
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads((self.proj / ".get-found/config.json").read_text())
        self.assertEqual(cfg["site_url"].rstrip("/"), "https://www.example.com")
        self.assertEqual(cfg["site_types"], ["content"])
        self.assertEqual((cfg["blog"]["path_prefix"], cfg["blog"]["cadence_days"]), ("/articles/", 14))
        self.state("mark", "social-profiles", "--status", "manual", "--evidence", "waiting")
        self.assertTrue(self.ledger()["social-profiles"].get("manual_pending"))
        self.state("mark", "social-profiles", "--status", "pass")
        self.assertFalse(self.ledger()["social-profiles"].get("manual_pending"))


class MonorepoTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="gf-mono-"))
        (self.repo / ".git").mkdir()
        (self.repo / "package.json").write_text(json.dumps({"name": "mono", "private": True, "workspaces": ["apps/*"]}))
        for app in ("web", "admin"):
            d = self.repo / "apps" / app
            d.mkdir(parents=True)
            (d / "package.json").write_text(json.dumps({"name": app, "dependencies": {"next": "15"}}))
            (d / "next.config.js").write_text("module.exports = {}\n")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_detect_init_and_key(self):
        det = json.loads(run(SCRIPTS / "state.py", "detect", "--json", cwd=self.repo / "apps" / "web").stdout)
        self.assertTrue(det["monorepo"])
        self.assertEqual({a["app_dir"] for a in det["apps"]}, {"apps/web", "apps/admin"})
        self.assertTrue(next(a for a in det["apps"] if a["app_dir"] == "apps/admin")["looks_internal"])

        r = run(SCRIPTS / "state.py", "init", "--site", "https://example.com", "--type", "saas", cwd=self.repo)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--app-dir apps/web", r.stderr)
        r = run(SCRIPTS / "state.py", "init", "--site", "https://example.com", "--type", "saas", "--app-dir", "apps/web",
                cwd=self.repo / "apps" / "web")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.repo / ".get-found/config.json").exists(), "state lives at the repo root")
        self.assertFalse((self.repo / "apps/web/.get-found").exists())
        cfg = json.loads((self.repo / ".get-found/config.json").read_text())
        self.assertEqual((cfg["app_dir"], cfg["public_dir"]), ("apps/web", "apps/web/public"))

        # documented `--state .get-found` from an app dir finds the root ledger
        r = run(SCRIPTS / "state.py", "--state", ".get-found", "status", cwd=self.repo / "apps" / "web")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.repo / "apps/web/.get-found").exists())

        r = run(SCRIPTS / "submit.py", "indexnow-key", cwd=self.repo / "apps" / "web")
        self.assertNotEqual(r.returncode, 0, "public dir doesn't exist yet")
        self.assertIn("does not exist", r.stderr)
        r = run(SCRIPTS / "submit.py", "indexnow-key", "--create", cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        key = json.loads((self.repo / ".get-found/config.json").read_text())["indexnow"]["key"]
        self.assertTrue((self.repo / "apps/web/public" / f"{key}.txt").exists())

    def test_rules_does_not_create_state(self):
        cache = self.repo / "cache"
        r = run(SCRIPTS / "rules.py", "status", cwd=self.repo / "apps" / "web", env={"XDG_CACHE_HOME": str(cache)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.repo / "apps/web/.get-found").exists())
        self.assertFalse((self.repo / ".get-found").exists())
        self.assertTrue((cache / "get-found").is_dir())


class IndexNowTests(unittest.TestCase):
    """submit.py host matching, in-process with fetch/jpost stubbed (no network)."""

    def setUp(self):
        import importlib
        self.submit = importlib.import_module("submit")
        self.dir = Path(tempfile.mkdtemp(prefix="gf-ix-"))
        (self.dir / "config.json").write_text(json.dumps({"site_url": "https://example.com/", "indexnow": {"key": "k123"}}))
        self.posts = []
        self.live = set()

        class R:
            def __init__(self, ok, body=""):
                self.ok, self.status, self.error, self.body = ok, 200 if ok else 404, None, body.encode()

            def text(self):
                return self.body.decode()
        self._orig = (self.submit.fetch, self.submit.jpost)
        self.submit.fetch = lambda url, **k: R(urllib_host(url) in self.live, "k123")
        self.submit.jpost = lambda url, body, **k: (self.posts.append(body), R(True))[1]

    def tearDown(self):
        self.submit.fetch, self.submit.jpost = self._orig
        shutil.rmtree(self.dir, ignore_errors=True)

    def call(self, urls, dry_run=False):
        import argparse, contextlib, io
        from state import State
        a = argparse.Namespace(site=None, urls=urls, from_state=False, dry_run=dry_run, endpoint=None)
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                self.submit.cmd_indexnow(State(self.dir), a)
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_groups_by_host_and_drops_other_sites(self):
        self.live = {"example.com", "www.example.com"}
        code, out, err = self.call(["https://www.example.com/a", "https://example.com/b", "https://other.com/c"])
        self.assertEqual(code, 0, err)
        self.assertEqual({p["host"] for p in self.posts}, {"www.example.com", "example.com"})
        www = next(p for p in self.posts if p["host"] == "www.example.com")
        self.assertEqual((www["urlList"], www["keyLocation"]), (["https://www.example.com/a"], "https://www.example.com/k123.txt"))
        self.assertIn("skipping 1 URLs", err)

    def test_dry_run_warns_on_dead_key(self):
        code, out, err = self.call(["https://example.com/a"], dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("warning: key file", err)
        self.assertIn('"host": "example.com"', out)
        self.assertEqual(self.posts, [])

    def test_dead_key_host_fails_loudly(self):
        self.live = {"www.example.com"}
        code, out, err = self.call(["https://www.example.com/a", "https://example.com/b"])
        self.assertNotEqual(code, 0)
        self.assertEqual([p["host"] for p in self.posts], ["www.example.com"])
        self.assertIn("example.com", err)

    def test_none_match_dies(self):
        code, _, err = self.call(["https://other.com/a"])
        self.assertNotEqual(code, 0)
        self.assertIn("none of the 1 URLs", err)


def urllib_host(url):
    import urllib.parse
    return urllib.parse.urlsplit(url).hostname


class RulesTests(unittest.TestCase):
    def test_changes_stay_reported_until_ack_and_failures_stay_due(self):
        site = Site()
        st = Path(tempfile.mkdtemp(prefix="gf-rules-"))
        try:
            (st / "sources.override.json").write_text(json.dumps({"sources": [
                {"id": "about", "url": site.base + "/about.html", "items": ["titles"]},
                {"id": "gone", "url": site.base + "/no-such-page.html", "items": ["titles"]},
                {"id": "blocked", "url": site.base + "/x.html", "items": ["titles"], "fetch": "browser"}]}))
            refresh = lambda *x: json.loads(run(SCRIPTS / "rules.py", "--state", st, "refresh", "--json", *x, cwd=st).stdout)
            rep = refresh()
            self.assertEqual((rep["fetched"], len(rep["changed"]), len(rep["failed"])), (2, 0, 1))
            self.assertEqual([b["id"] for b in rep["browser_only"]], ["blocked"])
            meta = json.loads((st / "rules.json").read_text())
            self.assertNotIn("fetched", meta["gone"], "a failed fetch must not count as fresh")
            rep = refresh()
            self.assertEqual(rep["fetched"], 1, "the failed source is retried, the good one is fresh")
            about = site.dir / "about.html"
            about.write_text(about.read_text().replace("</body>", "<p>A new paragraph about policy.</p></body>"))
            rep = refresh("--force")
            self.assertEqual([c["id"] for c in rep["changed"]], ["about"])
            rep = refresh("--force")
            self.assertEqual([c["id"] for c in rep["changed"]], ["about"], "unreviewed change is reported again")
            self.assertTrue(Path(rep["changed"][0]["diff"]).exists())
            r = run(SCRIPTS / "rules.py", "--state", st, "ack", "about", cwd=st)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(refresh("--force")["changed"], [])
        finally:
            site.close()
            shutil.rmtree(st, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
