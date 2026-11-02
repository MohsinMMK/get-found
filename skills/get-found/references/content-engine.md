# Content engine: one real keyword per page, one useful post per week

## Contents
- Keyword workflow (free, official data first)
- The keyword map (`.get-found/keywords.json`)
- Cannibalisation — act on data, not theory
- Post workflow (brief → draft → checks → publish → distribute)
- Refreshing old content
- Cadence honesty
- RSS/Atom feed

## Keyword workflow
Sources in order of trust:
1. **Search Console** — `submit.py google performance --days 90 --dimensions query page --out .get-found/reports/gsc-<date>.json`. Gold: queries with impressions at positions ~8–30 (you're already relevant; a better page can win). Also branded vs non-branded filter and Insights query groups in the UI.
2. **Bing Webmaster** — `submit.py bing queries`, Keyword Research tool (UI), and AI Performance *grounding queries* (what Copilot searched when it cited you).
3. **Google Keyword Planner** (free with an Ads account with billing set up; low-spend accounts see ranges), Google Trends (seasonality/comparisons), autocomplete and "People also ask" for phrasing.
4. Customer language: support tickets, sales calls, WhatsApp/DM questions, reviews — the questions people actually ask become posts.
For each candidate record: keyword, intent (informational / commercial / transactional / local / navigational), evidence (numbers + source + date), and which page should own it. Check the live results for the query: if the top results are a different content type (videos, product listings, government sites), match that type or pick another keyword. Prefer specific, achievable phrases ("whatsapp appointment reminders for clinics") over head terms ("clinic software") for a new site.

## The keyword map
```json
{
  "pages": {
    "/": {"primary": "clinic booking software", "secondary": ["hospital appointment system"], "intent": "commercial", "evidence": "GSC 90d: 1.2k impr, pos 14 (2026-11-02)"},
    "/blog/reduce-no-shows": {"primary": "how to reduce no-shows clinic", "intent": "informational", "evidence": "PAA + Planner 1k-10k"}
  },
  "backlog": [
    {"keyword": "whatsapp reminders for doctors", "intent": "informational", "status": "idea", "planned_for": "2026-11-10", "evidence": "GSC impressions on /features, pos 22", "notes": "answer: setup steps + template messages"}
  ]
}
```
Statuses: `idea → brief → draft → published` (set `url` when published). `state.py keywords --check` flags pages without a primary keyword, duplicate primaries, keywords not reflected in titles, and backlog items already owned by a page.

## Cannibalisation — act on data
Two pages targeting similar words is only a problem when Search Console shows them **swapping** for the same query (both get impressions, positions fluctuate). Then: merge into the stronger page (301 the weaker), or differentiate intent clearly, and update the map. Don't merge pages on theory.

## Post workflow
1. **Pick**: next backlog item (earliest `planned_for`, else highest evidence). Confirm no existing page owns the keyword — if one does, refresh that page instead.
2. **Brief** (`assets/templates/blog-brief.md`): reader, search intent, the question to answer in the first 2 sentences, outline of h2s (often real sub-questions), facts/data/examples to include, internal links (from 2+ existing pages, to 1–2 money pages), CTA, OG image text. Ask the owner for first-hand material (numbers, client story, screenshots, opinion) — this is what makes it non-commodity.
3. **Draft**: in the site's content system (MDX/markdown/CMS) following existing post conventions. Answer first, then depth; descriptive h2/h3; lists/tables where they help; cite sources; author byline (real person with an author page); no fluff intro. AI-assisted drafting is fine — fact-check every claim, title, description, alt text and structured data.
4. **On-page checks**: title (keyword + promise + brand), meta description, slug (short, keyword), h1, intro answers the query, internal links in/out, images with alt + compressed, OG image (per post), `BlogPosting` JSON-LD with author/datePublished/dateModified matching the visible date, canonical, in sitemap + feed.
5. **Publish** (owner approves/merges/deploys).
6. **Sync**: run sync mode (audit the new URL, IndexNow, Google sitemap status, update keyword map status → published, ledger `blog`).
7. **Distribute**: share on the brand's main profiles (preview checked), relevant communities where self-promotion is allowed, email newsletter; link from related older posts.
Never publish without the owner's approval; never invent quotes, statistics, testimonials or case studies.

## Refreshing old content
Monthly from the measurement report: posts losing clicks, high impressions/low CTR, outdated facts. Update substance (facts, screenshots, steps), then `dateModified` and the visible "Updated" date — never bump dates without real changes. Merge thin overlapping posts (301). Deleting old content just because it's old is not a Google recommendation; improve or consolidate instead.

## Cadence honesty
Google doesn't reward posting frequency itself; freshness matters only for queries that deserve it, and "lots of content on many topics" or mass AI pages are spam signals. The weekly cadence exists because consistent, useful posts compound coverage of what customers ask. If a week's post would be filler, publish a refresh of an existing post instead and say so in the ledger evidence.

## RSS/Atom feed
Keep an RSS/Atom feed for the blog (frameworks have plugins). Google and Bing accept feeds as supplementary sitemaps for recent URLs; newsletter tools and readers use them. Add `<link rel="alternate" type="application/rss+xml">` in `<head>`.
