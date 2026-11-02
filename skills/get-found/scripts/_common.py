"""Shared helpers for get-found scripts. Standard library only (Python 3.9+)."""
from __future__ import annotations

import datetime as _dt
import gzip
import hashlib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

VERSION = "1.0.0"
SKILL_DIR = Path(__file__).resolve().parent.parent
REFERENCES = SKILL_DIR / "references"
USER_AGENT = f"Mozilla/5.0 (compatible; get-found-audit/{VERSION}; +https://github.com/MohsinMMK/get-found)"


# --------------------------------------------------------------------------- time / io

def now_iso(precise: bool = False) -> str:
    """UTC timestamp. precise=True keeps microseconds (used for change/submission ordering)."""
    d = _dt.datetime.now(_dt.timezone.utc)
    return (d if precise else d.replace(microsecond=0)).isoformat()


def today() -> _dt.date:
    return _dt.datetime.now(_dt.timezone.utc).date()


def parse_date(value) -> _dt.datetime | None:
    """Parse ISO-8601 / W3C datetime strings leniently. Returns aware UTC datetime or None."""
    if not value or not isinstance(value, str):
        return None
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    for candidate in (v, v[:10]):
        try:
            d = _dt.datetime.fromisoformat(candidate)
            if d.tzinfo is None:
                d = d.replace(tzinfo=_dt.timezone.utc)
            return d.astimezone(_dt.timezone.utc)
        except ValueError:
            continue
    return None


def load_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        die(f"{path} is not valid JSON: {exc}")


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)


def die(msg: str, code: int = 2):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def load_reference(name: str, state_dir: Path | None = None):
    """Load a JSON reference shipped with the skill, merged with an optional project override.

    Override file: <state_dir>/<stem>.override.json. Dicts merge recursively; lists in the
    override replace the baseline list unless the override uses {"append": [...]}.
    This is how live-doc deltas are applied without editing the skill itself.
    """
    base = load_json(REFERENCES / name, default={}) or {}
    if state_dir:
        override = load_json(Path(state_dir) / (Path(name).stem + ".override.json"), default=None)
        if override:
            base = _merge(base, override)
    return base


def _merge(base, over):
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = _merge(base.get(k), v) if k in base else v
        return out
    if isinstance(base, list) and isinstance(over, dict) and "append" in over:
        return base + list(over["append"])
    return over


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


# --------------------------------------------------------------------------- http

@dataclass
class Response:
    url: str
    status: int = 0
    final_url: str = ""
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    error: str = ""
    redirects: list = field(default_factory=list)
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def text(self) -> str:
        ctype = self.headers.get("content-type", "")
        m = re.search(r"charset=([\w-]+)", ctype, re.I)
        enc = m.group(1) if m else "utf-8"
        try:
            return self.body.decode(enc, "replace")
        except LookupError:
            return self.body.decode("utf-8", "replace")


class _RecordingRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        self.chain = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.chain.append((code, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _ssl_context():
    ctx = ssl.create_default_context()
    bundle = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if bundle and os.path.exists(bundle):
        ctx.load_verify_locations(bundle)
    return ctx


def fetch(url: str, method: str = "GET", headers: dict | None = None, data: bytes | None = None,
          timeout: float = 20.0, max_bytes: int = 6_000_000, user_agent: str | None = None,
          follow_redirects: bool = True, range_bytes: int | None = None) -> Response:
    hdrs = {"User-Agent": user_agent or USER_AGENT, "Accept": "*/*", "Accept-Encoding": "gzip"}
    if range_bytes:
        hdrs["Range"] = f"bytes=0-{range_bytes - 1}"
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    rec = _RecordingRedirect()
    handlers = [urllib.request.HTTPSHandler(context=_ssl_context()), rec]
    if not follow_redirects:
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        handlers = [urllib.request.HTTPSHandler(context=_ssl_context()), _NoRedirect()]
    opener = urllib.request.build_opener(*handlers)
    resp = Response(url=url)
    t0 = time.monotonic()
    try:
        with opener.open(req, timeout=timeout) as r:
            resp.status = r.status
            resp.final_url = r.geturl()
            resp.headers = {k.lower(): v for k, v in r.headers.items()}
            body = r.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        resp.status = e.code
        resp.final_url = e.geturl() or url
        resp.headers = {k.lower(): v for k, v in (e.headers or {}).items()}
        try:
            body = e.read(max_bytes + 1)
        except Exception:
            body = b""
    except Exception as e:  # network, DNS, TLS, timeout
        resp.error = f"{type(e).__name__}: {e}"
        resp.elapsed_ms = int((time.monotonic() - t0) * 1000)
        return resp
    resp.elapsed_ms = int((time.monotonic() - t0) * 1000)
    if resp.headers.get("content-encoding", "").lower() == "gzip" or body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except Exception:
            pass
    resp.body = body[:max_bytes]
    resp.redirects = rec.chain
    return resp


# --------------------------------------------------------------------------- urls

def normalize_url(url: str, base: str | None = None) -> str | None:
    if base:
        url = urllib.parse.urljoin(base, url)
    p = urllib.parse.urlsplit(url.strip())
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    path = p.path or "/"
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), path, p.query, ""))


def site_key(host: str) -> str:
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def same_site(url: str, root: str) -> bool:
    return site_key(urllib.parse.urlsplit(url).netloc) == site_key(urllib.parse.urlsplit(root).netloc)


def path_of(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return (p.path or "/") + (("?" + p.query) if p.query else "")


# --------------------------------------------------------------------------- html

_SKIP_TEXT = {"script", "style", "noscript", "template", "svg", "head", "iframe", "canvas"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class PageParser(HTMLParser):
    """Collects the SEO-relevant parts of an HTML document in one pass."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lang = ""
        self.title = None
        self._in_title = False
        self.metas = []          # list of dicts of attrs
        self.links = []          # <link> attrs
        self.anchors = []        # href values from <a>
        self.jsonld_raw = []
        self._in_jsonld = False
        self._jsonld_buf = []
        self.headings = []       # (level, text)
        self._heading = None
        self._heading_buf = []
        self.text_parts = []
        self._skip_depth = 0
        self.images = 0
        self.images_no_alt = 0
        self.scripts = 0
        self.has_noscript_warning = False
        self._in_noscript = False
        self.root_containers = []  # ids of typical SPA mount points

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "title" and self.title is None:
            self._in_title = True
            self.title = ""
        elif tag == "meta":
            self.metas.append(a)
        elif tag == "link":
            self.links.append(a)
        elif tag == "a" and a.get("href"):
            self.anchors.append(a["href"])
        elif tag == "img":
            self.images += 1
            if "alt" not in a:
                self.images_no_alt += 1
        elif tag == "script":
            self.scripts += 1
            if "ld+json" in a.get("type", "").lower():
                self._in_jsonld = True
                self._jsonld_buf = []
        elif tag in ("h1", "h2", "h3"):
            self._heading = int(tag[1])
            self._heading_buf = []
        elif tag == "noscript":
            self._in_noscript = True
        if tag in ("div", "main", "app-root") and a.get("id") in ("root", "app", "__next", "__nuxt", "svelte", "___gatsby") or tag == "app-root":
            self.root_containers.append(a.get("id") or tag)
        if tag in _SKIP_TEXT and tag not in _VOID:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._in_jsonld:
            self.jsonld_raw.append("".join(self._jsonld_buf))
            self._in_jsonld = False
        elif tag in ("h1", "h2", "h3") and self._heading:
            self.headings.append((self._heading, " ".join("".join(self._heading_buf).split())))
            self._heading = None
        elif tag == "noscript":
            self._in_noscript = False
        if tag in _SKIP_TEXT and tag not in _VOID and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_jsonld:
            self._jsonld_buf.append(data)
            return
        if self._in_noscript and re.search(r"enable javascript|requires javascript|need to enable", data, re.I):
            self.has_noscript_warning = True
        if self._heading:
            self._heading_buf.append(data)
        if self._skip_depth == 0:
            self.text_parts.append(data)

    # convenience accessors -------------------------------------------------
    def meta(self, key: str) -> str | None:
        key = key.lower()
        for m in self.metas:
            if m.get("name", "").lower() == key or m.get("property", "").lower() == key:
                return m.get("content", "").strip()
        return None

    def link_rel(self, rel: str) -> list:
        rel = rel.lower()
        return [l for l in self.links if rel in l.get("rel", "").lower().split()]

    def visible_text(self) -> str:
        return " ".join(" ".join(self.text_parts).split())

    def jsonld(self) -> tuple[list, list]:
        """Return (objects, errors). Flattens @graph and lists."""
        objs, errs = [], []
        for raw in self.jsonld_raw:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                errs.append(str(e))
                continue
            stack = data if isinstance(data, list) else [data]
            while stack:
                item = stack.pop(0)
                if isinstance(item, dict):
                    if "@graph" in item and isinstance(item["@graph"], list):
                        stack.extend(item["@graph"])
                    objs.append(item)
                elif isinstance(item, list):
                    stack.extend(item)
        return objs, errs


def parse_html(html: str) -> PageParser:
    p = PageParser()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    return p


def jsonld_types(objs: list) -> list:
    types = []
    for o in objs:
        t = o.get("@type")
        if isinstance(t, list):
            types.extend(str(x) for x in t)
        elif t:
            types.append(str(t))
    return sorted(set(types))


# --------------------------------------------------------------------------- images

def image_size(data: bytes):
    """Return (width, height, format) from the first bytes of PNG/JPEG/GIF/WebP, else (None, None, None)."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"), "png"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little"), "gif"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            chunk = data[12:16]
            if chunk == b"VP8X":
                return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little"), "webp"
            if chunk == b"VP8 ":
                return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF, "webp"
            if chunk == b"VP8L":
                b = data[21:25]
                w = 1 + (((b[1] & 0x3F) << 8) | b[0])
                h = 1 + (((b[3] & 0xF) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
                return w, h, "webp"
        if data[:2] == b"\xff\xd8":
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h = int.from_bytes(data[i + 5:i + 7], "big")
                    w = int.from_bytes(data[i + 7:i + 9], "big")
                    return w, h, "jpeg"
                seg_len = int.from_bytes(data[i + 2:i + 4], "big")
                i += 2 + seg_len
    except Exception:
        pass
    return None, None, None
