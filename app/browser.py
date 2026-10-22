from app import run_options as run_opts
import asyncio
import logging
from contextlib import asynccontextmanager
from playwright.async_api import async_playwright
from app.config import settings
from app.runtime import browser_installation_status, ensure_resources, cleanup_runtime_temp

log = logging.getLogger(__name__)

class SingleBrowser:
    # A single lock guarantees the old browser is cleaned up before another launches.
    def __init__(self):
        self.lock = asyncio.Lock()
        self.current_page = None
        self.current_context = None
        self.current_task_id = None

    @asynccontextmanager
    async def session(self, proxy: dict | None = None):
        async with self.lock:
            pw = browser = context = page = None
            try:
                status = await asyncio.to_thread(ensure_resources)
                installation = await asyncio.to_thread(browser_installation_status)
                if not installation.get('available'):
                    raise RuntimeError(f"Playwright Chromium unavailable: {installation.get('error', 'unknown installation error')}")
                pw = await async_playwright().start()
                bopts = run_opts.current().get('browser') or {}
                headless = bopts.get('headless') if bopts.get('headless') is not None else settings.browser_headless
                launch_options = {'headless': headless, 'slow_mo': int(bopts.get('slow_mo_ms') or 0), 'args': [
                    "--disable-dev-shm-usage", "--disable-background-networking",
                    "--disable-extensions", "--disable-sync", "--no-first-run",
                    "--no-default-browser-check", "--disable-gpu",
                    "--disable-background-timer-throttling", "--disable-renderer-backgrounding"]}
                if proxy:
                    launch_options['proxy'] = {key: value for key, value in proxy.items() if key in {'server', 'username', 'password'} and value}
                browser = await pw.chromium.launch(**launch_options)
                context_options = {"viewport": {"width": int(bopts.get('viewport_width') or 1280), "height": int(bopts.get('viewport_height') or 800)}, "accept_downloads": True}
                if bopts.get('user_agent'):
                    context_options['user_agent'] = str(bopts['user_agent'])
                context = await browser.new_context(**context_options)
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
                try:
                    await asyncio.to_thread(cleanup_runtime_temp)
                except Exception as exc:
                    log.warning("Runtime temporary cleanup failed: %s", type(exc).__name__)

browser = SingleBrowser()
