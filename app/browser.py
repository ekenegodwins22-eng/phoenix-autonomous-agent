import asyncio
import logging
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright
from app.config import settings

log = logging.getLogger(__name__)

class SingleBrowser:
    # A single lock guarantees the old browser is cleaned up before another launches.
    def __init__(self):
        self.lock = asyncio.Lock()
        self.current_page = None
        self.current_context = None
        self.current_task_id = None

    @asynccontextmanager
    async def session(self):
        async with self.lock:
            pw = browser = context = page = None
            try:
                pw = await async_playwright().start()
                browser = await pw.chromium.launch(headless=settings.browser_headless, args=[
                    "--disable-dev-shm-usage", "--disable-background-networking",
                    "--disable-extensions", "--disable-sync", "--no-first-run",
                    "--no-default-browser-check", "--disable-gpu",
                    "--disable-background-timer-throttling", "--disable-renderer-backgrounding"
                ])
                context = await browser.new_context(viewport={"width": 1280, "height": 800}, accept_downloads=True)
                page = await context.new_page()
                self.current_page = page
                self.current_context = context
                async def dismiss_dialog(dialog):
                    log.info("Dismissing browser dialog of type %s", dialog.type)
                    await dialog.dismiss()
                page.on("dialog", dismiss_dialog)
                page.set_default_timeout(settings.browser_action_timeout_ms)
                page.set_default_navigation_timeout(settings.browser_navigation_timeout_ms)
                yield page
            finally:
                self.current_page = None
                self.current_context = None
                self.current_task_id = None
                for obj in (page, context, browser):
                    if obj is not None:
                        try:
                            await asyncio.wait_for(obj.close(), timeout=settings.browser_close_timeout_seconds)
                        except Exception as exc:
                            log.warning("Browser cleanup failed: %s", type(exc).__name__)
                if pw is not None:
                    try:
                        await asyncio.wait_for(pw.stop(), timeout=settings.browser_close_timeout_seconds)
                    except Exception as exc:
                        log.warning("Playwright shutdown failed: %s", type(exc).__name__)

browser = SingleBrowser()
