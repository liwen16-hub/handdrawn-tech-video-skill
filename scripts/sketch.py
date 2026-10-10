# -*- coding: utf-8 -*-
"""
手绘图元引擎
============
把 shot.json 里的图元定义，编译成「带手绘抖动的笔画序列」，并支持
按进度 t（0..1）逐笔绘制 —— 这是逐笔动画的基础。

坐标一律使用 1080x1920 逻辑像素（竖版）。渲染时按 S 倍超采样后缩小，
从而得到抗锯齿的手绘线条。

图元类型
--------
  title     标题文字 + 手绘下划线
  text      正文文字
  box       圆角框 + 框内文字
  arrow     带箭头直线
  line      普通直线（可 dashed，可带中点标签）
  group     虚线分组框 + 左上角标签
  dot       手画圆点 + 可选标签
  scatter   散点图 / 向量空间（整张图算一个图元，内部点依次亮起）
  cylinder  数据库圆柱
  bars      一排竖条，表示「一串数字」即向量的分量

每个图元可带 "cue" 字段 —— 一个词，用于在配音里定位它的出场时刻。
详见 references/contract.md。
"""
import math
import os
import random

from PIL import ImageFont

TAU = math.pi * 2

# ---- 手绘节奏基准（逻辑像素/秒、秒/字）----
# 750 px/s 约等于「一笔画完一个 320x140 的框要 1.2 秒」，是看着舒服的板书速度。
DRAW_SPEED = 780.0
CHAR_SPEED = 0.125
MIN_STROKE = 0.18
MIN_TEXT = 0.22


# ----------------------------------------------------------------- 主题
THEMES = {
    "ink": {
        "bg": (250, 247, 242),
        "ink": (32, 32, 34),
        "accent": (13, 72, 206),
        "muted": (122, 130, 146),
        "box_fill": (231, 238, 252),
        "box_line": (13, 72, 206),
        "warn": (176, 58, 48),
        "line": (72, 78, 90),
    },
}

# 手写体优先：华文行楷 > 华文楷体 > 楷体 > 微软雅黑
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\STXINGKA.TTF",
    r"C:\Windows\Fonts\STKAITI.TTF",
    r"C:\Windows\Fonts\simkai.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]

# 元素级字体别名。行楷渲染 PLC / OPC UA / AI 这类拉丁缩写可读性会下降，
# 需要工整字形时给该元素单独指定 "font": "sans"。
FONT_ALIASES = {
    "sans": [r"C:\Windows\Fonts\simhei.ttf",
             r"C:\Windows\Fonts\msyh.ttc",
             r"C:\Windows\Fonts\Deng.ttf"],
    "hei": [r"C:\Windows\Fonts\simhei.ttf",
            r"C:\Windows\Fonts\msyhbd.ttc"],
    "kai": [r"C:\Windows\Fonts\STXINGKA.TTF",
            r"C:\Windows\Fonts\STKAITI.TTF",
            r"C:\Windows\Fonts\simkai.ttf"],
}


def resolve_font(name):
    """把字体别名或绝对路径解析成可用路径；解析不到返回 None（用默认字体）。"""
    if not name:
        return None
    if os.path.exists(str(name)):
        return str(name)
    for p in FONT_ALIASES.get(str(name).lower(), []):
        if os.path.exists(p):
            return p
    return None


def pick_font(explicit=None):
    """返回可用的中文字体路径（优先手写体）。"""
    cands = ([explicit] if explicit else []) + FONT_CANDIDATES
    for p in cands:
        if p and os.path.exists(p):
            return p
    raise FileNotFoundError("找不到可用中文字体，请用 --font 指定")


class Fonts(object):
    """按 (字体路径, 逻辑字号) 缓存 TrueType 字体对象。"""

    def __init__(self, path):
        self.path = path
        self._cache = {}

    def get(self, size, scale=1.0, path=None):
        p = path or self.path
        key = (p, int(round(size * scale)))
        if key not in self._cache:
            self._cache[key] = ImageFont.truetype(p, key[1])
        return self._cache[key]

    def measure(self, text, size, scale=1.0, path=None):
        f = self.get(size, scale, path)
        return f.getlength(text)


# ----------------------------------------------------------------- 手绘抖动
def hand_line(p0, p1, rng, amp=1.2, step=16.0, waves=1.7):
    """一条带手抖的直线：端点严格固定，中段按正弦包络抖动。"""
    x0, y0 = p0
    x1, y1 = p1
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return [p0, p1]
    nx, ny = -dy / length, dx / length          # 单位法向
    n = max(2, int(length / step))
    phase = rng.uniform(0.0, TAU)
    out = []
    for i in range(n + 1):
        t = i / float(n)
        env = math.sin(math.pi * t) ** 0.7      # 两端收，中段鼓
        off = rng.uniform(-amp, amp) * env
        off += math.sin(t * math.pi * waves + phase) * amp * 0.55 * env
        out.append((x0 + dx * t + nx * off, y0 + dy * t + ny * off))
    return out


def hand_polyline(points, rng, amp=1.1):
    """对已有折线逐点加手抖（用于闭合路径，首尾点保持不动）。"""
    n = len(points)
    if n < 3:
        return list(points)
    out = []
    for i, (x, y) in enumerate(points):
        if i == 0 or i == n - 1:
            out.append((x, y))
            continue
        px, py = points[i - 1]
        qx, qy = points[i + 1]
        tx, ty = qx - px, qy - py
        seg = math.hypot(tx, ty) or 1.0
        nx, ny = -ty / seg, tx / seg
        env = math.sin(math.pi * i / float(n - 1)) ** 0.7
        off = rng.uniform(-amp, amp) * env
        out.append((x + nx * off, y + ny * off))
    return out


# ----------------------------------------------------------------- 几何
def _seg_points(p0, p1, step=16.0):
    length = math.dist(p0, p1)
    n = max(1, int(length / step))
    return [(p0[0] + (p1[0] - p0[0]) * i / float(n),
             p0[1] + (p1[1] - p0[1]) * i / float(n)) for i in range(n + 1)]


def _arc_points(cx, cy, r, a0, a1, n=9):
    """角度制，屏幕坐标（y 轴向下）。"""
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / float(n))),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / float(n))))
            for i in range(n + 1)]


def near_circle(cx, cy, r, rng, amp=1.0, start=-95.0, sweep=330.0, n=14):
    """手绘近似圆：留一个小缺口、半径轻微起伏，像人手随手画的圈。

    用于散点图的「点」。缺口让每个点看起来是独立画的，而不是机器印的。
    """
    pts = []
    for i in range(n + 1):
        a = math.radians(start + sweep * i / float(n))
        rr = r * (1.0 + rng.uniform(-0.05, 0.05) * amp)
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


def ellipse_path(cx, cy, rx, ry, a0=0.0, a1=360.0, n=18, rng=None, amp=1.0):
    """椭圆弧 / 整圈（角度制，屏幕坐标 y 向下）。

    a0=180 -> a1=0 得到下凸弧；a0=180 -> a1=360 得到上凸弧。
    给 rng 会加轻微起伏，用于圆柱口的「手画椭圆」质感。
    """
    pts = []
    for i in range(n + 1):
        a = math.radians(a0 + (a1 - a0) * i / float(n))
        jx = rng.uniform(-amp, amp) if rng else 0.0
        jy = rng.uniform(-amp, amp) if rng else 0.0
        pts.append((cx + rx * math.cos(a) + jx, cy + ry * math.sin(a) + jy))
    return pts


def round_rect_path(x, y, w, h, r, step=16.0):
    """圆角矩形闭合路径（顺时针），返回点列。"""
    r = max(0.0, min(r, w / 2.0, h / 2.0))
    if r < 1.0:
        pts = _seg_points((x, y), (x + w, y), step)
        pts += _seg_points((x + w, y), (x + w, y + h), step)[1:]
        pts += _seg_points((x + w, y + h), (x, y + h), step)[1:]
        pts += _seg_points((x, y + h), (x, y), step)[1:]
        return pts
    pts = []
    pts += _arc_points(x + r, y + r, r, 180, 270)                      # 左上角
    pts += _seg_points((x + r, y), (x + w - r, y), step)[1:]           # 上边
    pts += _arc_points(x + w - r, y + r, r, 270, 360)[1:]              # 右上角
    pts += _seg_points((x + w, y + r), (x + w, y + h - r), step)[1:]   # 右边
    pts += _arc_points(x + w - r, y + h - r, r, 0, 90)[1:]             # 右下角
    pts += _seg_points((x + w - r, y + h), (x + r, y + h), step)[1:]   # 下边
    pts += _arc_points(x + r, y + h - r, r, 90, 180)[1:]               # 左下角
    pts += _seg_points((x, y + h - r), (x, y + r), step)[1:]           # 左边
    pts.append(pts[0])                                                 # 闭合
    return pts


def dashed_path(points, dash=18.0, gap=12.0):
    """把点列切成虚线，返回多段点列。"""
    segs = []
    carry = 0.0
    drawing = True
    cur = [points[0]]
    for i in range(1, len(points)):
        p0, p1 = points[i - 1], points[i]
        seg_len = math.dist(p0, p1)
        consumed = 0.0
        while consumed < seg_len - 1e-9:
            remain = (dash if drawing else gap) - carry
            take = min(remain, seg_len - consumed)
            consumed += take
            carry += take
            f = consumed / seg_len if seg_len else 0.0
            pt = (p0[0] + (p1[0] - p0[0]) * f, p0[1] + (p1[1] - p0[1]) * f)
            if drawing:
                cur.append(pt)
            if carry >= (dash if drawing else gap) - 1e-9:
                if drawing:
                    if len(cur) >= 2:
                        segs.append(cur)
                    cur = []
                else:
                    cur = [pt]
                carry = 0.0
                drawing = not drawing
    if drawing and len(cur) >= 2:
        segs.append(cur)
    return segs


# ----------------------------------------------------------------- 数据结构
class Stroke(object):
    """一条笔画。delay/dur 相对元素起点，用于控制笔画顺序。"""

    __slots__ = ("pts", "width", "color", "delay", "dur", "_cum")

    def __init__(self, pts, width=3.0, color=None, delay=0.0, dur=0.0):
        self.pts = pts
        self.width = width
        self.color = color
        self.delay = delay
        self.dur = dur
        self._cum = None

    @property
    def cum(self):
        if self._cum is None:
            c = [0.0]
            for i in range(1, len(self.pts)):
                c.append(c[-1] + math.dist(self.pts[i - 1], self.pts[i]))
            self._cum = c
        return self._cum

    @property
    def length(self):
        return self.cum[-1]


class TextRun(object):
    """一段文字。per_char=True 时逐字出现（模拟书写）。
    font 为该段文字的字体路径覆盖（None = 用镜头默认字体）。"""

    __slots__ = ("text", "x", "y", "size", "color", "align",
                 "per_char", "wobble", "delay", "dur", "font")

    def __init__(self, text, x, y, size=34, color=None, align="left",
                 per_char=True, wobble=True, delay=0.0, dur=0.0, font=None):
        self.text = text
        self.x = x
        self.y = y
        self.size = size
        self.color = color
        self.align = align
        self.per_char = per_char
        self.wobble = wobble
        self.delay = delay
        self.dur = dur
        self.font = font


def stroke_dur(st):
    """一条笔画的绘制时长。显式指定优先，否则按长度/手绘速度推算。"""
    return st.dur if st.dur else max(st.length / DRAW_SPEED, MIN_STROKE)


def text_dur(tr):
    """一段文字的入场时长。显式指定优先，否则按字数推算。"""
    n = len(tr.text) if tr.per_char else 1
    return tr.dur if tr.dur else max(n * CHAR_SPEED, MIN_TEXT)


class Element(object):
    __slots__ = ("kind", "strokes", "texts", "cue", "spec")

    def __init__(self, kind, strokes, texts, cue=None, spec=None):
        self.kind = kind
        self.strokes = strokes
        self.texts = texts
        self.cue = cue
        self.spec = spec or {}

    def duration(self):
        """本元素画完所需时长（秒）—— 与 element_progress 用同一套口径。"""
        total = 0.0
        for st in self.strokes:
            total = max(total, st.delay + stroke_dur(st))
        for tr in self.texts:
            total = max(total, tr.delay + text_dur(tr))
        return max(total, 0.35)


# ----------------------------------------------------------------- 图元编译
def _labeled_box(x, y, w, h, r, rng, theme, width, amp, fill=True):
    """圆角框：闭合路径一笔画完 + 可选填充。"""
    path = round_rect_path(x, y, w, h, r)
    jittered = hand_polyline(path, rng, amp=amp)
    strokes = []
    if fill:
        strokes.append(Stroke(jittered, width=width, color=theme["box_line"]))
    else:
        strokes.append(Stroke(jittered, width=width, color=theme["box_line"]))
    return strokes, jittered


def build_element(spec, theme, rng, default_font_size=34):
    """把一条图元定义编译成 Element（笔画已抖动完毕）。"""
    kind = spec.get("type", "box")
    cue = spec.get("cue")
    color = tuple(spec["color"]) if spec.get("color") else None
    strokes = []
    texts = []
    x = float(spec.get("x", 0))
    y = float(spec.get("y", 0))
    width = float(spec.get("width", 3.2))
    amp = float(spec.get("jitter", 1.15))
    fs = int(spec.get("size", default_font_size))
    fc = color or theme["ink"]
    # 元素级字体覆盖：行楷写拉丁缩写不好认，术语密集的元素可指定 "font": "sans"
    font_override = resolve_font(spec.get("font"))

    if kind == "title":
        texts.append(TextRun(spec.get("text", ""), x, y, size=fs, color=fc,
                             align=spec.get("align", "left"),
                             per_char=spec.get("per_char", True),
                             delay=0.0, font=font_override))
        # 手绘下划线
        tw = spec.get("text_width")
        if tw is None:
            tw = _estimate_width(spec.get("text", ""), fs)
        uy = y + fs * 1.22
        pts = hand_line((x, uy), (x + tw, uy), rng, amp=amp * 1.5, step=13)
        # 下划线等标题写完约 60% 再落笔，像人写完标题顺手划一道
        head_dur = max(len(spec.get("text", "")) * CHAR_SPEED, MIN_TEXT)
        strokes.append(Stroke(pts, width=width * (spec.get("rule_scale", 1.25)),
                              color=color or theme["accent"],
                              delay=head_dur * 0.6,
                              dur=max(abs(tw) / (DRAW_SPEED * 0.75), MIN_STROKE)))
    elif kind == "text":
        texts.append(TextRun(spec.get("text", ""), x, y, size=fs, color=fc,
                             align=spec.get("align", "left"),
                             per_char=spec.get("per_char", True),
                             font=font_override))
    elif kind == "box":
        w = float(spec.get("w", 240))
        h = float(spec.get("h", 110))
        r = float(spec.get("r", 16))
        s, path = _labeled_box(x, y, w, h, r, rng, theme, width, amp)
        strokes += s
        label = spec.get("text", "")
        if label:
            lsize = int(spec.get("text_size", min(fs, 40)))
            tw = _estimate_width(label, lsize)
            texts.append(TextRun(label, x + w / 2.0, y + h / 2.0 - lsize * 0.52,
                                 size=lsize, color=fc, align="center",
                                 per_char=spec.get("per_char", True),
                                 delay=max(path_len(path) / DRAW_SPEED, MIN_STROKE) + 0.06,
                                 font=font_override))
    elif kind in ("line", "arrow"):
        p0 = (x, y)
        p1 = (float(spec.get("x2", x + 100)), float(spec.get("y2", y)))
        pts = hand_line(p0, p1, rng, amp=amp, step=15)
        if spec.get("dash"):        # 虚线：表示「距离 / 相似度」这类度量关系
            segs = dashed_path(pts, dash=float(spec.get("dash", 20)),
                               gap=float(spec.get("gap", 14)))
            for i, seg in enumerate(segs):
                strokes.append(Stroke(seg, width=width * 0.9, color=fc,
                                      delay=i * 0.02, dur=0.06))
        else:
            strokes.append(Stroke(pts, width=width, color=fc))
        line_dur = max(path_len(pts) / DRAW_SPEED, MIN_STROKE)
        if spec.get("label"):       # 线段中点标签（如「近」「远」）
            lsize = int(spec.get("text_size", 32))
            lc = tuple(spec["label_color"]) if spec.get("label_color") else fc
            texts.append(TextRun(spec["label"],
                                 (p0[0] + p1[0]) / 2.0 + float(spec.get("label_dx", 0)),
                                 (p0[1] + p1[1]) / 2.0 - lsize * 1.45,
                                 size=lsize, color=lc, align="center",
                                 per_char=spec.get("per_char", True),
                                 delay=line_dur * 0.75, font=font_override))
        if kind == "arrow":
            head = float(spec.get("head", 26))
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            L = math.hypot(dx, dy) or 1.0
            ang = math.atan2(dy, dx)
            tip_delay = max(path_len(pts) / DRAW_SPEED, MIN_STROKE)
            for s_ in (1, -1):
                b = ang + math.pi + s_ * math.radians(spec.get("head_angle", 25))
                tp = (p1[0] + head * math.cos(b), p1[1] + head * math.sin(b))
                seg = hand_line(p1, tp, rng, amp=amp * 0.7, step=10)
                strokes.append(Stroke(seg, width=width, color=fc,
                                      delay=tip_delay * 0.88, dur=0.12))
    elif kind == "group":
        w = float(spec.get("w", 400))
        h = float(spec.get("h", 300))
        r = float(spec.get("r", 18))
        path = round_rect_path(x, y, w, h, r)
        dash_segs = dashed_path(path, dash=float(spec.get("dash", 20)),
                                gap=float(spec.get("gap", 13)))
        per = 0.02
        for i, seg in enumerate(dash_segs):
            strokes.append(Stroke(hand_polyline(seg, rng, amp=amp * 0.6),
                                  width=width * 0.85,
                                  color=color or theme["muted"],
                                  delay=i * per, dur=0.055))
        label = spec.get("text", "")
        if label:
            texts.append(TextRun(label, x + 18, y - int(spec.get("text_size", 30)) * 1.18,
                                 size=int(spec.get("text_size", 30)),
                                 color=color or theme["muted"], align="left",
                                 delay=0.35, font=font_override))
    elif kind == "dot":
        # 一个手画圆点 + 可选标签。散点的原子单位。
        r = float(spec.get("r", 18))
        d = max(TAU * r / DRAW_SPEED, MIN_STROKE)
        strokes.append(Stroke(near_circle(x, y, r, rng, amp=amp),
                              width=width * 0.9, color=fc, dur=d))
        label = spec.get("text", "")
        if label:
            lsize = int(spec.get("text_size", 34))
            pos = spec.get("label_pos", "below")
            if pos == "above":
                lx, ly, al = x, y - r - lsize * 1.3, "center"
            elif pos == "right":
                lx, ly, al = x + r * 1.5, y - lsize * 0.52, "left"
            else:
                lx, ly, al = x, y + r + lsize * 0.32, "center"
            texts.append(TextRun(label, lx + float(spec.get("label_dx", 0)), ly,
                                 size=lsize, color=fc, align=al,
                                 per_char=spec.get("per_char", True),
                                 delay=d * 0.7, font=font_override))

    elif kind == "scatter":
        # 散点图 / 向量空间：一批点放在同一张坐标底板上。
        # 整张图算「一个」图元（内部点按 stagger 依次亮起），
        # 所以密度预算不会被点数拖爆。
        w = float(spec.get("w", 700))
        h = float(spec.get("h", 560))
        if spec.get("axes", True):
            ax = [(x, y), (x, y + h), (x + w, y + h)]      # 竖轴 + 横轴（原点在左下）
            strokes.append(Stroke(hand_polyline(ax, rng, amp=amp * 0.8),
                                  width=width * 0.75, color=theme["muted"],
                                  dur=float(spec.get("axes_dur", 0.42))))
        base = float(spec.get("axes_dur", 0.42) if spec.get("axes", True) else 0.0)
        stagger = float(spec.get("stagger", 0.09))
        pr = float(spec.get("point_r", 19))
        for i, p in enumerate(spec.get("points", [])):
            px = x + float(p.get("dx", 0.5)) * w
            py = y + float(p.get("dy", 0.5)) * h
            rr = float(p.get("r", pr))
            pc = tuple(p["color"]) if p.get("color") else fc
            d = max(TAU * rr / DRAW_SPEED, 0.13)
            delay = base + i * stagger
            strokes.append(Stroke(near_circle(px, py, rr, rng, amp=amp),
                                  width=width * 0.9, color=pc,
                                  delay=delay, dur=d))
            if p.get("text"):
                lsize = int(p.get("text_size", spec.get("point_text_size", 32)))
                off = float(p.get("text_dy", -(rr + lsize * 1.25)))
                texts.append(TextRun(p["text"], px + float(p.get("text_dx", 0)),
                                     py + off, size=lsize, color=pc, align="center",
                                     per_char=p.get("per_char", True),
                                     delay=delay + d * 0.6, font=font_override))
        for g in spec.get("groups", []):
            gx = x + float(g.get("dx", 0.5)) * w
            gy = y + float(g.get("dy", 0.5)) * h
            texts.append(TextRun(g.get("text", ""), gx, gy,
                                 size=int(g.get("size", 34)),
                                 color=tuple(g["color"]) if g.get("color") else theme["muted"],
                                 align=g.get("align", "center"),
                                 per_char=g.get("per_char", True),
                                 delay=float(g.get("delay", base + 0.55)),
                                 font=resolve_font(g.get("font")) or font_override))

    elif kind == "cylinder":
        # 数据库圆柱：顶面椭圆 + 侧面（左壁 + 底弧 + 右壁），一笔连画。
        w = float(spec.get("w", 280))
        h = float(spec.get("h", 300))
        ry = w * float(spec.get("cap", 0.17))
        cx = x + w / 2.0
        top = ellipse_path(cx, y + ry, w / 2.0, ry, 0, 360, n=22, rng=rng, amp=amp * 0.5)
        strokes.append(Stroke(top, width=width, color=fc,
                              dur=max(math.pi * (w / 2.0 + ry) / DRAW_SPEED, MIN_STROKE)))
        side = _seg_points((x, y + ry), (x, y + h - ry), 14)
        side += ellipse_path(cx, y + h - ry, w / 2.0, ry, 180, 0, n=12,
                             rng=rng, amp=amp * 0.4)[1:]
        side += _seg_points((x + w, y + h - ry), (x + w, y + ry), 14)[1:]
        strokes.append(Stroke(hand_polyline(side, rng, amp=amp * 0.5),
                              width=width, color=fc, delay=0.16))
        label = spec.get("text", "")
        if label:
            lsize = int(spec.get("text_size", 38))
            texts.append(TextRun(label, cx, y + h * float(spec.get("label_dy", 0.55)),
                                 size=lsize, color=fc, align="center",
                                 per_char=spec.get("per_char", True),
                                 delay=0.55, font=font_override))

    elif kind == "bars":
        # 一排竖条：表示「一串数字」——向量的分量。
        vals = [float(v) for v in spec.get("values", [])]
        w = float(spec.get("w", 600))
        h = float(spec.get("h", 200))
        n = len(vals)
        base_y = y + h
        stagger = float(spec.get("stagger", 0.12))
        if spec.get("axis", True):
            strokes.append(Stroke(hand_line((x, base_y), (x + w, base_y), rng,
                                            amp=amp * 0.7, step=14),
                                  width=width * 0.7, color=theme["muted"],
                                  dur=max(w / (DRAW_SPEED * 2.4), MIN_STROKE)))
        if n:
            vmax = max(abs(v) for v in vals) or 1.0
            gapx = w / float(n)
            bw = gapx * float(spec.get("bar_ratio", 0.46))
            for i, v in enumerate(vals):
                bh = max(h * (abs(v) / vmax) * float(spec.get("height_ratio", 0.86)), 6.0)
                bx = x + i * gapx + (gapx - bw) / 2.0
                pts = [(bx, base_y)]
                pts += _seg_points((bx, base_y), (bx, base_y - bh), 11)[1:]
                pts += _seg_points((bx, base_y - bh), (bx + bw, base_y - bh), 11)[1:]
                pts += _seg_points((bx + bw, base_y - bh), (bx + bw, base_y), 11)[1:]
                strokes.append(Stroke(hand_polyline(pts, rng, amp=amp * 0.55),
                                      width=width * 0.9, color=fc,
                                      delay=0.14 + i * stagger,
                                      dur=max((2 * bh + bw) / DRAW_SPEED, 0.14)))
        if spec.get("text"):
            lsize = int(spec.get("text_size", 34))
            texts.append(TextRun(spec["text"], x + w / 2.0, base_y + lsize * 0.42,
                                 size=lsize, color=color or theme["muted"],
                                 align="center", per_char=spec.get("per_char", True),
                                 delay=0.14 + n * stagger + 0.12, font=font_override))

    else:
        raise ValueError("未知图元类型: %s" % kind)

    # 整体绘制速度缩放：复合作图（散点/圆柱）笔画多，可整体调快，
    # 让「画完这张图」的耗时与这一句话的时长对得上。
    sc = float(spec.get("dur_scale", 1.0))
    if sc != 1.0:
        for st in strokes:
            st.dur = stroke_dur(st) * sc
        for tr in texts:
            tr.dur = text_dur(tr) * sc

    if spec.get("delay"):
        for st in strokes:
            st.delay += float(spec["delay"])
        for tr in texts:
            tr.delay += float(spec["delay"])

    return Element(kind, strokes, texts, cue=cue, spec=spec)


def path_len(pts):
    total = 0.0
    for i in range(1, len(pts)):
        total += math.dist(pts[i - 1], pts[i])
    return total


def _estimate_width(text, size):
    """CJK 粗略宽度估算（字形宽度 ≈ 0.98 * size，ASCII ≈ 0.55）。"""
    w = 0.0
    for ch in text:
        w += size * (0.55 if ord(ch) < 0x2E80 else 0.98)
    return w


# ----------------------------------------------------------------- 绘制
def partial_points(pts, cum, t):
    """取笔画前 t 比例的点列（t<=0 返回空，t>=1 返回全量）。"""
    if t <= 0.0 or len(pts) < 2:
        return []
    if t >= 1.0:
        return pts
    total = cum[-1]
    if total <= 0:
        return pts
    target = total * t
    out = [pts[0]]
    for i in range(1, len(pts)):
        if cum[i] <= target:
            out.append(pts[i])
        else:
            seg = cum[i] - cum[i - 1]
            f = (target - cum[i - 1]) / seg if seg > 1e-9 else 0.0
            out.append((pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * f,
                        pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * f))
            break
    return out


def element_progress(el, t):
    """把元素整体进度 t 展开成每条笔画/文字的局部进度。"""
    dur = el.duration()
    now = t * dur
    out_s, out_t = [], []
    for st in el.strokes:
        d = stroke_dur(st)
        local = (now - st.delay) / d if d > 0 else 1.0
        out_s.append(max(0.0, min(1.0, local)))
    for tr in el.texts:
        d = text_dur(tr)
        local = (now - tr.delay) / d if d > 0 else 1.0
        out_t.append(max(0.0, min(1.0, local)))
    return out_s, out_t


def _scale_pts(pts, s):
    return [(p[0] * s, p[1] * s) for p in pts]


def draw_element(draw, el, t, fonts, scale, theme, rng_chars=None):
    """把一个元素画到 draw 上，t 为该元素整体进度（0..1）。"""
    if t <= 0.0:
        return
    prog_s, prog_t = element_progress(el, t)

    for st, lp in zip(el.strokes, prog_s):
        if lp <= 0:
            continue
        pts = partial_points(st.pts, st.cum, lp)
        if len(pts) < 2:
            continue
        sp = _scale_pts(pts, scale)
        wpx = max(1, int(round(st.width * scale)))
        draw.line(sp, fill=st.color or theme["ink"], width=wpx, joint="curve")
        r = wpx / 2.0
        for p in (sp[0], sp[-1]):
            draw.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r],
                         fill=st.color or theme["ink"])

    for tr, lp in zip(el.texts, prog_t):
        if lp <= 0:
            continue
        _draw_text_run(draw, tr, lp, fonts, scale, theme, rng_chars)


def _draw_text_run(draw, tr, lp, fonts, scale, theme, rng):
    font = fonts.get(tr.size, scale, tr.font)
    color = tr.color or theme["ink"]
    chars = list(tr.text)
    if not tr.per_char:
        if lp < 0.6:
            return
        x = tr.x
        if tr.align == "center":
            x = tr.x - _estimate_width(tr.text, tr.size) / 2.0
        x += tr.wobble and (rng.uniform(-0.8, 0.8) if rng else 0)
        draw.text((x * scale, tr.y * scale), tr.text, font=font, fill=color)
        return

    widths = [font.getlength(c) for c in chars]
    total_w = sum(widths)
    shown = total_w * lp
    x = tr.x
    if tr.align == "center":
        x = tr.x - total_w / (2.0 * scale)
    acc = 0.0
    for c, cw in zip(chars, widths):
        if acc >= shown:
            break
        if acc + cw <= shown:
            k = 1.0
        else:
            k = (shown - acc) / cw if cw > 1e-6 else 0.0
        if k > 0.22:
            jy = 0.0
            jx = 0.0
            if tr.wobble and rng:
                jy = rng.uniform(-0.55, 0.55)
                jx = rng.uniform(-0.45, 0.45)
            draw.text(((x + jx) * scale, (tr.y + jy) * scale), c,
                      font=font, fill=color)
        x += cw / scale
        acc += cw


def draw_static(img, draw, elements, progress, fonts, scale, theme, seed=7):
    """按 progress 字典（元素索引 -> t）绘制全部元素到 draw。"""
    rng = random.Random(seed)
    for i, el in enumerate(elements):
        t = progress.get(i, 1.0)
        draw_element(draw, el, t, fonts, scale, theme, rng)


# ----------------------------------------------------------------- 自测入口
def main():
    import argparse
    import json

    from PIL import Image, ImageDraw

    ap = argparse.ArgumentParser(description="把一个 shot JSON 渲染成静态 PNG（t=1）")
    ap.add_argument("shot", help="shot JSON 路径")
    ap.add_argument("--out", default="sketch_preview.png")
    ap.add_argument("--t", type=float, default=1.0, help="整体进度 0..1")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--font", default=None)
    a = ap.parse_args()

    with open(a.shot, encoding="utf-8") as f:
        spec = json.load(f)

    theme = THEMES[spec.get("theme", "ink")]
    size = tuple(spec.get("size", [1080, 1920]))
    scale = a.scale
    fonts = Fonts(pick_font(a.font))
    rng = random.Random(spec.get("seed", 11))

    elements = [build_element(e, theme, rng) for e in spec.get("elements", [])]

    img = Image.new("RGB", (int(size[0] * scale), int(size[1] * scale)), theme["bg"])
    draw = ImageDraw.Draw(img)
    for i, el in enumerate(elements):
        draw_element(draw, el, a.t, fonts, scale, theme, random.Random(100 + i))
    img.save(a.out)
    print("OK ->", a.out)


if __name__ == "__main__":
    main()
