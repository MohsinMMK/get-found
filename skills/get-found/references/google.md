# Google: Search Console, API, and Search rules

Verified 2026-11-02. Re-check with `rules.py refresh --items search-console sitemap structured-data` before acting on anything that looks stale.

## Contents
- Setup (property, verification, users, AI setting)
- API access for automation
- Sitemaps and URL Inspection
- What NOT to use (Indexing API, sitemap ping)
- Titles, snippets, images, favicon, site name
- Structured data that still matters
- AI Overviews / AI Mode
- Content rules that affect a weekly blog
- Reports to use in maintenance

## Setup (user does the clicks; agent supplies exact values)
1. **Property type**: create a **Domain property** for `example.com` (covers all subdomains and http/https). It is verifiable **only via DNS** (TXT or CNAME record at the registrar/DNS host). Add a URL-prefix property in addition only if path-level data or non-DNS verification is needed.
2. **Verification methods**: Domain → DNS TXT. URL-prefix → HTML file upload, `<meta name="google-site-verification">`, Google Analytics, Google Tag Manager, or DNS. Keep the token in place forever (removing it un-verifies after a grace period); adding a second method is a good backup.
   - If the repo controls `<head>`, the agent can add the meta tag via the framework's metadata (`verification: { google: '...' }` in Next.js).
3. **Submit the sitemap**: Sitemaps report → enter `sitemap.xml` (or index) → Submit. Also keep the `Sitemap:` line in robots.txt.
4. **Search generative AI setting** (Settings → *Search generative AI*, all sites since 2026-08-31): Include (default) / Exclude / inherit. Exclude removes the site from AI Overviews, AI Mode and Discover AI features; it does **not** affect training (that's Google-Extended) and is not a ranking signal. For get-found the target is **Include** unless the owner decides otherwise — record the choice in config notes.
5. **Users**: Settings → Users and permissions. To automate, add the service account's email as **Owner** or **Full** user (Restricted users can't submit sitemaps or inspect).
6. **Platform properties** (since July 2026): add the brand's Instagram, TikTok, X and YouTube accounts as properties by authorising them — gives social search performance data.
7. **Annotations**: Performance charts accept custom annotations (UI only). Ask the user to annotate major deploys/redesigns so later traffic changes have context.

## API access for automation (optional, recommended)
- APIs: Search Console API — `https://www.googleapis.com/webmasters/v3` (sites, sitemaps, searchAnalytics) and `POST https://searchconsole.googleapis.com/v1/urlInspection/index:inspect`.
- Auth: OAuth 2.0. Scope `https://www.googleapis.com/auth/webmasters` (`.readonly` for reports only).
- Easiest for a project: Google Cloud project → enable "Google Search Console API" → create a service account → download JSON key → add its email to the property (Owner/Full) → `export GOOGLE_APPLICATION_CREDENTIALS=/path/key.json` and `pip install google-auth requests`. Or provide a short-lived token in `GSC_ACCESS_TOKEN`.
- Never commit the key; add it to `.gitignore`; in CI use repository secrets.
- `siteUrl` format: `sc-domain:example.com` (Domain property) or `https://www.example.com/` (URL-prefix, trailing slash).
- Quotas: URL Inspection 2,000/day and 600/min per site; Search Analytics 1,200 QPM per site/user, max 25,000 rows per request (`startRow` to page), data lags 2–3 days; `dataState: "all"` includes fresh data.

## Sitemaps and URL Inspection
- Limits: 50,000 URLs or 50 MB uncompressed per sitemap; sitemap index up to 50,000 sitemaps; children on the same site.
- `lastmod`: only when the main content, structured data or links significantly changed. Footer/sidebar changes don't count. Omit it rather than fake it.
- Image/video/news sitemaps exist for those content types (news: last 2 days only).
- URL Inspection API shows the **indexed** version (coverage, Google-selected canonical, last crawl). It cannot request indexing — "Request indexing" is a UI button; ask the user to press it for a handful of important new URLs at most.

## What NOT to use
- **Indexing API**: built for pages with `JobPosting` or `BroadcastEvent` embedded in `VideoObject`; since Oct 2026 Google also accepts news/UGC/real-time content **only through its application program**. Submissions get spam detection and misuse can revoke access. Don't use it for blog posts or normal pages unless the site has been approved through that program — sitemaps + Search Console are the route for everything else.
- **Sitemap ping** (`google.com/ping?sitemap=`): retired in 2023, returns 404.
- **Changing dates** to look fresh, mass-generating pages for query variants (scaled content abuse), keyword-stuffed titles.

## Titles, snippets, images, favicon, site name
- Title links come from `<title>` mostly, but Google may use the h1, og:title, prominent text, anchor text or WebSite data. No length limit; truncation is by device width. Unique, descriptive, brand at start or end with a delimiter, same language as the page.
- Meta descriptions: no length limit; unique per page; used when they describe the page better than on-page text. `nosnippet` / `max-snippet` / `data-nosnippet` restrict snippets **and** AI Overview/AI Mode use.
- Robots meta in `<body>` is honoured too (since Mar 2026) — make sure components don't inject stray noindex.
- `max-image-preview:large` allows large image previews (Discover).
- Thumbnails: Google uses `og:image`, `primaryImageOfPage` or the main entity's `image`. Use a representative image, not just a logo or text-heavy graphic.
- **Favicon**: `<link rel="icon">` on the homepage, square, > 48×48 recommended, stable crawlable URL. Supported (as listed 2026-11): BMP, GIF, ICO, PNG, JPEG, PPM, TIFF — **SVG isn't listed** (re-check source `google-favicon`; the list was revised in Aug 2026), so always ship a PNG/ICO too.
- **Site name**: `WebSite` JSON-LD (`name`, optional `alternateName`, `url`) on the homepage only; consistent with `og:site_name` and the homepage title.

## Structured data that still matters (gallery as of 2026-10)
Article · Breadcrumb (desktop) · Carousel · Course list · Dataset · Discussion forum · Education Q&A · Employer rating · Event · Image metadata · Job posting · Local business · Math solver · Movie · Organization · Product · Profile page · Q&A · Recipe · Review snippet · Software app · Speakable · Paywalled content · Vacation rental · Video.
Removed: FAQ (May 2026), HowTo (2023), Course info, ClaimReview, Estimated salary, Learning video, Special announcement, Vehicle listing (2025), Practice problem (Jan 2026).
Organization: no required properties; `logo` ≥ 112×112 crawlable; `sameAs` list of profiles.

## AI Overviews / AI Mode
- No special file, markup or "AI text" needed. A page must be indexed and snippet-eligible; ranking/quality systems decide.
- Google's AI optimization guide (2026): "AEO/GEO" is just SEO; **llms.txt is ignored by Google Search**; don't chunk or rewrite content for AI; write non-commodity, first-hand content; creating pages for fan-out query variants = scaled content abuse.
- Controls: Search Console *Search generative AI* setting (site), `nosnippet`/`data-nosnippet`/`max-snippet`/`noindex` (page). `Google-Extended` only controls Gemini training and Gemini Apps/Vertex grounding — not Search or AI Overviews.
- Reporting: AI Overviews/AI Mode clicks and impressions are inside the Performance report ("Web"); separate *Generative AI performance* report shows impressions only.
- Google aims to support the shared AI-training opt-out token in robots.txt (`__usage_ai-training`, IETF individual draft) in 2027 — see ai-visibility.md.

## Content rules that affect a weekly blog
- People-first: show first-hand experience, cite sources, byline with author page, no preferred word count, avoid producing lots of content on many unrelated topics.
- Generative AI is allowed, but fact-check everything (titles, descriptions, structured data, alt text) and consider disclosing how content was made. Many pages without added value = scaled content abuse.
- Dates: visible "Published"/"Updated" date + matching `datePublished`/`dateModified` with timezone; no future dates; never change dates without substantial changes.
- Freshness systems apply only to queries that deserve fresh results — posting often is not itself rewarded.
- Internal links: every page you care about should be linked from at least one other page with descriptive anchor text.

## Reports to use in maintenance
- Pages (indexing) report — reasons for "Crawled/Discovered – currently not indexed", duplicates, redirects.
- Performance — queries/pages; **branded vs non-branded filter**; weekly/monthly views; query groups in Insights.
- Generative AI performance (impressions in AI features).
- Sitemaps report status and discovered URL counts.
- Core Web Vitals and HTTPS reports (fix if "Poor").
- Manual actions / Security issues (should be empty).
