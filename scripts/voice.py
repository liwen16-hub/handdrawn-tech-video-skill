# -*- coding: utf-8 -*-
"""
配音引擎
========
用 edge-tts 合成旁白，同时取回**词级时间戳**（WordBoundary），
并按标点切出行级字幕。

产出三件套：
  voice.mp3   —— 音频
  voice.srt   —— 行级字幕（可直接烧进视频）
  voice.json  —— {audio, duration, words:[{t,d,w}], subs:[[a,b,text]]}

voice.json 里的 words 是「声画同步」的燃料：timeline.py 用它把每个图元的
cue 词映射成出场时刻。

注意：edge-tts >= 7 的 boundary 默认是 SentenceBoundary，必须显式指定
WordBoundary，否则拿不到词级时间戳。
"""
import argparse
import asyncio
import json
import os
import re
import sys

VOICES = {
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",           # 晓晓 · 女声 · 温暖通用（默认）
    "xiaoyi": "zh-CN-XiaoyiNeural",               # 晓伊 · 女声 · 活泼
    "yunxi": "zh-CN-YunxiNeural",                 # 云希 · 男声 · 年轻
    "yunjian": "zh-CN-YunjianNeural",             # 云健 · 男声 · 沉稳解说
    "yunyang": "zh-CN-YunyangNeural",             # 云扬 · 男声 · 新闻播报
    "xiaobei": "zh-CN-liaoning-XiaobeiNeural",    # 晓北 · 东北女声
}

STRONG = "。！？!?…"
WEAK = "，,、；;：:"


# --------------------------------------------------------------- 文本处理
def strip_punct(s):
    """去掉会影响匹配的标点与空白，用于「净文本」对齐。"""
    return re.sub(r"[\s，。！？；：、,.!?;:…—\-·\"'”“‘’（）()《》【】\[\]]", "", s)


def make_lines(text, max_len=16):
    """按标点把旁白切成适合一屏的字幕行。"""
    text = re.sub(r"\s+", "", text.strip())
    sents = re.findall(r"[^。！？!?…]+[。！？!?…]?", text)
    lines = []
    for s in sents:
        s = s.strip()
        if not s:
            continue
        if len(s) <= max_len:
            lines.append(s)
            continue
        subs = re.findall(r"[^，,、；;：:]+[，,、；;：:]?", s)
        buf = ""
        for sub in subs:
            if len(buf) + len(sub) <= max_len:
                buf += sub
            else:
                if buf:
                    lines.append(buf)
                buf = sub
        if buf:
            lines.append(buf)
    out = []
    for l in lines:
        if out and len(out[-1]) + len(l) <= max_len + 4:
            out[-1] += l
        else:
            out.append(l)
    # 兜底硬切：上面只按标点断句，遇到「一逗到底」或整句无标点的旁白，
    # 一段会整块落成一行。50px 字号下超过约 21 字就会溢出 1080 画布，
    # 所以这里必须再切一道，保证每行都不超过 max_len。
    hard = []
    for l in out:
        l = l.strip()
        while len(l) > max_len:
            hard.append(l[:max_len])
            l = l[max_len:]
        if l:
            hard.append(l)
    return hard


def _align(lines, words):
    """按行净字数，把词级时间戳聚合成行级字幕。"""
    subs = []
    wi = 0
    for line in lines:
        target = len(strip_punct(line))
        acc = ""
        start = end = None
        while wi < len(words) and len(acc) < target:
            w = words[wi]
            if start is None:
                start = w["t"]
            end = w["t"] + w["d"]
            acc += strip_punct(w["w"])
            wi += 1
        subs.append((start if start is not None else 0.0,
                     end if end is not None else (start or 0.0), line))
    fixed = []
    for i, (a, b, t) in enumerate(subs):
        nxt = subs[i + 1][0] if i + 1 < len(subs) else None
        if nxt is not None and b > nxt:
            b = nxt
        if nxt is not None and nxt - b > 0.35:
            b = b + 0.35
        fixed.append((a, max(b, a + 0.2), t))
    return fixed


def write_srt(subs, path):
    def fmt(t):
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = t % 60
        return "%02d:%02d:%06.3f" % (h, m, s)

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for i, (a, b, txt) in enumerate(subs, 1):
            f.write("%d\n%s --> %s\n%s\n\n"
                    % (i, fmt(a).replace(".", ","), fmt(b).replace(".", ","), txt))


# --------------------------------------------------------------- 合成
async def _stream(text, voice, rate, volume, pitch):
    import edge_tts
    comm = edge_tts.Communicate(text, voice, rate=rate, volume=volume,
                                pitch=pitch, boundary="WordBoundary")
    audio = bytearray()
    words = []
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
        elif chunk["type"] == "WordBoundary":
            words.append({"t": chunk["offset"] / 1e7,
                          "d": chunk["duration"] / 1e7,
                          "w": chunk["text"]})
    return bytes(audio), words


def synthesize(text, out_dir, voice="zh-CN-XiaoxiaoNeural", rate="+8%",
               volume="+0%", pitch="+0Hz", max_len=16, name="voice",
               retries=3, log=print):
    """合成配音。返回 voice.json 的内容 dict。

    edge-tts 走的是公共在线服务，偶发 socket 超时很常见，必须重试。
    """
    import time

    os.makedirs(out_dir, exist_ok=True)
    audio_bytes, words, last_err = None, None, None
    for attempt in range(1, retries + 1):
        try:
            audio_bytes, words = asyncio.run(_stream(text, voice, rate, volume, pitch))
            break
        except Exception as exc:                       # noqa: BLE001
            last_err = exc
            if attempt < retries:
                wait = 1.2 * attempt
                log("  配音合成失败（第 %d/%d 次）：%s；%.1fs 后重试"
                    % (attempt, retries, type(exc).__name__, wait))
                time.sleep(wait)
    if audio_bytes is None:
        raise RuntimeError("edge-tts 合成失败，已重试 %d 次。最后一次错误：%s"
                           % (retries, last_err))

    mp3 = os.path.join(out_dir, name + ".mp3")
    with open(mp3, "wb") as f:
        f.write(audio_bytes)

    lines = make_lines(text, max_len=max_len)
    subs = _align(lines, words)
    srt = os.path.join(out_dir, name + ".srt")
    write_srt(subs, srt)

    duration = max([b for _, b, _ in subs] + [0.0])
    if words:
        duration = max(duration, words[-1]["t"] + words[-1]["d"])

    info = {
        "audio": mp3,
        "srt": srt,
        "duration": round(duration, 3),
        "voice": voice,
        "rate": rate,
        "text": text,
        "words": words,
        "subs": [[round(a, 3), round(b, 3), t] for a, b, t in subs],
    }
    with open(os.path.join(out_dir, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    return info


def main():
    ap = argparse.ArgumentParser(description="合成配音 + 词级时间戳 + SRT")
    ap.add_argument("--text")
    ap.add_argument("--text-file")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--name", default="voice")
    ap.add_argument("--voice", default="zh-CN-XiaoxiaoNeural",
                    help="音色全名，或别名: " + "/".join(VOICES))
    ap.add_argument("--rate", default="+8%")
    ap.add_argument("--volume", default="+0%")
    ap.add_argument("--pitch", default="+0Hz")
    ap.add_argument("--max-len", type=int, default=16)
    a = ap.parse_args()

    text = a.text
    if not text and a.text_file:
        with open(a.text_file, encoding="utf-8") as f:
            text = f.read()
    if not text:
        sys.exit("需要 --text 或 --text-file")

    voice = VOICES.get(a.voice, a.voice)
    info = synthesize(text, a.out_dir, voice=voice, rate=a.rate,
                      volume=a.volume, pitch=a.pitch, max_len=a.max_len,
                      name=a.name)
    print(json.dumps({"audio": info["audio"], "srt": info["srt"],
                      "duration": info["duration"], "words": len(info["words"]),
                      "subs": len(info["subs"])}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
