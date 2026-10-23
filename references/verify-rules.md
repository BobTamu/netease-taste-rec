# 原版校验口径

推荐歌单里出现翻唱、Remix、精选集，等于交付次品。本文件说明校验规则、已知漏判、以及怎么处理。

## 一、基本规则

1. **艺人必须精确匹配**（normalize 后相等），不是包含匹配。
   `周杰伦.` 这类翻唱歌手名会冒充成功，必须排除。
2. 命中多个版本时，取**发行年份最早**且**专辑名非合集**的。
3. 纯文本输出不写年份，所以年份只用于**排序**，不要写进交付物。
4. 榜单直接取到的 id 无需再搜（榜单条目基本都是首版发行），但仍需与歌单/历史去重。

## 二、normalize 规则

```python
def norm(s):
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = s.replace("&", " and ")
    return re.sub(r"[\s\-_/,\.\(\)\[\]《》'\"!:：?？·+，,。~—-]", "", s)
```

要点：NFKC 归一（全角转半角）、`&` → `and`、去掉所有标点与空白。否则 `Fade into You` 与 `Fade Into You` 会被判成两首歌。

## 三、合集判定 `COMP_KW`

当前正则覆盖：`精选|合辑|慢摇|必點|怀旧|岁月|金曲|演唱会|巡演|Live|live|KTV|彩铃|抖音|DJ|DJ版|story|Chart`

**只覆盖中文合集词，这是已知缺陷。** 以下三类会漏判为 canonical：

1. **欧美合辑**：`Now That's What I Call Music`、`Megahits`、`Original Album Classics`、`Way To Blue - An Introduction To Nick Drake`
2. **合作/混音版**：`Bang!` 命中 `AJR/Younotus`
3. **翻奏合集**：见下节

处理方式：`note` 字段为 `canonical` 不代表真原版，**必须人工看 `album` 和 `artists` 字段**。

## 四、「艺人命中但不是首艺人」= 翻奏信号

实例：`Comptine d'un autre été` 查到 `Melody'ssée/Yann Tiersen`（钢琴翻奏合集，2023）。`pick()` 因为 `artists` 数组里含 Yann Tiersen 就判了 canonical，但首艺人是翻奏者。

规则：**目标艺人排在 `artists` 第二位及以后时，一律人工复核。** 纯器乐曲尤其容易被翻奏专辑抢位。

## 五、`null` 的三种成因

1. **艺人名带后缀差异**：`莉莉周她说` vs `莉莉周她说 Lily Chou-Chou Lied`
2. **独立音乐人未索引**
3. **版权受限**（典型：周杰伦原版曲目在搜索侧不可索引，遇到就换人）

**关键：`null` 也可能是限流/瞬时故障。** 批量连跑 30+ 次后，原本能搜到的曲目（Hozier、伍佰等）会突然全线 null。

→ **先对同一查询重试一次**，仍为 null 才判定不存在。否则会误杀大量正常曲目。

## 六、调用 `pick()` 的两个坑

**坑 1：不要自己包 stdout。**
`search_song` 在 import 时会重绑 `sys.stdout`。再包一层 `io.TextIOWrapper(sys.stdout.buffer, ...)`，旧包装器 GC 时会关掉 buffer，报 `ValueError: I/O operation on closed file`。直接 import 后 print 即可。

**坑 2：返回结构。**
`pick()` 返回的 `artists` 是**字符串数组**、`album` 是**字符串**，不是对象。而 `search()` 的原始响应里是 `artists[].name` / `album.name`。两者不同层，别混。

## 七、批量校验

```bash
python scripts/verify_batch.py candidates.txt out.json
```

`candidates.txt` 每行 `歌名 | 艺人 [| group]`，`#` 开头为注释。已内置单次重试、节流、落 JSON。

`note` 为 `compilation-album fallback` 的条目是回退到合集的结果，需复核。
