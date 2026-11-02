# get-found

**Make any website findable by Google, Bing and AI answer engines (ChatGPT, Claude, Perplexity, Gemini, Copilot) — and keep it that way for the life of the project.**

`get-found` is an [Agent Skill](https://agentskills.io) for Claude Code, Claude.ai and other agents that support the open skills format. Run it once to set everything up; run it again whenever you ship pages, publish a post or just want to know where things stand. It remembers what was done and when in a small ledger inside your repo, and it re-checks its rules against the official Google, Bing, OpenAI, Anthropic, Perplexity, Apple, Cloudflare and IndexNow documentation on every run — so you don't have to update the skill when the rules change.

## The checklist it maintains

The popular "get-found checklist" covers 9 items. get-found turns each into tracked, verifiable ledger items and adds the pieces that quietly break visibility:

| You asked for | get-found ledger items | What it actually checks / does |
|---|---|---|
| Search Console | `search-console`, `bing-webmaster`, `indexnow` | Domain property + DNS verification guide, sitemap submission via API, the 2026 "Search generative AI" setting, Bing import from GSC, IndexNow key + pings for new/changed/deleted URLs |
| Sitemap | `sitemap`, `crawlability` | Framework-generated sitemap, only canonical 200 indexable URLs, *real* lastmod, robots.txt, canonicals, noindex accidents, soft-404s, https/www |
| A real title | `titles` | Unique, keyword-led titles + descriptions, generic-title detection ("Home", "Vite + React"), duplicates, favicon formats |
| Preview image | `preview-images`, `social-profiles` | OG + X tags, 1200×630 images under WhatsApp's 600 KB limit, `<head>` within 300 KB, platform cache refresh, sameAs links to profiles |
| Pages AI can read | `ai-readable`, `structured-data` | Detects client-rendered pages AI crawlers see as empty, headings, nosnippet/noarchive traps, JSON-LD (Organization, WebSite, BlogPosting…), optional llms.txt |
| AI bots allowed | `ai-bots-allowed` | RFC 9309 robots.txt matrix for 30+ search / user / training / preview bots, CDN/WAF traps (Cloudflare's 2026 AI bot policies), new-bot discovery |
| 10 directories | `directories` | Curated listings by site type & country (incl. India: Justdial, Practo, IndiaMART…), listing kit, status tracking |
| 1 real keyword | `keywords` | One primary keyword per page from Search Console/Bing data, cannibalisation check, keyword-in-title check |
| 1 blog post / week | `blog` | Backlog → brief → draft → on-page checks → publish → notify engines; cadence tracked from real publish dates |
| — | `measurement`, `rules-fresh` | Monthly report (GSC, Bing AI citations, AI referrals) and live-doc rule refresh |

## How it stays current

1. Procedures live in the skill; **facts that drift** (bot names, API endpoints, size limits, rich-result eligibility, directory status) live in small JSON files.
2. Every run, `rules.py refresh` re-fetches ~55 official pages, diffs them against the last snapshot, and reads the Google/Bing/OpenAI changelogs. The agent reviews real changes and writes project-level overrides (`.get-found/*.override.json`, `rules-notes.md`) that take precedence over the skill's baseline.
3. `rules.py ai-bots` compares the community AI-agent list against known bots, so new crawlers surface automatically.

## Install

**Claude Code — plugin marketplace (recommended, gets updates):**
```
/plugin marketplace add MohsinMMK/get-found
/plugin install get-found@get-found
```

**Claude Code — manual:** copy `skills/get-found/` to `~/.claude/skills/get-found/` (all projects) or `<project>/.claude/skills/get-found/` (one project).

**Skills CLI (Claude Code, Codex, Cursor and others):**
```
npx skills add MohsinMMK/get-found
```

**Claude.ai:** download `get-found.zip` from Releases (or zip the `skills/get-found` folder) → Customize → Skills → upload, then enable it.

## Use

```
/get-found setup     # first run: interview, audit, fix on-site items, guide Search Console/Bing/IndexNow, keyword map, directories
/get-found check     # audit + what's due, nothing changed
/get-found sync      # after you add/change/remove pages: fix them, ping engines, update the ledger
/get-found post      # this week's post from the keyword backlog
/get-found report    # monthly Search Console + Bing + AI-citation report
```
Plain language works too: *"I just added a pricing page and two blog posts"*, *"why doesn't ChatGPT find my app?"*, *"is my site set up for Google?"*.
(Installed as a plugin, the command is namespaced: `/get-found:get-found sync`.)

## What it creates in your project

```
.get-found/
├── config.json        site URL, type, market, brand, social profiles, AI-training choice
├── ledger.json        every checklist item: status, last checked, last changed, next due, evidence
├── pages.json         page inventory with content hashes → detects new / changed / removed pages
├── keywords.json      one primary keyword per page + post backlog with planned dates
├── listings.json      directory listings and their status
├── history.jsonl      append-only log of every check, fix and submission
├── rules-notes.md     live-doc changes that override the skill's baseline
├── rules.json         when each official source was last fetched / changed, unreviewed changes
├── .gitignore         keeps rules-cache/ and reports/audit-* out of git
├── rules-cache/       local snapshots of the official pages (not committed)
└── reports/           monthly reports (committed) and audit reports (not committed)
```
Commit it. It contains no secrets.

- **Monorepos:** `.get-found/` lives at the repo root. `state.py init --app-dir apps/web` records which app is the site and where its public folder is, so the scripts work from the root or from inside the app.
- **First check before setup:** `/get-found check` on a project without `.get-found/` only reads: the audit report goes to a temp folder and nothing is written to the repo until you run setup.
- **Docs changes:** when `rules.py refresh` finds a change in an official page, it keeps reporting it until the agent has reviewed it and run `rules.py ack <id>`. Sources that fail to fetch are retried next run, not marked fresh.

## Optional credentials (environment variables only)

| Variable | Enables |
|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` (service-account JSON, added as Owner/Full user in Search Console) or `GSC_ACCESS_TOKEN` | Sitemap submission, URL inspection and performance data via the Search Console API (`pip install google-auth requests`) |
| `BING_WEBMASTER_API_KEY` | Bing URL/sitemap submission, quota and query stats |
| — | IndexNow needs no secret: the key is public by design and stored in config |

Without credentials, get-found still audits, fixes and tracks everything, and gives you exact click-paths for the dashboard steps.

## Automation

- Ask your agent to schedule a weekly `/get-found check` (and a monthly `report`).
- Or use the GitHub Action template in `skills/get-found/assets/templates/github-action.yml`: after each production deploy it audits, pings IndexNow for changed URLs and commits the ledger.

## Requirements

Python 3.9+ (standard library only for the core). Network access to your site and to the official docs. Optional: Pillow (OG image generator), google-auth + requests (Search Console API).

## What it will not do

- Create accounts, enter passwords, change DNS or click in dashboards — it gives you exact values and steps.
- Deploy without you.
- Use the Google Indexing API for normal pages (it's for job postings, livestreams and Google-approved programs), fake `lastmod`/dates, mass-generate pages, submit to spam directories or invent reviews — all of which violate search-engine spam policies.
- Promise rankings. It removes every technical and structural reason not to be found and keeps a steady content habit; results come from being genuinely useful.

## Why one skill instead of three

Setup, maintenance and content all read and write the same ledger and keyword map, and they'd compete for the same triggers. One skill with five modes keeps a single source of truth; the heavy details load only when a mode needs them.

## Development

```
python3 -m unittest discover -s tests -v     # runs against a local fixture site
claude plugin validate .
```
Releases: bump `version` in `.claude-plugin/plugin.json`, `skills/get-found/SKILL.md` and `VERSION` in `skills/get-found/scripts/_common.py`, add `.github/release-notes/vX.Y.Z.md`, update CHANGELOG.md, and push to `main` — the release workflow runs the tests, creates the `vX.Y.Z` tag and publishes `get-found.zip`.
Facts were verified against official documentation on 2026-11-02. If a vendor changes something and the live refresh catches it in your project, please open an issue or PR with the override and the source link.

## License

MIT © Mohammed Mohsin Khan
