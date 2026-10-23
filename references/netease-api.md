# 网易云接口口径（2026-10 实测）

本文件记录取数接口的可用性、加密方式与字段陷阱。改动脚本前先读这里，不要凭印象试接口。

## 一、可用接口

| 用途 | 接口 | 方法 | 关键参数 |
|---|---|---|---|
| 歌单详情 + 全量 trackIds | `/weapi/v3/playlist/detail` | POST（weapi 加密） | `{id, n:100000, s:8}` |
| 曲目详情（批量） | `/weapi/v3/song/detail` | POST（weapi 加密） | `{c: JSON.stringify([{id}...]), ids: JSON.stringify([...])}`，每批 100 |
| 搜索（校验用） | `/api/search/get` | POST（普通表单） | `s=<关键词> type=1 limit=30 offset=0` |

搜索接口必须带 `Referer: https://music.163.com/`，否则可能被拒。

## 二、已废弃 / 不可用（不要再试）

| 接口 | 返回 |
|---|---|
| `GET /api/playlist/detail?id=` | `code:20001` |
| `GET /api/song/detail?ids=` | `code:400` |
| `GET /api/playlist/track/all` | 404 |
| `POST /weapi/cloudsearch/get/web` | `code:50000005`（需登录 cookie） |

## 三、关键陷阱

**1. playlist detail 的 `tracks` 只有样品。**
v3/v6 版本只回约 6 首。必须走 `trackIds` 拿全量 id，再用 `song/detail` 分批补齐元数据。直接读 `tracks` 会得到一份严重不完整的歌单。

**2. 两个接口都不返回 `al.publishTime`。**
所以抓下来的 dump 里没有发行年份，**年代分布算不出来**。需要年份只能逐首走搜索接口（348 首约 6 分钟且易触发限流）。日常流程不要为它额外请求，也**不要为了填字段编造年代分布**。

**3. 字段结构两套，不能混用。**
- `fetch_playlist.js` 输出：`artists` 是字符串数组，`album` 是字符串
- 原始 `song/detail` 响应：`ar` 是对象数组，`al` 是对象
- 榜单 json 同样用 `artists`/`album`；`songs_full.json` 这类原始 dump 用 `ar`/`al`

写脚本时先确认手上是哪一套。

**4. 多歌单合并的 `sources` 字段。**
`fetch_playlist.js` 支持逗号分隔多 id，按曲目 id 去重，每首带 `sources: [playlist_id, ...]`。`sources` 长度 > 1 说明跨歌单重复收藏，是强偏好信号。

## 四、weapi 加密流程

1. preset key `0CoJUm6Qyw8W8jud`，IV `0102030405060708`
2. 明文 JSON → AES-128-CBC → base64，得 `step1`
3. 随机 16 字节 secret（hex 前 16 位）→ 用 secret 对 `step1` 再 AES-CBC → base64，得 `params`
4. secret 字节反转 → 转大整数 → RSA 模幂（指数 `010001`，网易云公钥）→ 补足 256 位十六进制，得 `encSecKey`
5. body：`params=<urlencode>&encSecKey=<urlencode>`

Node 内置 `crypto` 即可，无需第三方包。BigInt 做模幂。

## 五、节流与重试

- 批量请求间隔 **400-500ms**
- 单批失败重试 **3 次**，间隔 800ms
- 高频调用会被限流，表现为搜索突然全线返回 null（见 `verify-rules.md`）

## 六、没有开放写入接口

网易云**没有对外开放「建歌单 / 加曲」的 API**。最终写入只能由客户端完成：App →「我的」→ 三点 →「一键导入外部音乐」→ 截图导入 / 粘贴链接。

脚本侧能做的上限是生成一张高识别率的清单图。**不要承诺"全自动导入"**。
