# -*- coding: utf-8 -*-
"""音频工具：转 wav / 混音。由本机工具链 Python（含 librosa+soundfile）执行。"""
import argparse
import sys

import numpy as np


def to_wav(src, dst, sr=44100):
    import librosa
    import soundfile as sf
    y, rate = librosa.load(src, sr=sr, mono=False)
    if y.ndim == 1:
        y = np.stack([y, y], axis=0)
    sf.write(dst, y.T, rate, subtype="PCM_16")
    print("TO_WAV_OK", y.shape, rate)


def _to_stereo(x):
    """任意输入转为 (N,2) float32，保持原样如果已是立体声。"""
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 1:
        return np.stack([x, x], axis=1)
    if x.ndim == 2 and x.shape[1] == 1:
        return np.concatenate([x, x], axis=1)
    return x


def mix(vocals, instrumental, out, vocal_gain=1.0, inst_gain=0.7, peak=0.95, sr=44100):
    import soundfile as sf
    v, sr_v = sf.read(vocals, dtype="float32", always_2d=False)
    ins, sr_i = sf.read(instrumental, dtype="float32", always_2d=False)
    v = _to_stereo(v)
    ins = _to_stereo(ins)
    if sr_v != sr or sr_i != sr:
        raise RuntimeError("sample rate mismatch: %d %d" % (sr_v, sr_i))
    n = max(v.shape[0], ins.shape[0])
    if v.shape[0] < n:
        v = np.pad(v, ((0, n - v.shape[0]), (0, 0)))
    if ins.shape[0] < n:
        ins = np.pad(ins, ((0, n - ins.shape[0]), (0, 0)))
    m = v * vocal_gain + ins * inst_gain
    m_peak = np.max(np.abs(m))
    if m_peak > 0:
        m = m / m_peak * peak
    sf.write(out, m, sr, subtype="PCM_16")
    print("MIX_OK", out, m.shape, sr)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("to_wav")
    p1.add_argument("src")
    p1.add_argument("dst")
    p1.add_argument("--sr", type=int, default=44100)
    p2 = sub.add_parser("mix")
    p2.add_argument("vocals")
    p2.add_argument("instrumental")
    p2.add_argument("out")
    p2.add_argument("--vg", type=float, default=1.0)
    p2.add_argument("--ig", type=float, default=0.7)
    p2.add_argument("--peak", type=float, default=0.95)
    args = ap.parse_args()
    if args.cmd == "to_wav":
        to_wav(args.src, args.dst, args.sr)
    elif args.cmd == "mix":
        mix(args.vocals, args.instrumental, args.out, args.vg, args.ig, args.peak)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa
        print("AUDIO_UTILS_ERROR:", repr(e))
        sys.exit(1)
