# -*- coding: utf-8 -*-
"""
多镜拼接 + 统一烧字幕
====================
animate.py 逐镜产出的是**不带字幕**的片段（字幕要统一烧，避免每镜样式不一）。
本脚本负责最后一步：按顺序拼接所有片段，并把每镜 voice.json 里的
字幕行（subs）按时间偏移合并成一条 ASS 字幕轨，烧到成片上。

为什么字幕必须在最后统一烧：
  - 每镜单独烧字幕，缝合处会出现样式/位置不一致
  - 字幕时间轴是「相对本镜」的，只有拼接后才知道每镜的全局起点
  - 分开烧要重编码两次，画质损失叠加

字幕样式：暖白纸底上**不能用白字**，用墨黑文字 + 纸色描边，
位置 MarginV=400（≈ y=1520，在 1536 安全线之上）。

用法：
  python join.py -o final.mp4 \
      --clip a.mp4 b.mp4 c.mp4 \
      --voice a/voice.json b/voice.json c/voice.json
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

# 与 sketch.py 的主题色保持一致
INK = "&H00342020"        # 墨黑 (32,32,34) -> BGR
PAPER = "&H00F2F7FA"      # 纸底 (250,247,242) -> BGR


def dur_of(path):
    """用 ffmpeg -i 解析媒体时长（秒）。"""
    p = subprocess.run([FFMPEG, "-i", path],
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    m = re.search(rb"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", p.stderr or b"")
    if not m:
        raise RuntimeError("读不到时长: " + path)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def ass_time(t):
    """秒 -> H:MM:SS.cc（ASS 用厘秒）。"""
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return "%d:%02d:%05.2f" % (h, m, s)


def build_ass(events, path, font="Microsoft YaHei", size=52,
              margin_v=400, outline=3):
    """events: [(start, end, text)] 已带全局偏移。"""
    head = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: CN,%s,%d,%s,%s,%s,&H00000000,-1,0,0,0,100,100,0.5,0,1,%d,0,2,60,60,%d,134

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" % (font, size, INK, INK, PAPER, outline, margin_v)

    lines = [head]
    for st, en, txt in events:
        if en - st < 0.08:
            en = st + 0.08
        t = txt.replace("\n", " ").replace("{", "(").replace("}", ")")
        lines.append("Dialogue: 0,%s,%s,CN,,0,0,0,,%s\n"
                     % (ass_time(st), ass_time(en), t))
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(lines))


def concat_list(clip_paths, list_path):
    """写 concat demuxer 的清单，用正斜杠绝对路径。"""
    with open(list_path, "w", encoding="utf-8") as f:
        for p in clip_paths:
            f.write("file '%s'\n" % os.path.abspath(p).replace("\\", "/"))


def main():
    ap = argparse.ArgumentParser(description="多镜拼接 + 统一烧字幕")
    ap.add_argument("-o", "--out", required=True)
    # action="append" + nargs="+"：--clip a b --clip c 和 --clip a b c 都能用。
    # 只写 nargs="+" 的话，重复给 --clip 会被后一次覆盖，静默只剩一段。
    ap.add_argument("--clip", nargs="+", action="append", required=True,
                    help="按顺序的片段（可重复给 --clip）")
    ap.add_argument("--voice", nargs="+", action="append", default=None,
                    help="按顺序的 voice.json（取 subs），与 --clip 一一对应")
    ap.add_argument("--no-subs", action="store_true", help="只拼接，不烧字幕")
    ap.add_argument("--font", default="Microsoft YaHei")
    ap.add_argument("--size", type=int, default=52)
    ap.add_argument("--margin-v", type=int, default=400)
    ap.add_argument("--crf", type=int, default=20)
    a = ap.parse_args()

    a.clip = [p for grp in a.clip for p in grp]
    if a.voice:
        a.voice = [p for grp in a.voice for p in grp]

    out = os.path.abspath(a.out)
    outdir = os.path.dirname(out)
    os.makedirs(outdir, exist_ok=True)

    if a.voice and len(a.voice) != len(a.clip):
        sys.exit("--clip 与 --voice 数量必须一致")

    # 1) 拼接清单
    list_path = os.path.join(outdir, "_concat.txt")
    concat_list(a.clip, list_path)

    # 2) 字幕（可选）
    vf = None
    if a.voice and not a.no_subs:
        offset = 0.0
        events = []
        for cp, vp in zip(a.clip, a.voice):
            d = dur_of(cp)
            with open(vp, encoding="utf-8") as f:
                subs = json.load(f).get("subs", [])
            for st, en, txt in subs:
                events.append((offset + float(st), offset + float(en), txt))
            offset += d
        ass_path = os.path.join(outdir, "_subtitle.ass")
        build_ass(events, ass_path, font=a.font, size=a.size,
                  margin_v=a.margin_v)
        print("字幕 %d 条，总时长 %.1fs" % (len(events), offset))
        # 用相对文件名 + cwd，规避 Windows 下滤镜路径转义的大坑
        vf = "ass=%s" % os.path.basename(ass_path)

    # 3) 拼接（重编码，保证拼接点干净）
    cmd = [FFMPEG, "-y", "-nostats", "-loglevel", "error",
           "-f", "concat", "-safe", "0", "-i", list_path]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-c:v", "libx264", "-crf", str(a.crf), "-preset", "medium",
            "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-b:a", "192k", "-ar", "24000", "-ac", "1",
            "-movflags", "+faststart", out]
    print("拼接 %d 个片段 -> %s" % (len(a.clip), out))
    r = subprocess.run(cmd, cwd=outdir, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE)
    if r.returncode != 0:
        sys.exit("ffmpeg 失败:\n" + (r.stderr or b"").decode("utf-8", "ignore")[-2500:])
    print("成片 %.2fs -> %s" % (dur_of(out), out))


if __name__ == "__main__":
    main()
