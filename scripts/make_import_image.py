# -*- coding: utf-8 -*-
"""Render a NetEase "screenshot import" list image.

NetEase has no public write API, so the playlist has to be created client-side
from a screenshot. OCR quality depends on the image: white background, black
text, one song per line, and **no line wrapping** (a wrapped line breaks OCR's
line grouping and the song gets mis-parsed).

This script used to be re-written from scratch every day with the song list
hard-coded into it. Now it takes the list as input.

Usage
-----
    python make_import_image.py songs.json out.png
    python make_import_image.py songs.json out.png --title "每日推荐 2026-01-01"
    python make_import_image.py songs.txt out.png --subtitle "北京 -3℃ 晴"

Input — JSON (preferred, keeps groups):
    {
      "title": "每日推荐 2026-01-01",
      "subtitle": "北京 -3℃ 晴 · 工作日",
      "groups": [
        {"name": "情境组", "songs": [["示例曲名一", "示例艺人甲"], ...]},
        {"name": "华语拓展", "songs": [["示例曲名二", "示例艺人乙"], ...]}
      ]
    }

Input — plain text (one song per line, "歌名 | 歌手" or "歌名 - 歌手";
a line starting with "#" or "【" opens a new group):
    【情境组】
    示例曲名一 | 示例艺人甲
    示例曲名二 | 示例艺人乙

Requires Pillow (`pip install pillow`). Everything else is stdlib.
"""
import argparse
import json
import os
import re
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Pillow is required: pip install pillow", file=sys.stderr)
    sys.exit(3)

W = 1200
MARGIN = 70
USABLE = W - MARGIN * 2
LINE_H = 60
HEAD_H = 86
BASE = 32
FLOOR = 24
FOOTER = "共 {n} 首 · 网易云「一键导入外部音乐 → 截图导入」"

# Candidate fonts, in priority order. Windows first, then macOS, then Linux.
FONT_CANDIDATES = [
    r"C:/Windows/Fonts/msyh.ttc",
    r"C:/Windows/Fonts/msyhbd.ttc",
    r"C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]
FONT_BOLD_CANDIDATES = [
    r"C:/Windows/Fonts/msyhbd.ttc",
    r"C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
]


def font(size, bold=False):
    for p in (FONT_BOLD_CANDIDATES if bold else []) + FONT_CANDIDATES:
        if p and os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _as_song(s):
    """Normalise one entry to (name, artist) from either shape."""
    if isinstance(s, (list, tuple)) and len(s) >= 2:
        return str(s[0]), str(s[1])
    if isinstance(s, dict):
        artists = s.get("artists")
        if isinstance(artists, list):
            artist = ", ".join(a if isinstance(a, str) else a.get("name", "") for a in artists)
        else:
            artist = ", ".join(a.get("name", "") for a in (s.get("ar") or []))
        return str(s.get("name", "")), artist
    return None


def load_json(path):
    d = json.load(open(path, encoding="utf-8"))

    # Shape A: explicit groups (preferred — keeps the 情境/华语/欧美 split).
    if d.get("groups"):
        groups = []
        for g in d["groups"]:
            songs = [x for x in (_as_song(s) for s in (g.get("songs") or [])) if x]
            groups.append({"name": g.get("name") or "未分组", "songs": songs})
        return d.get("title"), d.get("subtitle"), groups

    # Shape B: a raw fetch_playlist.js dump — flatten to one group.
    if d.get("songs"):
        songs = [x for x in (_as_song(s) for s in d["songs"]) if x]
        name = (d.get("playlist") or {}).get("name") or "歌单"
        return d.get("title"), d.get("subtitle"), [{"name": name, "songs": songs}]

    raise ValueError(f"{path}: expected 'groups' or 'songs'")


def load_text(path):
    """Parse a plain-text list.

    A generated .txt usually starts with a title line and a subtitle line before
    the first 【group】 header. Those are NOT songs — naively parsing every line
    silently adds two fake entries (observed: 30 songs -> 32, 3 groups -> 4).
    So: if the file contains any header line, everything before the first header
    is treated as title/subtitle, not as songs.
    """
    lines = [l.rstrip("\n") for l in open(path, encoding="utf-8")]
    is_header = lambda s: s.strip().startswith("#") or s.strip().startswith("【")
    has_header = any(is_header(l) for l in lines if l.strip())

    title = subtitle = None
    groups, cur = [], {"name": "歌单", "songs": []}

    if has_header:
        first = next(i for i, l in enumerate(lines) if l.strip() and is_header(l))
        preamble = [l.strip() for l in lines[:first] if l.strip()]
        if preamble:
            title = preamble[0]
        if len(preamble) > 1:
            subtitle = preamble[1]
        lines = lines[first:]

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if is_header(line):
            name = line.lstrip("#").strip().strip("【】").strip()
            if cur["songs"]:
                groups.append(cur)
            cur = {"name": name or "歌单", "songs": []}
            continue
        line = re.sub(r"^\d+[\.\、\)]\s*", "", line)
        if "|" in line:
            n, a = line.split("|", 1)
        elif " - " in line:
            n, a = line.split(" - ", 1)
        elif " — " in line:
            n, a = line.split(" — ", 1)
        else:
            n, a = line, ""
        cur["songs"].append((n.strip(), a.strip()))
    if cur["songs"]:
        groups.append(cur)
    return title, subtitle, groups


def fit(draw, text, base=BASE, floor=FLOOR):
    """Shrink until the line fits — wrapping would break OCR line grouping."""
    size = base
    while size > floor:
        f = font(size)
        if draw.textlength(text, font=f) <= USABLE:
            return f
        size -= 1
    return font(floor)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="songs.json or songs.txt")
    ap.add_argument("output", help="output .png")
    ap.add_argument("--title", default=None)
    ap.add_argument("--subtitle", default=None)
    ap.add_argument("--width", type=int, default=W)
    a = ap.parse_args()

    if a.input.lower().endswith(".json"):
        t, st, groups = load_json(a.input)
    else:
        t, st, groups = load_text(a.input)
    title = a.title or t or "每日推荐"
    subtitle = a.subtitle or st or ""

    n_songs = sum(len(g["songs"]) for g in groups)
    if not n_songs:
        print("no songs parsed from input", file=sys.stderr)
        return 1

    total_h = 170 + len(groups) * HEAD_H + n_songs * LINE_H + 150
    img = Image.new("RGB", (a.width, total_h), "white")
    d = ImageDraw.Draw(img)

    y = 70
    d.text((MARGIN, y), title, font=font(52, bold=True), fill=(0, 0, 0))
    y += 78
    if subtitle:
        d.text((MARGIN, y), subtitle, font=font(26), fill=(70, 70, 70))
        y += 60
    d.line([(MARGIN, y), (a.width - MARGIN, y)], fill=(0, 0, 0), width=3)
    y += 34

    for g in groups:
        if not g["songs"]:
            continue
        d.text((MARGIN, y + 14), g["name"], font=font(28, bold=True), fill=(110, 110, 110))
        d.line([(MARGIN, y + 60), (a.width - MARGIN, y + 60)], fill=(190, 190, 190), width=2)
        y += HEAD_H
        for name, artist in g["songs"]:
            line = f"{name} - {artist}" if artist else name
            d.text((MARGIN, y + 10), line, font=fit(d, line), fill=(0, 0, 0))
            y += LINE_H
        y += 6

    y += 20
    d.text((MARGIN, y), FOOTER.format(n=n_songs), font=font(24), fill=(90, 90, 90))

    img.save(a.output, "PNG", optimize=True)
    print(f"saved {a.output} ({img.size[0]}x{img.size[1]}, {n_songs} songs, {len(groups)} groups)")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
