# -*- coding: utf-8 -*-
"""Build a reproducible taste profile from one or more NetEase playlist dumps.

Why this exists
---------------
The daily-recommendation flow used to derive the taste profile by having a model
"read" the playlist and summarise it in prose. That is not reproducible: the same
playlist can yield different artist rankings on different runs, and there is no
way to audit where a number came from.

This script turns the profile into computed facts (counts, weights, ratios,
decade spread) so that:
  * switching to another person's playlist is just a different input,
  * every number in the output can be traced back to a song list,
  * genre labels stay a model-side judgement but are anchored to real statistics.

Usage
-----
    python build_profile.py playlist.json [-o profile.json]
    python build_profile.py me.json friend.json -o merged_profile.json
    python build_profile.py a.json b.json --weights 1,2 -o profile.json

Accepts both shapes:
  * {"playlist": {...}, "songs": [...]}      (single, from fetch_playlist.js)
  * {"playlists": [...], "songs": [...]}     (multi, from fetch_playlist.js)
  * [ {...}, {...} ]                          (raw song array, e.g. songs_full.json)

Weighting
---------
By default every playlist contributes the SAME total weight (1.0), so a 1000-song
playlist cannot drown out a 40-song one. Within a playlist, each song gets
1 / len(playlist). A song present in several playlists accumulates weight from
each — that overlap is a strong "actually listened to" signal and is reported
separately in `overlap`.

Known limitation
----------------
Neither /weapi/v3/playlist/detail nor /weapi/v3/song/detail returns
`al.publishTime`, so `decades` comes back empty on a raw dump. It only fills in
when the input already carries publish times (e.g. enriched via search_song.py).
Do not fabricate an era distribution to fill the gap.
"""
import argparse
import json
import re
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def norm(s):
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = s.replace("&", " and ")
    return re.sub(r"[\s\-_/,\.\(\)\[\]《》'\"!:：?？·+，,。~—-]", "", s)


def load(path):
    """Return (label, [song...]) with songs normalised to a common shape."""
    data = json.load(open(path, encoding="utf-8"))
    if isinstance(data, list):
        songs, label = data, path
    elif isinstance(data, dict) and "songs" in data:
        songs = data["songs"]
        if "playlist" in data:
            label = data["playlist"].get("name") or path
        elif "playlists" in data:
            label = " + ".join(p.get("name", "?") for p in data["playlists"])
        else:
            label = path
    else:
        raise ValueError(f"unrecognised playlist json shape: {path}")

    out = []
    for s in songs:
        # fetch_playlist.js -> artists[]; raw weapi song/detail -> ar[{name}]
        if isinstance(s.get("artists"), list):
            artists = [a if isinstance(a, str) else a.get("name", "") for a in s["artists"]]
        else:
            artists = [a.get("name", "") for a in (s.get("ar") or [])]
        album = s.get("album")
        if isinstance(album, dict):
            album = album.get("name")
        pub = s.get("publish_ms")
        if pub is None and isinstance(s.get("al"), dict):
            pub = s["al"].get("publishTime")
        out.append(
            {
                "id": s.get("id"),
                "name": s.get("name") or "",
                "artists": [a for a in artists if a],
                "album": album or "",
                "publish_ms": pub,
                "duration_ms": s.get("duration_ms") or s.get("dt"),
            }
        )
    return label, out


def year_of(song):
    pt = song.get("publish_ms")
    if not pt:
        return None
    try:
        pt = int(pt)
        if pt > 1e12:
            pt = pt / 1000
        import time as _t

        return _t.gmtime(pt).tm_year
    except Exception:
        return None


def build(sources, weights=None):
    """sources: [(label, [song...])]; weights: per-source multiplier or None."""
    n = len(sources)
    if weights is None:
        weights = [1.0] * n
    if len(weights) != n:
        raise ValueError("--weights count must match number of input files")

    total_w = sum(weights)
    artist_w = defaultdict(float)
    artist_c = Counter()
    album_w = defaultdict(float)
    song_w = defaultdict(float)
    song_meta = {}
    song_src = defaultdict(list)
    lang_w = {"cjk": 0.0, "non_cjk": 0.0}
    decade_w = defaultdict(float)
    durations = []
    per_source = []

    for idx, ((label, songs), mult) in enumerate(zip(sources, weights)):
        if not songs:
            per_source.append({"source": label, "tracks": 0, "weight": 0.0})
            continue
        # each playlist contributes `mult / total_w` of the grand total
        share = mult / total_w
        per_song = share / len(songs)
        per_source.append({"source": label, "tracks": len(songs), "weight": round(share, 4)})
        for s in songs:
            artists = s["artists"] or [""]
            # split credit across credited artists so duets don't double-count
            credit = per_song / len(artists)
            for a in artists:
                artist_w[a] += credit
                artist_c[a] += 1
            if s["album"]:
                album_w[s["album"]] += per_song
            key = (norm(s["name"]), norm(artists[0]))
            song_w[key] += per_song
            song_meta.setdefault(key, {"name": s["name"], "artist": artists[0]})
            if idx not in song_src[key]:
                song_src[key].append(idx)

            # Language bucket follows the ARTIST name, not the song title.
            # A Chinese artist singing an English-titled song is still 华语 —
            # bucketing by title inflates the foreign share.
            blob = " ".join(artists)
            lang_w["cjk" if CJK.search(blob) else "non_cjk"] += per_song

            y = year_of(s)
            if y:
                decade_w[f"{(y // 10) * 10}s"] += per_song

            if s.get("duration_ms"):
                durations.append(s["duration_ms"] / 60000.0)

    lang_total = lang_w["cjk"] + lang_w["non_cjk"] or 1.0
    artists_sorted = sorted(artist_w.items(), key=lambda kv: -kv[1])
    top = [
        {
            "artist": a,
            "tracks": artist_c[a],
            "weight": round(w, 4),
            "share": round(w * 100, 2),
        }
        for a, w in artists_sorted[:25]
    ]
    overlap = [
        {
            "name": song_meta[k]["name"],
            "artist": song_meta[k]["artist"],
            "in_sources": [sources[i][0] for i in sorted(v)],
            "count": len(v),
        }
        for k, v in sorted(song_src.items(), key=lambda kv: -len(kv[1]))
        if len(v) > 1
    ]
    return {
        "sources": per_source,
        "total_unique_songs": len(song_meta),
        "language": {
            "cjk_weight": round(lang_w["cjk"], 4),
            "non_cjk_weight": round(lang_w["non_cjk"], 4),
            "cjk_ratio": round(lang_w["cjk"] / lang_total * 100, 1),
            "non_cjk_ratio": round(lang_w["non_cjk"] / lang_total * 100, 1),
        },
        "artists_top": top,
        "artists_distinct": len(artist_w),
        "albums_top": [
            {"album": a, "weight": round(w, 4)}
            for a, w in sorted(album_w.items(), key=lambda kv: -kv[1])[:15]
        ],
        "decades": {
            k: round(v * 100, 1) for k, v in sorted(decade_w.items())
        },
        "duration_median_min": round(statistics.median(durations), 2) if durations else None,
        "overlap": overlap[:40],
        "overlap_count": len(overlap),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", help="playlist json file(s)")
    ap.add_argument("-o", "--out", default="profile.json")
    ap.add_argument(
        "--weights",
        default=None,
        help="comma-separated per-file weights, e.g. 1,2 (default: equal)",
    )
    a = ap.parse_args()

    weights = [float(x) for x in a.weights.split(",")] if a.weights else None
    sources = [load(p) for p in a.inputs]
    prof = build(sources, weights)
    json.dump(prof, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"sources      : {len(sources)}")
    for s in prof["sources"]:
        print(f"  - {s['source']}: {s['tracks']} tracks (weight {s['weight']})")
    print(f"unique songs : {prof['total_unique_songs']}")
    print(f"artists      : {prof['artists_distinct']} distinct")
    print(f"language     : CJK {prof['language']['cjk_ratio']}% / non-CJK {prof['language']['non_cjk_ratio']}%")
    print(f"median length: {prof['duration_median_min']} min")
    if prof["decades"]:
        print("decades      : " + ", ".join(f"{k} {v}%" for k, v in prof["decades"].items()))
    else:
        # /weapi/v3/playlist/detail and /weapi/v3/song/detail do NOT return
        # al.publishTime, so decade spread is unavailable on a raw dump.
        # It only fills in if the input was enriched with publish times
        # (e.g. via search_song.py). Say so instead of printing an empty line.
        print("decades      : unavailable (dump has no publish time)")
    print(f"overlap      : {prof['overlap_count']} songs appear in >1 playlist")
    print("top artists  :")
    for t in prof["artists_top"][:12]:
        print(f"  {t['artist']:<28} {t['tracks']:>3} 首  {t['share']:>5}%")
    print(f"\nsaved -> {a.out}")


if __name__ == "__main__":
    main()
