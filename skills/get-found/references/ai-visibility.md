# AI visibility: bots, robots.txt policy, CDN traps, readable pages, citations

Verified 2026-11-02 against vendor docs (sources `openai-bots`, `anthropic-crawlers`, `perplexity-crawlers`, `google-common-crawlers`, `apple-applebot`, `meta-crawlers`, `amazon-bots`, `mistral-bots`, `duckduckgo-assistbot`, `cloudflare-*`, `ietf-aipref-*`). The live list of tokens is `references/ai-bots.json` + `.get-found/ai-bots.override.json`.

## Contents
- Three kinds of AI bots (and what blocking each costs)
- Recommended robots.txt policy + templates
- New opt-out signals (Content-Signal, __usage_ai-training, IETF aipref)
- CDN / WAF traps (Cloudflare especially)
- JavaScript and "pages AI can read"
- llms.txt — honest guidance
- Writing so answer engines cite you
- Verifying real bot traffic

## Three kinds of AI bots
| Kind | Examples | Block it and… |
|---|---|---|
| **Search / index** | OAI-SearchBot (ChatGPT search), Claude-SearchBot, PerplexityBot, Amzn-SearchBot, meta-webindexer, MistralAI-Index, DuckAssistBot, YouBot — plus Googlebot, Bingbot, Applebot (classic search that also feeds AI features) | you disappear from that engine's answers and citations |
| **User-triggered fetch** | ChatGPT-User, Claude-User, Perplexity-User, MistralAI-User, Amzn-User, meta-externalfetcher, Google-Agent | the assistant can't open your page when a user asks it to (some of these ignore robots.txt anyway) |
| **Training** | GPTBot, ClaudeBot, Google-Extended (token), Applebot-Extended (token), meta-externalagent, Amazonbot, CCBot, Bytespider, MistralAI-Training, cohere-training-data-crawler | your content isn't used to train future models — search visibility is unaffected per OpenAI, Anthropic, Google, Apple, Amazon, DuckDuckGo |

Vendor statements: OpenAI — settings for its bots are independent (allow OAI-SearchBot, disallow GPTBot is valid). Anthropic — blocking Claude-SearchBot stops search indexing; blocking ClaudeBot only excludes training. Google — Google-Extended doesn't affect inclusion in Google Search (but it does govern grounding in Gemini Apps/Vertex, so blocking it costs Gemini-app citations). Apple — Applebot-Extended opt-out keeps pages in Siri/Spotlight search.

Business judgement (say this to the owner, don't decide for them): blocking training is legitimate; it may slightly reduce what models "know" about the brand without live retrieval. Record the decision in `config.ai_training_policy` (`allow` | `block`).

## Recommended robots.txt policy
Default for get-found (`ai_training_policy: allow`) — `assets/templates/robots-allow-all.txt`:
```
User-agent: *
Allow: /
Disallow: /api/
Disallow: /admin/

Sitemap: https://example.com/sitemap.xml
```
With training opt-out (`ai_training_policy: block`) — `assets/templates/robots-block-training.txt` adds explicit groups for each training token with `Disallow: /`, while search/user/preview bots fall under `*`.
Rules of thumb:
- A bot that has its own group ignores the `*` group entirely — if you add `User-agent: OAI-SearchBot` for some reason, repeat the allows/disallows it needs.
- Don't copy community "block all AI" lists (e.g. ai.robots.txt) wholesale: they include search bots, Applebot and `facebookexternalhit` (breaks WhatsApp/Facebook link previews).
- robots.txt is public and not access control; private content needs auth.
- After editing, run `robots.py https://site --paths / /blog/` and the audit.

## New opt-out signals (status 2026-11)
- **Content-Signal** (Cloudflare's Content Signals Policy, CC0): inside a group, e.g. `Content-Signal: search=yes, ai-input=yes, ai-train=no`. `search` = indexing & showing links/excerpts; `ai-input` = RAG/grounding; `ai-train` = training. Cloudflare's managed robots.txt now emits `Content-signal: search=yes, ai-train=no, use=reference`. It's a stated preference, not enforced by crawlers; Search Console may show "syntax not understood" (harmless). For get-found: if present, ensure `search=yes` and `ai-input=yes`.
- **`__usage_ai-training`** (IETF individual draft, Oct 2026): `User-Agent: __usage_ai-training` + `Disallow: /` is a shared training opt-out token across vendors; `*` does not match it. Google's Search Central blog (2026-10-30) says it aims to support it in 2027 and plans to treat it as a Google-Extended disallow when Google-Extended isn't set explicitly; other vendors have signalled support (verify each on its own docs). Offer it as the simple training opt-out once rules refresh confirms vendor support.
- **IETF aipref** (`draft-ietf-aipref-vocab`, `draft-ietf-aipref-attach`): `Content-Usage:` lines in robots.txt / HTTP header — still drafts, not standards. Watch only.

## CDN / WAF traps
robots.txt can say "allow" while the edge blocks the bot. Check these whenever the site is behind a CDN:
**Cloudflare** (biggest risk):
1. Security → *AI bot policies* (Search / Agent / Training, each Block / Block on pages with ads / Allow; launched July 2026, replacing the old "Block AI bots" toggle, which was deprecated on 2026-09-15 when the new defaults took effect). New domains default to blocking Training and Agent on pages with ads. Cloudflare's docs say crawlers that serve **both search and training** are blocked by any setting that blocks training (the robots.txt doc describes an exception for Training = "Disallow" with Bot Preference Sync). So a training opt-out at the edge can cost search visibility: before blocking Training, check in AI Crawl Control which of your important search crawlers are classed as mixed-use. Set Search = Allow, Agent ≠ Block.
2. AI Crawl Control → crawler list: each search and user bot's action should be Allow; look for failed requests.
3. AI Crawl Control → Directives: when Bot Preference Sync is on and a policy blocks/disallows, the managed robots.txt is **prepended** to your own file and disallows GPTBot, ClaudeBot, CCBot, Google-Extended, Applebot-Extended, meta-externalagent, Amazonbot, Bytespider — fine for a training opt-out, wrong if the owner chose `allow`.
4. Bot Fight Mode can't be bypassed by WAF skip rules — if enabled, verified AI search bots may be challenged.
5. WAF custom rules: add a Skip rule for verified search/user bots (UA **and** vendor IP list) above block rules.
6. Security Events: filter by those user-agents for 403/challenges.
Other CDNs/hosts (Vercel Firewall/Bot protection, Netlify, Akamai, Fastly, AWS WAF, Sucuri, Wordfence): same idea — find the bot-management setting, allow verified search/user AI bots.
The audit's `--probe-bots` flag requests the homepage with AI user-agents; a 403 there is a hint only (spoofed UAs from the wrong IP can be blocked legitimately). Confirm in the dashboard.

## JavaScript and "pages AI can read"
- GPTBot, ClaudeBot, PerplexityBot, CCBot don't execute JS (Vercel/MERJ crawl study; Common Crawl docs); ChatGPT-User/Claude-User fetches also didn't in independent tests. Googlebot and Applebot render.
- So: SSR/SSG/ISR or prerender; content, prices, FAQs and JSON-LD in initial HTML; real `<a href>` links; no key answers only inside tabs/accordions that need JS, images or PDFs.
- Quick test: `curl -sL https://site/page | sed -n '1,80p'` — readable text should appear.

## llms.txt — honest guidance
- Format (llmstxt.org): `# Site name`, `> one-line summary`, optional prose, `## Section` lists of `[Title](url): note`, optional `## Optional` section.
- Google Search ignores it (stated in its AI optimization guide). Studies in 2026 found ~1 in 4 sites have one but almost none get requests from AI crawlers; coding agents/IDEs read them more.
- get-found policy: optional, low-cost. If kept, generate it from the sitemap's key pages, keep it truthful and short, no instructions to models, regenerate on sync. Never prioritise it over readable HTML. `assets/templates/llms.txt`.

## Writing so answer engines cite you
From Microsoft's guidance on inclusion in AI answers (2025), Google's AI optimization guide (2026) and Bing's guidelines:
- Align title, h1 and meta description on the same topic.
- Specific, descriptive h2/h3 headings (often the question the user asks); short self-contained answer (1–2 sentences) right under it, then detail.
- Lists and tables for comparisons/steps; facts with numbers, sources and dates; original data, first-hand experience, real examples ("non-commodity" content).
- One topic per page; consolidate near-duplicates with canonicals/redirects.
- Fresh where freshness matters; accurate `lastmod` + IndexNow.
- Don't: chunk pages for AI, create pages for every query variant, hide answers in tabs/images/PDFs, stuff keywords, fabricate mentions or reviews (Google's spam policies now explicitly cover manipulating AI responses).
- Entity clarity: Organization JSON-LD with sameAs, consistent name everywhere, About page with who/where/since, author pages for bylines.

## Verifying real bot traffic
- Server/CDN logs: count hits by token; verify against vendor IP JSON lists (openai.com/searchbot.json, gptbot.json, chatgpt-user.json; claude.com/crawling/bots.json; perplexity.ai/perplexitybot.json; Google common-crawlers.json; search.developer.apple.com/applebot.json; duckduckgo.com/duckassistbot.json; Common Crawl ccbot.json) or reverse DNS (googlebot.com, applebot.apple.com).
- Watch 404 rates from AI bots (they often request stale URLs — add redirects).
- Referrals: see measurement.md.
