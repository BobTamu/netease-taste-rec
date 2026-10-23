"""Verify a song exists on NetEase Cloud Music and get its canonical metadata.

Uses the legacy public search endpoint (no crypto, no login needed).
Always matches the artist exactly - covers/re-masters/remixes are rejected.

Usage:
    python search_song.py "青花瓷" "周杰伦"
    python search_song.py "Going Home" "Sophie Zelmani" --alias 苏菲珊曼妮

Prints one JSON object per hit: id, name, artists, album, year, duration_ms.
Selection rule: exact title AND exact artist, then earliest publish year
(originals predate covers), preferring non-compilation albums.
"""
import json, sys, io, time, urllib.request, urllib.parse, re, unicodedata

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

COMP_KW = re.compile(r"精选|合辑|慢摇|必點|怀旧|岁月|金曲|演唱会|巡演|Live|live|KTV|彩铃|抖音|DJ|DJ版|story|Chart")


def norm(s):
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = s.replace("&", " and ")
    return re.sub(r"[\s\-_/,\.\(\)\[\]()《》'\"!:：?？·+，,。~—-]", "", s)


def search(q, limit=30):
    data = urllib.parse.urlencode({"s": q, "type": 1, "limit": limit, "offset": 0}).encode()
    req = urllib.request.Request(
        "https://music.163.com/api/search/get", data=data,
        headers={"Referer": "https://music.163.com/", "User-Agent": "Mozilla/5.0",
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))


def year_of(song):
    pt = (song.get("album") or {}).get("publishTime")
    if not pt:
        return None
    try:
        if pt > 1e12:
            pt = pt / 1000
        return time.gmtime(pt).tm_year
    except Exception:
        return None


def pick(title, artist, aliases=()):
    expected = {norm(artist)} | {norm(a) for a in aliases}
    songs = (search(f"{title} {artist}").get("result") or {}).get("songs") or []
    tn = norm(title)
    hits = [s for s in songs
            if norm(s.get("name")) == tn
            and any(norm(a.get("name")) in expected for a in s.get("artists", []))]
    clean = [s for s in hits if not COMP_KW.search((s.get("album") or {}).get("name") or "")]
    pool = clean or hits
    if not pool:
        return None
    pool.sort(key=lambda s: (year_of(s) or 9999, -(s.get("duration") or 0)))
    s = pool[0]
    return {
        "id": s.get("id"),
        "name": s.get("name"),
        "artists": [a.get("name") for a in s.get("artists", [])],
        "album": (s.get("album") or {}).get("name"),
        "year": year_of(s),
        "duration_ms": s.get("duration"),
        "note": "compilation-album fallback" if not clean else "canonical",
    }


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    aliases = sys.argv[sys.argv.index("--alias") + 1: sys.argv.index("--alias") + 2] if "--alias" in sys.argv else []
    title, artist = args[0], args[1]
    result = pick(title, artist, aliases)
    print(json.dumps(result, ensure_ascii=False))
