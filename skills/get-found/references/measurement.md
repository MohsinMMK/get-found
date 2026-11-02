# Measurement: the monthly get-found report

## Data to pull (use what's connected; ask the user for exports otherwise)
| Source | How | What to extract |
|---|---|---|
| Google Search Console | `submit.py google performance --days 28 --dimensions query page --out ...` (compare with previous 28 days); UI for branded filter, Insights, Generative AI performance | clicks, impressions, CTR, avg position; top gaining/losing queries & pages; non-branded share; AI-feature impressions |
| Search Console Pages report | UI (no API) — ask for screenshot or numbers | indexed vs not indexed, top reasons |
| Bing Webmaster | `submit.py bing queries`; UI for AI Performance (CSV export) | clicks/impressions; **AI citations**, cited pages, grounding queries |
| Analytics (GA4, Plausible, etc.) | user export or connector | sessions from organic search; **AI referrals** — referrer hosts chatgpt.com, perplexity.ai, copilot.microsoft.com, gemini.google.com, claude.ai, and `utm_source=chatgpt.com` (ChatGPT adds it automatically) |
| Server/CDN logs (optional) | log query or Cloudflare AI Crawl Control | AI bot hits by token, 404s served to bots |
| get-found state | `state.py status --json`, `listings`, `keywords` | readiness %, overdue items, listings live, posts published |

## Report template (`assets/templates/monthly-report.md`)
1. Headline: 3 numbers vs last period (organic clicks, AI referrals/citations, indexed pages).
2. What moved and why (link to deploys/posts/annotations).
3. Keywords: wins (entered top 10), opportunities (pos 8–30 with impressions), losses.
4. AI visibility: citations, AI referrals, bot access status.
5. Checklist health: readiness %, items failing/overdue.
6. Next month: max 5 actions, each tied to a checklist item or backlog keyword.
Save as `.get-found/reports/report-YYYY-MM.md`, then `state.py mark measurement --status pass --evidence "report YYYY-MM"`. Update `keywords.json` (new backlog items from opportunities) and suggest the user add a Search Console annotation for big changes.

## Reading the numbers sensibly
- Search Console data lags 2–3 days; compare equal-length periods; weekday/seasonality matter.
- A new page usually takes days to weeks to be crawled and indexed; don't change everything after one week.
- AI citations are volatile; look at trends over months.
- Position averages hide distribution — look at page-level data for decisions.
