# Implementing get-found items per framework

Always prefer the framework's own mechanism: generated sitemaps stay correct as pages change, hand-written ones rot. Framework APIs evolve — before editing, open the framework doc listed for that row (also in `sources.json` under `framework-*`) and follow the current API, not this summary.

## Contents
- Detect the stack
- Next.js (App Router / Pages Router)
- Astro · Nuxt · SvelteKit · Remix / React Router · Gatsby · TanStack Start
- Vite / CRA / Lovable / Bolt single-page apps (the AI-readability trap)
- Static site generators: Hugo · Jekyll · Eleventy · Docusaurus / VitePress / MkDocs
- Hosted builders & CMS: WordPress · Shopify · Webflow · Framer · Wix · Squarespace · Ghost
- Hosting gotchas (Vercel, Netlify, Cloudflare)
- JSON-LD snippet pattern

## Detect the stack

| Signal in repo | Stack |
|---|---|
| `next.config.*`, `app/` or `pages/` | Next.js (App Router if `app/layout.*` exists) |
| `astro.config.*` | Astro |
| `nuxt.config.*` | Nuxt |
| `svelte.config.*` + `src/routes` | SvelteKit |
| `react-router.config.*` / `remix.config.*` / `app/routes` | React Router v7 / Remix |
| `gatsby-config.*` | Gatsby |
| `vite.config.*` + `index.html` with `<div id="root">` and no SSR framework | **client-only SPA** — see the SPA section |
| `hugo.toml`/`config.toml` + `content/` | Hugo |
| `_config.yml` + `_posts/` | Jekyll |
| `.eleventy.js` / `eleventy.config.*` | Eleventy |
| `wp-config.php`, `wp-content/` | WordPress |
| only `.html` files (+ assets) in a `public/`/root folder, no build tool | **plain static site** — see "Plain static HTML" below |
| no repo, hosted builder | Shopify / Webflow / Framer / Wix / Squarespace — changes happen in the builder's settings UI; guide the user step by step |

Find the **public/static dir** (served at site root) for files like `robots.txt`, `llms.txt`, the IndexNow key file: `public/` (Next, Vite, Astro, Nuxt), `static/` (SvelteKit, Hugo, Docusaurus), site root (Jekyll, Eleventy passthrough).

**Monorepos.** Look for the signals above inside each workspace, not only at the repo root: workspaces in `package.json` / `pnpm-workspace.yaml`, or `turbo.json` / `nx.json` / `lerna.json` with `apps/*` and `packages/*`. `state.py detect` lists every web app it finds with its framework and public dir, and flags names like admin/ops/dashboard as internal. When there is more than one, ask the owner which app is the public website, then run `state.py init --app-dir apps/<app>` from the repo root. Keep one `.get-found/` at the repo root (the scripts find it from any subfolder). The public dir is then `apps/<app>/public` (or `static/`), and that is where `robots.txt`, `llms.txt` and the IndexNow key go. Fix the public app only; an internal app on another host should be `noindex` and out of the public sitemap.

## Next.js

**App Router** (doc: nextjs.org/docs/app/api-reference/file-conventions/metadata)
- Sitemap: `app/sitemap.ts` returning `MetadataRoute.Sitemap` (`url`, `lastModified`). Pull URLs + real modified dates from the CMS/MDX frontmatter/DB — never `new Date()` for every URL (that's fake freshness and engines learn to ignore your lastmod). Large sites: `generateSitemaps`.
- Robots: `app/robots.ts` returning `MetadataRoute.Robots` with `rules` and `sitemap`. Put the AI-bot policy here (see ai-visibility.md); remember `rules` can be an array of per-userAgent objects.
- Titles/meta: root `app/layout.tsx` `export const metadata = { metadataBase: new URL(SITE), title: { default: 'Brand — what it does', template: '%s | Brand' }, description, openGraph: {...}, twitter: { card: 'summary_large_image' } }`. Per page `export const metadata` or `generateMetadata()` for dynamic routes. Set `alternates: { canonical: '/path' }`.
- OG images: `opengraph-image.tsx` (and `twitter-image.tsx`) per route segment using `ImageResponse` from `next/og`; or static `opengraph-image.png` files. Default 1200×630.
- JSON-LD: render `<script type="application/ld+json">` in the page/layout server component (pattern at the end).
- llms.txt: `public/llms.txt` or a route handler `app/llms.txt/route.ts` that builds it from content.
- AI readability: Server Components render HTML by default. Red flags: `'use client'` page components that fetch content in `useEffect`; content behind client-only tabs/accordions that never render server-side.

**Pages Router**: `next-sitemap` package (postbuild) or `pages/sitemap.xml.ts` with `getServerSideProps`; `next/head` for meta; `getStaticProps`/`getServerSideProps` so content is in HTML.

## Astro
- Sitemap: `@astrojs/sitemap` integration; requires `site` in `astro.config.*`. Use `serialize` to set `lastmod` from content collection dates.
- Robots/llms/IndexNow key: files in `public/`, or `src/pages/robots.txt.ts` endpoint to embed the sitemap URL dynamically.
- Meta/OG/JSON-LD: one `<SEO>`/`<BaseHead>` component in the layout taking `title`, `description`, `image`, `canonical` props. OG images: `astro-og-canvas` or `satori` endpoints, or static files.
- Static by default → excellent AI readability. Watch islands with `client:only` holding main content.

## Nuxt
- The Nuxt SEO module set (`@nuxtjs/seo`: sitemap, robots, og-image, schema-org, site config) covers most items; or `useSeoMeta()` / `useHead()` per page.
- Ensure `ssr: true` (default) or prerender routes; `ssr: false` SPAs fail AI readability.

## SvelteKit
- `src/routes/sitemap.xml/+server.ts` returning XML; `src/routes/robots.txt/+server.ts` or `static/robots.txt`.
- `<svelte:head>` for title/meta/OG/JSON-LD; `export const prerender = true` for content pages.

## Remix / React Router v7 (framework mode)
- `meta` export per route for title/description/OG; resource routes `app/routes/sitemap[.]xml.tsx`, `robots[.]txt.tsx`.
- `prerender` option in `react-router.config.ts` for static content routes. SPA mode (`ssr: false`) without prerender = client-rendered.

## Gatsby
- `gatsby-plugin-sitemap`, `gatsby-plugin-robots-txt`; Head API (`export const Head`) for meta. Static HTML output is AI-readable.

## TanStack Start
- `head()` per route for meta; server routes for sitemap/robots; enable prerendering for content routes.

## Vite / CRA / Lovable / Bolt / v0 single-page apps — the AI-readability trap
A client-only React/Vue SPA ships `<div id="root"></div>` and builds the page in the browser. Google renders JS (with delay); most AI crawlers do not — they see an empty page and every route shares one `<title>` from `index.html`. The audit flags this as `client-rendered`.
Fix options, best first:
1. Move content pages to a framework with SSR/SSG (Next.js, Astro, React Router framework mode, Nuxt) — the app part can stay client-side.
2. Prerender at build time: React Router `prerender`, `vite-ssg` (Vue), `vite-plugin-ssr`/Vike, or a static-export step that writes real HTML per route (verify the chosen tool is maintained before adopting it).
3. At minimum: a static marketing site (Astro) on the main domain with the app on `app.` subdomain.
Per-route meta via `react-helmet-async` alone does not fix AI crawlers because it still requires JS.

## Plain static HTML (no framework)
- Sitemap: there's no generator, so keep `sitemap.xml` honest by hand **or** add a tiny build step (a script that lists the `.html` files, skips noindex pages, and sets `lastmod` from `git log -1 --format=%cI -- <file>`). Either is fine for a small site; the rule is accuracy, not tooling.
- Titles/meta/OG/JSON-LD live directly in each file's `<head>` — keep a shared snippet and copy carefully; audit after every edit.
- Consider moving to an SSG (Astro, Eleventy, Hugo) once there are more than ~20 pages or a weekly blog — templates prevent drift.

## App pages that aren't meant to rank
Logged-in dashboards, editors and app shells (`/app`, `/dashboard`) don't need to be AI-readable: add `noindex` (meta or `X-Robots-Tag`), keep them out of the sitemap, and don't link them as content. Put the *explanation* of the app on server-rendered marketing pages (features, pricing, docs, blog). The audit's `client-rendered` error is only a problem on pages you want found.

## Static site generators
- **Hugo**: built-in `sitemap.xml`; `enableRobotsTXT = true` + `layouts/robots.txt`; internal templates `opengraph`, `twitter_cards`, `schema` partials; `lastmod` from `enableGitInfo`/front matter.
- **Jekyll**: `jekyll-sitemap`, `jekyll-seo-tag` (title, meta, OG, JSON-LD), `jekyll-feed`.
- **Eleventy**: sitemap via a Nunjucks/Liquid template over `collections.all`; meta in base layout; `eleventy-img` for OG images.
- **Docusaurus / VitePress / MkDocs (Material)**: built-in or first-party sitemap plugins; set `url`/`siteUrl`; per-page `description` front matter; social cards plugins (MkDocs Material `social`).

## Hosted builders & CMS (no repo — guide the user through the UI)
- **WordPress**: core sitemap at `/wp-sitemap.xml` (SEO plugins replace it with their own, e.g. `/sitemap_index.xml`); an SEO plugin (Yoast, Rank Math, AIOSEO, SEOPress) for titles, OG, schema, robots.txt editor; IndexNow via Microsoft's IndexNow plugin or an SEO plugin's IndexNow setting. Check *Settings → Reading → "Discourage search engines"* is off.
- **Shopify**: automatic `/sitemap.xml` and `robots.txt` (customise with `robots.txt.liquid`); titles/descriptions per product/collection/page in admin; theme `theme.liquid` for OG/JSON-LD (most themes emit Product schema); Google & YouTube channel app for Merchant Center.
- **Webflow**: Site settings → SEO (auto sitemap, robots.txt), per-page SEO + Open Graph settings, CMS template fields bound to meta; JSON-LD via page custom code.
- **Framer / Wix / Squarespace / Ghost**: built-in sitemaps and per-page SEO panels; verify robots.txt isn't blocking and per-page titles are unique. Use their Search Console integration/verification fields.

## Hosting gotchas
- **Preview/staging deployments** must not be indexed (noindex header or auth) and must never be the URLs in your sitemap or canonical tags — the audit flags `sitemap-other-host`.
- **Cloudflare** (proxied DNS): check the dashboard's AI-crawler / bot-management settings and any "managed robots.txt" — they can block AI search bots at the edge or rewrite robots.txt regardless of your file. See ai-visibility.md.
- **Trailing slashes / www / http**: pick one canonical form, 301 the rest, use it everywhere (sitemap, canonical, internal links, Search Console property).
- **Cache**: after changing `robots.txt`, OG tags or images, purge the CDN cache before validating.

## JSON-LD snippet pattern

```html
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"Organization","@id":"https://example.com/#org","name":"Brand","url":"https://example.com/",
  "logo":"https://example.com/logo-512.png","sameAs":["https://www.linkedin.com/company/brand","https://x.com/brand"]},
 {"@type":"WebSite","@id":"https://example.com/#website","name":"Brand","url":"https://example.com/","publisher":{"@id":"https://example.com/#org"}}
]}
</script>
```
In React/Next, serialize with `JSON.stringify(data).replace(/</g, '\\u003c')` inside `dangerouslySetInnerHTML` to avoid script-breaking characters. Templates live in `assets/templates/jsonld/`.
