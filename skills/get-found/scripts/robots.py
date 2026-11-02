#!/usr/bin/env python3
"""RFC 9309 robots.txt parser + AI/search bot access matrix.

Usage:
  python3 robots.py https://example.com                 # fetch live robots.txt, print bot matrix
  python3 robots.py --file robots.txt --paths / /blog/  # test a local file
  python3 robots.py https://example.com --json

Matching follows RFC 9309: case-insensitive product-token match (groups with the same
token are merged, "*" is the fallback), longest-path-match wins, Allow wins a tie,
"*" and "$" wildcards, /robots.txt itself is always allowed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import fetch, load_reference  # noqa: E402


class Robots:
    def __init__(self, text: str):
        self.groups: list[tuple[list[str], list[tuple[str, str]]]] = []
        self.sitemaps: list[str] = []
        self.unknown_lines: list[str] = []
        self._parse(text or "")

    def _parse(self, text: str):
        agents: list[str] = []
        rules: list[tuple[str, str]] = []
        last_was_agent = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                if line:
                    self.unknown_lines.append(raw)
                continue
            key, value = line.split(":", 1)
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                if not last_was_agent and agents:
                    self.groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(value.lower())
                last_was_agent = True
            elif key in ("allow", "disallow"):
                if agents:
                    rules.append((key, value))
                last_was_agent = False
            elif key == "sitemap":
                self.sitemaps.append(value)
            else:
                # crawl-delay, content-signal, host, etc. are not part of RFC 9309 matching
                last_was_agent = False if key not in ("content-signal",) else last_was_agent
        if agents:
            self.groups.append((agents, rules))

    def rules_for(self, token: str) -> list[tuple[str, str]] | None:
        token = token.lower()
        matched = [r for a, r in self.groups if token in a]
        if not matched:
            matched = [r for a, r in self.groups if "*" in a]
        if not matched:
            return None
        merged = []
        for r in matched:
            merged.extend(r)
        return merged

    def has_specific_group(self, token: str) -> bool:
        return any(token.lower() in a for a, _ in self.groups)

    @staticmethod
    def _pattern_matches(pattern: str, path: str) -> bool:
        if pattern == "":
            return False
        anchored = pattern.endswith("$")
        if anchored:
            pattern = pattern[:-1]
        regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in pattern)
        regex = "^" + regex + ("$" if anchored else "")
        return re.match(regex, path) is not None

    def allowed(self, token: str, url_or_path: str) -> tuple[bool, str]:
        """Return (allowed, matching_rule_description)."""
        p = urllib.parse.urlsplit(url_or_path)
        path = (p.path or "/") + (("?" + p.query) if p.query else "")
        path = urllib.parse.unquote(path)
        if path == "/robots.txt":
            return True, "robots.txt is always allowed"
        rules = self.rules_for(token)
        if rules is None:
            return True, "no matching group"
        best = None  # (length, is_allow, rule)
        for kind, pattern in rules:
            pat = urllib.parse.unquote(pattern)
            if not self._pattern_matches(pat, path):
                continue
            length = len(pat.rstrip("$"))
            cand = (length, kind == "allow", f"{kind}: {pattern}")
            if best is None or cand[0] > best[0] or (cand[0] == best[0] and cand[1] and not best[1]):
                best = cand
        if best is None:
            return True, "no matching rule"
        return best[1], best[2]


def bot_matrix(robots: Robots, bots: list[dict], paths: list[str]) -> list[dict]:
    rows = []
    for bot in bots:
        token = bot["token"]
        results = {}
        for path in paths:
            ok, why = robots.allowed(token, path)
            results[path] = {"allowed": ok, "rule": why}
        rows.append({
            "token": token,
            "note": bot.get("note", ""),
            "vendor": bot.get("vendor", ""),
            "category": bot.get("category", ""),
            "specific_group": robots.has_specific_group(token),
            "results": results,
            "all_allowed": all(r["allowed"] for r in results.values()),
            "all_blocked": not any(r["allowed"] for r in results.values()),
        })
    return rows


def verdict(rows: list[dict]) -> list[dict]:
    """Translate the matrix into issues. Blocking search/user bots hides you from AI answers."""
    issues = []
    for r in rows:
        cat = r["category"]
        if r["all_allowed"]:
            continue
        blocked_paths = [p for p, v in r["results"].items() if not v["allowed"]]
        if cat in ("search", "search-engine", "user"):
            sev = "error" if r["all_blocked"] or "/" in blocked_paths else "warn"
            issues.append({"severity": sev, "code": "bot-blocked", "token": r["token"],
                           "message": f"{r['token']} ({r['vendor']}, {cat}) is blocked for {', '.join(blocked_paths)} — "
                                      f"this removes the site from that engine's answers/results."})
        elif cat == "training":
            issues.append({"severity": "info", "code": "training-bot-blocked", "token": r["token"],
                           "message": f"{r['token']} ({r['vendor']}) is blocked — a valid training opt-out; does not affect search visibility."
                                      + (f" Note: {r['note']}" if r.get("note") else "")})
        elif cat == "preview":
            issues.append({"severity": "error", "code": "preview-bot-blocked", "token": r["token"],
                           "message": f"{r['token']} is blocked — link previews on that platform will break."})
        else:
            issues.append({"severity": "info", "code": "bot-blocked", "token": r["token"],
                           "message": f"{r['token']} ({cat}) is blocked for {', '.join(blocked_paths)}."})
    return issues


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("site", nargs="?", help="site origin, e.g. https://example.com")
    ap.add_argument("--file", help="local robots.txt to test instead of fetching")
    ap.add_argument("--paths", nargs="*", default=["/"], help="paths to test (default: /)")
    ap.add_argument("--state", default=".get-found", help="project state dir (for ai-bots.override.json)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    elif args.site:
        r = fetch(urllib.parse.urljoin(args.site, "/robots.txt"))
        if r.error:
            print(f"could not fetch robots.txt: {r.error}", file=sys.stderr)
            sys.exit(1)
        text = r.text() if r.ok else ""
        if not r.ok:
            print(f"robots.txt returned HTTP {r.status} — RFC 9309 treats 4xx as 'allow all', 5xx as 'disallow all'.", file=sys.stderr)
    else:
        ap.error("give a site URL or --file")

    reg = load_reference("ai-bots.json", Path(args.state))
    robots = Robots(text)
    rows = bot_matrix(robots, reg.get("bots", []), args.paths)
    issues = verdict(rows)
    if args.json:
        print(json.dumps({"sitemaps": robots.sitemaps, "matrix": rows, "issues": issues}, indent=2))
        return
    print(f"Sitemap lines: {robots.sitemaps or 'none'}")
    w = max(len(r["token"]) for r in rows) if rows else 10
    for r in rows:
        mark = "ALLOW" if r["all_allowed"] else ("BLOCK" if r["all_blocked"] else "PART ")
        print(f"  {mark}  {r['token']:<{w}}  {r['category']:<14} {r['vendor']}")
    for i in issues:
        print(f"[{i['severity']}] {i['message']}")


if __name__ == "__main__":
    main()
