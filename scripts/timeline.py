# -*- coding: utf-8 -*-
"""
时间轴引擎 —— 「说到哪画到哪」
============================
把 voice.json 里的**词级时间戳**，映射到 shot.json 里每个图元的出场时刻。

规则很简单：每个图元可以声明一个 cue 词（例如 "cue": "网关"），
引擎去配音的净文本流里找到这个词第一次被念到的时间，那一刻就是它开始画的时刻。
没有 cue 的图元，顺排在它前一个图元之后。

这就是整条链最关键的一环 —— 声画同步完全自动，不需要任何人工标注。
"""
import argparse
import json
import os
import re
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sketch import THEMES, Fonts, build_element, pick_font  # noqa: E402
from voice import strip_punct  # noqa: E402


def index_net_text(words):
    """把词级时间戳拼成净文本流，并记录每个字符属于第几个词。"""
    net = []
    pos2word = []
    for i, w in enumerate(words):
        n = strip_punct(w["w"])
        net.append(n)
        pos2word.extend([i] * len(n))
    return "".join(net), pos2word


def find_cue_time(cue, net, pos2word, words, cursor):
    """在净文本流里找 cue 词，返回 (时间, 新的游标)。找不到返回 (None, cursor)。"""
    nc = strip_punct(cue)
    if not nc:
        return None, cursor
    pos = net.find(nc, cursor)
    if pos < 0:
        pos = net.find(nc)          # 回退：允许非顺序出现
    if pos < 0:
        return None, cursor
    wi = pos2word[pos]
    return words[wi]["t"], pos + len(nc)


def build_timeline(shot, voice_info, lead=0.14, min_gap=0.22, min_draw=0.55,
                   font_path=None):
    """返回 {"total": 秒, "items": [...], "warning": 可选}"""
    theme = THEMES[shot.get("theme", "ink")]
    words = voice_info.get("words", [])
    total = float(voice_info.get("duration", 0.0))

    rng = random.Random(shot.get("seed", 11))
    fonts = Fonts(font_path or pick_font(shot.get("font")))
    elems = [build_element(e, theme, rng) for e in shot.get("elements", [])]

    net, pos2word = index_net_text(words)

    starts = []
    cursor = 0
    prev_start = 0.0
    for i, (spec, el) in enumerate(zip(shot.get("elements", []), elems)):
        s = None
        if el.cue and words:
            t, cursor = find_cue_time(el.cue, net, pos2word, words, cursor)
            if t is not None:
                s = max(0.0, t - lead)
        if s is None:
            s = 0.0 if i == 0 else prev_start + 0.35
        if i > 0 and s < starts[-1] + min_gap:
            s = starts[-1] + min_gap
        starts.append(round(s, 3))
        prev_start = s

    items = []
    for i, el in enumerate(elems):
        natural = el.duration()
        if i + 1 < len(starts):
            room = starts[i + 1] - starts[i]
        else:
            room = max(total - starts[i], min_draw)
        dur = min(natural, max(min_draw, room * 1.25))
        items.append({"i": i,
                      "start": starts[i],
                      "dur": round(dur, 3),
                      "natural": round(natural, 3),
                      "cue": el.cue,
                      "type": el.kind})

    # 密度体检：顺序画完所有元素所需时间，不应显著超过这条配音的长度。
    # 经验值：1 秒语音大约只能承载 0.5–0.6 个图元的绘制量。
    need = sum(it["natural"] for it in items)
    warn = None
    if total > 0 and need > total * 1.05:
        cap = max(1, int(total / 1.0))
        warn = ("本镜 %d 个图元需要 %.1fs 才能画完，而配音只有 %.1fs。"
                "建议拆成多镜，或把图元减到 %d 个以内。"
                % (len(items), need, total, cap))

    tl = {"total": round(total, 3), "items": items,
          "voice": voice_info.get("voice"), "seed": shot.get("seed", 11),
          "draw_need": round(need, 3)}
    if warn:
        tl["warning"] = warn
    return tl


def progress_at(timeline, t):
    """给定全局时间 t，返回 {元素索引: 本地进度 0..1}。"""
    out = {}
    for it in timeline["items"]:
        if t <= it["start"]:
            out[it["i"]] = 0.0
        else:
            d = it["dur"] if it["dur"] > 0 else 1.0
            out[it["i"]] = min(1.0, (t - it["start"]) / d)
    return out


def main():
    ap = argparse.ArgumentParser(description="词级时间戳 -> 图元出场时间轴")
    ap.add_argument("shot")
    ap.add_argument("voice_json")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--lead", type=float, default=0.14,
                    help="画面比声音提前多少秒出现")
    ap.add_argument("--font", default=None)
    a = ap.parse_args()

    with open(a.shot, encoding="utf-8") as f:
        shot = json.load(f)
    with open(a.voice_json, encoding="utf-8") as f:
        vinfo = json.load(f)

    tl = build_timeline(shot, vinfo, lead=a.lead, font_path=a.font)
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.voice_json)),
                                "timeline.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(tl, f, ensure_ascii=False, indent=2)

    for it in tl["items"]:
        print("  #%d %-6s start=%5.2fs dur=%5.2fs  cue=%s"
              % (it["i"], it["type"], it["start"], it["dur"], it["cue"]))
    print("配音 %.2fs / 绘制需要 %.2fs -> %s"
          % (tl["total"], tl.get("draw_need", 0), out))
    if tl.get("warning"):
        print("\n[注意] " + tl["warning"])


if __name__ == "__main__":
    main()
