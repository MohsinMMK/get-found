# Changelog

## 1.0.1 — 2026-11-02
Found by running get-found on a live Next.js e-commerce site:
- Fix: `https://site.com` and `https://site.com/` (common in generated sitemaps) were audited as two pages, producing false duplicate-title/description warnings.
- New: broken-link warnings name the pages that link to the 404.
- New: `noindex-summary` (which sections are noindex) and `commercial-pages-noindex` warning when product/shop/category pages are hidden from search.

## 1.0.0 — 2026-11-02
- First release: `setup`, `check`, `sync`, `post`, `report` modes; 16-item ledger.
- Scripts: audit (crawler + checks), state (ledger, page inventory, change detection, keywords, listings), robots (RFC 9309 bot matrix), submit (IndexNow, Search Console API, Bing Webmaster API), rules (live-doc refresh, new AI bots, official changelogs), og_image.
- Baseline facts verified 2026-11-02, including: FAQ rich results removed (May 2026), Search Console "Search generative AI" setting (Aug 2026), Bing SOAP/POX API retirement (Aug 2026), Cloudflare AI bot policies (July 2026; legacy toggle deprecated Sept 2026), Apple Business replacing Business Connect (Apr 2026), Bing Places at bing.com/forbusiness, `__usage_ai-training` draft token.
