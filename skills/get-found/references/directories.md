# Directories and listings ("10 directories")

## Contents
- What counts as a good listing
- Choosing the 10 (by site type and market)
- The listing kit (prepare once, reuse everywhere)
- Workflow and tracking
- Keeping listings fresh

## What counts as a good listing
Good: real users browse it, it has editorial review or verification, it ranks for your category or brand, and it describes your entity consistently (name, URL, category, location). Bad: "submit to 500 directories" services, link farms, sites that require a reciprocal link, pages with no traffic. Google's spam policies list low-quality directory links and paid links for ranking as link spam; paid placements are fine only if the link is `rel="sponsored"`/`nofollow`. The point of a listing is discovery, trust and consistent entity data — not link juice.

## Choosing the 10
`state.py init` seeds `.get-found/listings.json` from `directories.json`, filtered by `--type`, `--country`, `--vertical`. Start with **core**, then **high**. Open each submit page before recommending it — owners, pricing and rules change (e.g. G2 bought Capterra/GetApp/Software Advice in 2026; several AI-tool directories are now paid).
- **Local business**: Google Business Profile, Bing Places (bing.com/forbusiness, import from GBP), Apple Business, Facebook Page, plus national directories (India: Justdial, Sulekha, IndiaMART for B2B; vertical: Practo for clinics, 99acres/MagicBricks/Housing.com for real estate; US/UK/AU: Yelp) and the main industry association/chamber listing.
- **SaaS / AI tool / app**: Product Hunt (one well-prepared launch), G2, Capterra, AlternativeTo, SaaSHub, Show HN (only if usable without signup), Crunchbase, relevant GitHub awesome-lists, app stores; paid AI directories only if budget allows (mark links sponsored).
- **E-commerce**: Google Merchant Center free listings, Microsoft Merchant Center, Trustpilot (if you'll collect reviews), Pinterest business + Rich Pins, marketplace storefronts you actually sell on.
- **Agency / services**: Clutch-style review platforms relevant to the market, LinkedIn Company Page, Google Business Profile if there's an office, partner directories of tools you're certified in (e.g. a platform's partner/experts directory).
Add any listing not in the seed with `state.py listing <id> --name "..." --status todo`.

## The listing kit (save to `.get-found/listing-kit.md`)
- Official name (exact spelling/casing), legal name if different
- Website URL (canonical, https), logo (square PNG ≥ 512px) + 1200×630 cover
- One-liner (≤ 60 chars), short description (≤ 160 chars), long description (≈ 750 chars) — factual, no superlatives
- Category / tags; pricing summary; founding year; location
- Local: NAP (name, address, phone) **identical everywhere**, hours, service area, photos
- Social profile URLs; contact email for listings (shared inbox)
- 3–5 screenshots (SaaS) or product photos (e-commerce)
The agent drafts this from the site and config; the owner approves it once.

## Workflow and tracking
1. Agent: pick next listings from `state.py listings`, prepare tailored copy from the kit, and give the user the submit URL + exact fields. Agents must not create accounts or enter passwords — the user submits.
2. User submits → `state.py listing <id> --status submitted`.
3. When live → `state.py listing <id> --status live --url <listing url>` (the ledger's `directories` item turns pass at the target count).
4. Add live profile-like listings (LinkedIn, Facebook, Crunchbase, G2, Product Hunt) to `sameAs` if they represent the organisation.

## Keeping listings fresh
Quarterly (or on rebrand/new address/new pricing): open each live listing, fix drift, reply to reviews, update screenshots. Note changes in history (`state.py log`).
