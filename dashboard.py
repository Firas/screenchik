#!/usr/bin/env python3
"""Claude Code usage on the AX206 USB screen — minimal layout.

Top band: full-width clock, white on black.
Bottom band: split in half — big "remaining %" for the 5h window (left, "h")
and the weekly window (right, "w").

No Windows, no VM, no AIDA64 — talks to the display directly from macOS
over libusb. Run:

  .venv/bin/python dashboard.py

Stop with Ctrl-C.
"""
from __future__ import annotations

import argparse
import time

from PIL import Image, ImageDraw, ImageFont

from ax206 import AX206Display
import claude_stats
import extra_stats

W, H = 480, 320
SS = 2  # supersample factor for smooth text/shapes

BLACK = (0, 0, 0)
WHITE = (245, 247, 250)
DIM = (110, 120, 140)
GREEN = (72, 199, 142)
YELLOW = (240, 195, 90)
RED = (235, 90, 90)
CYAN = (90, 190, 220)
ORANGE = (240, 165, 80)

TOP_H = 130  # px, height of the clock band

F_CLOCK = None  # set in _load_fonts()
F_BIG = None
F_TAG = None
F_TINY = None


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    paths = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _load_fonts():
    global F_CLOCK, F_BIG, F_TAG, F_TINY
    F_CLOCK = _font(117 * SS, bold=True)  # sized to reach both edges at "23:17:31"
    F_BIG = _font(92 * SS, bold=True)
    F_TAG = _font(20 * SS, bold=True)
    F_TINY = _font(13 * SS)


_load_fonts()


def text_centered(d, cx, cy, text, font, fill):
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text((cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]), text, font=font, fill=fill)


def remaining_color(pct: float):
    """pct = % remaining. Plenty left = green, getting low = red."""
    if pct > 40:
        return GREEN
    if pct > 15:
        return YELLOW
    return RED


def half(d, cx, cy_big, big_text, color, tag, reset_text):
    """Draw one 'remaining' block: big % number, small tag letter beside it,
    tiny reset countdown underneath."""
    bbox = d.textbbox((0, 0), big_text, font=F_BIG)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    num_x = cx - tw / 2 - bbox[0]
    num_y = cy_big - th / 2 - bbox[1]
    d.text((num_x, num_y), big_text, font=F_BIG, fill=color)

    # small tag letter, baseline-aligned to the bottom of the big number
    tag_bbox = d.textbbox((0, 0), tag, font=F_TAG)
    tag_h = tag_bbox[3] - tag_bbox[1]
    tag_x = num_x + tw + 10 * SS
    tag_y = num_y + th - tag_h
    d.text((tag_x, tag_y), tag, font=F_TAG, fill=DIM)

    if reset_text:
        text_centered(d, cx, cy_big + th / 2 + 26 * SS, reset_text, F_TINY, DIM)


def _frame(top_text: str, top_color, left, right) -> Image.Image:
    """Shared layout: big text top band + two 'half' blocks at the bottom.
    left/right are (big_text, color, tag, sub_text) tuples."""
    img = Image.new("RGB", (W * SS, H * SS), BLACK)
    d = ImageDraw.Draw(img)

    text_centered(d, W * SS // 2, TOP_H * SS // 2, top_text, F_CLOCK, top_color)
    d.line([(0, TOP_H * SS), (W * SS, TOP_H * SS)], fill=(40, 40, 44), width=1 * SS)

    bottom_cy = TOP_H * SS + (H - TOP_H) * SS // 2
    d.line([(W * SS // 2, TOP_H * SS), (W * SS // 2, H * SS)], fill=(40, 40, 44), width=1 * SS)

    half(d, W * SS // 4, bottom_cy, *left)
    half(d, W * SS * 3 // 4, bottom_cy, *right)

    return img.resize((W, H), Image.LANCZOS)


def render_usage(stats: dict) -> Image.Image:
    hours_left = 100 - stats["window_pct"]
    week_left = 100 - stats["weekly_pct"]
    return _frame(
        time.strftime("%H:%M:%S"), WHITE,
        (f"{hours_left:.0f}%", remaining_color(hours_left), "h",
         stats["window_reset"] and f"сброс {stats['window_reset']}"),
        (f"{week_left:.0f}%", remaining_color(week_left), "w",
         stats["weekly_reset"] and f"сброс {stats['weekly_reset']}"),
    )


def render_info(extra: dict) -> Image.Image:
    rate = extra["usd_rate"]
    tmax = extra["weather_tmax"]
    tmin = extra["weather_tmin"]
    return _frame(
        extra["date_str"], WHITE,
        (f"{rate:.1f}" if rate is not None else "—", CYAN, "руб", "USD, ЦБ РФ"),
        (f"{tmax:.0f}°" if tmax is not None else "—", ORANGE, "C",
         f"ночью {tmin:.0f}° · {extra['weather_desc']}" if tmin is not None else "завтра"),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between frames")
    ap.add_argument("--stats-interval", type=float, default=20.0, help="seconds between Claude stats refresh")
    ap.add_argument("--screen-interval", type=float, default=10.0, help="seconds between screen switches")
    args = ap.parse_args()

    stats = claude_stats.parse_stats()
    extra = extra_stats.get_extra()
    last_stats_fetch = time.time()
    screen = 0
    last_switch = time.time()

    with AX206Display() as s:
        print(f"dashboard running ({s.width}x{s.height}), Ctrl-C to stop")
        count = 0
        consec = 0
        MAX_CONSEC = 6
        while True:
            time.sleep(args.interval)
            now = time.time()
            if now - last_stats_fetch >= args.stats_interval:
                try:
                    stats = claude_stats.parse_stats()
                    extra = extra_stats.get_extra()
                except Exception as e:
                    print(f"stats error: {e}", flush=True)
                last_stats_fetch = now

            if now - last_switch >= args.screen_interval:
                screen = 1 - screen
                last_switch = now

            frame = render_usage(stats) if screen == 0 else render_info(extra)
            try:
                s.draw_image(frame, fit="stretch")
            except Exception as e:
                consec += 1
                print(f"frame {count + 1}: glitch ({e}) consec={consec}", flush=True)
                if consec <= 2:
                    s.recover()
                else:
                    print("  attempting full reopen…", flush=True)
                    ok = s.reopen()
                    print(f"  reopen {'OK' if ok else 'FAILED'}", flush=True)
                if consec >= MAX_CONSEC:
                    print("Too many consecutive failures — device wedged, replug needed.", flush=True)
                    return 1
                continue
            consec = 0
            count += 1
            if count == 1 or count % 10 == 0:
                print(f"frame {count}: h-left {100-stats['window_pct']:.0f}% "
                      f"w-left {100-stats['weekly_pct']:.0f}%", flush=True)
    return 0


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
