# -*- coding: utf-8 -*-
"""Batch original-version verification for netease-taste-rec.

Feeds a candidate list through search_song.pick() with per-item retry and
throttling, then dumps a JSON report. Rejects compilation albums.

Usage:
    python verify_batch.py candidates.txt out.json
    # candidates.txt: one per line, "歌名 | 艺人 [| group]"

Notes:
  * Do NOT wrap sys.stdout yourself after importing search_song — that module
    already rebinds sys.stdout, and a second TextIOWrapper over the same buffer
    raises "I/O operation on closed file". Just import it first.
  * nulls are retried once (rate-limit false negatives are common).
  * 'note' == 'compilation-album fallback' or an album name matching
    COMP_KW means the hit is a compilation/OST — review before using.
"""
import json
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0] + "/scripts")
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import search_song as S  # noqa: E402  (rebinds sys.stdout on import)

ALIASES = {
    "Sophie Zelmani": ["苏菲珊曼妮"],
    "Jose Gonzalez": ["José González"],
    "G.E.M.邓紫棋": ["邓紫棋"],
}


def main():
    src, dst = sys.argv[1], sys.argv[2]
    rows = []
    for line in open(src, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        rows.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))

    out = []
    for title, artist, grp in rows:
        rec = None
        for _ in range(2):
            try:
                rec = S.pick(title, artist, ALIASES.get(artist, ()))
            except Exception:
                rec = None
            if rec:
                break
            time.sleep(1.2)
        if rec:
            item = {
                "group": grp, "q_title": title, "q_artist": artist,
                "name": rec["name"], "artists": rec["artists"],
                "album": rec["album"], "year": rec.get("year"),
                "note": rec.get("note"), "ok": True,
            }
            print("OK   %s / %s -> %s | %s | %s | %s | %s" % (
                title, artist, rec["name"], "/".join(rec["artists"]),
                rec["album"], rec.get("year"), rec.get("note")))
        else:
            item = {"group": grp, "q_title": title, "q_artist": artist, "ok": False}
            print("NULL %s / %s" % (title, artist))
        out.append(item)
        time.sleep(0.55)

    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("saved %d, ok=%d" % (len(out), sum(1 for x in out if x["ok"])))


if __name__ == "__main__":
    main()
