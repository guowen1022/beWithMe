from playwright.async_api import BrowserContext, Page, Error as PlaywrightError, TimeoutError as PlaywrightTimeout
import trafilatura


class WebFetchError(Exception):
    """Raised when a URL cannot be fetched or readable text cannot be extracted."""


async def fetch_readable(url: str, context: BrowserContext, keep_open: bool = False) -> tuple[str, str, Page | None]:
    """Open `url` in the shared persistent Chromium context, extract readable text.

    Follows the gstack /browse pattern: one long-lived BrowserContext with an
    on-disk user-data-dir, default Playwright args (no stealth, no UA spoofing),
    one fresh Page per request. Cookies and profile state accumulate naturally
    across requests and restarts, which is what gets us past sites like 36kr
    that rate-limit by fingerprint / profile freshness.

    Returns (title, text, page_or_none). When keep_open=True the Page stays alive
    for browsing / selection detection; caller is responsible for closing it.
    Raises WebFetchError on timeout, navigation failure, captcha wall, or empty extraction.
    """
    page = await context.new_page()
    try:
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2500)
        except PlaywrightTimeout:
            raise WebFetchError("Timed out loading URL (20s).")
        except PlaywrightError as e:
            msg = getattr(e, "message", None) or str(e)
            raise WebFetchError(f"Failed to load URL: {msg}")

        if response is None:
            raise WebFetchError("Failed to load URL (no response).")
        if not response.ok:
            raise WebFetchError(f"Failed to load URL (HTTP {response.status}).")

        html = await page.content()
        page_title = (await page.title()) or ""

        # Captcha-wall heuristic: some sites serve a tiny JS shell with a verify
        # iframe even on a 200 response.
        if len(html) < 5000 and not page_title.strip():
            html_lower = html.lower()
            if any(k in html_lower for k in ("captcha", "verify", "安全验证", "ttgcaptcha")):
                raise WebFetchError(
                    "This site blocks automated access (anti-bot captcha). "
                    "Paste the article text manually instead."
                )

        # output_format="markdown" emits blank lines between paragraphs, which
        # chunk_text() splits on (the default "txt" format only uses single \n
        # so everything collapses into one oversize chunk).
        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            output_format="markdown",
        )
        if not text or not text.strip():
            try:
                text = await page.inner_text("body")
                # inner_text also uses single \n per line — normalize.
                if text:
                    text = "\n\n".join(line for line in text.splitlines() if line.strip())
            except PlaywrightError:
                text = None

        if not text or not text.strip():
            raise WebFetchError("Could not extract readable text from this page.")

        title = page_title.strip() or url
        return title, text.strip(), page if keep_open else None
    except:
        await page.close()
        raise
    else:
        if not keep_open:
            await page.close()
