# Titles, descriptions, preview images and social profiles

Verified 2026-11-02 (sources `google-title-links`, `google-snippets`, `ogp`, `facebook-images`, `whatsapp-previews`, `x-cards`, `google-organization`, `google-site-names`).

## Contents
- Writing a real title and description
- Open Graph + X tags (copy-paste block)
- Preview image spec by platform
- Refreshing platform caches
- Social profiles: setup, linking, sameAs

## Writing a real title and description
Title formula: **`<What this page is / primary keyword, in natural words> — <differentiator> | <Brand>`**
- Homepage: what the product/business is + for whom: `Clinic Booking Software for Small Hospitals | Acme Health`.
- Product/feature page: the job it does: `WhatsApp Appointment Reminders for Clinics | Acme Health`.
- Blog post: the promise of the post: `How to Reduce Clinic No-Shows: 8 Tactics That Work | Acme Health`.
- Local: service + place: `Pediatric Clinic in Banjara Hills, Hyderabad | Al Noor Clinic`.
Rules: unique per page; match the h1's meaning; no stuffing, no pipes of keywords, no ALL CAPS; same language as the page; ~50–60 characters displays fully on most devices (heuristic — Google truncates by pixel width and may rewrite titles that don't describe the page).
Description: one or two sentences, unique, says what the reader gets and why this page; ~150–160 characters displays fully (heuristic). Programmatic descriptions are fine for large catalogues if they're specific.

## Open Graph + X tags
```html
<meta property="og:type" content="website">            <!-- article for posts -->
<meta property="og:site_name" content="Brand">
<meta property="og:title" content="Page title without the brand suffix">
<meta property="og:description" content="~1–2 sentences (≈80 chars shows on WhatsApp)">
<meta property="og:url" content="https://example.com/canonical-path">
<meta property="og:image" content="https://example.com/og/page-slug.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="What the image shows">
<meta name="twitter:card" content="summary_large_image">
<!-- optional: twitter:site @brand, twitter:image:alt; X falls back to og:* for title/description/image -->
<!-- posts: <meta property="article:published_time" content="2026-11-02T09:00:00+05:30"> -->
```

## Preview image spec by platform
| Platform | What it needs | Notes |
|---|---|---|
| Facebook / Messenger | ≥ 1200×630 recommended (min 600×315 for large), ~1.91:1, ≤ 8 MB | Cached by image URL — new image needs a new URL. Title locks after ~50 interactions. |
| WhatsApp | og:title, og:description, og:url, og:image; image **< 600 KB**, ≥ 300 px wide, ratio ≤ 4:1; `<head>` within first **300 KB** of HTML | No refresh tool — change the URL (e.g. `?v=2`) to force a new preview. |
| X | `summary_large_image`: 2:1, ≥ 300×157, ≤ 4096×4096, < 5 MB, JPG/PNG/WEBP/GIF (no SVG) | Card validator preview removed; test by pasting in a draft post. Cards show image + domain only. |
| LinkedIn | ~1200×627 commonly cited | Post Inspector refreshes cache. |
| Slack / Discord / Telegram / iMessage | read OG (+ X tags) | Telegram: send the URL to @WebpageBot to refresh. |
| Pinterest | Rich Pins from OG/schema (Article, Product, Recipe) | URL Debugger. |
| Google | og:image is a thumbnail candidate | Use a representative image, not just a logo. |
**Safe default**: one 1200×630 PNG/JPG, < 300 KB, text and logo centred with generous margins (crops to square on some apps), high contrast, readable at phone size. Per-post images for blog posts; a branded default for everything else.

## Refreshing platform caches (after changing titles/images)
- Facebook Sharing Debugger: developers.facebook.com/tools/debug → "Scrape Again".
- LinkedIn Post Inspector: linkedin.com/post-inspector.
- X: paste into a draft post; cache expires on its own.
- Telegram: @WebpageBot. WhatsApp: new URL.
Purge your CDN first, otherwise platforms re-fetch the old version.

## Social profiles: setup, linking, sameAs
1. Decide which platforms matter for the audience (B2B: LinkedIn, X, YouTube; local/consumer: Instagram, Facebook, YouTube, WhatsApp Business; dev tools: GitHub, X). Fewer, active profiles beat many empty ones.
2. Same name, handle (if available), logo, one-line description and website URL on each. Record them in `config.social_profiles` (`{"linkedin": "https://...", ...}`).
3. Link the site → profiles (footer icons with real `<a href>`), and profiles → site (bio/website field).
4. Add the profile URLs to homepage Organization JSON-LD `sameAs` (Google treats it as a hint for entity understanding; it auto-discovers profiles too).
5. Search Console platform properties (Instagram, TikTok, X, YouTube) for social search data.
6. When a blog post ships: share it on the main profiles with the OG preview — previews come from the page's tags, so check them before posting.
