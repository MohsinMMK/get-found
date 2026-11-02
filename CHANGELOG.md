# Changelog

## 1.1.0 — 2026-11-02
From a code review and a read-only check of a live Next.js monorepo. Several behaviours change:

**Ledger and state**
- A partial audit (`--urls`, or a crawl that hit `--max-pages`) can only make site-wide items worse, never mark them passing, and never marks uncrawled pages removed.
- Monorepos: `.get-found/` is found by walking up to the repo root, so the scripts work from inside an app folder. New `state.py detect` and `init --app-dir apps/web` record the website app and its public folder; `init` stops and lists the apps when it finds more than one.
- `state.py ingest` refuses a report for another host (`--force` overrides). An apex/www difference is recorded as `canonical_url`.
- Off-site steps (Search Console, Bing, directories) stay flagged as `manual_pending` when the on-site half fails, instead of being lost.
- "Run sync" for Google only shows up when Search Console credentials are set; otherwise use `state.py submitted --channel google --all-pending` after submitting by hand.
- `init --force` keeps your existing settings (site types, blog prefix and cadence). `--blog-prefix` takes several values (`/blog/ /guides/`).
- `/get-found check` on a project without `.get-found/` is read-only: the audit report goes to a temp folder.

**Audit**
- The crawler obeys robots.txt for Googlebot (`--ignore-robots` to override). Disallowed-but-linked URLs show as `robots-disallowed-linked`.
- robots.txt that is unreachable or returns 5xx is treated as disallow-all (RFC 9309) and reported as `robots-5xx`. A UTF-8 BOM no longer hides the first group.
- HTML parsing: a missing `</head>` no longer makes a page look empty; `<title>` inside SVG is ignored; `robots` and `googlebot` metas are kept separate.
- Transient network errors are retried before a page counts as failed.
- New checks: `lastmod-datemodified-mismatch`, WebP/AVIF og:image (info), preview tags on noindex pages (info), http://www and https://www redirect chains, Google DNS TXT verification.
- `intentional_noindex` in `config.json` turns `commercial-pages-noindex` into info for gated sections.
- Fixes: a `sameAs` containing objects crashed the audit; with `--urls`, a same-site redirect target is now audited too.

**Submissions**
- IndexNow sends one request per host (apex and www separately), fails loudly when a host's key is not live, and `--dry-run` warns instead of failing. `--site` now wins over config.
- `submit.py indexnow-key` uses the public folder recorded by `init`, and only creates it with `--create`.

**Live docs**
- `rules.py refresh` fetches in parallel, does not mark failed sources fresh, and keeps reporting a change until `rules.py ack <id>`. Sources that need a browser are listed, not fetched.
- `rules.py ai-bots` prints counts by default (`--all` for the list) and handles a bad response.
- Without a project, `rules.py` uses `~/.cache/get-found` instead of creating `.get-found/` in the current folder.

**Other**
- `fetch()` decompresses gzip safely within the size limit and reads UTF-8 with a BOM.
- `og_image.py`: the brand no longer overlaps the subtitle when there is a logo; batch jobs missing `title`/`out` are skipped, and `--subtitle` no longer applies to every batch image.
- GitHub Action template: accurate comments, `git pull --rebase` before push, notes on vendoring the skill.

## 1.0.1 — 2026-11-02
Found by running get-found on a live Next.js e-commerce site:
- Fix: `https://site.com` and `https://site.com/` (common in generated sitemaps) were audited as two pages, producing false duplicate-title/description warnings.
- New: broken-link warnings name the pages that link to the 404.
- New: `noindex-summary` (which sections are noindex) and `commercial-pages-noindex` warning when product/shop/category pages are hidden from search.

## 1.0.0 — 2026-11-02
- First release: `setup`, `check`, `sync`, `post`, `report` modes; 16-item ledger.
- Scripts: audit (crawler + checks), state (ledger, page inventory, change detection, keywords, listings), robots (RFC 9309 bot matrix), submit (IndexNow, Search Console API, Bing Webmaster API), rules (live-doc refresh, new AI bots, official changelogs), og_image.
- Baseline facts verified 2026-11-02, including: FAQ rich results removed (May 2026), Search Console "Search generative AI" setting (Aug 2026), Bing SOAP/POX API retirement (Aug 2026), Cloudflare AI bot policies (July 2026; legacy toggle deprecated Sept 2026), Apple Business replacing Business Connect (Apr 2026), Bing Places at bing.com/forbusiness, `__usage_ai-training` draft token.
