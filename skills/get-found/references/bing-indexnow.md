# Bing Webmaster Tools, IndexNow and other engines

Verified 2026-11-02. Sources: `bing-*`, `indexnow-*` in sources.json.

## Contents
- Why Bing matters for AI visibility
- Bing Webmaster Tools setup
- Bing Webmaster API
- IndexNow
- Other engines and consoles
- Local listing consoles tied to engines

## Why Bing matters for AI visibility
- Microsoft Copilot grounds answers by sending queries to Bing search. Bing also supplies most classic results for DuckDuckGo and Yahoo. Other answer engines use third-party search providers (OpenAI doesn't name them) plus their own crawlers.
- Bing's webmaster guidelines (2026 rewrite) cover Copilot: `NOARCHIVE` keeps page content out of Copilot answers; `NOCACHE` limits Copilot to URL/title/snippet; `data-nosnippet` is honoured for AI answers.
- **AI Performance report** (public preview since 2026-02-10): total citations, cited pages, sampled grounding queries, page-level citations; Intents/Topics/Citation Share/Compare views (June 2026) and Topics Portfolio beta (Oct 2026). Export via UI (no documented API) — ask the user for a CSV for the monthly report.

## Bing Webmaster Tools setup (user clicks; agent guides)
1. Sign in at bing.com/webmasters → **Import from Google Search Console** (fastest: sites + sitemaps arrive verified; Bing re-syncs ownership periodically). Otherwise verify via BingSiteAuth.xml file, `<meta name="msvalidate.01">`, DNS CNAME/TXT or Domain Connect.
2. Sitemaps → submit the sitemap URL (also discovered via robots.txt). Bing re-reads sitemaps at least daily; `lastmod` should be ISO 8601 with time and true.
3. Settings → **API access** → generate API key → user stores it as env var `BING_WEBMASTER_API_KEY` (one key per user, works for all their verified sites). OAuth 2.0 (scopes `webmaster.read`, `webmaster.manage`) is the alternative for apps.
4. Useful tools: URL Inspection, Site Scan, robots.txt tester, Keyword Research, Backlinks, IndexNow insights, Copilot assistant inside BWT.

## Bing Webmaster API (JSON only — SOAP/POX retired 2026-08-31)
- GET `https://ssl.bing.com/webmaster/api.svc/json/METHOD?apikey=KEY&siteUrl=...`
- POST `https://ssl.bing.com/webmaster/api.svc/json/METHOD?apikey=KEY` with `Content-Type: application/json; charset=utf-8`
- `SubmitUrlbatch` body `{"siteUrl":"https://example.com","urlList":["https://example.com/a", ...]}` — max 500 per call, never more than remaining quota. `GetUrlSubmissionQuota` returns `DailyQuota`/`MonthlyQuota` (up to 10,000/day depending on site). `SubmitFeed` `{"siteUrl","feedUrl"}` for sitemaps; `GetFeeds`, `GetQueryStats` (weekly data), `GetPageStats`, `GetCrawlIssues`, `GetUserSites`.
- Errors look like `{"ErrorCode":3,"Message":"InvalidApiKey"}`.
- `scripts/submit.py bing ...` wraps these. Prefer IndexNow for change notifications; use the URL Submission API when you want Bing-specific quota feedback.

## IndexNow
- Participants: Bing, Yandex, Naver, Seznam, Yep (Amazon announced 2025). Submitting to one endpoint shares with all. **Google does not participate.**
- Key: 8–128 chars `[a-zA-Z0-9-]` (use lowercase hex). Host `/{key}.txt` containing exactly the key (UTF-8) at the site root, or pass `keyLocation` on the same host (the file's folder scopes which URLs it can vouch for).
- Submit: `POST https://api.indexnow.org/indexnow` JSON `{"host":"www.example.com","key":"…","keyLocation":"https://www.example.com/….txt","urlList":[…]}` — up to 10,000 URLs. Single URL: `GET https://api.indexnow.org/indexnow?url=…&key=…`.
- Responses: 200 received · 202 accepted, key validation pending · 400 bad format · 403 key invalid/not found · 422 URLs not on host / key mismatch · 429 too many requests.
- Rules: submit added, updated **and deleted** URLs (404/410, redirects); don't resubmit the same URL within ~10 minutes; keep the sitemap too; don't ping unchanged URLs on every build.
- Platforms: many CMS/CDN plugins do this automatically (WordPress IndexNow plugin or SEO plugin setting, Cloudflare Crawler Hints). If one is active, don't double-submit — mark the item pass with that evidence.
- CI option: run `submit.py indexnow --from-state` after deploy (see assets/templates/github-action.yml).

## Other engines and consoles
| Engine | Console | Notes |
|---|---|---|
| Yandex | webmaster.yandex.com | Verify via HTML file (recommended), meta `yandex-verification`, or DNS; www and non-www are separate sites. IndexNow covers submission. Worth it for Russian/Turkish/CIS audiences. |
| Naver | searchadvisor.naver.com | Korea. Meta `naver-site-verification` or file; submit sitemap; IndexNow supported. |
| Seznam | reporter.seznam.cz/wm | Czech. IndexNow supported. |
| Baidu | ziyuan.baidu.com | Only for China-targeted sites (hosting/ICP considerations). |
| Brave Search | no console | Own index; request recrawl at search.brave.com/submit-url. Crawler follows Googlebot rules. |
| DuckDuckGo | no console | Results largely from Bing + own DuckDuckBot; DuckAssistBot for AI answers. Bing setup covers it. |
| Apple (Siri/Spotlight/Safari) | no console | Allow Applebot; Applebot-Extended is the training opt-out token only. |

Only set up regional consoles when the site targets those markets (config `country`/locale).

## Local listing consoles tied to engines
- Google Business Profile — business.google.com (add/claim, verify).
- Bing Places for Business — relaunched Oct 2025 at **bing.com/forbusiness**; import from Google Business Profile gives instant verification.
- Apple Business — **replaced Apple Business Connect on 2026-04-14** (business.apple.com); add locations under Brands → Locations; check availability in the country.
Track these as listings (`state.py listing google-business-profile --status live --url ...`).
