# jsMain_fixed_browser_config.py
import asyncio
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
import hashlib
import csv

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    CacheMode,
    __version__ as crawl4ai_version,
)

def make_safe_filename(url: str, max_base_len: int = 150):
    p = urlparse(url)
    base = (p.netloc + p.path) or p.netloc or "page"
    safe_base = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in base)
    short_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    return f"{safe_base[:max_base_len]}_{short_hash}.html"

async def save_many(urls, out_dir="pages", wait_for_selector=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.csv"

    # Build BrowserConfig normally
    browser_conf = BrowserConfig(headless=True)

    # IMPORTANT: pass the browser config as `config=` to AsyncWebCrawler (not browser_config=)
    # This avoids the duplicate-argument error that occurs in some versions.
    run_conf = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        stream=True,
        wait_for=(f"css:{wait_for_selector}" if wait_for_selector else None),
        js_code=[
            "(() => { window.scrollTo(0, document.body.scrollHeight); return true; })();"
        ],
        page_timeout=90_000,
        delay_before_return_html=2.0,
        scan_full_page=True,
        wait_for_images=True,
        scroll_delay=0.2,
    )

    with manifest_path.open("w", newline="", encoding="utf-8") as mf:
        writer = csv.writer(mf)
        writer.writerow(["filename", "url", "success", "error_message", "elapsed_s"])

        # <-- FIX: pass BrowserConfig as `config=` here
        async with AsyncWebCrawler(config=browser_conf) as crawler:
            results_iter = await crawler.arun_many(urls, config=run_conf)
            if hasattr(results_iter, "__aiter__"):
                async for result in results_iter:
                    await _process_result(result, out_dir, writer)
            else:
                for result in results_iter:
                    await _process_result(result, out_dir, writer)

async def _process_result(result, out_dir: Path, writer):
    url = getattr(result, "url", "<unknown>")
    fname = make_safe_filename(url)
    out_path = out_dir / fname
    start = time.time()
    try:
        if getattr(result, "success", False):
            html = getattr(result, "html", None) or getattr(result, "content", "") or ""
            if isinstance(html, (bytes, bytearray)):
                html = html.decode("utf-8", errors="replace")
            out_path.write_text(html, encoding="utf-8")
            elapsed = time.time() - start
            print(f"[OK] {url} -> {out_path} ({elapsed:.2f}s)")
            writer.writerow([fname, url, "OK", "", f"{elapsed:.2f}"])
        else:
            err = getattr(result, "error_message", "unknown error")
            elapsed = time.time() - start
            print(f"[ERR] {url} -> {err}")
            writer.writerow([fname, url, "ERR", err, f"{elapsed:.2f}"])
    except Exception as e:
        elapsed = time.time() - start
        print(f"[FSERR] {url} -> {e!r}")
        writer.writerow([fname, url, "FSERR", str(e), f"{elapsed:.2f}"])

if __name__ == "__main__":
    print("crawl4ai version:", crawl4ai_version)
    urls = sys.argv[1:] or [
        "https://www.meesho.com/search?q=kurthi&searchType=manual&searchIdentifier=text_search"
    ]
    asyncio.run(save_many(urls, wait_for_selector="product-item"))
