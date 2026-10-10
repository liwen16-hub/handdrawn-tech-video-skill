# -*- coding: utf-8 -*-
"""
逐笔动画渲染器
==============
把 shot.json + timeline.json 渲染成竖版 mp4 片段。

渲染策略：
  * 按 scale 倍超采样绘制（PIL 不做抗锯齿），再 LANCZOS 缩回目标尺寸
  * 逐帧通过 rawvideo 管道直接喂给 ffmpeg，不落中间 PNG（快且不占磁盘）
  * 文字抖动用「每元素固定种子」，保证帧间稳定、不闪

产出 mp4 不含字幕 —— 字幕由流水线末端统一烧录，避免重复。
"""
import argparse
import json
import os
import random
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw  # noqa: E402
import imageio_ffmpeg  # noqa: E402

from sketch import THEMES, Fonts, build_element, draw_element, pick_font  # noqa: E402
from timeline import progress_at  # noqa: E402

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def build_shot(shot, font=None):
    theme = THEMES[shot.get("theme", "ink")]
    rng = random.Random(shot.get("seed", 11))
    fonts = Fonts(font or pick_font(shot.get("font")))
    elems = [build_element(e, theme, rng) for e in shot.get("elements", [])]
    return theme, fonts, elems


def render(shot, timeline, out_mp4, voice_audio=None, scale=2.0, fps=30,
           tail=0.8, crf=20, preset="medium", log=print):
    theme, fonts, elems = build_shot(shot)
    W, H = shot.get("size", [1080, 1920])
    seed = shot.get("seed", 11)
    total = float(timeline["total"]) + float(tail)
    nframes = max(2, int(round(total * fps)))

    # 注意：不要用 -shortest。它配合「stdin 喂 rawvideo」会在音频输入读完时
    # 提前判停（实测只编出 1.07s 就 BrokenPipe）。用输出端 -t 显式定时长，
    # 音频再用 apad 补齐，这样视频一定按帧数走完。
    cmd = [FFMPEG, "-y", "-nostats", "-loglevel", "error",
           "-f", "rawvideo", "-pixel_format", "rgb24",
           "-video_size", "%dx%d" % (W, H), "-framerate", str(fps), "-i", "-"]
    if voice_audio:
        cmd += ["-i", voice_audio]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-crf", str(crf), "-preset", preset]
    if voice_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-af", "apad"]
    cmd += ["-t", "%.3f" % total, "-movflags", "+faststart", out_mp4]

    os.makedirs(os.path.dirname(os.path.abspath(out_mp4)), exist_ok=True)
    log("渲染 %d 帧 @ %dfps（%.2fs）…" % (nframes, fps, total))

    # stderr 必须落盘：6MB/帧的管道流量下，若 stderr 走 PIPE 且不实时读，
    # 缓冲区填满会让 ffmpeg 停止消费 stdin，进而死锁。
    # 日志名带上输出名做区分 —— 多镜并行渲染时共用一个文件名会互相覆盖。
    outdir = os.path.dirname(os.path.abspath(out_mp4))
    err_path = os.path.join(
        outdir, "_ffmpeg_err_%s.log"
        % os.path.splitext(os.path.basename(out_mp4))[0])
    err_f = open(err_path, "wb")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=err_f)

    sw, sh = int(W * scale), int(H * scale)
    written = 0
    broken = False
    try:
        for fi in range(nframes):
            t = fi / float(fps)
            prog = progress_at(timeline, t)
            img = Image.new("RGB", (sw, sh), theme["bg"])
            draw = ImageDraw.Draw(img)
            for i, el in enumerate(elems):
                pr = prog.get(i, 0.0)
                if pr <= 0.0:
                    continue
                draw_element(draw, el, pr, fonts, scale, theme,
                             random.Random(seed * 1000 + i))
            if scale != 1.0:
                img = img.resize((W, H), Image.LANCZOS)
            try:
                proc.stdin.write(img.tobytes())
                written += 1
            except BrokenPipeError:
                broken = True
                break
            if (fi + 1) % 60 == 0 or fi + 1 == nframes:
                log("  帧 %d/%d" % (fi + 1, nframes))
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.wait()
        err_f.close()

    with open(err_path, "rb") as f:
        err = f.read().decode("utf-8", "ignore")
    if os.path.exists(err_path) and not err.strip():
        # 清理只是顺手，失败不能影响渲染结果（Windows 上沙箱的删除可能被拦）
        try:
            os.remove(err_path)
        except OSError:
            pass

    if proc.returncode != 0 or broken:
        raise RuntimeError(
            "ffmpeg 失败（已写入 %d/%d 帧，BrokenPipe=%s）:\n%s"
            % (written, nframes, broken, err[-3000:]))
    log("完成 -> %s" % out_mp4)
    return {"output": out_mp4, "frames": nframes, "fps": fps,
            "duration": round(total, 3)}


def render_preview(shot, timeline, t, out_png, scale=2.0):
    """渲染指定时刻的单帧 PNG，用于快速校对。"""
    theme, fonts, elems = build_shot(shot)
    W, H = shot.get("size", [1080, 1920])
    seed = shot.get("seed", 11)
    sw, sh = int(W * scale), int(H * scale)
    img = Image.new("RGB", (sw, sh), theme["bg"])
    draw = ImageDraw.Draw(img)
    prog = progress_at(timeline, t)
    for i, el in enumerate(elems):
        pr = prog.get(i, 0.0)
        if pr <= 0:
            continue
        draw_element(draw, el, pr, fonts, scale, theme, random.Random(seed * 1000 + i))
    if scale != 1.0:
        img = img.resize((W, H), Image.LANCZOS)
    img.save(out_png)
    return out_png


def main():
    ap = argparse.ArgumentParser(description="逐笔动画渲染 -> 竖版 mp4")
    ap.add_argument("shot")
    ap.add_argument("timeline")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--audio", default=None, help="配音 mp3；留空则输出无声片")
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--tail", type=float, default=0.8)
    ap.add_argument("--crf", type=int, default=20)
    ap.add_argument("--preview", type=float, default=None,
                    help="只渲染某一时刻的 PNG，不合成视频")
    ap.add_argument("--preview-out", default="frame-preview.png")
    a = ap.parse_args()

    with open(a.shot, encoding="utf-8") as f:
        shot = json.load(f)
    with open(a.timeline, encoding="utf-8") as f:
        tl = json.load(f)
    if a.fps:
        shot.setdefault("fps", a.fps)

    if a.preview is not None:
        p = render_preview(shot, tl, a.preview, a.preview_out, scale=a.scale)
        print("OK ->", p)
        return

    audio = a.audio
    if audio is None:
        cand = os.path.join(os.path.dirname(os.path.abspath(a.timeline)), "voice.mp3")
        if os.path.exists(cand):
            audio = cand
    info = render(shot, tl, a.out, voice_audio=audio, scale=a.scale,
                  fps=a.fps, tail=a.tail, crf=a.crf)
    print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
