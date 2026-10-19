"""Safe runtime diagnostics and maintenance for the Phoenix container."""
from __future__ import annotations
import argparse
import asyncio
import os
import shutil
import time
from pathlib import Path

from app.config import settings


def _memory_status() -> dict:
    try:
        values = {}
        for line in Path('/proc/meminfo').read_text().splitlines():
            key, value = line.split(':', 1)
            values[key] = int(value.strip().split()[0]) * 1024
        total = values.get('MemTotal')
        available = values.get('MemAvailable', values.get('MemFree'))
        return {'total_bytes': total, 'available_bytes': available, 'used_percent': round((1 - available / total) * 100, 2) if total and available is not None else None}
    except (OSError, ValueError, ZeroDivisionError):
        return {'total_bytes': None, 'available_bytes': None, 'used_percent': None}


def _browser_process_count() -> int:
    count = 0
    proc = Path('/proc')
    try:
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                command = (entry / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='ignore').lower()
            except OSError:
                continue
            if any(name in command for name in ('chromium', 'chrome-headless', 'playwright')):
                count += 1
    except OSError:
        pass
    return count


def resource_status() -> dict:
    root = Path(settings.resource_disk_path)
    try:
        usage = shutil.disk_usage(root)
        disk = {'path': str(root), 'total_bytes': usage.total, 'free_bytes': usage.free, 'used_percent': round((usage.used / usage.total) * 100, 2) if usage.total else None}
    except OSError as exc:
        disk = {'path': str(root), 'total_bytes': None, 'free_bytes': None, 'used_percent': None, 'error': type(exc).__name__}
    cache = Path(settings.playwright_browsers_path)
    cache_bytes = 0
    if cache.exists():
        for item in cache.rglob('*'):
            try:
                if item.is_file(): cache_bytes += item.stat().st_size
            except OSError: pass
    temp = Path(settings.runtime_temp_dir)
    temp_bytes = 0
    if temp.exists():
        for item in temp.rglob('*'):
            try:
                if item.is_file(): temp_bytes += item.stat().st_size
            except OSError: pass
    return {'timestamp': time.time(), 'disk': disk, 'memory': _memory_status(), 'cpu_count': os.cpu_count(), 'browser_processes': _browser_process_count(), 'playwright_cache_bytes': cache_bytes, 'runtime_temp_bytes': temp_bytes}


def cleanup_runtime_temp() -> dict:
    root = Path(settings.runtime_temp_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - settings.runtime_temp_retention_seconds
    removed = 0
    bytes_freed = 0
    entries = sorted(root.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0)
    for item in entries[:settings.runtime_cleanup_max_entries]:
        try:
            if item.stat().st_mtime >= cutoff:
                continue
            size = sum(child.stat().st_size for child in item.rglob('*') if child.is_file()) if item.is_dir() else item.stat().st_size
            if item.is_dir(): shutil.rmtree(item)
            else: item.unlink()
            removed += 1; bytes_freed += size
        except OSError:
            continue
    return {'directory': str(root), 'removed_entries': removed, 'bytes_freed': bytes_freed}


def browser_installation_status(*, verify_launch: bool = True) -> dict:
    if not verify_launch:
        roots = [Path(settings.playwright_browsers_path), Path.home() / '.cache' / 'ms-playwright']
        candidates = []
        for root in roots:
            if not root.exists():
                continue
            candidates.extend(root.glob('chromium*/**/chrome'))
            candidates.extend(root.glob('chromium_headless_shell*/**/chrome-headless-shell'))
        executable = next((item for item in candidates if item.is_file() and os.access(item, os.X_OK)), None)
        return {'available': executable is not None, 'executable': str(executable) if executable else None, 'playwright_browsers_path': os.environ.get('PLAYWRIGHT_BROWSERS_PATH', settings.playwright_browsers_path), 'error': None if executable else 'Chromium executable is missing or not executable'}
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            executable = Path(pw.chromium.executable_path)
            exists = executable.is_file() and os.access(executable, os.X_OK)
            return {'available': exists, 'executable': str(executable), 'playwright_browsers_path': os.environ.get('PLAYWRIGHT_BROWSERS_PATH', ''), 'error': None if exists else 'Chromium executable is missing or not executable'}
    except Exception as exc:
        return {'available': False, 'executable': None, 'playwright_browsers_path': os.environ.get('PLAYWRIGHT_BROWSERS_PATH', ''), 'error': f'{type(exc).__name__}: {str(exc)[:180]}'}


def ensure_resources() -> dict:
    status = resource_status()
    disk = status['disk']; memory = status['memory']
    low_disk = disk.get('free_bytes') is not None and disk['free_bytes'] < settings.minimum_free_disk_bytes
    high_memory = memory.get('used_percent') is not None and memory['used_percent'] > settings.maximum_memory_used_percent
    if low_disk or high_memory:
        cleanup_runtime_temp()
        status = resource_status()
        disk = status['disk']; memory = status['memory']
        low_disk = disk.get('free_bytes') is not None and disk['free_bytes'] < settings.minimum_free_disk_bytes
        high_memory = memory.get('used_percent') is not None and memory['used_percent'] > settings.maximum_memory_used_percent
    if low_disk or high_memory:
        raise RuntimeError('Insufficient runtime resources after safe cleanup')
    return status


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-browser', action='store_true')
    args = parser.parse_args()
    if args.check_browser:
        result = browser_installation_status()
        print(result)
        raise SystemExit(0 if result['available'] else 1)
    print(resource_status())
