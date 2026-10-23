---
name: netease-taste-rec
description: 从网易云歌单生成口味驱动的推荐歌单（每日或单次均可）：抓全量曲目、算可复现画像、叠加天气/假期/时间情境、按榜单与流派邻接拓展新歌、逐首校验原版、跨日去重、出截图导入图。当用户发来 music.163.com/playlist?id=... 链接，或要求「每日推荐 / 根据我的歌单推荐 / 给别人的歌单做一份 / 分析听歌口味 / 按天气推荐 / 按曲风歌手拓展新歌」时使用。支持多歌单合并与任意城市（含海外）天气，可换人客制化。Use when the user shares a NetEase Cloud Music playlist link, or asks for daily or one-off music recommendations, taste profiling, weather-driven or mood-driven playlists, multi-playlist merging, recommendations for someone else's playlist, or artist/genre-based song discovery. Covers weapi playlist fetching, reproducible taste profile, original-version verification, cross-day dedupe, screenshot-import image, and plain-text or HTML output.
agent_created: true
---

# netease-taste-rec：网易云歌单 → 口味驱动的推荐歌单

从网易云歌单出发，抓全量曲目 → 算画像 → 叠加**情境层（天气/假期/时间）**与**拓展层（新歌/新面孔）** → 校验原版 → 去重 → 输出歌单。

核心原则：**所有判断都要有数可核，取不到的外部事实就明说缺哪一路，不臆造。**

## 功能矩阵

| # | 能力 | 做什么 | 入口 | 产出 |
|---|---|---|---|---|
| 1 | 歌单抓取 | 抓单张歌单全量曲目；多张合并去重，标记跨歌单重复 | `fetch_playlist.js` | `playlist.json` / `merged.json` |
| 2 | 口味画像 | 艺人权重、语种比例、时长分布、跨歌单重叠，全部可复现 | `build_profile.py` | `profile.json` |
| 3 | 情境判定 | 任意城市天气 → 经纬度+时区 → 基调 + 逐条依据 | `weather.py` | 基调字符串 + 依据列表 |
| 4 | 假期/节气 | 中国法定节假日临近度、节气氛围标签 | 见 `references/context-rules.md` | 情境标签 |
| 5 | 新歌拓展 | 抓新歌榜/原创榜，按画像筛同源新发行 | `fetch_playlist.js` + 榜单 id | 候选清单 |
| 6 | 艺人邻接 | 常听艺人 → 同流派代表艺人，补冷门与欧美线 | 模型判断 + 校验 | 候选清单 |
| 7 | 原版校验 | 艺人精确匹配、排除合集/翻奏/Remix | `search_song.py` / `verify_batch.py` | canonical 标记 |
| 8 | 跨日去重 | 两遍比对（含歌名单独比对），同艺人 7 天 ≤2 首 | 见「去重」节 | 净名单 |
| 9 | 导入图 | 生成网易云截图导入用的清单图，单行不换行 | `make_import_image.py` | PNG |
| 10 | 落库 | 追加本期记录，供下一期去重 | 历史 JSON | `daily_rec_history.json` |

**输入**：歌单 id（一个或多个）、城市、输出形态。
**输出**：纯文本歌单（默认）／导入图 PNG／HTML 单页。

**可换人使用**：歌单 id 是参数，画像现算，城市是参数。换人需同时重建艺人邻接映射、替换节假日表——详见「换人使用」节。

## 触发场景

- 用户发来 `https://music.163.com/playlist?id=<id>`
- 「帮我做每日推荐 / 根据我的歌单推荐 / 分析下我的听歌口味」
- 「按天气、按是否快放假来推荐 / 推荐点新歌 / 曲目拓展」
- 「按别人的歌单给他做一份 / 合并我这几张歌单」

## 前置

- 需要知道：歌单 id、城市（天气要用）、输出形态（纯文本 / HTML）
- 缺城市就先问，不要默认某个城市

## 工作流

### 1. 抓歌单

```bash
node scripts/fetch_playlist.js <playlist_id> playlist.json
node scripts/fetch_playlist.js <id1>,<id2>,<id3> merged.json   # 多歌单合并
```

多歌单输出 `{playlists, songs}`，单歌单仍是 `{playlist, songs}`（向后兼容）。合并后每首歌带 `sources: [id, ...]`，长度 > 1 表示跨歌单重复收藏，是强偏好信号。

必须走 `trackIds` + `song/detail` 两步，直接读 `tracks` 只能拿到约 6 首。接口可用性清单见 `references/netease-api.md`。

### 2. 算画像（落库，不要凭印象）

**不要"读一遍歌单然后口头总结"**——同一份歌单两次归纳的艺人排序可能不同，且没有数可核对。先算成 JSON：

```bash
python scripts/build_profile.py playlist.json -o profile.json
python scripts/build_profile.py me.json partner.json --weights 3,1 -o profile.json
```

加权规则：每张歌单默认贡献相同总权重，内部每首分 `1/len(playlist)`，大歌单压不死小歌单。

读 `artists_top`、`language`、`duration_median_min`、`overlap`。注意：

- `language` 的 CJK 比例**按艺人名判定，不看歌名**（按歌名判会把外语比例虚高约 4 个百分点）
- `decades` 通常为空，接口不返回发行时间，**不要为了填字段编年代分布**
- `total_unique_songs` 小于输入首数 → 歌单里有重复曲目，可提示用户

**流派标签仍由模型判断**，但必须挂在上述统计上：先说依据是哪几个艺人、多少首，再给结论。字典匹配只是辅助。

### 3. 情境层

**a) 天气**——用脚本，不要手拼 URL：

```bash
python scripts/weather.py "上海"
python scripts/weather.py "Tokyo" --json
python scripts/weather.py "朝阳" --pick 1
```

脚本完成 城市名 → 经纬度+时区 → 预报 → 基调，并逐条打印依据。

手拼 URL 有两个必踩的坑：接口吃经纬度不吃城市名；`timezone` 写死会让「今天」整体错位几小时，日期一错，星期、假期倒计时、情境行全跟着错且**不报错**。

**b) 假期临近度**：法定节假日 ≤7 天或假期中 → 假期模式；假期后 1-2 天 → 收心；周五晚 → 周末；周日晚 → 收尾。节假日表每年变动，必须核对当年官方安排。2026 年已核实的表见 `references/context-rules.md`。

**c) 时间/节气**：深夜 22 点后 → 安静、器乐、低 bpm；清晨 → 明亮、民谣；节气前后 3 天可作氛围标签。

三路信号缺一路就跳过并在输出里注明，不臆造。天气接口挂了就说"天气接口不可用"。

### 4. 拓展层

```bash
node scripts/fetch_playlist.js 3779629 newsong.json    # 新歌榜
node scripts/fetch_playlist.js 2884035 original.json   # 原创榜
```

榜单 id 可当普通歌单抓。筛的时候排除 OST / 游戏联动 / 短视频神曲 / 电音 Remix。

**榜单池会枯竭**：同一批新面孔会连续多轮重复出现，且噪音随周次上升（整张专辑上架、游戏联动、影视 OST、Rap Battle 合集）。对策是情境组与华语组优先走「画像艺人 → 同流派代表艺人」邻接，榜单只补 2-4 首真正的新发行。

已验证可用的邻接池见 `references/context-rules.md` 与下节。

### 5. 原版校验（不可跳过）

```bash
python scripts/search_song.py "青花瓷" "周杰伦"
python scripts/verify_batch.py candidates.txt out.json
```

- 艺人必须**精确匹配**（normalize 后相等），不是包含——`周杰伦.` 这类翻唱歌手名会冒充
- 命中多个取**发行年份最早**且**专辑非合集**的版本
- 目标艺人**不在 `artists` 首位 = 翻奏信号**，一律人工复核
- 返回 `null` 先重试一次再判定不存在（限流会伪装成不存在）
- 校验不过就换曲，**不编造歌曲**

完整口径（合集漏判、`pick()` 的 stdout 坑、字段结构差异）见 `references/verify-rules.md`。

### 6. 去重（必须两遍）

**第一遍**按 `(歌名, 首艺人)` 比对，快但会漏——榜单是 `artists` 数组（`黄龄/邓典`），历史是单艺人写法（`黄龄`），首艺人能过而歌名其实撞了。

**第二遍（终稿前，强制）**只按歌名 normalize 后单独比对，不掺艺人：

```python
def norm(s):
    return re.sub(r"[\s\.\-_·、,，!！?？'\"“”‘’()（）\[\]【】]", "", s).lower()
pl_names = {norm(s["name"]) for s in playlist}
hist_names = {norm(x["name"]) for r in history for x in r["songs"]}
```

实测：第一遍过后仍会漏 5 首左右，第二遍全部抓出。**不做第二遍不要交付。**

另外约束：同一艺人 7 天内不超过 2 首。

### 7. 输出

**默认纯文本**：编号 + `歌名 — 歌手`，按 情境组 / 华语拓展 / 欧美拓展 分组，每组前一行写推荐依据（一句，含天气与星期等可核事实）。

需要图片版时（网易云只能靠客户端截图导入）：

```bash
python scripts/make_import_image.py songs.json out.png --title "每日推荐 2026-10-23"
```

白底黑字、单行一首、**不换行**（换行会破坏 OCR 的行归属）。也接受纯文本输入。

需要 HTML 时：单文件深色主题，头部日期+来源歌单，统计卡，画像卡片，推荐行（歌名 + 艺人·专辑·标签 + 理由），页脚写明方法与局限。注意 HTML 里若要写年份，必须逐首核实，合集命中的年份会失真。

### 8. 落库

把本期 `date` / `round` / `context` / `songs` 追加进历史文件，供下一期去重。结构见 `INSTALL.md`。

## 换人使用（客制化边界）

代码层已完全参数化：歌单 id 是命令行参数，画像现算，城市是参数。换人只换输入。

**但三样是绑定的，换人必须一起换：**

1. **艺人邻接映射**——从画像推的，换人要重建
2. **节假日表**——中国的；换地区要换表，否则"距假期几天"整个判断是错的
3. **节气标签**——只在中国语境下用；南半球季节还相反

**做不到**：从歌单推断年龄、职业、收听场景（通勤/健身/睡前）。这类只能问，不能猜。

## 硬约束

- 不编造歌曲、不编造年份、不编造天气
- 外部事实（天气/假期）取不到就注明缺失，不用"大概"填充
- 校验不过的曲子换掉，不硬塞
- **不承诺"全自动导入"**：网易云没有开放写入接口，最终写入只能由客户端完成
- 只输出「歌名 — 歌手」；**不扩展音频、歌词、封面**（原因见 `references/compliance.md`）

## 参考文档

| 文件 | 内容 |
|---|---|
| `references/netease-api.md` | 接口可用性、weapi 加密、字段陷阱、节流 |
| `references/verify-rules.md` | 原版校验口径、合集漏判、翻奏信号、null 处理 |
| `references/context-rules.md` | 天气/假期/节气口径、地理编码坑、WMO 表、中国假期表 |
| `references/compliance.md` | 合规三层：违约 vs 侵权 vs 不正当竞争 |
| `INSTALL.md` | 安装路径、依赖、冒烟测试、排错 |

## 已知限制

- 两个 weapi 接口都不返回发行时间，年代分布算不出来；要补只能逐首搜，慢且易限流
- 同名行政区（如「朝阳」）解析不可靠，需 `--pick` 或直接给坐标
- 榜单噪音随周次上升，长期使用必须依赖邻接拓展而非榜单
- 版权受限曲目在搜索侧不可索引（典型：周杰伦原版），遇到换人
