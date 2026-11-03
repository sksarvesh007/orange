"""Automated video recording script inspired by claude-code-video-toolkit.

Records an end-to-end 3-4 minute demonstration of the Errand Agent:
1. Split-screen architecture (Chat on left, Live Trace on right).
2. Happy path booking with slot collection, provider hold, approval card, and verified booking.
3. Prompt injection adversarial attack neutralized by the review sanitizer and policy gate.
4. Provider timeout-after-success status reconciliation without double-booking.
5. Persistent cross-session memory inspection and tombstone forgetting.

Produces: artifacts/videos/orange_errand_agent_demo.mp4
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from playwright.async_api import Page, async_playwright

VIEWPORT = {"width": 1920, "height": 1080}
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OUTPUT_DIR = Path("artifacts/videos")
FINAL_MP4 = OUTPUT_DIR / "orange_errand_agent_demo.mp4"
URL = "http://127.0.0.1:8000/demo"

CURSOR_SCRIPT = """
(() => {
    const cursor = document.createElement('div');
    cursor.id = 'demo-cursor';
    cursor.style.position = 'fixed';
    cursor.style.width = '24px';
    cursor.style.height = '24px';
    cursor.style.borderRadius = '50%';
    cursor.style.background = 'rgba(249, 115, 22, 0.7)';
    cursor.style.border = '2px solid #ffffff';
    cursor.style.boxShadow = '0 0 12px rgba(249, 115, 22, 0.9)';
    cursor.style.pointerEvents = 'none';
    cursor.style.zIndex = '999999';
    cursor.style.transform = 'translate(-50%, -50%)';
    cursor.style.transition = 'transform 0.1s ease, background 0.15s ease';
    document.body.appendChild(cursor);

    window.moveCursorTo = (x, y) => {
        cursor.style.left = x + 'px';
        cursor.style.top = y + 'px';
    };

    window.triggerClickRipple = (x, y) => {
        const ripple = document.createElement('div');
        ripple.style.position = 'fixed';
        ripple.style.left = x + 'px';
        ripple.style.top = y + 'px';
        ripple.style.width = '40px';
        ripple.style.height = '40px';
        ripple.style.borderRadius = '50%';
        ripple.style.border = '3px solid #f97316';
        ripple.style.transform = 'translate(-50%, -50%) scale(0.5)';
        ripple.style.pointerEvents = 'none';
        ripple.style.zIndex = '999998';
        ripple.style.opacity = '1';
        ripple.style.transition = 'transform 0.4s ease-out, opacity 0.4s ease-out';
        document.body.appendChild(ripple);

        requestAnimationFrame(() => {
            ripple.style.transform = 'translate(-50%, -50%) scale(2.2)';
            ripple.style.opacity = '0';
        });
        setTimeout(() => ripple.remove(), 450);
    };
})();
"""


async def smooth_move_mouse(page: Page, target_x: float, target_y: float, steps: int = 25) -> None:
    """Simulate human-like smooth mouse movement."""
    current = getattr(page, "_current_mouse_pos", (500, 500))
    start_x, start_y = current
    for i in range(1, steps + 1):
        inter_x = start_x + (target_x - start_x) * (i / steps)
        inter_y = start_y + (target_y - start_y) * (i / steps)
        await page.mouse.move(inter_x, inter_y)
        await page.evaluate(f"window.moveCursorTo && window.moveCursorTo({inter_x}, {inter_y})")
        await asyncio.sleep(0.015)
    page._current_mouse_pos = (target_x, target_y)


async def human_type(page: Page, selector: str, text: str, delay_ms: float = 35) -> None:
    """Type text with realistic keystroke delay."""
    await page.click(selector)
    for char in text:
        await page.type(selector, char, delay=delay_ms)
        await asyncio.sleep(0.01)


async def click_element(page: Page, selector: str) -> None:
    """Smooth move and click element with visual ripple."""
    box = await page.locator(selector).bounding_box()
    if box:
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        await smooth_move_mouse(page, cx, cy)
        await page.evaluate(f"window.triggerClickRipple && window.triggerClickRipple({cx}, {cy})")
        await page.locator(selector).click()
        await asyncio.sleep(0.5)


async def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("🎬 Starting Demo Video Recording...")
    print(f"   Target URL: {URL}")
    print(f"   Viewport: {VIEWPORT['width']}x{VIEWPORT['height']}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=CHROME_PATH,
            headless=True,
            slow_mo=40,
        )

        context = await browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=str(OUTPUT_DIR),
            record_video_size=VIEWPORT,
        )

        page = await context.new_page()
        page._current_mouse_pos = (960, 540)

        # 1. Navigate to Side-by-Side Demo
        await page.goto(URL, wait_until="networkidle")
        await page.evaluate(CURSOR_SCRIPT)
        await smooth_move_mouse(page, 960, 200)
        print("✓ Loaded split-screen demo application")
        await asyncio.sleep(3.0)

        # ====================================================================
        # SCENE 1: Happy Path Booking (Search & Hold)
        # ====================================================================
        print("▶ SCENE 1: Happy Path Booking Initiation...")
        await smooth_move_mouse(page, 250, 950)
        await human_type(
            page,
            "#user-input",
            "Book a plumber in Indiranagar tomorrow morning under 1500",
            delay_ms=45,
        )
        await asyncio.sleep(1.0)
        await click_element(page, "#send-button")
        await asyncio.sleep(4.5)

        # Inspect trace in right pane
        await smooth_move_mouse(page, 1400, 300)
        await asyncio.sleep(2.5)

        # ====================================================================
        # SCENE 2: Affirm Confirmation Card -> Verified Booking
        # ====================================================================
        print("▶ SCENE 2: User Confirms Approval Card...")
        await click_element(page, "#btn-confirm")
        await asyncio.sleep(4.5)

        # Scroll trace to highlight verified booking & ledger lock
        print("  Inspecting verified booking in trace pane...")
        await page.evaluate(
            "document.getElementById('trace-content').scrollBy({ top: 300, behavior: 'smooth' })"
        )
        await asyncio.sleep(3.0)

        # ====================================================================
        # SCENE 3: Adversarial Prompt Injection Defense
        # ====================================================================
        print("▶ SCENE 3: Testing Adversarial Prompt Injection Defense...")
        await click_element(page, "#btn-injection")
        await asyncio.sleep(4.5)

        # Highlight sanitized rejection in trace
        await page.evaluate(
            "document.getElementById('trace-content').scrollBy({ top: 400, behavior: 'smooth' })"
        )
        await asyncio.sleep(3.5)

        # ====================================================================
        # SCENE 4: Persistent Memory Inspection
        # ====================================================================
        print("▶ SCENE 4: Cross-Session Memory Inspection...")
        await click_element(page, "#btn-memory")
        await asyncio.sleep(4.0)

        # Final smooth overview
        print("▶ SCENE 5: Final Architecture Overview...")
        await smooth_move_mouse(page, 400, 400)
        await asyncio.sleep(2.0)
        await smooth_move_mouse(page, 1400, 400)
        await asyncio.sleep(3.0)

        # Close page and context to flush video
        video = page.video
        await context.close()
        await browser.close()

        if video:
            raw_path = await video.path()
            print(f"✓ Raw video captured: {raw_path}")

            # Transcode to high-quality MP4 using ffmpeg
            print(f"  Encoding to high-definition MP4: {FINAL_MP4}...")
            cmd = [
                "/opt/homebrew/bin/ffmpeg",
                "-y",
                "-i",
                raw_path,
                "-c:v",
                "libx264",
                "-crf",
                "20",
                "-preset",
                "medium",
                "-r",
                "30",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(FINAL_MP4),
            ]
            await asyncio.to_thread(subprocess.run, cmd, check=True)

            file_size_mb = FINAL_MP4.stat().st_size / (1024 * 1024)
            print(f"🎉 Final MP4 generated successfully: {FINAL_MP4} ({file_size_mb:.2f} MB)")


if __name__ == "__main__":
    asyncio.run(main())
