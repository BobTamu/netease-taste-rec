# netease-taste-rec

从网易云歌单出发，生成**口味驱动**的推荐歌单：抓全量曲目 → 算可复现画像 → 叠加天气/假期/时间情境 → 按榜单与流派邻接拓展新歌 → 逐首校验原版 → 跨日去重 → 输出歌单或截图导入图。

这不是"读一遍歌单然后凭印象说几句"的封装。所有判断都落在**可核对的数字**上：画像由脚本算成 JSON，情境由接口取真实天气，原版校验逐首匹配艺人。取不到的外部事实会明确标注缺失，不臆造。

## 功能矩阵

| # | 能力 | 做什么 | 入口 | 产出 |
|---|---|---|---|---|
| 1 | 歌单抓取 | 单张全量；多张合并去重，标记跨歌单重复 | `scripts/fetch_playlist.js` | `playlist.json` / `merged.json` |
| 2 | 口味画像 | 艺人权重、语种比、时长分布、跨歌单重叠，全部可复现 | `scripts/build_profile.py` | `profile.json` |
| 3 | 情境判定 | 任意城市天气 → 经纬度+时区 → 基调 + 逐条依据 | `scripts/weather.py` | 基调 + 依据 |
| 4 | 假期/节气 | 中国法定节假日临近度、节气氛围标签 | `references/context-rules.md` | 情境标签 |
| 5 | 新歌拓展 | 抓新歌榜/原创榜，按画像筛同源新发行 | `scripts/fetch_playlist.js` + 榜单 id | 候选清单 |
| 6 | 艺人邻接 | 常听艺人 → 同流派代表，补冷门与欧美线 | 模型判断 + 校验 | 候选清单 |
| 7 | 原版校验 | 艺人精确匹配、排除合集/翻奏/Remix | `scripts/search_song.py` / `verify_batch.py` | canonical 标记 |
| 8 | 跨日去重 | 两遍比对（含歌名单独比对），同艺人 7 天 ≤2 首 | 见 `SKILL.md`「去重」节 | 净名单 |
| 9 | 导入图 | 网易云截图导入用清单图，单行不换行 | `scripts/make_import_image.py` | PNG |
| 10 | 落库 | 追加本期记录，供下期去重 | 历史 JSON | `daily_rec_history.json` |

**输入**：歌单 id（一个或多个）、城市、输出形态
**输出**：纯文本歌单（默认）／导入图 PNG／HTML 单页

## 快速开始

```bash
# 1. 抓歌单（逗号分隔可合并多张）
node scripts/fetch_playlist.js 3779629 playlist.json

# 2. 算画像
python scripts/build_profile.py playlist.json -o profile.json

# 3. 情境层
python scripts/weather.py "上海"

# 4. 原版校验
python scripts/search_song.py "云烟成雨" "房东的猫"

# 5. 出截图导入图
python scripts/make_import_image.py songs.json out.png --title "每日推荐 2026-10-23"
```

## 依赖

| 依赖 | 版本 | 用途 | 必需 |
|---|---|---|---|
| Node.js | ≥ 18（需内置 `fetch`） | 抓取歌单/榜单 | 是 |
| Python | ≥ 3.9 | 画像、天气、校验、出图 | 是 |
| Pillow | 任意近期版本 | 生成截图导入图 | 仅出图时需要 |

除 Pillow 外全部为标准库，不需要 npm 安装任何包。

```bash
python -m pip install pillow
```

## 安装

技能是「一个目录 + `SKILL.md`」，把仓库克隆到对应技能目录即可。

| 工具 | 用户级路径 | 项目级路径 |
|---|---|---|
| WorkBuddy | `~/.workbuddy-ai/skills/netease-taste-rec/` | `<项目>/.workbuddy-ai/skills/netease-taste-rec/` |
| Claude Code | `~/.claude/skills/netease-taste-rec/` | `<项目>/.claude/skills/netease-taste-rec/` |
| Codex | — | `<项目>/.agents/skills/netease-taste-rec/` |

```bash
git clone https://github.com/BobTamu/netease-taste-rec.git \
  ~/.claude/skills/netease-taste-rec
```

安装后**重启会话**，技能才会出现在可用列表里。冒烟测试与排错见 [`INSTALL.md`](INSTALL.md)。

## 目录结构

```
netease-taste-rec/
├── SKILL.md                       # 主文档：工作流与判定规则
├── INSTALL.md                     # 安装路径、依赖、冒烟测试、排错
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

## 已知限制

- 两个 weapi 接口都不返回发行时间，**年代分布算不出来**；要补只能逐首搜，慢且易限流
- 同名行政区（如「朝阳」）解析不可靠，需 `--pick N` 或直接给坐标
- 榜单噪音随周次上升，长期使用必须依赖艺人邻接拓展而非榜单
- 版权受限曲目在搜索侧不可索引（典型：周杰伦原版），遇到需换人
- 网易云**没有开放写入接口**，最终建歌单只能由客户端截图导入完成

## 免责与合规

本技能依赖未公开接口，属非官方用法（详见 [`references/compliance.md`](references/compliance.md)）：

- 用私有 weapi 读公开歌单违反平台用户协议，属**合同违约**，后果是限流/封号，不是著作权赔偿
- 输出仅限「歌名 — 歌手」这类事实性信息，**不扩展音频、歌词、封面**
- 接口随时可能失效或加强风控；高频调用可能触发账号处罚
- 不得将 cookie 或账号凭证写入脚本、日志或配置文件
- 仅个人自用；对外提供或收费前须单独评估
