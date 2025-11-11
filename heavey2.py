# smart_pipeline.py
"""
Production-minded single-worker crawler:
- Fast-path: aiohttp GET -> parse inline JSON (ld+json / window.__INITIAL_STATE__ / XHR hints)
- API probe: attempt direct JSON endpoint fetch when discovered
- Heavy-path: Crawl4AI AsyncWebCrawler.arun() with robust CrawlerRunConfig and retries
- Per-domain concurrency + global concurrency
- Writes results to out/<domain>/<safe_filename>.html and out/manifest.jsonl
"""

import asyncio
import aiohttp
import re
import json
import time
import hashlib
from pathlib import Path
from urllib.parse import urlparse, urljoin
from collections import defaultdict
import csv
import logging

# crawl4ai imports
from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    CacheMode,
)

# Tenacity for retry backoff (pip install tenacity) - used for HTTP retries
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# -------- CONFIG ----------
OUT_DIR = Path("out2")
OUT_DIR.mkdir(parents=True, exist_ok=True)
MANIFEST_PATH = OUT_DIR / "manifest.jsonl"

GLOBAL_CONCURRENCY = 16            # global concurrency (cheap+heavy together)
HEAVY_CONCURRENCY = 6              # concurrent headless browsers at a time (tune per worker)
PER_DOMAIN_LIMIT = 3               # requests per domain concurrently
HTTP_TIMEOUT = 25                  # seconds for aiohttp GET
USER_AGENTS = [
    # add more realistic UAs
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
]
PROXIES = []                       # set to ["http://user:pass@host:port", ...] if available
# ---------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Domain semaphores
domain_locks = defaultdict(lambda: asyncio.Semaphore(PER_DOMAIN_LIMIT))
global_sem = asyncio.Semaphore(GLOBAL_CONCURRENCY)
heavy_sem = asyncio.Semaphore(HEAVY_CONCURRENCY)

# helpers
def safe_filename_for_url(url: str):
    p = urlparse(url)
    base = (p.netloc + p.path) or p.netloc or "page"
    safe = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in base)[:200]
    short_hash = hashlib.sha1(url.encode()).hexdigest()[:10]
    return f"{safe}_{short_hash}.html"

async def fetch_html(session: aiohttp.ClientSession, url: str):
    headers = {"User-Agent": USER_AGENTS[0]}
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
    proxy = None
    if PROXIES:
        proxy = PROXIES[hash(url) % len(PROXIES)]
    async with session.get(url, headers=headers, timeout=timeout, proxy=proxy) as resp:
        text = await resp.text(errors="ignore")
        return resp.status, text, str(resp.url)

# detect embedded JSON and API endpoints
LD_JSON_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S)
INLINE_JSON_VAR_RE = re.compile(r'(?:(?:window|window\.)?[_A-Za-z0-9]+(?:State|INITIAL_DATA|INITIAL_STATE|__PRELOADED_STATE__) *= *({.*?});)', re.S)
XHR_ENDPOINT_RE = re.compile(r"""(?:(?:fetch\(|axios\.get|axios\(|XMLHttpRequest\().*?['"]([^'"]{10,200})['"])""", re.I)

def extract_ld_json(html):
    out = []
    for m in LD_JSON_RE.finditer(html):
        try:
            payload = json.loads(m.group(1))
            out.append(payload)
        except Exception:
            continue
    return out

def extract_inline_json(html):
    for m in INLINE_JSON_VAR_RE.finditer(html):
        txt = m.group(1)
        try:
            return json.loads(txt)
        except Exception:
            # try to "fix" trailing commas etc. — light attempt
            try:
                txt2 = re.sub(r',\s*([}\]])', r'\1', txt)
                return json.loads(txt2)
            except Exception:
                continue
    return None

def find_xhr_candidates(html, base_url):
    candidates = []
    for m in XHR_ENDPOINT_RE.finditer(html):
        uri = m.group(1)
        # make absolute
        if uri.startswith("/"):
            uri = urljoin(base_url, uri)
        candidates.append(uri)
    return candidates

# Tenacity retry for API calls
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), retry=retry_if_exception_type(Exception))
async def fetch_json_api(session, api_url):
    headers = {"User-Agent": USER_AGENTS[0], "Accept": "application/json, text/javascript, */*; q=0.01"}
    timeout = aiohttp.ClientTimeout(total=15)
    proxy = None
    if PROXIES:
        proxy = PROXIES[hash(api_url) % len(PROXIES)]
    async with session.get(api_url, headers=headers, timeout=timeout, proxy=proxy) as resp:
        txt = await resp.text(errors="ignore")
        try:
            return json.loads(txt)
        except Exception:
            # some endpoints return JSONP — try to strip wrapper
            j = re.sub(r'^[^(]*\(\s*', '', txt)
            j = re.sub(r'\)\s*;?$', '', j)
            return json.loads(j)

# Heavy render via Crawl4AI
async def heavy_render(url, expected_selector=None):
    # run inside heavy_sem to limit browsers
    async with heavy_sem:
        browser_conf = BrowserConfig(headless=True)
        # core run config
        run_conf = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            stream=False,
            wait_for=(f"css:{expected_selector}" if expected_selector else None),
            js_code=[
                # small scroll pattern
                "(()=>{window.scrollTo(0,document.body.scrollHeight/3);return true})()",
                "(()=>{window.scrollTo(0,document.body.scrollHeight);return true})()"
            ],
            page_timeout=90000,
            delay_before_return_html=1.5,
            scan_full_page=True,
            wait_for_images=False,
            scroll_delay=0.2,
        )
        async with AsyncWebCrawler(config=browser_conf) as crawler:
            try:
                res = await crawler.arun(url, config=run_conf)
                # res.html expected
                html = getattr(res, "html", "") or getattr(res, "content", "") or ""
                screenshot = getattr(res, "screenshot", None)
                return res, html, screenshot
            except Exception as e:
                logging.exception("heavy_render error")
                return None, "", None

# Main pipeline per URL
async def process_url(url, session, manifest_fh):
    parsed = urlparse(url)
    domain = parsed.netloc
    async with domain_locks[domain]:
        async with global_sem:
            ts0 = time.time()
            status = {"url": url, "start": ts0}
            try:
                # 1) cheap fetch
                st, html, final_url = await fetch_html(session, url)
                status["http_status"] = st
                status["final_url"] = final_url
                # 2) try extract JSON-LD
                ld = extract_ld_json(html)
                inline = extract_inline_json(html)
                if ld or inline:
                    # fast path success
                    save_path = OUT_DIR / domain
                    save_path.mkdir(parents=True, exist_ok=True)
                    fname = safe_filename_for_url(url)
                    (save_path / fname).write_text(json.dumps({"ld": ld, "inline": inline}, ensure_ascii=False), encoding="utf-8")
                    status.update({"stage": "fast-json", "ok": True, "path": str(save_path / fname), "elapsed": time.time()-ts0})
                    manifest_fh.write(json.dumps(status, default=str) + "\n")
                    manifest_fh.flush()
                    return

                # 3) try to discover XHR endpoints and fetch JSON
                candidates = find_xhr_candidates(html, final_url)
                if candidates:
                    for c in candidates:
                        try:
                            api_json = await fetch_json_api(session, c)
                            if api_json:
                                save_path = OUT_DIR / domain
                                save_path.mkdir(parents=True, exist_ok=True)
                                fname = safe_filename_for_url(url)
                                (save_path / fname).write_text(json.dumps({"api": api_json}, ensure_ascii=False), encoding="utf-8")
                                status.update({"stage": "fast-api", "api": c, "ok": True, "path": str(save_path / fname), "elapsed": time.time()-ts0})
                                manifest_fh.write(json.dumps(status, default=str) + "\n")
                                manifest_fh.flush()
                                return
                        except Exception:
                            continue

                # 4) heavy render fallback
                # pick a generic expected selector heuristic
                expected_selector = ".product, .product-card, .product-item, .search-result, ul > li"
                res, rendered_html, screenshot = await heavy_render(url, expected_selector=expected_selector)
                if res and getattr(res, "success", False) and rendered_html and len(rendered_html) > 2000:
                    save_path = OUT_DIR / domain
                    save_path.mkdir(parents=True, exist_ok=True)
                    fname = safe_filename_for_url(url)
                    (save_path / fname).write_text(rendered_html, encoding="utf-8")
                    # save screenshot if present
                    if screenshot:
                        try:
                            import base64
                            ssb = screenshot
                            if isinstance(ssb, str):
                                ssb = base64.b64decode(ssb)
                            (save_path / (fname.replace(".html", ".png"))).write_bytes(ssb)
                        except Exception:
                            pass
                    status.update({"stage": "heavy", "ok": True, "path": str(save_path / fname), "elapsed": time.time()-ts0})
                    manifest_fh.write(json.dumps(status, default=str) + "\n")
                    manifest_fh.flush()
                    return
                else:
                    # final failure
                    save_path = OUT_DIR / domain
                    save_path.mkdir(parents=True, exist_ok=True)
                    fname = safe_filename_for_url(url)
                    (save_path / ("failed_" + fname)).write_text(html[:4000], encoding="utf-8")
                    status.update({"stage": "failed", "ok": False, "err": "no content", "elapsed": time.time()-ts0})
                    manifest_fh.write(json.dumps(status, default=str) + "\n")
                    manifest_fh.flush()
                    return

            except Exception as e:
                status.update({"stage": "exception", "ok": False, "err": str(e), "elapsed": time.time()-ts0})
                manifest_fh.write(json.dumps(status, default=str) + "\n")
                manifest_fh.flush()
                logging.exception("process_url exception")
                return

async def main(urls):
    connector = aiohttp.TCPConnector(limit=GLOBAL_CONCURRENCY)
    async with aiohttp.ClientSession(connector=connector) as session:
        with open(MANIFEST_PATH, "a", encoding="utf-8") as mf:
            tasks = [process_url(u, session, mf) for u in urls]
            # schedule with concurrency
            await asyncio.gather(*tasks)

if __name__ == "__main__":
    import sys
    # Example usage: python smart_pipeline.py urls.txt
    if len(sys.argv) >= 2 and Path(sys.argv[1]).exists():
        lines = [l.strip() for l in open(sys.argv[1], "r", encoding="utf-8") if l.strip()]
        asyncio.run(main(lines))
    else:
        sample = [
            "https://www.myntra.com/purse?rawQuery=purse"
        ]
        asyncio.run(main(sample))
