# 安装说明

## 一、依赖

| 依赖 | 版本 | 用途 | 必需 |
|---|---|---|---|
| Node.js | ≥ 18（需内置 `fetch`） | 抓取歌单/榜单 | 是 |
| Python | ≥ 3.9 | 画像、天气、校验、出图 | 是 |
| Pillow | 任意近期版本 | 生成截图导入图 | 仅出图时需要 |

除 Pillow 外**全部为标准库**，不需要 npm/pip 装其他包。

```bash
python -m pip install pillow
```

## 二、安装路径

技能是「一个目录 + SKILL.md」，把整个 `netease-taste-rec/` 目录复制到对应位置即可。

| 工具 | 用户级路径 | 项目级路径 |
|---|---|---|
| WorkBuddy | `~/.workbuddy-ai/skills/netease-taste-rec/` | `<项目>/.workbuddy-ai/skills/netease-taste-rec/` |
| Claude Code | `~/.claude/skills/netease-taste-rec/` | `<项目>/.claude/skills/netease-taste-rec/` |
| Codex | — | `<项目>/.agents/skills/netease-taste-rec/` |

Windows 下 `~` 通常是 `C:\Users\<用户名>`。

安装后**重启会话**，技能才会出现在可用列表里。

## 三、目录结构

```
netease-taste-rec/
├── SKILL.md                       # 主文档：工作流与判定规则
├── INSTALL.md                     # 本文件
├── references/
│   ├── netease-api.md             # 接口可用性、weapi 加密、字段陷阱
│   ├── verify-rules.md            # 原版校验口径、漏判、null 处理
│   ├── context-rules.md           # 天气/假期/节气口径、地理编码坑
│   └── compliance.md              # 合规边界
└── scripts/
    ├── fetch_playlist.js          # 抓歌单/榜单（单张或多张合并）
    ├── build_profile.py           # 画像落库（可复现统计）
    ├── weather.py                 # 城市名 → 天气 → 基调
    ├── search_song.py             # 单曲原版校验
    ├── verify_batch.py            # 批量原版校验
    └── make_import_image.py       # 生成截图导入图
```

## 四、冒烟测试

装完按顺序跑一遍，全部通过才算可用。先建一个临时目录（**不要用 `/tmp`**，Windows 下 Node 解析不了 POSIX 路径）：

```bash
mkdir -p ./smoke
```

```bash
# 1) 抓一张公开歌单（榜单 id 3779629 = 新歌榜，稳定可用）
node scripts/fetch_playlist.js 3779629 ./smoke/t.json
# 期望：saved 100 unique songs from 1 playlist(s)

# 2) 画像
python scripts/build_profile.py ./smoke/t.json -o ./smoke/p.json
# 期望：sources / unique songs / language / top artists 各行都打印

# 3) 天气（任意城市）
python scripts/weather.py "上海"
# 期望：地点、当地时间、当前温湿度、基调、依据

# 4) 单曲校验
python scripts/search_song.py "云烟成雨" "房东的猫"
# 期望：JSON，note 为 canonical，year 为 2017
# （换周杰伦等版权受限曲目会返回 null，那是限制不是故障）

# 5) 出图
python scripts/make_import_image.py ./smoke/t.json ./smoke/t.png
# 期望：saved .../t.png (1200x6406, 100 songs, 1 groups)
```

第 5 步喂原始 dump 会压成单组；喂带 `groups` 的 JSON 则保留分组。

## 五、排错

| 现象 | 原因 | 处理 |
|---|---|---|
| `playlist fetch failed: 20001` | 用了废弃的 `/api/playlist/detail` | 改走 `/weapi/v3/playlist/detail` |
| 抓到的歌只有 6 首 | 直接读了 `tracks` 字段 | 必须走 `trackIds` + `song/detail` |
| 校验大量返回 null | 被限流 | 单次重试一次；仍 null 再判定不存在，并放慢节奏 |
| `ValueError: I/O operation on closed file` | import 后又包了一层 stdout | 不要再包 TextIOWrapper |
| `Pillow is required` | 没装 Pillow | `pip install pillow` |
| 城市解析到错误地点 | 同名地名歧义 | 用 `--pick N` 选候选，或 `--lat/--lon` 直接给坐标 |
| 图片里出现换行的歌名 | 字号过大 | 脚本已自动缩字号；若仍换行，缩短歌名或加大 `--width` |
| `ModuleNotFoundError` | Python 版本过低 | 升到 3.9+ |

## 六、数据文件约定

技能本身不存数据，以下文件由使用方维护（路径自定，通过参数传入）：

- `playlist.json` / `merged.json` — 歌单 dump
- `profile.json` — 画像
- `daily_rec_history.json` — 历史推荐，用于跨日去重

`daily_rec_history.json` 建议结构：

```json
{
  "generated": [
    {
      "date": "2026-10-23",
      "round": 8,
      "context": "上海 20.3℃ 晴转阵雨 + 霜降 + 周五",
      "songs": [
        {"group": "ctx", "name": "秋，落日与芦苇荡", "artist": "雪叶子"}
      ]
    }
  ]
}
```
