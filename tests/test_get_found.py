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

from _common import image_size  # noqa: E402
from robots import Robots, bot_matrix, verdict  # noqa: E402


def run(*args, cwd):
    return subprocess.run([sys.executable, *map(str, args)], cwd=cwd, capture_output=True, text=True, timeout=120)


class Site:
    """Copy the fixture, point its absolute URLs at a free local port, serve it."""

    def __init__(self, mutate=None):
        self.dir = Path(tempfile.mkdtemp(prefix="gf-site-"))
        shutil.copytree(FIXTURE, self.dir, dirs_exist_ok=True)
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *a):
                pass
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
            self.assertEqual(noindex_issues, [], "noindex pages should not get presentation issues")
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


if __name__ == "__main__":
    unittest.main()
