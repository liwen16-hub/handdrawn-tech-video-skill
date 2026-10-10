---
name: ZLW_技术讲解视频
description: 把一句技术主旨做成竖版手绘讲解短视频（1080×1920，可直接发抖音 / 视频号 / 小红书）。覆盖口播脚本 → TTS 配音 → 手绘逐笔动画 → 视频合成。核心能力是「说到哪画到哪」：图元按配音的词级时间戳自动出场，零人工标注，中文零错字。当用户说「做个技术讲解视频」「把手绘讲解做成视频」「把这个知识点做成短视频」「做竖版口播教程」「做技术 IP 短视频」时使用。
agent_created: true
---

# 技术讲解视频

一句话主旨 → 竖版手绘讲解短片。

**核心承诺**：画面跟着配音走。说到「网关」两个字，网关的框就在那一刻开始画。
靠的是 edge-tts 的词级时间戳，**不需要任何人工标注**。

## 与其它手绘 / 视频技能的分工

| 需求 | 用什么 |
|---|---|
| **连续手绘讲解成片（竖版、发抖音）** | **本技能** |
| 单张白板手绘解说图（静态） | `白板手绘解说图` |
| 横版精品白板长片（8 步流程、人工标区域） | `whiteboard-video-studio` |
| AI 生图 + 运镜的「数字相册」式短视频 | `ZLW_ZH_AI短视频流水线` |

本技能的定位差异：**画面是代码生成的矢量线稿，不是 AI 生图**。由此换来三件事 ——
中文零错字、改稿只改 JSON、能与语音精确对齐。代价是画面偏冷、偏「板书感」，
不如 AI 生图温暖。技术类内容（架构、流程、对比、原理）用本技能；情感/故事类不合适。

## 环境（本机已就绪）

| 能力 | 组件 |
|---|---|
| 渲染 | Pillow 12.3（超采样抗锯齿） |
| 配音 | edge-tts 7.2.8（免费、无需 Key、**联网**） |
| 合视频 | ffmpeg 7.1（`imageio-ffmpeg` 自带） |
| 字体 | `STXINGKA.TTF` 行楷 → `simkai.ttf` 楷体 → 微软雅黑，自动择优 |

```bash
# ==== 改这里：换成你本机的实际路径（Windows 请用 C:/ 正斜杠形式）====
PY="C:/Users/<你的用户名>/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
SK="C:/Users/<你的用户名>/.workbuddy/skills/ZLW_技术讲解视频"

# 常见变体：
#   系统 Python：        PY="C:/Python313/python.exe"
#   Claude skills 目录： SK="<你的 skills 根目录>/ZLW_技术讲解视频"
#   路径含空格要加引号：   SK="C:/Users/me/My Skills/ZLW_技术讲解视频"
```

装依赖：`pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`

## 全流程

### 第 0 步：定三件事

**主旨 / 受众 / 目标时长。** 三者缺一不要往下走。
时长建议：**45–90 秒最优**（抖音技术区完播率甜点），超过 2 分钟必须靠强钩子撑。

### 第 1 步：写口播脚本

读 `references/script-method.md`。产出 `script.txt`（纯旁白文本，一段一条镜）。

关键：**一条镜 = 一个信息点 = 3–8 秒**。一镜里塞两个信息点，画面就装不下。

### 第 2 步：写 shot.json

读 `references/contract.md`（字段契约 + 密度规则 + 安全区），
需要版式参考时读 `references/primitives.md`。

这一步是全部工作量的大头 —— 需要把每镜要画的东西翻译成图元坐标。
直接跑 `examples/demo-shot.json` 跑通再改，比从空白写快。

**密度自查**：本镜图元数 ≤ 配音秒数 × 0.55。超了就拆镜。

### 第 3 步：配音 + 时间轴

```bash
"$PY" "$SK/scripts/voice.py" --text-file "$PRJ/script-01.txt" --out-dir "$PRJ" --voice xiaoxiao
"$PY" "$SK/scripts/timeline.py" "$PRJ/shot-01.json" "$PRJ/voice.json"
```

`voice.py` 产出 `voice.mp3 / voice.srt / voice.json`（含词级时间戳）。
`timeline.py` 把 cue 词映射成出场时刻，并做密度体检。

**看到 `[注意]` 告警就回去减图元或拆镜，不要硬跑。**

### 第 4 步：渲染

```bash
"$PY" "$SK/scripts/animate.py" "$PRJ/shot-01.json" "$PRJ/timeline.json" \
      -o "$PRJ/out/shot-01.mp4" --audio "$PRJ/voice.mp3" --scale 2
```

先看单帧再渲染全片，省时间：

```bash
"$PY" "$SK/scripts/animate.py" "$PRJ/shot-01.json" "$PRJ/timeline.json" \
      -o x.mp4 --preview 3.5 --preview-out "$PRJ/out/frame.png"
```

`--scale 2` 是超采样倍率，渲染慢一倍但线条明显更顺；赶时间用 `--scale 1.5`。

### 第 5 步：验收

1. **中文逐字核对** —— 这是本方案的核心优势，出现错字说明用了图片素材
2. **A 点检查**：每镜开头 0.5s 内第一个图元是否已开始落笔
3. **同步检查**：cue 词被念出的瞬间，对应图元是否刚起笔（允许提前约 0.14s）
4. **字幕**：不出现在 `y > 1536` 之外，单行 ≤ 16 字
5. **结尾**：最后一个图元画完后至少停 0.8s

### 第 6 步：多镜拼接 + 统一烧字幕

`animate.py` 逐镜产出的是**无字幕**片段。字幕必须在最后统一烧 ——
每镜单独烧会在缝合处出现样式跳变，而且字幕时间轴是本镜相对的，
只有拼接后才知道每镜的全局起点。

```bash
"$PY" "$SK/scripts/join.py" -o "$PRJ/out/final.mp4" \
      --clip "$PRJ/clips/shot-01.mp4" --clip ... \
      --voice "$PRJ/audio/shot-01/voice.json" --voice ...
```

`--clip` 和 `--voice` 按同一顺序给，脚本会：
1. 用 ffprobe 口径读出每个片段的**实际时长**（含 tail）做偏移量
2. 把各镜 `voice.json` 的 `subs` 合并成一条 ASS 字幕轨
3. concat + 重编码 + 烧字幕，一次成型

**字幕配色必须注意**：纸底是暖白 `[250,247,242]`，**白字会看不见**。
`join.py` 已按主题色固化为「墨黑文字 + 纸色描边」，位置 `MarginV=400`（≈ y=1520）。

**多镜并行渲染**（片长时首选）：

```bash
# 8 核机器开 3–4 个并发即可，单镜 1080×1920@scale2 约 2–3 分钟
for i in 01 02 03; do "$PY" "$SK/scripts/animate.py" ... & done; wait
```

## 规范速查

| 项 | 值 |
|---|---|
| 画幅 | 1080×1920（9:16 竖版） |
| 帧率 | 30 fps |
| 图元密度 | ≤ 配音秒数 × 0.55 |
| 单屏图元 | ≤ 5 |
| 字幕安全线 | y ≤ 1536 |
| 单条字幕 | ≤ 16 字，距底 20% |
| 字幕提前量 | `lead = 0.14s`（画面先于声音） |
| 手绘速度 | 780 px/s（约 1.2 秒画一个 320×140 框） |
| 逐字速度 | 0.125 秒/字 |
| 推荐时长 | 45–90 秒 |
| 编码 | H.264 + AAC，crf 20 |
| 元素字体 | `"font": "sans"`（黑体，术语用）/ `"kai"`（楷体）/ 或绝对路径 |
| 图元类型 | `title` `text` `box` `arrow` `line` `group` `dot` `scatter` `cylinder` `bars` |

**复合作图能绕开密度约束。** `scatter`（散点图）不管里面放几个点，都只算**一个**图元 ——
「聚类 / 相似度 / 向量空间」这类内容，一张散点图就能装下，比拆成四五个框划算得多。
`cylinder`（数据库圆柱）、`bars`（向量条）同理。用法见 `references/primitives.md` 模板 E / F。

**写 shot.json 前的预算自查**（比事后看告警省事）：

本镜图元数 ≤ 配音秒数 × 0.55。渲染后若 `timeline.py` 打印 `[注意]` 告警，
回去减图元或拆镜，**不要硬跑**。超了先缩框（周长是主要成本）或缩短框内文字，
再考虑加长旁白。

## 坑（实测）

1. **edge-tts 偶发 socket 超时**：`voice.py` 已内置 3 次重试。
   若连续失败，说明网络或服务端问题，不要反复重跑，先确认能连通。
2. **词级时间戳必须显式要**：`Communicate(..., boundary="WordBoundary")`。
   edge-tts ≥ 7 默认是 `SentenceBoundary`，拿不到词级时间戳，字幕和同步全废。
3. **绘制节奏跟不上语音节奏**：这是本方案最大的设计约束，不是 bug。
   8 秒配音只能承载 4–5 个图元。靠 `timeline.py` 的告警兜住。
4. **cue 顺序必须与绘制顺序一致**：数组里第 3 个图元的 cue 若比第 2 个更早，
   第 2 个会被顺延，同步失效。写 shot.json 时按语音顺序排图元。
5. **文字抖动必须逐帧稳定**：`animate.py` 给每个元素固定种子。
   若改成每帧随机，文字会抖成一片糊。
6. **中文路径**：Windows 下给 Python 传路径要用 `C:/...` 形式，
   Git Bash 的 `/c/...` 只在部分场景被自动转换，写在 `python -c` 字符串里必定失败。
7. **行楷的拉丁字形**：`PLC` `OPC UA` 这类缩写用行楷渲染会偏手写体，
   可读性下降。密集术语建议改用 `simhei.ttf`，或 `"font"` 单独指定。
8. **TTS 读法必须逐条试听**：缩写（PLC / OPC UA / MCP）可能被读成字母或整词，
   与画面预期不符。口播稿里的术语先用 `voice.py` 试听一小段再定稿。
9. **行楷写拉丁字母不好认**：实测 `AI` 会被看成 `A7`。**主题词、术语必须给该元素
   单独指定 `"font": "sans"`**（黑体），否则观众会误读。改的是元素，不是整镜字体。
10. **多镜并行渲染时日志名会撞车**：`animate.py` 的 stderr 日志已按输出文件名区分，
   别改回固定名，否则并发时互相覆盖。同理删日志失败不能中断渲染。
11. **字幕不能白字**：纸底是暖白，白字完全看不见。用墨黑字 + 纸色描边，
   位置 `MarginV=400`。`join.py` 已固化。
12. **复合作图里的长标签必须 `per_char: false`**：`bars` 的 `[ 0.4, -0.9, 0.6, ... ]`
   逐字出现要 2.75 秒，足以把一镜直接拖成超载。凡是超过 6 个字符的辅助标签
   （数字串、英文说明）一律设 `"per_char": false`，让它整体出现。
13. **散点图的点要铺开**：`dy` 别全挤在 0.1–0.4，否则坐标轴下半截全空、画面失衡。
   五个点左右时 `dy` 建议覆盖 0.2–0.8，聚类组与组之间拉开 0.4 以上。
14. **复合作图落进短镜要用 `dur_scale`**：整镜吃紧时，给 `scatter` / `cylinder` / `bars`
   加 `"dur_scale": 0.8` 整体提速，比砍内容划算。

## 文件

```
scripts/voice.py      配音 + 词级时间戳 + SRT
scripts/timeline.py   词级时间戳 -> 图元出场时间轴 + 密度体检
scripts/sketch.py     手绘图元引擎（10 种图元 + 抖动 + 逐笔绘制 + 元素级字体覆盖）
scripts/animate.py    逐帧渲染 -> 竖版 mp4（无字幕）
scripts/join.py       多镜拼接 + 统一烧字幕 -> 成片
references/contract.md     shot.json 契约（必读）
references/primitives.md   图元画法与版式参考
references/script-method.md 口播脚本方法论
examples/demo-shot.json    最小可跑示例
```
