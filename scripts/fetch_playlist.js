// NetEase Cloud Music data fetcher (Node, zero dependencies).
//
// Usage:
//   node fetch_playlist.js <playlist_id> <output.json>
//   node fetch_playlist.js <id1,id2,id3> <output.json>     # multi-playlist merge
//
// Pipeline:
//   1) POST /weapi/v3/playlist/detail        -> playlist meta + all trackIds
//   2) POST /weapi/v3/song/detail (batched)  -> full metadata for every track
//
// Output JSON:
//   single: { playlist: {name, creator, trackCount, playCount}, songs: [...] }
//   multi : { playlists: [{id,name,creator,trackCount,playCount}], songs: [...] }
//
// Each song: { id, name, artists[], album, publish_ms, duration_ms, popularity }
// For multi-playlist runs, each song also carries `sources: [playlist_id, ...]`
// so downstream profile building can weight cross-playlist overlap.

const crypto = require("crypto");
const fs = require("fs");

const IV = "0102030405060708";
const PRESET_KEY = "0CoJUm6Qyw8W8jud";
const MODULUS =
  "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7";
const EXPONENT = "010001";

function aesEncrypt(text, key) {
  const cipher = crypto.createCipheriv("aes-128-cbc", key, IV);
  return cipher.update(text, "utf8", "base64") + cipher.final("base64");
}
function modpow(b, e, m) {
  let r = 1n;
  b %= m;
  while (e > 0n) {
    if (e & 1n) r = (r * b) % m;
    b = (b * b) % m;
    e >>= 1n;
  }
  return r;
}
function rsaEncrypt(text) {
  const reversed = Buffer.from(text).reverse();
  return modpow(BigInt("0x" + reversed.toString("hex")), BigInt("0x" + EXPONENT), BigInt("0x" + MODULUS))
    .toString(16)
    .padStart(256, "0");
}

async function weapi(urlPath, data) {
  const secretKey = crypto.randomBytes(16).toString("hex").slice(0, 16);
  const step1 = aesEncrypt(JSON.stringify(data), PRESET_KEY, IV);
  const params = aesEncrypt(step1, secretKey, IV);
  const body = "params=" + encodeURIComponent(params) + "&encSecKey=" + encodeURIComponent(rsaEncrypt(secretKey));
  const resp = await fetch("https://music.163.com" + urlPath, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
      Referer: "https://music.163.com/",
      cookie: "os=pc",
    },
    body,
  });
  return JSON.parse(await resp.text());
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function fetchOne(pid) {
  const pl = await weapi("/weapi/v3/playlist/detail", { id: pid, n: 100000, s: 8 });
  if (pl.code !== 200 || !pl.playlist) {
    throw new Error(`playlist ${pid} fetch failed: code=${pl.code}`);
  }
  const p = pl.playlist;
  const meta = {
    id: Number(pid),
    name: p.name,
    creator: (p.creator || {}).nickname,
    trackCount: p.trackCount,
    playCount: p.playCount,
  };
  const ids = (p.trackIds || []).map((t) => t.id);
  console.error(`playlist="${meta.name}" (${pid}) tracks=${ids.length} (payload only has ${(p.tracks || []).length})`);

  const songs = [];
  for (let i = 0; i < ids.length; i += 100) {
    const chunk = ids.slice(i, i + 100);
    let res = null;
    for (let attempt = 0; attempt < 3 && !res; attempt++) {
      try {
        res = await weapi("/weapi/v3/song/detail", {
          c: JSON.stringify(chunk.map((id) => ({ id: Number(id) }))),
          ids: JSON.stringify(chunk),
        });
      } catch (e) {
        console.error(`batch ${i}: ${e.message}, retrying`);
        await sleep(800);
      }
    }
    if (!res || res.code !== 200) {
      console.error(`batch ${i}: FAILED code=${res && res.code}`);
      continue;
    }
    for (const s of res.songs || []) {
      songs.push({
        id: s.id,
        name: s.name,
        artists: (s.ar || []).map((a) => a.name),
        album: (s.al || {}).name,
        publish_ms: (s.al || {}).publishTime || null,
        duration_ms: s.dt,
        popularity: s.pop,
      });
    }
    console.error(`batch ${i}: got ${(res.songs || []).length}`);
    await sleep(400);
  }
  return { meta, songs };
}

(async () => {
  const [pidArg, outPath] = process.argv.slice(2);
  if (!pidArg || !outPath) {
    console.error("usage: node fetch_playlist.js <playlist_id[,id2,id3]> <output.json>");
    process.exit(1);
  }
  const pids = String(pidArg)
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (!pids.length) {
    console.error("no playlist id given");
    process.exit(1);
  }

  const metas = [];
  const merged = new Map(); // song id -> song
  for (const pid of pids) {
    const { meta, songs } = await fetchOne(pid);
    metas.push(meta);
    for (const s of songs) {
      const prev = merged.get(s.id);
      if (prev) {
        if (!prev.sources.includes(meta.id)) prev.sources.push(meta.id);
      } else {
        merged.set(s.id, { ...s, sources: [meta.id] });
      }
    }
    await sleep(400);
  }

  const songs = [...merged.values()];
  const payload =
    metas.length === 1
      ? { playlist: metas[0], songs }
      : { playlists: metas, songs };

  fs.writeFileSync(outPath, JSON.stringify(payload, null, 1));
  console.error(
    `saved ${songs.length} unique songs from ${metas.length} playlist(s) -> ${outPath}`
  );
})();
