# -*- coding: utf-8 -*-
"""
AI 翻唱工坊 CoverStudio v1.4
一键翻唱：导入原音频或搜索歌名 -> MSST 分离 -> DDSP 转音 -> 混音 -> 歌词字幕 -> 成品
视频合成：背景（图片/GIF/视频/网上搜索/氛围生成）+ 音乐 + 字幕 -> MP4（免 PR）
自动分发：安装包小（仅 exe + ffmpeg），组件缺失时软件自主联网查找官方资源
            （GitHub 最新源码/预训练、ModelScope、ffmpeg 官方构建）并自动安装；
            角色模型随包内置到 exe 同级 models\\，无需下载。
"""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import wave
import zipfile
from html import unescape
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_NAME = "AI翻唱工坊 CoverStudio"
VERSION = "1.4.1"

# ---------------------------------------------------------------- 路径/配置

def app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


CONFIG_FILE = app_dir() / "config.json"
AUDIO_UTILS = app_dir() / "audio_utils.py"

ROLE_TONES = {
    "祥子": "清冷古典",
    "素世": "温柔内敛",
    "喵梦": "活泼元气",
    "高松灯": "高亢深情",
    "立希": "摇滚力量",
    "初华": "清亮通透",
    "若叶睦": "安静淡雅",
    "爱音": "明亮灵动",
}

# 角色目录简称 -> 角色全名（用于下拉栏展示）
ROLE_FULL_NAMES = {
    "祥子": "丰川祥子",
    "素世": "长崎素世",
    "喵梦": "祐天寺喵梦",
    "高松灯": "高松灯",
    "立希": "椎名立希",
    "初华": "三角初华",
    "若叶睦": "若叶睦",
    "爱音": "千早爱音",
}

DEFAULT_CONFIG = {
    "ddsp_dir": r"E:\AI音乐\.DDSP\DDSP-barbara-6.2",
    "ddsp_python": r"E:\AI音乐\.DDSP\DDSP-barbara-6.2\env\python.exe",
    "msst_dir": r"E:\AI音乐\分离\MSST-GUI-1.4.0",
    "msst_python": r"E:\AI音乐\分离\MSST-GUI-1.4.0\env\python.exe",
    "model_dir": r"E:\AI音乐\.DDSP\DDSP模型",
    "msst_config": "config_vocals_mel_band_roformer_kim.yaml",
    "msst_ckpt": "mel_band_roformer_vocals_becruily.ckpt",
    "use_gpu_separate": True,
    "output_dir": str(Path.home() / "Desktop" / "AI翻唱成品"),
    # v1.4：组件由软件自动联网获取（内置 GitHub/ModelScope/官方源，无需手动填写直链）
    # 角色模型随软件包内置：放在 exe 同级 models\ 即自动识别
}


# ---------------------------------------------------------------- 组件状态

def component_status(cfg):
    """返回各组件就绪状态字典。key: msst/ddsp/models/ffmpeg。"""
    st = {}
    st["msst"] = {
        "ready": bool(cfg.get("msst_dir")) and Path(cfg["msst_dir"]).exists()
                 and bool(cfg.get("msst_python")) and Path(cfg["msst_python"]).exists(),
        "label": "MSST 人声分离工具链",
        "desc": str(cfg.get("msst_dir", "")),
        "size_hint": "约 2~6 GB（含 env 与分离模型）",
    }
    st["ddsp"] = {
        "ready": bool(cfg.get("ddsp_dir")) and Path(cfg["ddsp_dir"]).exists()
                 and bool(cfg.get("ddsp_python")) and Path(cfg["ddsp_python"]).exists(),
        "label": "DDSP 转音工具链",
        "desc": str(cfg.get("ddsp_dir", "")),
        "size_hint": "约 2~6 GB（含 env 与 vocoder）",
    }
    md = cfg.get("model_dir", "")
    bundled = app_dir() / "models"
    if (not md or not Path(md).exists()) and bundled.exists():
        md = str(bundled)
    n = 0
    if md and Path(md).exists():
        n = len(find_models(md))
    tag = "（随包内置）" if md and Path(md) == bundled else ""
    st["models"] = {
        "ready": n > 0,
        "label": "角色模型（%d 个）%s" % (n, tag),
        "desc": str(md),
        "size_hint": "随软件包分发，放在 exe 同级 models\\ 即自动识别",
    }
    st["ffmpeg"] = {
        "ready": bool(find_ffmpeg()),
        "label": "ffmpeg（视频合成）",
        "desc": str(find_ffmpeg() or "未找到"),
        "size_hint": "约 80 MB",
    }
    return st


def download_component(url, dest_dir, log_cb=None, progress_cb=None):
    """从 zip 直链下载组件并解压到 dest_dir。返回解压目录。"""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not url:
        raise RuntimeError("下载地址为空")
    tmp = dest_dir / ("_download_%s.zip" % int(time.time()))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = r.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if progress_cb and total:
                    progress_cb(got / total)
                elif progress_cb:
                    progress_cb(-1)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("下载失败: %s" % e)
    if log_cb:
        log_cb("下载完成（%d MB），正在解压…" % (got // 1024 // 1024))
    out = dest_dir / ("comp_%s" % int(time.time()))
    out.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(tmp) as z:
            z.extractall(str(out))
    except Exception as e:
        shutil.rmtree(str(out), ignore_errors=True)
        tmp.unlink(missing_ok=True)
        raise RuntimeError("解压失败: %s" % e)
    tmp.unlink(missing_ok=True)
    return out


# ---------------------------------------------------------------- 组件自动获取（v1.4：软件自主联网找资源）

# 内置候选源；软件按顺序自动尝试，第一个可用的即使用。
# v1.4.1：优先从作者仓库 Mortis77/coverstudio Release 拉取全量组件（0 门槛），第三方官方源作兜底。
REL_BASE = "https://github.com/Mortis77/coverstudio/releases/download/v1.4-components"
COMPONENT_SOURCES = {
    "ffmpeg": [
        {"kind": "direct", "url": REL_BASE + "/ffmpeg-win64.zip",
         "label": "ffmpeg（作者仓库 v1.4-components，约 141 MB）"},
        {"kind": "direct", "url": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
         "label": "ffmpeg 官方构建（gyan.dev，约 110 MB）"},
    ],
    "msst": [
        {"kind": "direct", "url": REL_BASE + "/MSST-GUI-1.4.0-src.zip",
         "label": "MSST-GUI 源码（作者仓库 v1.4-components，含 inference.py）"},
        {"kind": "github_zipball", "repo": "AliceNavigator/Music-Source-Separation-Training-GUI",
         "label": "MSST-GUI 官方源码（GitHub 最新版，含 inference.py）"},
    ],
    "msst_model": [
        {"kind": "direct", "url": REL_BASE + "/mel_band_roformer_vocals_becruily.ckpt",
         "label": "人声分离模型（作者仓库 v1.4-components，约 870 MB）"},
        {"kind": "direct", "url": "https://huggingface.co/becruily/mel-band-roformer-vocals/resolve/main/mel_band_roformer_vocals_becruily.ckpt",
         "label": "人声分离模型（HuggingFace becruily，约 870 MB）"},
        {"kind": "direct", "url": "https://hf-mirror.com/becruily/mel-band-roformer-vocals/resolve/main/mel_band_roformer_vocals_becruily.ckpt",
         "label": "人声分离模型（HF 镜像）"},
    ],
    "ddsp": [
        {"kind": "direct", "url": REL_BASE + "/DDSP-SVC-6.2-src.zip",
         "label": "DDSP-SVC 源码（作者仓库 v1.4-components，含 main_reflow.py）"},
        {"kind": "github_zipball", "repo": "yxlllc/DDSP-SVC",
         "label": "DDSP-SVC 官方源码（GitHub 最新版，含 main_reflow.py）"},
    ],
    "ddsp_pretrain": [
        {"kind": "direct", "url": REL_BASE + "/contentvec_checkpoint_best_legacy_500.pt",
         "path": "contentvec/checkpoint_best_legacy_500.pt",
         "label": "contentvec 编码器（作者仓库 v1.4-components）"},
        {"kind": "direct", "url": REL_BASE + "/hubert-soft-0d54a1f4.pt",
         "path": "contentvec/hubert-soft-0d54a1f4.pt",
         "label": "hubert-soft 编码器（作者仓库 v1.4-components）"},
        {"kind": "direct", "url": REL_BASE + "/rmvpe_model.pt",
         "path": "rmvpe/model.pt",
         "label": "RMVPE 音高提取器（作者仓库 v1.4-components）"},
        {"kind": "direct", "url": REL_BASE + "/nsf_hifigan_config.json",
         "path": "nsf_hifigan/config.json",
         "label": "NSF-HiFiGAN 配置（作者仓库 v1.4-components）"},
        {"kind": "direct", "url": REL_BASE + "/nsf_hifigan_model",
         "path": "nsf_hifigan/model",
         "label": "NSF-HiFiGAN 声码器模型（作者仓库 v1.4-components）"},
        {"kind": "direct", "url": REL_BASE + "/nsf_hifigan_44.1k_model.ckpt",
         "path": "nsf_hifigan/nsf_hifigan_44.1k_hop512_128bin_2024.02.ckpt",
         "label": "NSF-HiFiGAN 44.1k（作者仓库 v1.4-components）"},
    ],
}


def github_latest_release(repo):
    """解析 GitHub 仓库最新 release，返回 {tag, zipball_url, assets:[{name,url,size}]}。"""
    api = "https://api.github.com/repos/%s/releases/latest" % repo
    req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0 (CoverStudio)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    assets = [{"name": a["name"], "url": a["browser_download_url"], "size": a.get("size", 0)}
              for a in (d.get("assets") or [])]
    return {"tag": d.get("tag_name", ""), "zipball_url": d.get("zipball_url"), "assets": assets}


def resolve_component_sources(key, log_cb=None):
    """把 COMPONENT_SOURCES 解析为可下载项列表（统一 direct 形态）。动态源在此实时展开。"""
    items = []
    for src in COMPONENT_SOURCES.get(key, []):
        try:
            if src["kind"] == "direct":
                items.append({"kind": "direct", "url": src["url"], "label": src["label"],
                              "rel_path": src.get("path", "")})
            elif src["kind"] == "github_zipball":
                rel = github_latest_release(src["repo"])
                if rel.get("zipball_url"):
                    items.append({"kind": "direct", "url": rel["zipball_url"],
                                  "label": src["label"] + "（tag=%s）" % rel.get("tag", "")})
            elif src["kind"] == "github_asset":
                rel = github_latest_release(src["repo"])
                for a in rel.get("assets", []):
                    if re.search(src["pattern"], a["name"]):
                        items.append({"kind": "direct", "url": a["url"],
                                      "label": src["label"] + "（%s）" % a["name"]})
                        break
            elif src["kind"] == "modelscope":
                url = "https://modelscope.cn/models/%s/resolve/master/%s" % (src["repo"], src["path"])
                items.append({"kind": "direct", "url": url, "label": src["label"],
                              "rel_path": src["path"]})
        except Exception as e:
            if log_cb:
                log_cb("候选源解析失败（%s）：%s" % (src.get("label", src), e))
    return items


def download_file(url, dst, log_cb=None, progress_cb=None):
    """下载单个文件到 dst（用于 .pt/.json/model 等非 zip 资源）。"""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r, open(str(dst), "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if progress_cb and total:
                progress_cb(got / total)
    if log_cb:
        log_cb("下载完成（%d MB）：%s" % (got // 1024 // 1024, dst.name))
    return dst


def setup_python_env(comp_dir, requirements_rel="requirements.txt", log_cb=None):
    """在组件目录自动创建运行环境（venv + torch CPU + requirements），返回 env\\python.exe 路径或 None。"""
    comp_dir = Path(comp_dir)
    py = shutil.which("python")
    if not py:
        py = shutil.which("py")
    if not py:
        if log_cb:
            log_cb("未检测到系统 Python，无法自动创建环境。请安装 Python 3.10/3.11 后重试，或改用本地安装。")
        return None
    env_dir = comp_dir / "env"
    if (env_dir / "python.exe").exists():
        return str(env_dir / "python.exe")
    if log_cb:
        log_cb("创建 Python 环境（venv）…")
    rc = subprocess.run([py, "-m", "venv", str(env_dir)], capture_output=True, text=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if rc.returncode != 0:
        if log_cb:
            log_cb("创建 venv 失败：%s" % (rc.stderr or "")[-300:])
        return None
    epy = str(env_dir / "python.exe")

    def pip(args):
        return subprocess.run([epy, "-m", "pip", "install", "--disable-pip-version-check", "-q"] + args,
                              capture_output=True, text=True,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    if log_cb:
        log_cb("安装 torch（CPU 版）…（约 250~400 MB，请耐心等待）")
    r = pip(["--index-url", "https://download.pytorch.org/whl/cpu", "torch", "torchaudio"])
    if r.returncode != 0:
        if log_cb:
            log_cb("torch CPU 源失败，改用清华源重试…")
        r = pip(["-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "torch", "torchaudio"])
    req = comp_dir / requirements_rel
    if req.exists() and r.returncode == 0:
        if log_cb:
            log_cb("安装组件依赖（requirements.txt）…（耗时较长）")
        r = pip(["-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "-r", str(req)])
    if r.returncode != 0:
        if log_cb:
            log_cb("依赖安装未完全成功（%s）。环境已创建，可后续手动补齐。" % ((r.stderr or "")[-150:]))
    else:
        if log_cb:
            log_cb("依赖安装完成。")
    return epy


def portable_cfg(base_dir):
    """探测 exe 同级 toolkit\\ 便携版结构，命中则返回配置（角色模型支持 exe 同级 models\\ 内置）。"""
    tk = Path(base_dir) / "toolkit"
    cands = sorted([p for p in tk.glob("DDSP/*") if (p / "env" / "python.exe").exists()]) if tk.exists() else []
    dd = cands[0] if cands else None
    cands = sorted([p for p in tk.glob("MSST/*") if (p / "env" / "python.exe").exists()]) if tk.exists() else []
    ms = cands[-1] if cands else None
    if not dd or not ms:
        return None
    ddpy = dd / "env" / "python.exe"
    mspy = ms / "env" / "python.exe"
    if not ddpy.exists() or not mspy.exists():
        return None
    md = tk / "models" if tk.exists() else None
    if md is None or not md.exists():
        bundled = Path(base_dir) / "models"
        md = bundled if bundled.exists() else None
    if md is None:
        return None
    cfg = dict(DEFAULT_CONFIG)
    cfg.update({
        "ddsp_dir": str(dd),
        "ddsp_python": str(ddpy),
        "msst_dir": str(ms),
        "msst_python": str(mspy),
        "model_dir": str(md),
    })
    return cfg


def load_config():
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            cfg = dict(DEFAULT_CONFIG)
            cfg.update({k: v for k, v in data.items() if v})
            cfg.pop("component_urls", None)  # v1.4 移除手动直链配置
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def auto_detect_config():
    p = portable_cfg(app_dir())
    if p:
        return p
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            cfg = dict(DEFAULT_CONFIG)
            cfg.update({k: v for k, v in data.items() if v})
            cfg.pop("component_urls", None)
            if Path(cfg.get("msst_dir", "")).exists() and Path(cfg.get("model_dir", "")).exists():
                return cfg
        except Exception:
            pass
    cfg = dict(DEFAULT_CONFIG)
    base = Path("E:/AI音乐")
    if not base.exists():
        return cfg
    ddsp_candidates = list(base.glob(".DDSP/DDSP-barbara-6.2"))
    if ddsp_candidates and ddsp_candidates[0].exists():
        dd = ddsp_candidates[0]
        py = dd / "env" / "python.exe"
        if py.exists():
            cfg["ddsp_dir"], cfg["ddsp_python"] = str(dd), str(py)
    msst_candidates = list(base.glob("分离/MSST-GUI-*"))
    if msst_candidates:
        ms = sorted(msst_candidates, key=lambda p: p.name)[-1]
        py = ms / "env" / "python.exe"
        if py.exists():
            cfg["msst_dir"], cfg["msst_python"] = str(ms), str(py)
    model_dirs = list(base.glob(".DDSP/DDSP模型"))
    if model_dirs:
        cfg["model_dir"] = str(model_dirs[0])
    return cfg


def find_models(model_dir):
    models = []
    root = Path(model_dir)
    if not root.exists():
        return models
    pat = re.compile(r"(.+?)\s*步数(\d+)")
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        m = pat.search(d.name)
        role = d.name
        if m:
            role = m.group(1).strip()
        ckpt = None
        best = (0, None)
        for p in d.rglob("model_*.pt"):
            mm = re.search(r"model_(\d+)\.pt$", p.name)
            if not mm:
                continue
            step = int(mm.group(1))
            if step > best[0]:
                best = (step, p)
        ckpt = best[1]
        if ckpt is None:
            for p in d.rglob("*.pt"):
                ckpt = p
                break
        if ckpt:
            # 下拉显示角色全名（如「丰川祥子」），优先查全名映射，未知角色回退目录名
            display = ROLE_FULL_NAMES.get(role, d.name.strip())
            models.append((display, role, str(ckpt)))
    return models


# ---------------------------------------------------------------- 网易云

def netease_search(keyword, limit=8):
    url = "https://music.163.com/api/search/get/web?" + urllib.parse.urlencode(
        {"s": keyword, "type": 1, "limit": limit}
    )
    req = urllib.request.Request(url, headers={"Referer": "https://music.163.com/", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    out = []
    for s in (data.get("result") or {}).get("songs") or []:
        artists = "/".join(a.get("name", "") for a in s.get("artists") or [])
        dur = s.get("duration", 0) / 1000
        out.append({
            "name": s.get("name", ""),
            "artist": artists,
            "id": s.get("id"),
            "duration": int(dur),
        })
    return out


def netease_download(song_id, dst):
    url = f"http://music.163.com/song/media/outer/url?id={song_id}.mp3"
    tmp = str(dst) + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as f:
        shutil.copyfileobj(resp, f)
    os.replace(tmp, str(dst))
    return dst


def netease_lyric(song_id):
    url = f"https://music.163.com/api/song/lyric?id={song_id}&lv=1&kv=1&tv=-1"
    req = urllib.request.Request(url, headers={"Referer": "https://music.163.com/", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    return (data.get("lrc") or {}).get("lyric") or ""


def lrc_to_srt(lrc):
    def ts(sec):
        h = int(sec // 3600); m = int(sec % 3600 // 60); s = int(sec % 60)
        ms = int(round((sec - int(sec)) * 1000))
        if ms >= 1000:
            s += 1; ms = 0
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    pat = re.compile(r"\[(\d+):(\d+)(?:\.(\d+))?\]\s*(.*)")
    for raw in lrc.splitlines():
        m = pat.match(raw)
        if not m:
            continue
        mm, ss = int(m.group(1)), int(m.group(2))
        frac = (m.group(3) or "0").ljust(3, "0")[:3]
        sec = mm * 60 + ss + int(frac) / 1000
        text = m.group(4).strip()
        if text:
            lines.append((sec, text))
    lines.sort()
    srt = []
    for i, (t, txt) in enumerate(lines):
        end = lines[i + 1][0] if i + 1 < len(lines) else t + 4
        srt.append(f"{i+1}\n{ts(t)} --> {ts(end)}\n{txt}\n")
    return "\n".join(srt)


# ---------------------------------------------------------------- ffmpeg / 合成

FFMPEG_DL_URLS = [
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
]

BG_PALETTES = {
    "星河紫": ["0x0d0628", "0x2b1055", "0x4a1a7a", "0x6d28d9"],
    "深海蓝": ["0x020617", "0x0b2e4f", "0x1b4965", "0x134e4a"],
    "晚霞橙": ["0x2d0a2a", "0x7b2d4e", "0xd95d39", "0xf2a65a"],
    "樱花粉": ["0x2d1b3d", "0x8e5573", "0xc98f9b", "0xf2d5d7"],
    "霓虹青": ["0x001514", "0x003844", "0x006a71", "0x00c2a8"],
    "极夜黑金": ["0x0a0a0a", "0x1a1a2e", "0x3d2c6a", "0xc9a227"],
}
BG_PALETTE_KEYS = list(BG_PALETTES.keys())


def find_ffmpeg():
    for p in [app_dir() / "ffmpeg.exe", app_dir() / "ffmpeg" / "bin" / "ffmpeg.exe"]:
        if p.exists():
            return str(p)
    p = shutil.which("ffmpeg")
    if p:
        return p
    return None


def download_ffmpeg(log_cb):
    tmp = Path(tempfile.gettempdir()) / "ffmpeg_dl.zip"
    for url in FFMPEG_DL_URLS:
        try:
            log_cb("下载 ffmpeg（%s）…" % url.split("/")[2])
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=900) as r, open(str(tmp), "wb") as f:
                shutil.copyfileobj(r, f)
            with zipfile.ZipFile(str(tmp)) as z:
                names = [n for n in z.namelist() if n.endswith("bin/ffmpeg.exe")]
                if not names:
                    raise RuntimeError("压缩包内未找到 ffmpeg.exe")
                name = names[0]
                dst = app_dir() / "ffmpeg.exe"
                with z.open(name) as src, open(str(dst), "wb") as d:
                    shutil.copyfileobj(src, d)
            try:
                tmp.unlink()
            except Exception:
                pass
            log_cb("ffmpeg 就绪: %s" % dst)
            return str(dst)
        except Exception as e:
            log_cb("下载失败: %s" % e)
    return None


def audio_duration(path, ffmpeg=None):
    if Path(path).suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                return w.getnframes() / float(w.getframerate())
        except Exception:
            pass
    if not ffmpeg:
        ffmpeg = find_ffmpeg()
    if ffmpeg:
        p = subprocess.run([ffmpeg, "-i", str(path)], capture_output=True, text=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        m = re.search(r"Duration: (\d+):(\d+):(\d+)\.(\d+)", p.stderr)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 100.0
    return 0.0


def bing_image_search(keyword, limit=8):
    url = "https://www.bing.com/images/search?q=" + urllib.parse.quote(keyword) + "&form=HDRSC2&first=1"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", "replace")
    found = []
    for m in re.findall(r'm="([^"]+)"', html):
        txt = unescape(m)
        try:
            d = json.loads(txt)
            u = d.get("murl")
        except Exception:
            u = None
        if u and u.startswith("http") and u not in found:
            found.append(u)
        if len(found) >= limit:
            break
    return found


def download_image(url, dst, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        ctype = r.headers.get("Content-Type", "")
        if not ctype.startswith("image/") and not ctype.startswith("application/octet-stream"):
            raise RuntimeError("非图片内容: %s" % ctype)
        data = r.read()
    Path(dst).write_bytes(data)
    return dst


def gen_gradient_bg(ffmpeg, out_mp4, dur, palette, size="1920x1080", fps=25, seed_text=""):
    """用 lavfi gradients 生成流动渐变背景视频（本地氛围生成，稳定零依赖）。"""
    if not palette:
        palette = BG_PALETTES[BG_PALETTE_KEYS[abs(hash(seed_text or "bg")) % len(BG_PALETTE_KEYS)]]
    cols = ":".join(f"c{i}=0x{v}" for i, v in enumerate(palette[:4]))
    src = f"gradients=s={size}:{cols}:nb_colors={len(palette[:4])}:d=8:speed=0.03:rate={fps}"
    cmd = [ffmpeg, "-y", "-f", "lavfi", "-i", src, "-t", str(max(dur, 1.0)),
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", str(out_mp4)]
    p = subprocess.run(cmd, capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode != 0 or not Path(out_mp4).exists():
        raise RuntimeError("氛围背景生成失败\n" + p.stderr[-500:])
    return out_mp4


def ff_escape_path(p):
    return str(p).replace("\\", "/").replace(":", "\\:")


def compose_mp4(ffmpeg, bg, audio, srt, out_mp4, size="1920x1080", fps=25,
                kenburns=True, log_cb=None, cancel=None):
    """背景(图片/GIF/视频) + 音乐 + 字幕 -> MP4"""
    def log(m):
        if log_cb:
            log_cb(m)

    bg = Path(bg)
    ext = bg.suffix.lower()
    is_img = ext in (".jpg", ".jpeg", ".png", ".bmp", ".webp")
    is_gif = ext == ".gif"
    w, h = size.split("x")

    sub_filter = ""
    if srt and Path(srt).exists():
        srt_s = ff_escape_path(srt)
        sub_filter = (",subtitles='%s':fontsdir='C\\:/Windows/Fonts':"
                      "force_style='FontName=Microsoft YaHei,FontSize=20,Bold=1,Outline=2,Shadow=1'"
                      % srt_s)

    cmd = [ffmpeg, "-y"]
    if is_img:
        cmd += ["-loop", "1", "-i", str(bg)]
    else:
        cmd += ["-stream_loop", "-1", "-i", str(bg)]
    cmd += ["-i", str(audio)]

    base_scale = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    if is_img and kenburns:
        vf = base_scale + (f",zoompan=z='min(1+0.0012*on,1.2)':"
                           f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:fps={fps}:s={w}x{h}") + sub_filter + "[v]"
    else:
        vf = base_scale + sub_filter + "[v]"
    cmd += ["-filter_complex", vf, "-map", "[v]", "-map", "1:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", "-shortest", str(out_mp4)]

    log("合成 MP4：%s" % bg.name)
    log("命令: " + " ".join(cmd[:4]) + " ...")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    last = ""
    for line in iter(proc.stdout.readline, b""):
        text = line.decode("utf-8", "replace").strip()
        if text:
            m = re.search(r"time=(\d+):(\d+):(\d+)\.(\d+)", text)
            if m:
                cur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                if int(cur) % 10 == 0 and text != last:
                    last = text
                    log("进度 %s" % m.group(0))
            elif "Error" in text or "error" in text:
                log("ffmpeg: " + text[-160:])
            else:
                log(text[-160:])
            if cancel and cancel.is_set():
                proc.terminate()
                raise RuntimeError("已取消")
    proc.wait()
    if proc.returncode != 0 or not Path(out_mp4).exists():
        raise RuntimeError("MP4 合成失败")
    log("MP4 完成: %s" % os.path.basename(out_mp4))
    return out_mp4


# ---------------------------------------------------------------- 子进程

def run_python(python_path, args, log_cb=None, timeout=None):
    cmd = [python_path] + args
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    buf = []
    if log_cb:
        for line in iter(proc.stdout.readline, b""):
            text = line.decode("utf-8", "replace").rstrip()
            if text:
                log_cb(text)
            buf.append(text)
    else:
        out, _ = proc.communicate(timeout=timeout)
        buf = out.decode("utf-8", "replace").splitlines()
    proc.wait(timeout=timeout)
    return proc.returncode, "\n".join(buf)


# ---------------------------------------------------------------- 流程

class Pipeline:
    def __init__(self, cfg, log_cb, stage_cb):
        self.cfg = cfg
        self.log = log_cb
        self.stage = stage_cb
        self.cancel = threading.Event()

    def _log(self, msg):
        self.log(msg)

    def to_wav(self, src, dst):
        self._log("[转码] %s -> %s" % (os.path.basename(src), os.path.basename(dst)))
        rc, out = run_python(self.cfg["ddsp_python"], [
            str(AUDIO_UTILS), "to_wav", str(src), str(dst), "--sr", "44100"
        ], self._log)
        if rc != 0 or "TO_WAV_OK" not in out:
            raise RuntimeError("音频转码失败\n" + out[-500:])
        return dst

    def separate(self, wav, workdir):
        self._log("===== 阶段1/5：人声分离（MSST）=====")
        in_dir = workdir / "sep_in"
        out_dir = workdir / "sep_out"
        in_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(wav, in_dir / "input.wav")
        cfg = self.cfg
        args = [
            os.path.join(cfg["msst_dir"], "inference.py"),
            "--model_type", "mel_band_roformer",
            "--config_path", os.path.join(cfg["msst_dir"], "configs", cfg["msst_config"]),
            "--start_check_point", os.path.join(cfg["msst_dir"], "pretrain", cfg["msst_ckpt"]),
            "--input_folder", str(in_dir),
            "--store_dir", str(out_dir),
            "--extract_instrumental",
            "--disable_detailed_pbar",
        ]
        if cfg.get("use_gpu_separate", True):
            args += ["--device_ids", "0"]
        else:
            args += ["--force_cpu"]
        self._log("调用 MSST 人声分离…")
        rc, out = run_python(cfg["msst_python"], args, self._log, timeout=None)
        if rc != 0:
            raise RuntimeError("人声分离失败\n" + out[-800:])
        vocals = out_dir / "input_vocals.wav"
        inst = out_dir / "input_instrumental.wav"
        if not vocals.exists():
            for p in out_dir.rglob("*.wav"):
                if "vocals" in p.name:
                    vocals = p
                if "instrumental" in p.name:
                    inst = p
        if not vocals.exists() or not inst.exists():
            raise RuntimeError("分离产物缺失: %s" % out_dir)
        return vocals, inst

    def convert(self, vocals, model_ckpt, out_wav, key=0):
        self._log("===== 阶段2/5：AI 转音（DDSP）=====")
        if self.cancel.is_set():
            raise RuntimeError("已取消")
        args = [
            os.path.join(self.cfg["ddsp_dir"], "main_reflow.py"),
            "-m", model_ckpt,
            "-i", str(vocals),
            "-o", str(out_wav),
            "-d", "cpu",
            "-k", str(key),
            "-pe", "rmvpe",
        ]
        rc, out = run_python(self.cfg["ddsp_python"], args, self._log, timeout=None)
        if rc != 0:
            raise RuntimeError("AI 转音失败\n" + out[-800:])
        if not out_wav.exists():
            raise RuntimeError("转音产物缺失")
        return out_wav

    def mix(self, vocals, inst, out_wav, vg=1.0, ig=0.7):
        self._log("===== 阶段3/5：混音 =====")
        args = [
            str(AUDIO_UTILS), "mix", str(vocals), str(inst), str(out_wav),
            "--vg", str(vg), "--ig", str(ig), "--peak", "0.95",
        ]
        rc, out = run_python(self.cfg["ddsp_python"], args, self._log, timeout=None)
        if rc != 0 or "MIX_OK" not in out:
            raise RuntimeError("混音失败\n" + out[-500:])
        return out_wav

    def make_lyrics(self, song_id, hint, out_srt):
        self._log("===== 阶段4/5：生成歌词字幕 =====")
        lrc = ""
        if song_id:
            try:
                lrc = netease_lyric(song_id)
            except Exception as e:
                self._log("取歌词失败: %s" % e)
        if not lrc and hint:
            self._log("尝试按文件名搜索歌词: %s" % hint)
            try:
                res = netease_search(hint, limit=3)
                if res:
                    lrc = netease_lyric(res[0]["id"])
                    self._log("命中: %s - %s" % (res[0]["name"], res[0]["artist"]))
            except Exception as e:
                self._log("歌词搜索失败: %s" % e)
        if not lrc:
            self._log("未能获取歌词，跳过字幕生成（可手动放置同名 .srt）")
            return None
        srt_text = lrc_to_srt(lrc)
        Path(out_srt).write_text(srt_text, encoding="utf-8-sig")
        self._log("字幕已生成: %s" % os.path.basename(out_srt))
        return out_srt

    def compose_video(self, ffmpeg, bg, audio, srt, out_mp4, size, fps, kenburns):
        self._log("===== 阶段5/5：合成 MP4 =====")
        compose_mp4(ffmpeg, bg, audio, srt, out_mp4, size=size, fps=fps,
                    kenburns=kenburns, log_cb=self._log, cancel=self.cancel)
        return out_mp4

    def run(self, source_audio, model_ckpt, role, workdir, out_wav, key=0, vg=1.0, ig=0.7,
            lyric_song_id=None, lyric_hint=None, lyric_out=None,
            compose_cfg=None):
        workdir = Path(workdir)
        workdir.mkdir(parents=True, exist_ok=True)
        self.stage("准备音频")
        wav = workdir / "source.wav"
        if Path(source_audio).suffix.lower() == ".wav":
            wav = Path(source_audio)
        else:
            self.to_wav(source_audio, wav)
        if self.cancel.is_set():
            raise RuntimeError("已取消")
        vocals, inst = self.separate(wav, workdir)
        if self.cancel.is_set():
            raise RuntimeError("已取消")
        self.stage("AI 转音")
        cv = workdir / "converted_vocals.wav"
        self.convert(vocals, model_ckpt, cv, key)
        if self.cancel.is_set():
            raise RuntimeError("已取消")
        self.stage("混音输出")
        self.mix(cv, inst, out_wav, vg, ig)
        if lyric_out:
            self.stage("生成歌词字幕")
            self.make_lyrics(lyric_song_id, lyric_hint, lyric_out)
        if compose_cfg and compose_cfg.get("enabled"):
            self.stage("合成 MP4")
            ffmpeg = compose_cfg["ffmpeg"]
            if not ffmpeg:
                raise RuntimeError("未找到 ffmpeg，请先在背景页安装/下载 ffmpeg")
            bg = compose_cfg["bg"]
            srt = compose_cfg.get("srt") or lyric_out
            if srt and not Path(srt).exists():
                srt = None
            out_mp4 = Path(out_wav).with_suffix(".mp4")
            self.compose_video(ffmpeg, bg, out_wav, srt, out_mp4,
                               compose_cfg.get("size", "1920x1080"),
                               compose_cfg.get("fps", 25),
                               compose_cfg.get("kenburns", True))
        self.stage("完成")
        return out_wav


# ---------------------------------------------------------------- 便携版

def make_portable(cfg, dst, log_cb):
    """把工具链+模型+软件复制为便携版文件夹。"""
    dst = Path(dst)
    tk = dst / "toolkit"
    tk.mkdir(parents=True, exist_ok=True)

    def copy(src, dest, label):
        src = Path(src)
        if not src.exists():
            log_cb("跳过缺失目录: %s" % src)
            return
        log_cb("复制 %s …（%s）" % (label, src))
        if dest.exists():
            shutil.rmtree(str(dest))
        shutil.copytree(str(src), str(dest),
                        ignore=shutil.ignore_patterns("__pycache__", ".git", "*.log", "events.out.*"))
        log_cb("完成 %s" % label)

    copy(cfg.get("msst_dir"), tk / "MSST" / Path(cfg.get("msst_dir")).name, "MSST 工具链")
    copy(cfg.get("ddsp_dir"), tk / "DDSP" / Path(cfg.get("ddsp_dir")).name, "DDSP 工具链")
    copy(cfg.get("model_dir"), tk / "models", "角色模型")
    md_src = cfg.get("model_dir")
    if md_src and Path(md_src).exists():
        copy(md_src, dst / "models", "角色模型（内置到根目录，随包分发）")
    for name in ("AI翻唱工坊.exe", "audio_utils.py", "README.md", "config.json", "ffmpeg.exe"):
        p = app_dir() / name
        if p.exists():
            shutil.copy2(str(p), str(dst / name))
    log_cb("便携版完成：%s" % dst)


# ---------------------------------------------------------------- GUI

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{VERSION}")
        self.geometry("860x720")
        self.minsize(760, 620)
        self.q = queue.Queue()
        self.worker = None
        self.portable_worker = None
        self.bg_worker = None
        self.cfg = self._init_config()
        self.bg_path = None
        self.bg_search_results = []
        self.bg_kind = "import"

        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")

        self._build_ui()
        self.after(100, self._poll)

    def _init_config(self):
        cfg = auto_detect_config()
        if not CONFIG_FILE.exists():
            save_config(cfg)
        self._models = find_models(cfg["model_dir"])
        if not self._models:
            messagebox.showwarning(
                APP_NAME,
                "未在 %s 找到角色模型。\n请打开 设置-工具链路径 检查模型目录，\n或在 组件管理 中按需安装缺失组件。" % cfg["model_dir"],
            )
        missing = [k for k, v in component_status(cfg).items() if not v["ready"]]
        if missing:
            names = {"msst": "MSST 工具链", "ddsp": "DDSP 工具链", "models": "角色模型", "ffmpeg": "ffmpeg"}
            self._missing_at_startup = [names[m] for m in missing]
        else:
            self._missing_at_startup = []
        return cfg

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=10, pady=8)
        ttk.Label(top, text="AI 翻唱工坊", font=("Microsoft YaHei UI", 16, "bold")).pack(side=tk.LEFT)
        ttk.Label(top, text="翻唱 + 视频合成（免 PR）", foreground="#666").pack(side=tk.LEFT, padx=10)
        ttk.Button(top, text="组件管理", command=self._open_component_manager).pack(side=tk.RIGHT)
        ttk.Button(top, text="设置", command=self._open_settings).pack(side=tk.RIGHT)

        nb = ttk.Notebook(self)
        nb.pack(fill=tk.X, padx=10)
        self.tab_file = ttk.Frame(nb)
        self.tab_search = ttk.Frame(nb)
        self.tab_bg = ttk.Frame(nb)
        nb.add(self.tab_file, text=" ① 导入原音频 ")
        nb.add(self.tab_search, text=" ② 搜索歌名 ")
        nb.add(self.tab_bg, text=" ③ 背景与合成 ")

        row = ttk.Frame(self.tab_file)
        row.pack(fill=tk.X, padx=8, pady=8)
        self.file_path = tk.StringVar()
        ttk.Entry(row, textvariable=self.file_path, width=56).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text="选择 mp3/wav", command=self._pick_file).pack(side=tk.LEFT, padx=6)

        srow = ttk.Frame(self.tab_search)
        srow.pack(fill=tk.X, padx=8, pady=8)
        self.search_text = tk.StringVar()
        ttk.Entry(srow, textvariable=self.search_text, width=40).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(srow, text="搜索", command=self._do_search).pack(side=tk.LEFT, padx=6)
        self.result_var = tk.StringVar(value="输入歌名搜索网易云音乐")
        ttk.Label(self.tab_search, textvariable=self.result_var, foreground="#777").pack(anchor=tk.W, padx=8)
        self.result_list = tk.Listbox(self.tab_search, height=7)
        self.result_list.pack(fill=tk.X, padx=8, pady=4)
        self._search_results = []

        self._build_bg_tab()

        rrow = ttk.Frame(self)
        rrow.pack(fill=tk.X, padx=10, pady=4)
        ttk.Label(rrow, text="翻唱角色:").pack(side=tk.LEFT)
        self.role_var = tk.StringVar()
        self.role_cb = ttk.Combobox(rrow, textvariable=self.role_var, state="readonly", width=28)
        names = [m[0] for m in self._models] or ["（未发现模型）"]
        self.role_cb["values"] = names
        if names:
            self.role_cb.current(0)
        self.role_cb.pack(side=tk.LEFT, padx=6)

        prow = ttk.Frame(self)
        prow.pack(fill=tk.X, padx=10, pady=4)
        ttk.Label(prow, text="变调 key:").pack(side=tk.LEFT)
        self.key_var = tk.StringVar(value="0")
        ttk.Spinbox(prow, from_=-12, to=12, textvariable=self.key_var, width=4).pack(side=tk.LEFT, padx=6)
        ttk.Label(prow, text="伴奏音量:").pack(side=tk.LEFT, padx=(12, 0))
        self.ig_var = tk.StringVar(value="0.7")
        ttk.Spinbox(prow, from_=0.0, to=1.0, increment=0.1, textvariable=self.ig_var, width=4).pack(side=tk.LEFT, padx=6)
        ttk.Label(prow, text="输出目录:").pack(side=tk.LEFT, padx=(12, 0))
        self.out_var = tk.StringVar(value=self.cfg.get("output_dir", ""))
        ttk.Entry(prow, textvariable=self.out_var, width=24).pack(side=tk.LEFT, padx=6)
        ttk.Button(prow, text="浏览", command=self._pick_outdir).pack(side=tk.LEFT)

        opt = ttk.Frame(self)
        opt.pack(fill=tk.X, padx=10, pady=2)
        self.lyric_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="输出歌词字幕（.srt）", variable=self.lyric_var).pack(side=tk.LEFT)
        self.auto_video_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="翻唱完成后自动合成 MP4（需先在背景页配好背景）",
                        variable=self.auto_video_var).pack(side=tk.LEFT, padx=20)

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill=tk.X, padx=10, pady=6)
        self.stage_var = tk.StringVar(value="就绪")
        ttk.Label(self, textvariable=self.stage_var).pack(anchor=tk.W, padx=10)

        self.log_text = tk.Text(self, height=10, state="disabled", font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 6))

        btm = ttk.Frame(self)
        btm.pack(fill=tk.X, padx=10, pady=(0, 8))
        self.start_btn = ttk.Button(btm, text="开始翻唱", command=self._start)
        self.start_btn.pack(side=tk.LEFT)
        self.cancel_btn = ttk.Button(btm, text="取消", command=self._cancel, state="disabled")
        self.cancel_btn.pack(side=tk.LEFT, padx=6)
        ttk.Button(btm, text="组件管理", command=self._open_component_manager).pack(side=tk.LEFT, padx=6)
        ttk.Button(btm, text="打开输出目录", command=self._open_outdir).pack(side=tk.RIGHT)

    # ---------- 背景页 ----------
    def _build_bg_tab(self):
        f = self.tab_bg
        self.bg_src_var = tk.StringVar(value="import")
        src = ttk.Frame(f)
        src.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(src, text="背景来源:").pack(side=tk.LEFT)
        for key, label in [("import", "导入文件"), ("search", "网上搜索"), ("gen", "生成背景")]:
            ttk.Radiobutton(src, text=label, value=key, variable=self.bg_src_var,
                            command=self._bg_src_changed).pack(side=tk.LEFT, padx=6)

        self.bg_import_frame = ttk.Frame(f)
        self.bg_import_frame.pack(fill=tk.X, padx=8, pady=2)
        self.bg_file = tk.StringVar()
        ttk.Entry(self.bg_import_frame, textvariable=self.bg_file, width=60).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(self.bg_import_frame, text="选择图片/GIF/视频", command=self._pick_bg).pack(side=tk.LEFT, padx=6)

        self.bg_search_frame = ttk.Frame(f)
        self.bg_search_frame.pack(fill=tk.X, padx=8, pady=2)
        ttk.Label(self.bg_search_frame, text="关键词:").pack(side=tk.LEFT)
        self.bg_kw = tk.StringVar(value="星空 动漫 壁纸")
        ttk.Entry(self.bg_search_frame, textvariable=self.bg_kw, width=24).pack(side=tk.LEFT, padx=4)
        ttk.Button(self.bg_search_frame, text="搜索背景", command=self._bg_search).pack(side=tk.LEFT, padx=4)
        self.bg_search_var = tk.StringVar(value="搜索后选中一条即可作为背景")
        ttk.Label(f, textvariable=self.bg_search_var, foreground="#777").pack(anchor=tk.W, padx=12)
        self.bg_list = tk.Listbox(f, height=4)
        self.bg_list.pack(fill=tk.X, padx=8, pady=2)
        self.bg_list.bind("<Double-Button-1>", lambda e: self._bg_pick_result())

        self.bg_gen_frame = ttk.Frame(f)
        self.bg_gen_frame.pack(fill=tk.X, padx=8, pady=2)
        ttk.Label(self.bg_gen_frame, text="氛围色板:").pack(side=tk.LEFT)
        self.bg_palette_var = tk.StringVar(value=BG_PALETTE_KEYS[0])
        ttk.Combobox(self.bg_gen_frame, textvariable=self.bg_palette_var, state="readonly",
                     values=BG_PALETTE_KEYS, width=10).pack(side=tk.LEFT, padx=4)
        ttk.Button(self.bg_gen_frame, text="生成氛围背景", command=self._bg_generate).pack(side=tk.LEFT, padx=4)

        sep = ttk.Separator(f, orient=tk.HORIZONTAL)
        sep.pack(fill=tk.X, padx=8, pady=6)

        cfg = ttk.Frame(f)
        cfg.pack(fill=tk.X, padx=8)
        ttk.Label(cfg, text="当前背景:").pack(side=tk.LEFT)
        self.bg_status_var = tk.StringVar(value="未选择（将使用黑色底）")
        ttk.Label(cfg, textvariable=self.bg_status_var, foreground="#2b6").pack(side=tk.LEFT, padx=4)
        ttk.Button(cfg, text="清除", command=self._bg_clear).pack(side=tk.LEFT, padx=6)
        ttk.Label(cfg, text="分辨率:").pack(side=tk.LEFT, padx=(20, 0))
        self.bg_size_var = tk.StringVar(value="1920x1080")
        ttk.Combobox(cfg, textvariable=self.bg_size_var, state="readonly",
                     values=["1920x1080", "1280x720", "1080x1920"], width=10).pack(side=tk.LEFT)
        self.bg_ken_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(cfg, text="背景轻微动态", variable=self.bg_ken_var).pack(side=tk.LEFT, padx=10)

        mux = ttk.Frame(f)
        mux.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(mux, text="音频(成品):").pack(side=tk.LEFT)
        self.mux_audio = tk.StringVar(value="")
        ttk.Entry(mux, textvariable=self.mux_audio, width=38).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        ttk.Button(mux, text="浏览", command=self._pick_mux_audio).pack(side=tk.LEFT)
        ttk.Label(mux, text="字幕:").pack(side=tk.LEFT, padx=(12, 0))
        self.mux_srt = tk.StringVar(value="")
        ttk.Entry(mux, textvariable=self.mux_srt, width=20).pack(side=tk.LEFT, padx=4)
        ttk.Button(mux, text="浏览", command=self._pick_mux_srt).pack(side=tk.LEFT)

        ff = ttk.Frame(f)
        ff.pack(fill=tk.X, padx=8, pady=4)
        self.ffmpeg_var = tk.StringVar(value=self._ffmpeg_status())
        ttk.Label(ff, textvariable=self.ffmpeg_var, foreground="#b50").pack(side=tk.LEFT)
        ttk.Button(ff, text="下载 ffmpeg", command=self._ffmpeg_download).pack(side=tk.LEFT, padx=6)
        ttk.Button(ff, text="合成 MP4", command=self._compose_video).pack(side=tk.RIGHT)

    def _ffmpeg_status(self):
        p = find_ffmpeg()
        if p:
            return "ffmpeg: 可用"
        return "ffmpeg: 未找到（合成 MP4 需要，点右侧下载）"

    def _bg_src_changed(self):
        v = self.bg_src_var.get()
        self.bg_import_frame.pack_forget()
        self.bg_search_frame.pack_forget()
        self.bg_gen_frame.pack_forget()
        if v == "import":
            self.bg_import_frame.pack(fill=tk.X, padx=8, pady=2, before=self.bg_search_frame)
        elif v == "search":
            self.bg_search_frame.pack(fill=tk.X, padx=8, pady=2, before=self.bg_gen_frame)
        else:
            self.bg_gen_frame.pack(fill=tk.X, padx=8, pady=2, before=sep)
        self._refresh_bg_frames()

    def _refresh_bg_frames(self):
        # 简化重排：按变量重新 pack 各 frame（pack_forget 后按序重放）
        pass

    def _pick_bg(self):
        p = filedialog.askopenfilename(
            title="选择背景", filetypes=[
                ("图片/视频", "*.jpg *.jpeg *.png *.bmp *.webp *.gif *.mp4 *.mov *.webm"),
                ("所有文件", "*.*")])
        if p:
            self.bg_file.set(p)
            self.bg_path = p
            self.bg_status_var.set(Path(p).name)

    def _bg_search(self):
        kw = self.bg_kw.get().strip() or "壁纸"
        self.bg_search_var.set("搜索中…")
        self.bg_list.delete(0, tk.END)
        self._bg_search_kw = kw
        t = threading.Thread(target=self._bg_search_job, args=(kw,), daemon=True)
        t.start()

    def _bg_search_job(self, kw):
        try:
            urls = bing_image_search(kw, limit=10)
        except Exception as e:
            self.q.put(("bg_search_result", [], "搜索失败: %s" % e))
            return
        self.q.put(("bg_search_result", urls, "找到 %d 张，双击选择下载" % len(urls)))

    def _bg_pick_result(self):
        sel = self.bg_list.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self.bg_search_results):
            return
        url = self.bg_search_results[idx]
        self.bg_search_var.set("下载中… %s" % url[:60])
        t = threading.Thread(target=self._bg_download_job, args=(url,), daemon=True)
        t.start()

    def _bg_download_job(self, url):
        try:
            work = Path(tempfile.gettempdir()) / "cover_bg"
            work.mkdir(exist_ok=True)
            dst = work / ("search_%d.jpg" % int(time.time()))
            download_image(url, dst)
            self.q.put(("bg_downloaded", str(dst), Path(dst).name))
        except Exception as e:
            self.q.put(("bg_search_result", [], "下载失败: %s" % e))

    def _bg_generate(self):
        pal = self.bg_palette_var.get()
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            self._append_log("未找到 ffmpeg，请先下载。")
            messagebox.showwarning(APP_NAME, "未找到 ffmpeg，请先点击「下载 ffmpeg」。")
            return
        t = threading.Thread(target=self._bg_generate_job, args=(ffmpeg, pal), daemon=True)
        t.start()

    def _bg_generate_job(self, ffmpeg, pal):
        try:
            work = Path(tempfile.gettempdir()) / "cover_bg"
            work.mkdir(exist_ok=True)
            out = work / ("bg_gen_%d.mp4" % int(time.time()))
            dur = 240
            self.q.put(("log", "生成氛围背景（%s，240 秒）…" % pal))
            gen_gradient_bg(ffmpeg, out, dur, BG_PALETTES[pal])
            self.q.put(("bg_downloaded", str(out), out.name))
        except Exception as e:
            self.q.put(("bg_search_result", [], "生成失败: %s" % e))

    def _bg_clear(self):
        self.bg_path = None
        self.bg_status_var.set("未选择（将使用黑色底）")

    def _pick_mux_audio(self):
        p = filedialog.askopenfilename(title="选择音频", filetypes=[("音频", "*.wav *.mp3 *.flac"), ("所有文件", "*.*")])
        if p:
            self.mux_audio.set(p)

    def _pick_mux_srt(self):
        p = filedialog.askopenfilename(title="选择字幕", filetypes=[("字幕", "*.srt *.ass"), ("所有文件", "*.*")])
        if p:
            self.mux_srt.set(p)

    def _ffmpeg_download(self):
        if self.bg_worker and self.bg_worker.is_alive():
            messagebox.showinfo(APP_NAME, "正在下载中…")
            return
        self.bg_worker = threading.Thread(target=self._ffmpeg_dl_job, daemon=True)
        self.bg_worker.start()

    def _ffmpeg_dl_job(self):
        self.q.put(("log", "开始下载 ffmpeg（约 90MB）…"))
        p = download_ffmpeg(lambda m: self.q.put(("log", m)))
        if p:
            self.q.put(("ffmpeg_ok", p))
        else:
            self.q.put(("bg_search_result", [], "ffmpeg 下载失败，可手动放置 ffmpeg.exe 到软件目录"))

    def _compose_video(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP_NAME, "正在处理中…")
            return
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            messagebox.showwarning(APP_NAME, "未找到 ffmpeg，请先点击「下载 ffmpeg」。")
            return
        audio = self.mux_audio.get().strip()
        if not audio or not Path(audio).exists():
            # 自动找输出目录里最近一次成品
            outdir = self.out_var.get() or self.cfg.get("output_dir")
            cands = sorted(Path(outdir).glob("*_翻唱_带伴奏.wav"), key=lambda p: p.stat().st_mtime, reverse=True) if Path(outdir).exists() else []
            if cands:
                audio = str(cands[0])
                self.mux_audio.set(audio)
                self._append_log("自动选用最近成品: %s" % Path(audio).name)
            else:
                messagebox.showwarning(APP_NAME, "请先选择要合成的音频（成品 wav）。")
                return
        srt = self.mux_srt.get().strip() or None
        if not srt and self.lyric_var.get():
            # 尝试同名 srt
            cand = Path(audio).with_suffix("").name
            outdir = Path(audio).parent
            for p in outdir.glob(cand + "_歌词.srt"):
                srt = str(p)
                break
            if not srt:
                for p in outdir.glob(Path(audio).stem + "*歌词.srt"):
                    srt = str(p)
                    break
            if srt:
                self.mux_srt.set(srt)
        bg = self.bg_path
        if not bg:
            messagebox.showinfo(APP_NAME, "未选择背景，将使用纯黑底。\n也可以在背景页选择/生成背景。")
        out_mp4 = Path(audio).with_suffix(".mp4")
        self._set_running(True)
        self._clear_log()
        self.progress.start(12)
        self.worker = threading.Thread(
            target=self._compose_job, args=(ffmpeg, bg, audio, srt, out_mp4), daemon=True)
        self.worker.start()

    def _compose_job(self, ffmpeg, bg, audio, srt, out_mp4):
        try:
            if bg is None:
                # 纯黑底：lavfi color
                w, h = self.bg_size_var.get().split("x")
                sub = ""
                if srt and Path(srt).exists():
                    sub = ",subtitles='%s':fontsdir='C\\:/Windows/Fonts':force_style='FontName=Microsoft YaHei,FontSize=20,Bold=1,Outline=2,Shadow=1'" % ff_escape_path(srt)
                cmd = [ffmpeg, "-y", "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r=25",
                       "-i", audio, "-filter_complex", f"[0:v]{sub}[v]",
                       "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "medium",
                       "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out_mp4)]
                p = subprocess.run(cmd, capture_output=True, text=True,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if p.returncode != 0 or not out_mp4.exists():
                    raise RuntimeError("MP4 合成失败\n" + p.stderr[-400:])
            else:
                compose_mp4(ffmpeg, bg, audio, srt, out_mp4,
                            size=self.bg_size_var.get(), kenburns=self.bg_ken_var.get(),
                            log_cb=lambda m: self.q.put(("log", m)))
            self.q.put(("video_done", str(out_mp4)))
        except Exception as e:
            self.q.put(("error", str(e)))

    # ---------- 通用 ----------
    def _pick_file(self):
        p = filedialog.askopenfilename(title="选择原音频", filetypes=[("音频", "*.mp3 *.wav *.flac *.m4a"), ("所有文件", "*.*")])
        if p:
            self.file_path.set(p)

    def _pick_outdir(self):
        p = filedialog.askdirectory(title="选择输出目录")
        if p:
            self.out_var.set(p)

    def _open_outdir(self):
        d = self.out_var.get() or self.cfg.get("output_dir")
        if d and os.path.isdir(d):
            os.startfile(d)  # noqa
        else:
            messagebox.showinfo(APP_NAME, "输出目录尚不存在，运行一次后即可打开。")

    def _do_search(self):
        kw = self.search_text.get().strip()
        if not kw:
            return
        self.result_var.set("搜索中…")
        self.update_idletasks()
        try:
            res = netease_search(kw)
        except Exception as e:
            self.result_var.set("搜索失败: %s" % e)
            return
        self._search_results = res
        self.result_list.delete(0, tk.END)
        for r in res:
            self.result_list.insert(tk.END, "%s - %s（%d秒）" % (r["name"], r["artist"], r["duration"]))
        self.result_var.set("共 %d 条结果，双击或选中后点开始" % len(res))

    def _selected_search(self):
        sel = self.result_list.curselection()
        if not sel:
            return None
        return self._search_results[sel[0]]

    def _open_component_manager(self):
        w = tk.Toplevel(self)
        w.title("组件管理 - 按需安装")
        w.geometry("720x480")
        w.transient(self)
        body = ttk.Frame(w)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

        ttk.Label(body, text="按需安装：软件本体很小，组件缺失时可自动联网获取或从本地安装。",
                  foreground="#555").pack(anchor=tk.W, pady=(0, 6))

        cols = ttk.Frame(body)
        cols.pack(fill=tk.X)
        for t in ("组件", "状态", "大小参考"):
            pass
        self.comp_rows = {}

        def refresh():
            st = component_status(self.cfg)
            for key, row in self.comp_rows.items():
                info = st[key]
                row["status"].config(text="已就绪" if info["ready"] else "缺失",
                                     foreground="#2b6" if info["ready"] else "#d33")
                row["desc"].config(text=info["desc"])

        def local_install(key):
            info = {
                "msst": ("选择 MSST 工具链目录（含 inference.py 与 env\\python.exe）", "dir"),
                "ddsp": ("选择 DDSP 工具链目录（含 main_reflow.py 与 env\\python.exe）", "dir"),
                "models": ("选择角色模型目录（含“角色名 步数N”子目录）", "dir"),
                "ffmpeg": ("选择 ffmpeg.exe", "file"),
            }[key]
            if info[1] == "dir":
                p = filedialog.askdirectory(title=info[0])
            else:
                p = filedialog.askopenfilename(title=info[0], filetypes=[("exe", "*.exe")])
            if not p:
                return
            self._append_log("组件管理器：本地安装 %s <- %s" % (key, p))
            t = threading.Thread(target=self._comp_install_job, args=(key, p), daemon=True)
            t.start()
            messagebox.showinfo(APP_NAME, "已开始安装，请留意主界面日志。")

        def auto_fetch(key):
            self._append_log("组件管理器：自动获取 %s（软件自主联网查找资源）" % key)
            t = threading.Thread(target=self._comp_auto_job, args=(key,), daemon=True)
            t.start()
            messagebox.showinfo(APP_NAME, "已开始自动获取：软件会联网查找官方资源并下载安装，\n耗时取决于组件大小与网速，请留意主界面日志。")

        for key, label in (("msst", "MSST 人声分离工具链"),
                           ("ddsp", "DDSP 转音工具链"),
                           ("models", "角色模型"),
                           ("ffmpeg", "ffmpeg（视频合成）")):
            f = ttk.Frame(cols)
            f.pack(fill=tk.X, pady=3)
            ttk.Label(f, text=label, width=26, anchor=tk.W).pack(side=tk.LEFT)
            stv = ttk.Label(f, text="…", width=6, anchor=tk.CENTER)
            stv.pack(side=tk.LEFT)
            desc = ttk.Label(f, text="", foreground="#888", anchor=tk.W)
            desc.pack(side=tk.LEFT, fill=tk.X, expand=True)
            ttk.Button(f, text="本地安装", command=lambda k=key: local_install(k)).pack(side=tk.RIGHT, padx=2)
            ttk.Button(f, text="自动获取", command=lambda k=key: auto_fetch(k)).pack(side=tk.RIGHT, padx=2)
            self.comp_rows[key] = {"status": stv, "desc": desc}

        sep = ttk.Separator(body, orient=tk.HORIZONTAL)
        sep.pack(fill=tk.X, pady=8)

        tip = ttk.Label(body, text=(
            "说明：\n"
            "· 自动获取：软件自动联网查找官方资源（GitHub 最新源码/预训练模型、ModelScope、ffmpeg 官方构建），"
            "下载并自动创建运行环境，适合新机器首装。\n"
            "· 本地安装：从本机已有目录复制组件到 exe 同级 toolkit\\，适合已有工具链的机器快速就绪。\n"
            "· 角色模型：随软件包内置，放在 exe 同级 models\\ 即自动识别（无需下载）；也可用“本地安装”导入自己的模型目录。"
        ), foreground="#666", justify=tk.LEFT, wraplength=680)
        tip.pack(anchor=tk.W)

        btns = ttk.Frame(body)
        btns.pack(fill=tk.X, pady=8)
        ttk.Button(btns, text="刷新状态", command=refresh).pack(side=tk.LEFT)
        ttk.Button(btns, text="关闭", command=w.destroy).pack(side=tk.RIGHT)
        refresh()

    # ---------- 组件安装/下载线程 ----------
    def _comp_install_job(self, key, src):
        try:
            self.q.put(("log", "安装组件 %s（%s）…" % (key, src)))
            dst_tk = app_dir() / "toolkit"
            if key == "ffmpeg":
                dst = dst_tk / "ffmpeg.exe"
                dst_tk.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, str(dst))
            elif key == "models":
                dst = dst_tk / "models"
                if dst.exists():
                    shutil.rmtree(str(dst))
                shutil.copytree(src, str(dst), ignore=shutil.ignore_patterns("__pycache__", ".git", "*.log"))
            else:
                src_p = Path(src)
                dst = dst_tk / ("MSST" if key == "msst" else "DDSP") / src_p.name
                if dst.exists():
                    shutil.rmtree(str(dst))
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(str(src_p), str(dst), ignore=shutil.ignore_patterns("__pycache__", ".git", "*.log", "events.out.*"))
            p = portable_cfg(app_dir())
            if p:
                self.cfg = p
                save_config(self.cfg)
                self._models = find_models(self.cfg["model_dir"])
                names = [m[0] for m in self._models] or ["（未发现模型）"]
                self.role_cb["values"] = names
                if names:
                    self.role_cb.current(0)
            self.q.put(("log", "组件 %s 安装完成。" % key))
            self.q.put(("info", "组件 %s 安装完成。" % key))
        except Exception as e:
            self.q.put(("error", "组件 %s 安装失败: %s" % (key, e)))

    def _comp_auto_job(self, key):
        """v1.4：自动获取组件 —— 软件自主联网解析官方源并下载安装，无需用户填直链。"""
        try:
            self.q.put(("log", "自动获取组件 %s：正在联网查找资源…" % key))
            dst_tk = app_dir() / "toolkit"
            dst_tk.mkdir(parents=True, exist_ok=True)

            def log(m):
                self.q.put(("log", m))

            if key == "ffmpeg":
                cands = resolve_component_sources("ffmpeg", log_cb=log)
                done = False
                for c in cands[:1]:
                    try:
                        tmp = dst_tk / "_dl_tmp"
                        out = download_component(c["url"], str(tmp), log_cb=log)
                        exes = [p for p in out.rglob("ffmpeg*.exe")]
                        if not exes:
                            raise RuntimeError("压缩包内未找到 ffmpeg.exe")
                        shutil.copy2(str(exes[0]), str(dst_tk / "ffmpeg.exe"))
                        shutil.rmtree(str(tmp), ignore_errors=True)
                        done = True
                        break
                    except Exception as e:
                        log("候选 %s 失败：%s" % (c.get("label"), e))
                if not done:
                    raise RuntimeError("ffmpeg 自动获取失败：所有候选源均不可达")
            elif key == "msst":
                self._auto_fetch_msst(log)
            elif key == "ddsp":
                self._auto_fetch_ddsp(log)
            elif key == "models":
                raise RuntimeError("角色模型随软件包内置，无需下载；请将模型目录放在 exe 同级 models\\ 后刷新状态。")
            p = portable_cfg(app_dir())
            if p:
                self.cfg = p
                save_config(self.cfg)
                self._models = find_models(self.cfg["model_dir"])
                names = [m[0] for m in self._models] or ["（未发现模型）"]
                self.role_cb["values"] = names
                if names:
                    self.role_cb.current(0)
            self.q.put(("log", "组件 %s 自动获取完成。" % key))
            self.q.put(("info", "组件 %s 自动获取完成。" % key))
        except Exception as e:
            self.q.put(("error", "组件 %s 自动获取失败: %s" % (key, e)))

    def _auto_fetch_msst(self, log):
        """自动获取 MSST：官方源码 + 人声分离模型 + 自动创建 Python 环境。"""
        dst_tk = app_dir() / "toolkit"
        cands = resolve_component_sources("msst", log_cb=log)
        if not cands:
            raise RuntimeError("MSST 官方源码解析失败（GitHub 不可达？）")
        tmp = dst_tk / "_dl_tmp"
        out = download_component(cands[0]["url"], str(tmp), log_cb=log)
        src_p = [p for p in out.rglob("inference.py")][0].parent
        dst = dst_tk / "MSST" / src_p.name
        if dst.exists():
            shutil.rmtree(str(dst))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(src_p), str(dst),
                        ignore=shutil.ignore_patterns("__pycache__", ".git", "events.out.*"))
        shutil.rmtree(str(tmp), ignore_errors=True)
        # 人声分离模型（约 870 MB；网络受限时允许失败，可稍后重试或本地安装）
        ckpt_name = self.cfg.get("msst_ckpt", "mel_band_roformer_vocals_becruily.ckpt")
        pretrain = dst / "pretrain"
        pretrain.mkdir(parents=True, exist_ok=True)
        if not (pretrain / ckpt_name).exists():
            m_cands = resolve_component_sources("msst_model", log_cb=log)
            got = False
            for mc in m_cands:
                try:
                    log("下载分离模型：%s" % mc.get("label"))
                    download_file(mc["url"], pretrain / ckpt_name, log_cb=log)
                    got = True
                    break
                except Exception as e:
                    log("候选失败：%s" % e)
            if not got:
                log("分离模型未下载成功（约 870 MB，网络受限时常见）。可稍后重试，或改用本地安装。")
        # 自动创建 Python 环境
        epy = setup_python_env(dst, "requirements.txt", log_cb=log)
        if epy:
            cfg = self.cfg
            cfg["msst_dir"] = str(dst)
            cfg["msst_python"] = epy
            ccfg = dst / "configs" / cfg.get("msst_config", "")
            if not ccfg.exists():
                names = sorted(p.name for p in (dst / "configs").glob("config_vocals*.yaml")) if (dst / "configs").exists() else []
                if not names:
                    names = sorted(p.name for p in (dst / "configs").glob("*.yaml")) if (dst / "configs").exists() else []
                if names:
                    cfg["msst_config"] = names[0]
                    log("默认分离配置不存在，已自动选用 %s" % names[0])
            if not (pretrain / ckpt_name).exists():
                cks = sorted(p.name for p in pretrain.glob("*.ckpt")) if pretrain.exists() else []
                if cks:
                    cfg["msst_ckpt"] = cks[0]
                    log("已自动选用分离模型 %s" % cks[0])
            save_config(cfg)
            log("MSST 环境就绪：%s" % epy)

    def _auto_fetch_ddsp(self, log):
        """自动获取 DDSP：官方源码 + 预训练（编码器/音高/vocoder）+ 自动创建 Python 环境。"""
        dst_tk = app_dir() / "toolkit"
        cands = resolve_component_sources("ddsp", log_cb=log)
        if not cands:
            raise RuntimeError("DDSP 官方源码解析失败（GitHub 不可达？）")
        tmp = dst_tk / "_dl_tmp"
        out = download_component(cands[0]["url"], str(tmp), log_cb=log)
        src_p = [p for p in out.rglob("main_reflow.py")][0].parent
        dst = dst_tk / "DDSP" / src_p.name
        if dst.exists():
            shutil.rmtree(str(dst))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(str(src_p), str(dst),
                        ignore=shutil.ignore_patterns("__pycache__", ".git", "events.out.*", ".*"))
        shutil.rmtree(str(tmp), ignore_errors=True)
        # 预训练模型（contentvec / hubert / rmvpe / nsf_hifigan）
        pretrain = dst / "pretrain"
        pretrain.mkdir(parents=True, exist_ok=True)
        p_cands = resolve_component_sources("ddsp_pretrain", log_cb=log)
        for pc in p_cands:
            rel = pc.get("rel_path", "")
            name = rel.split("/")[-1]
            sub = rel.split("/")[-2] if "/" in rel else ""
            target = (pretrain / sub / name) if sub in ("contentvec", "rmvpe", "nsf_hifigan") else (pretrain / name)
            if target.exists():
                continue
            try:
                log("下载预训练：%s" % pc.get("label"))
                download_file(pc["url"], target, log_cb=log)
            except Exception as e:
                log("候选失败：%s" % e)
        # 可选：GitHub Release 的 model_0.pt（供训练，推理不强制）
        try:
            for c in cands:
                if "model_0.pt" in c.get("label", ""):
                    target = pretrain / "model_0.pt"
                    if not target.exists():
                        log("下载预训练模型：%s" % c.get("label"))
                        download_file(c["url"], target, log_cb=log)
                    break
        except Exception as e:
            log("预训练模型 model_0.pt 下载跳过：%s" % e)
        # 自动创建 Python 环境
        epy = setup_python_env(dst, "requirements.txt", log_cb=log)
        if epy:
            cfg = self.cfg
            cfg["ddsp_dir"] = str(dst)
            cfg["ddsp_python"] = epy
            save_config(cfg)
            log("DDSP 环境就绪：%s" % epy)

    def _open_settings(self):
        w = tk.Toplevel(self)
        w.title("设置 - 工具链路径")
        w.geometry("620x430")
        w.transient(self)
        fields = [
            ("MSST 目录", "msst_dir"),
            ("MSST Python", "msst_python"),
            ("DDSP 目录", "ddsp_dir"),
            ("DDSP Python", "ddsp_python"),
            ("模型目录", "model_dir"),
            ("分离模型(yaml)", "msst_config"),
            ("分离权重(ckpt)", "msst_ckpt"),
        ]
        vars_ = {}
        body = ttk.Frame(w)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        for i, (label, key) in enumerate(fields):
            ttk.Label(body, text=label).grid(row=i, column=0, sticky=tk.W, pady=3)
            v = tk.StringVar(value=self.cfg.get(key, ""))
            vars_[key] = v
            ent = ttk.Entry(body, textvariable=v, width=60)
            ent.grid(row=i, column=1, sticky=tk.EW, pady=3, padx=4)
        body.columnconfigure(1, weight=1)
        gpu_var = tk.BooleanVar(value=self.cfg.get("use_gpu_separate", True))
        ttk.Checkbutton(body, text="分离使用 GPU（更快；DDSP 转音固定 CPU）", variable=gpu_var).grid(
            row=len(fields), column=0, columnspan=2, sticky=tk.W, pady=6)

        def save():
            for k, v in vars_.items():
                if v.get().strip():
                    self.cfg[k] = v.get().strip()
            self.cfg["use_gpu_separate"] = gpu_var.get()
            save_config(self.cfg)
            self._models = find_models(self.cfg["model_dir"])
            names = [m[0] for m in self._models] or ["（未发现模型）"]
            self.role_cb["values"] = names
            if names:
                self.role_cb.current(0)
            messagebox.showinfo(APP_NAME, "设置已保存")
            w.destroy()

        ttk.Button(w, text="保存", command=save).pack(pady=6)

    def _start(self):
        source = None
        song_id = None
        hint = None
        if self.file_path.get().strip():
            source = self.file_path.get().strip()
            hint = Path(source).stem
        sel = self._selected_search()
        if sel:
            source = sel
            song_id = sel["id"]
            hint = sel["name"]
        if source is None:
            messagebox.showwarning(APP_NAME, "请先选择本地音频，或搜索并选中一首歌。")
            return
        if not self._models or not self.role_var.get() or self.role_var.get().startswith("（"):
            messagebox.showwarning(APP_NAME, "未发现可用角色模型，请检查设置中的模型目录。")
            return
        idx = self.role_cb.current()
        if idx < 0:
            idx = 0
        role = self._models[idx][1]
        ckpt = self._models[idx][2]

        try:
            key = int(self.key_var.get())
            ig = float(self.ig_var.get())
        except ValueError:
            messagebox.showerror(APP_NAME, "key 需为整数，伴奏音量需为数字。")
            return
        outdir = self.out_var.get().strip() or self.cfg.get("output_dir")
        os.makedirs(outdir, exist_ok=True)

        workdir = Path(outdir) / ".job"
        source_audio = source
        if isinstance(source, dict):
            self.log("下载歌曲: %s - %s" % (source["name"], source["artist"]))
            mp3 = workdir / ("source_%s.mp3" % source["id"])
            try:
                netease_download(source["id"], mp3)
            except Exception as e:
                messagebox.showerror(APP_NAME, "下载失败: %s" % e)
                return
            source_audio = str(mp3)

        stem = Path(source_audio).stem
        out_wav = Path(outdir) / ("%s_%s_翻唱_带伴奏.wav" % (stem, role))
        lyric_out = Path(outdir) / ("%s_%s_翻唱_歌词.srt" % (stem, role)) if self.lyric_var.get() else None

        compose_cfg = None
        if self.auto_video_var.get():
            compose_cfg = {
                "enabled": True,
                "ffmpeg": find_ffmpeg(),
                "bg": self.bg_path,
                "srt": self.mux_srt.get().strip() or None,
                "size": self.bg_size_var.get(),
                "fps": 25,
                "kenburns": self.bg_ken_var.get(),
            }
            if not compose_cfg["ffmpeg"]:
                self._append_log("未找到 ffmpeg，跳过自动合成，可在背景页下载后手动合成。")

        self._set_running(True)
        self._clear_log()
        self.progress.start(12)
        pipe = Pipeline(self.cfg, self._log, self._set_stage)
        self.worker = threading.Thread(
            target=self._run_job,
            args=(pipe, source_audio, ckpt, role, workdir, out_wav, key, ig, song_id, hint, lyric_out, compose_cfg),
            daemon=True,
        )
        self.worker.start()

    def _run_job(self, pipe, source, ckpt, role, workdir, out_wav, key, ig, song_id, hint, lyric_out, compose_cfg):
        try:
            pipe.run(source, ckpt, role, workdir, out_wav, key, 1.0, ig, song_id, hint, lyric_out, compose_cfg)
        except Exception as e:
            self.q.put(("error", str(e)))
            return
        self.q.put(("done", str(out_wav)))

    def _make_portable(self):
        if self.portable_worker and self.portable_worker.is_alive():
            messagebox.showinfo(APP_NAME, "正在制作便携版，请稍候…")
            return
        dst = filedialog.askdirectory(title="选择便携版保存位置（将生成 toolkit\\ 子目录）")
        if not dst:
            return
        cfg = self.cfg
        confirm = messagebox.askyesno(
            APP_NAME,
            "将复制以下内容到所选目录（工具链合计约 40+ GB，耗时较久，请确保磁盘空间充足）：\n"
            "  1) MSST 工具链（含分离模型）\n"
            "  2) DDSP 工具链（含 vocoder）\n"
            "  3) 8 个角色模型\n"
            "  4) 软件本体（含 ffmpeg）\n"
            "复制完成后，把整个文件夹拷到新电脑即可直接使用。\n\n确定开始吗？",
        )
        if not confirm:
            return
        self._set_running(True)
        self._clear_log()
        self.progress.start(12)
        self.portable_worker = threading.Thread(
            target=self._portable_job, args=(cfg, Path(dst)), daemon=True
        )
        self.portable_worker.start()

    def _portable_job(self, cfg, dst):
        try:
            make_portable(cfg, dst, lambda m: self.q.put(("log", m)))
        except Exception as e:
            self.q.put(("error", "制作便携版失败: %s" % e))
            return
        self.q.put(("info", "便携版制作完成。将整个文件夹拷贝到新电脑，双击 AI翻唱工坊.exe 即可。"))
        self.q.put(("portable_done", str(dst)))

    def _cancel(self):
        if self.worker and self.worker.is_alive():
            self.q.put(("info", "正在取消…（等待当前子进程结束）"))

    def _set_running(self, running):
        self.start_btn.config(state="disabled" if running else "normal")
        self.cancel_btn.config(state="normal" if running else "disabled")

    def _set_stage(self, s):
        self.q.put(("stage", s))

    def _log(self, msg):
        self.q.put(("log", msg))

    def _clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state="disabled")

    def _poll(self):
        try:
            while True:
                kind, *rest = self.q.get_nowait()
                if kind == "log":
                    self._append_log(rest[0])
                elif kind == "stage":
                    self.stage_var.set("阶段: " + rest[0])
                elif kind == "info":
                    self._append_log(rest[0])
                elif kind == "bg_search_result":
                    urls, msg = rest[0], rest[1]
                    self.bg_search_results = urls
                    self.bg_list.delete(0, tk.END)
                    for i, u in enumerate(urls):
                        host = urllib.parse.urlparse(u).netloc
                        self.bg_list.insert(tk.END, "%d. %s" % (i + 1, host))
                    self.bg_search_var.set(msg)
                elif kind == "bg_downloaded":
                    path, name = rest[0], rest[1]
                    self.bg_path = path
                    self.bg_status_var.set(name)
                    self.bg_search_var.set("已选用: %s" % name)
                    self._append_log("背景已就绪: %s" % path)
                elif kind == "ffmpeg_ok":
                    self.ffmpeg_var.set("ffmpeg: 可用")
                    self._append_log("ffmpeg 可用: %s" % rest[0])
                elif kind == "portable_done":
                    self.progress.stop()
                    self._set_running(False)
                    self.stage_var.set("完成")
                    messagebox.showinfo(APP_NAME, "便携版制作完成：\n%s" % rest[0])
                elif kind == "video_done":
                    self.progress.stop()
                    self._set_running(False)
                    self.stage_var.set("完成")
                    self._append_log("【完成】MP4: %s" % rest[0])
                    messagebox.showinfo(APP_NAME, "视频合成完成！\n%s" % rest[0])
                elif kind == "error":
                    self.progress.stop()
                    self._set_running(False)
                    self.stage_var.set("失败")
                    self._append_log("【失败】" + rest[0])
                    messagebox.showerror(APP_NAME, "失败：\n" + rest[0][-600:])
                elif kind == "done":
                    self.progress.stop()
                    self._set_running(False)
                    self.stage_var.set("完成")
                    self._append_log("【完成】成品: " + rest[0])
                    messagebox.showinfo(APP_NAME, "翻唱完成！\n%s" % rest[0])
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _append_log(self, msg):
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
