# -*- coding: utf-8 -*-
# ============================================================
# 本文件：AI 翻唱工坊 CoverStudio 主程序源码（人工注释版）
# 功能总览：导入原音频/搜索歌名 -> MSST 人声分离 -> DDSP 转音
#           -> 混音 -> 歌词字幕 -> 视频合成 MP4 -> 组件自动获取/便携版制作
# 注释约定：# 开头为注释；docstring 与字符串内容保持原样不变
# ============================================================
"""
AI 翻唱工坊 CoverStudio v1.4
一键翻唱：导入原音频或搜索歌名 -> MSST 分离 -> DDSP 转音 -> 混音 -> 歌词字幕 -> 成品
视频合成：背景（图片/GIF/视频/网上搜索/氛围生成）+ 音乐 + 字幕 -> MP4（免 PR）
自动分发：安装包小（仅 exe + ffmpeg），组件缺失时软件自主联网查找官方资源
            （GitHub 最新源码/预训练、ModelScope、ffmpeg 官方构建）并自动安装；
            角色模型随包内置到 exe 同级 models\\，无需下载。
"""
import json  # 标准库 json：读写 config.json 配置，解析网易云/GitHub API 返回的 JSON 数据
import os  # 标准库 os：提供路径拼接、目录判断、进程启动等操作系统能力
import queue  # 标准库 queue：线程安全队列，后台线程用它向 UI 主线程投递消息（日志/进度/结果）
import re  # 标准库 re：正则表达式，用于解析 LRC 时间轴、模型步数、ffmpeg 输出等
import shutil  # 标准库 shutil：文件复制/移动/删除目录树、查找可执行程序
import subprocess  # 标准库 subprocess：启动外部进程（ffmpeg、MSST/DDSP 的 python、pip 等）
import sys  # 标准库 sys：判断是否打包为 exe（sys.frozen）、定位程序目录
import tempfile  # 标准库 tempfile：获取系统临时目录，存放下载的 ffmpeg 压缩包、搜索背景等
import threading  # 标准库 threading：后台线程（翻唱流水线/下载/搜索），避免阻塞 GUI
import time  # 标准库 time：以时间戳生成唯一临时文件名
import urllib.parse  # 标准库 urllib.parse：URL 编码、解析 host（网易云搜索参数、背景链接显示）
import urllib.request  # 标准库 urllib.request：发起 HTTP 请求（下载歌曲/歌词/图片/组件压缩包）
import wave  # 标准库 wave：读取 wav 文件头以计算音频时长
import zipfile  # 标准库 zipfile：解压组件 zip 压缩包
from html import unescape  # html 模块的 unescape：反转义 HTML 实体（必应图片搜索页内嵌 JSON）
from pathlib import Path  # pathlib 的 Path：面向对象路径操作（exists/mkdir/glob/rglob 等）

import tkinter as tk  # tkinter：Python 内置 GUI 库，本程序窗口基类
from tkinter import ttk, filedialog, messagebox  # ttk：主题控件；filedialog：文件选择对话框；messagebox：消息弹窗

APP_NAME = "AI翻唱工坊 CoverStudio"  # 全局常量：应用显示名称（窗口标题、弹窗标题）
VERSION = "1.4.5"  # 全局常量：版本号（窗口标题展示用）

# ---------------------------------------------------------------- 路径/配置
# 全局路径函数、配置文件路径、默认配置字典

def app_dir():
    """返回程序所在目录：打包为 exe 时返回 exe 同级目录；源码运行时返回本文件所在目录。"""
    if getattr(sys, "frozen", False):  # getattr 探测 sys.frozen：PyInstaller 打包后该属性存在
        return Path(sys.executable).resolve().parent  # 打包模式：exe 文件所在目录的绝对路径
    return Path(__file__).resolve().parent  # 源码模式：本 .py 文件所在目录的绝对路径


CONFIG_FILE = app_dir() / "config.json"  # 全局配置：配置文件路径（程序目录下 config.json）
AUDIO_UTILS = app_dir() / "audio_utils.py"  # 全局配置：音频工具脚本路径（提供 to_wav / mix 子命令）

ROLE_TONES = {  # 全局字典：角色名 -> 音色风格描述（用于界面展示/日志说明）
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
ROLE_FULL_NAMES = {  # 全局字典：模型目录里的角色简称 -> 完整角色名（如「丰川祥子」）
    "祥子": "丰川祥子",
    "素世": "长崎素世",
    "喵梦": "祐天寺喵梦",
    "高松灯": "高松灯",
    "立希": "椎名立希",
    "初华": "三角初华",
    "若叶睦": "若叶睦",
    "爱音": "千早爱音",
}

DEFAULT_CONFIG = {  # 全局字典：默认配置（工具链路径、分离模型、输出目录等），首次启动写入 config.json
    "ddsp_dir": r"E:\AI音乐\.DDSP\DDSP-barbara-6.2",  # DDSP 转音工具链根目录
    "ddsp_python": r"E:\AI音乐\.DDSP\DDSP-barbara-6.2\env\python.exe",  # DDSP 环境 Python 解释器路径
    "msst_dir": r"E:\AI音乐\分离\MSST-GUI-1.4.0",  # MSST 人声分离工具链根目录
    "msst_python": r"E:\AI音乐\分离\MSST-GUI-1.4.0\env\python.exe",  # MSST 环境 Python 解释器路径
    "model_dir": r"E:\AI音乐\.DDSP\DDSP模型",  # 角色模型存放目录
    "msst_config": "config_vocals_mel_band_roformer_kim.yaml",  # 默认人声分离模型配置文件（yaml）
    "msst_ckpt": "mel_band_roformer_vocals_becruily.ckpt",  # 默认人声分离模型权重（ckpt）
    "use_gpu_separate": True,  # 分离阶段是否使用 GPU（更快；DDSP 转音固定用 CPU）
    "output_dir": str(Path.home() / "Desktop" / "AI翻唱成品"),  # 默认输出目录：桌面/AI翻唱成品
    # v1.4：组件由软件自动联网获取（内置 GitHub/ModelScope/官方源，无需手动填写直链）
    # v1.4.4：角色模型同样可从作者仓库 Release 自动获取；也支持 exe 同级 models\ 内置
}


# ---------------------------------------------------------------- 组件状态
# 检查各组件是否就绪；组件自动下载；配置探测

def component_status(cfg):
    """返回各组件就绪状态字典。key: msst/ddsp/models/ffmpeg。"""
    st = {}  # 局部变量：就绪状态结果字典
    st["msst"] = {  # MSST 组件状态
        "ready": bool(cfg.get("msst_dir")) and Path(cfg["msst_dir"]).exists()  # 就绪条件：目录配置非空且存在
                 and bool(cfg.get("msst_python")) and Path(cfg["msst_python"]).exists(),  # 且 python 解释器存在
        "label": "MSST 人声分离工具链",  # 组件显示名称
        "desc": str(cfg.get("msst_dir", "")),  # 组件描述：显示配置的目录路径
        "size_hint": "约 2~6 GB（含 env 与分离模型）",  # 大小参考提示
    }
    st["ddsp"] = {  # DDSP 组件状态（结构与 MSST 相同）
        "ready": bool(cfg.get("ddsp_dir")) and Path(cfg["ddsp_dir"]).exists()
                 and bool(cfg.get("ddsp_python")) and Path(cfg["ddsp_python"]).exists(),
        "label": "DDSP 转音工具链",
        "desc": str(cfg.get("ddsp_dir", "")),
        "size_hint": "约 2~6 GB（含 env 与 vocoder）",
    }
    md = cfg.get("model_dir", "")  # 局部变量：配置中的模型目录
    bundled = app_dir() / "models"  # 局部变量：exe/源码同级的内置 models 目录（随包分发）
    if (not md or not Path(md).exists()) and bundled.exists():  # 若配置目录缺失但内置目录存在，则回退到内置目录
        md = str(bundled)  # 回退：使用内置 models 目录
    n = 0  # 局部变量：模型数量计数
    if md and Path(md).exists():  # 目录存在时统计模型数量
        n = len(find_models(md))  # 调用 find_models 统计模型数量
    tag = ""  # 局部变量：模型来源标签（用于展示“随包内置/已自动获取”）
    if md and Path(md) == bundled:  # 模型目录是随包内置目录
        tag = "（随包内置）"  # 标记为随包内置
    elif md and Path(md) == app_dir() / "toolkit" / "models":  # 模型目录是自动获取下载的位置
        tag = "（已自动获取）"  # 标记为已自动获取
    st["models"] = {  # 角色模型组件状态
        "ready": n > 0,  # 就绪条件：检测到至少 1 个模型
        "label": "角色模型（%d 个）%s" % (n, tag),  # 显示名称：模型数量 + 来源标签
        "desc": str(md),  # 描述：模型目录路径
        "size_hint": "每个约 300~600MB（组件管理可自动获取）",  # 大小参考
    }
    st["ffmpeg"] = {  # ffmpeg 组件状态
        "ready": bool(find_ffmpeg()),  # 就绪条件：find_ffmpeg 能找到 ffmpeg
        "label": "ffmpeg（视频合成）",  # 显示名称
        "desc": str(find_ffmpeg() or "未找到"),  # 描述：找到的 ffmpeg 路径或“未找到”
        "size_hint": "约 80 MB",  # 大小参考
    }
    return st  # 返回四组件就绪状态字典


def download_component(url, dest_dir, log_cb=None, progress_cb=None):
    """从 zip 直链下载组件并解压到 dest_dir。返回解压目录。"""
    dest_dir = Path(dest_dir)  # 参数：目标目录转成 Path 对象
    dest_dir.mkdir(parents=True, exist_ok=True)  # 创建目标目录（递归创建，已存在不报错）
    if not url:  # 校验：下载地址为空则报错
        raise RuntimeError("下载地址为空")  # 抛异常提示下载地址为空
    tmp = dest_dir / ("_download_%s.zip" % int(time.time()))  # 临时 zip 文件名：目标目录/_download_<时间戳>.zip
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})  # 构造 HTTP 请求，携带浏览器 UA 防拦截
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:  # 发起下载（60s 超时）并打开写入流
            total = int(r.headers.get("Content-Length") or 0)  # 从响应头读取总大小（无则 0）
            got = 0  # 已下载字节数计数
            while True:  # 循环读取响应体
                chunk = r.read(1024 * 256)  # 每次读取 256KB 分块
                if not chunk:  # 读到空块说明下载完毕
                    break  # 退出循环
                f.write(chunk)  # 将数据块写入临时文件
                got += len(chunk)  # 累加已下载字节
                if progress_cb and total:  # 有进度回调且总大小已知
                    progress_cb(got / total)  # 回调进度（0~1 比例）
                elif progress_cb:  # 有回调但总大小未知
                    progress_cb(-1)  # 回调 -1 表示不确定进度
    except Exception as e:  # 下载过程任何异常
        tmp.unlink(missing_ok=True)  # 清理残留临时 zip（不存在也忽略）
        raise RuntimeError("下载失败: %s" % e)  # 抛出“下载失败”异常并带原因
    if log_cb:  # 有日志回调
        log_cb("下载完成（%d MB），正在解压…" % (got // 1024 // 1024))  # 输出下载大小并提示解压
    out = dest_dir / ("comp_%s" % int(time.time()))  # 解压目标目录：目标目录/comp_<时间戳>
    out.mkdir(parents=True, exist_ok=True)  # 创建解压目录
    try:
        with zipfile.ZipFile(tmp) as z:  # 打开下载的 zip 文件
            z.extractall(str(out))  # 全部解压到 out 目录
    except Exception as e:  # 解压异常
        shutil.rmtree(str(out), ignore_errors=True)  # 清理已创建的解压目录
        tmp.unlink(missing_ok=True)  # 清理临时 zip
        raise RuntimeError("解压失败: %s" % e)  # 抛出“解压失败”异常
    tmp.unlink(missing_ok=True)  # 成功后删除临时 zip 文件
    return out  # 返回解压目录 Path 对象


# ---------------------------------------------------------------- 组件自动获取（v1.4：软件自主联网找资源）
# 组件候选下载源配置与解析函数

# 内置候选源；软件按顺序自动尝试，第一个可用的即使用。
# v1.4.1：优先从作者仓库 Mortis77/coverstudio Release 拉取全量组件（0 门槛），第三方官方源作兜底。
REL_BASE = "https://github.com/Mortis77/coverstudio/releases/download/v1.4-components"  # 作者仓库组件发布基址
COMPONENT_SOURCES = {  # 全局字典：各组件候选下载源（按 key 分组，软件按顺序尝试）
    "ffmpeg": [  # ffmpeg 候选源列表
        {"kind": "direct", "url": REL_BASE + "/ffmpeg-win64.zip",  # 类型 direct 直链下载
         "label": "ffmpeg（作者仓库 v1.4-components，约 141 MB）"},  # 候选源说明
        {"kind": "direct", "url": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",  # 官方构建直链
         "label": "ffmpeg 官方构建（gyan.dev，约 110 MB）"},
    ],
    "msst": [  # MSST 源码候选源列表
        {"kind": "direct", "url": REL_BASE + "/MSST-GUI-1.4.0-src.zip",  # 作者仓库打包的源码 zip
         "label": "MSST-GUI 源码（作者仓库 v1.4-components，含 inference.py）"},
        {"kind": "github_zipball", "repo": "AliceNavigator/Music-Source-Separation-Training-GUI",  # 动态解析 GitHub 最新 release 源码
         "label": "MSST-GUI 官方源码（GitHub 最新版，含 inference.py）"},
    ],
    "msst_model": [  # 人声分离模型候选源列表
        {"kind": "direct", "url": REL_BASE + "/mel_band_roformer_vocals_becruily.ckpt",  # 作者仓库直链
         "label": "人声分离模型（作者仓库 v1.4-components，约 870 MB）"},
        {"kind": "direct", "url": "https://huggingface.co/becruily/mel-band-roformer-vocals/resolve/main/mel_band_roformer_vocals_becruily.ckpt",  # HuggingFace 直链
         "label": "人声分离模型（HuggingFace becruily，约 870 MB）"},
        {"kind": "direct", "url": "https://hf-mirror.com/becruily/mel-band-roformer-vocals/resolve/main/mel_band_roformer_vocals_becruily.ckpt",  # HF 国内镜像直链
         "label": "人声分离模型（HF 镜像）"},
    ],
    "ddsp": [  # DDSP 源码候选源列表
        {"kind": "direct", "url": REL_BASE + "/DDSP-SVC-6.2-src.zip",  # 作者仓库打包源码
         "label": "DDSP-SVC 源码（作者仓库 v1.4-components，含 main_reflow.py）"},
        {"kind": "github_zipball", "repo": "yxlllc/DDSP-SVC",  # 官方仓库动态解析
         "label": "DDSP-SVC 官方源码（GitHub 最新版，含 main_reflow.py）"},
    ],
    "ddsp_pretrain": [  # DDSP 预训练资源候选源列表（编码器/音高提取器/vocoder）
        {"kind": "direct", "url": REL_BASE + "/contentvec_checkpoint_best_legacy_500.pt",  # contentvec 编码器
         "path": "contentvec/checkpoint_best_legacy_500.pt",  # 解压后相对路径
         "label": "contentvec 编码器（作者仓库 v1.4-components）"},
        {"kind": "direct", "url": REL_BASE + "/hubert-soft-0d54a1f4.pt",  # hubert-soft 编码器
         "path": "contentvec/hubert-soft-0d54a1f4.pt",  # 相对路径
         "label": "hubert-soft 编码器（作者仓库 v1.4-components）"},
        {"kind": "direct", "url": REL_BASE + "/rmvpe_model.pt",  # RMVPE 音高提取器
         "path": "rmvpe/model.pt",  # 相对路径
         "label": "RMVPE 音高提取器（作者仓库 v1.4-components）"},
        {"kind": "direct", "url": REL_BASE + "/nsf_hifigan_config.json",  # NSF-HiFiGAN 配置
         "path": "nsf_hifigan/config.json",  # 相对路径
         "label": "NSF-HiFiGAN 配置（作者仓库 v1.4-components）"},
        {"kind": "direct", "url": REL_BASE + "/nsf_hifigan_model",  # NSF-HiFiGAN 声码器模型
         "path": "nsf_hifigan/model",  # 相对路径
         "label": "NSF-HiFiGAN 声码器模型（作者仓库 v1.4-components）"},
        {"kind": "direct", "url": REL_BASE + "/nsf_hifigan_44.1k_model.ckpt",  # 44.1k 声码器模型
         "path": "nsf_hifigan/nsf_hifigan_44.1k_hop512_128bin_2024.02.ckpt",  # 相对路径
         "label": "NSF-HiFiGAN 44.1k（作者仓库 v1.4-components）"},
    ],
}

def github_latest_release(repo):
    """解析 GitHub 仓库最新 release，返回 {tag, zipball_url, assets:[{name,url,size}]}。"""
    api = "https://api.github.com/repos/%s/releases/latest" % repo  # 拼接 GitHub API 最新 release 接口地址
    req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0 (CoverStudio)"})  # 构造请求（带应用 UA）
    with urllib.request.urlopen(req, timeout=30) as r:  # 发起请求，30 秒超时
        d = json.loads(r.read().decode("utf-8", "replace"))  # 解析响应 JSON（replace 容忍非法字节）
    assets = [{"name": a["name"], "url": a["browser_download_url"], "size": a.get("size", 0)}  # 提取资产列表：名称/下载直链/大小
              for a in (d.get("assets") or [])]  # 遍历 release 的 assets 字段（为空时取空列表）
    return {"tag": d.get("tag_name", ""), "zipball_url": d.get("zipball_url"), "assets": assets}  # 返回 tag、源码 zip 直链、资产列表


def resolve_component_sources(key, log_cb=None):
    """把 COMPONENT_SOURCES 解析为可下载项列表（统一 direct 形态）。动态源在此实时展开。"""
    items = []  # 局部变量：解析后的可下载项列表
    for src in COMPONENT_SOURCES.get(key, []):  # 遍历指定组件的候选源
        try:  # 单个源解析异常不影响其他源
            if src["kind"] == "direct":  # 直链源：直接使用
                items.append({"kind": "direct", "url": src["url"], "label": src["label"],  # 追加为 direct 项
                              "rel_path": src.get("path", "")})  # 携带相对安装路径（可能为空）
            elif src["kind"] == "github_zipball":  # GitHub 源码源：动态查询最新 release
                rel = github_latest_release(src["repo"])  # 获取仓库最新 release 信息
                if rel.get("zipball_url"):  # 存在源码 zip 直链
                    items.append({"kind": "direct", "url": rel["zipball_url"],  # 追加为 direct 项
                                  "label": src["label"] + "（tag=%s）" % rel.get("tag", "")})  # 标签附带 tag 号
            elif src["kind"] == "github_asset":  # GitHub 资产源：在最新 release 中按正则找资产
                rel = github_latest_release(src["repo"])  # 获取最新 release 信息
                for a in rel.get("assets", []):  # 遍历资产
                    if re.search(src["pattern"], a["name"]):  # 资产名匹配正则模式
                        items.append({"kind": "direct", "url": a["url"],  # 追加为 direct 项
                                      "label": src["label"] + "（%s）" % a["name"]})  # 标签附带资产名
                        break  # 找到第一个匹配即停止
            elif src["kind"] == "modelscope":  # ModelScope 源：拼接下载直链
                url = "https://modelscope.cn/models/%s/resolve/master/%s" % (src["repo"], src["path"])  # 构造 ModelScope 直链
                items.append({"kind": "direct", "url": url, "label": src["label"],  # 追加为 direct 项
                              "rel_path": src["path"]})  # 携带相对路径
        except Exception as e:  # 候选源解析失败
            if log_cb:  # 有日志回调
                log_cb("候选源解析失败（%s）：%s" % (src.get("label", src), e))  # 记录失败原因
    return items  # 返回统一形态的可下载项列表


def download_file(url, dst, log_cb=None, progress_cb=None):
    """下载单个文件到 dst（用于 .pt/.json/model 等非 zip 资源）。"""
    dst = Path(dst)  # 参数：目标路径转 Path 对象
    dst.parent.mkdir(parents=True, exist_ok=True)  # 创建目标文件所在目录
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})  # 构造请求
    with urllib.request.urlopen(req, timeout=60) as r, open(str(dst), "wb") as f:  # 下载并写入目标文件
        total = int(r.headers.get("Content-Length") or 0)  # 总大小（字节）
        got = 0  # 已下载字节数
        while True:  # 分块循环
            chunk = r.read(1024 * 256)  # 读 256KB
            if not chunk:  # 读完
                break  # 退出
            f.write(chunk)  # 写入文件
            got += len(chunk)  # 累计
            if progress_cb and total:  # 有进度回调
                progress_cb(got / total)  # 回报进度比例
    if log_cb:  # 有日志回调
        log_cb("下载完成（%d MB）：%s" % (got // 1024 // 1024, dst.name))  # 记录下载完成与大小
    return dst  # 返回目标文件 Path


def setup_python_env(comp_dir, requirements_rel="requirements.txt", log_cb=None):
    """在组件目录自动创建运行环境（venv + torch CPU + requirements），返回 env\\python.exe 路径或 None。"""
    comp_dir = Path(comp_dir)  # 参数：组件目录转 Path 对象
    py = shutil.which("python")  # 查找系统 PATH 中的 python
    if not py:  # 未找到 python
        py = shutil.which("py")  # 尝试 py 启动器
    if not py:  # 两种都找不到
        if log_cb:  # 有日志回调
            log_cb("未检测到系统 Python，无法自动创建环境。请安装 Python 3.10/3.11 后重试，或改用本地安装。")  # 提示用户
        return None  # 返回 None 表示失败
    env_dir = comp_dir / "env"  # 环境目录：组件目录/env
    if (env_dir / "python.exe").exists():  # 环境已存在
        return str(env_dir / "python.exe")  # 直接返回已有解释器路径
    if log_cb:  # 有日志回调
        log_cb("创建 Python 环境（venv）…")  # 提示开始创建
    rc = subprocess.run([py, "-m", "venv", str(env_dir)], capture_output=True, text=True,  # 执行 python -m venv 创建虚拟环境
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # Windows 下不弹出黑窗
    if rc.returncode != 0:  # 创建失败
        if log_cb:  # 有日志回调
            log_cb("创建 venv 失败：%s" % (rc.stderr or "")[-300:])  # 输出错误尾部
        return None  # 返回失败
    epy = str(env_dir / "python.exe")  # 环境内 python 解释器路径

    def pip(args):  # 内部函数：在虚拟环境中执行 pip install
        return subprocess.run([epy, "-m", "pip", "install", "--disable-pip-version-check", "-q"] + args,  # 静默安装指定包
                              capture_output=True, text=True,  # 捕获输出
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # 不弹窗

    if log_cb:  # 有日志回调
        log_cb("安装 torch（CPU 版）…（约 250~400 MB，请耐心等待）")  # 提示安装 torch
    r = pip(["--index-url", "https://download.pytorch.org/whl/cpu", "torch", "torchaudio"])  # 从 PyTorch 官方 CPU 源安装
    if r.returncode != 0:  # 官方源失败
        if log_cb:  # 有日志回调
            log_cb("torch CPU 源失败，改用清华源重试…")  # 提示换源
        r = pip(["-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "torch", "torchaudio"])  # 清华镜像重试
    req = comp_dir / requirements_rel  # requirements 文件路径（组件目录/requirements.txt）
    if req.exists() and r.returncode == 0:  # 有依赖清单且 torch 安装成功
        if log_cb:  # 有日志回调
            log_cb("安装组件依赖（requirements.txt）…（耗时较长）")  # 提示安装依赖
        r = pip(["-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "-r", str(req)])  # 安装依赖清单
    if r.returncode != 0:  # 依赖安装未完全成功
        if log_cb:  # 有日志回调
            log_cb("依赖安装未完全成功（%s）。环境已创建，可后续手动补齐。" % ((r.stderr or "")[-150:]))  # 提示可手动补齐
    else:  # 全部安装成功
        if log_cb:  # 有日志回调
            log_cb("依赖安装完成。")  # 提示完成
    return epy  # 返回环境 python 路径


def portable_cfg(base_dir):
    """探测 exe 同级 toolkit\\ 便携版结构，命中则返回配置（角色模型支持 exe 同级 models\\ 内置）。"""
    tk = Path(base_dir) / "toolkit"  # 便携版工具链目录：程序目录/toolkit
    cands = sorted([p for p in tk.glob("DDSP/*") if (p / "env" / "python.exe").exists()]) if tk.exists() else []  # 找含 env 的 DDSP 子目录并排序
    dd = cands[0] if cands else None  # DDSP 目录：取排序后第一个
    cands = sorted([p for p in tk.glob("MSST/*") if (p / "env" / "python.exe").exists()]) if tk.exists() else []  # 找含 env 的 MSST 子目录
    ms = cands[-1] if cands else None  # MSST 目录：取排序后最后一个（版本号最大）
    if not dd or not ms:  # 任一缺失则不是便携版
        return None  # 返回 None
    ddpy = dd / "env" / "python.exe"  # DDSP 解释器路径
    mspy = ms / "env" / "python.exe"  # MSST 解释器路径
    if not ddpy.exists() or not mspy.exists():  # 解释器缺失
        return None  # 返回 None
    md = tk / "models" if tk.exists() else None  # 便携版模型目录：toolkit/models
    if md is None or not md.exists():  # toolkit/models 不存在
        bundled = Path(base_dir) / "models"  # 回退到程序目录/models（随包内置）
        md = bundled if bundled.exists() else None  # 存在才使用
    if md is None:  # 模型目录都没有
        return None  # 返回 None
    cfg = dict(DEFAULT_CONFIG)  # 基于默认配置创建副本
    cfg.update({  # 更新便携版相关路径
        "ddsp_dir": str(dd),  # DDSP 目录
        "ddsp_python": str(ddpy),  # DDSP 解释器
        "msst_dir": str(ms),  # MSST 目录
        "msst_python": str(mspy),  # MSST 解释器
        "model_dir": str(md),  # 模型目录
    })
    return cfg  # 返回便携版配置字典


def load_config():
    """读取 config.json 并合并默认配置；文件损坏时返回默认配置。"""
    if CONFIG_FILE.exists():  # 配置文件存在
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))  # 读取并解析 JSON 配置
            cfg = dict(DEFAULT_CONFIG)  # 以默认配置为基础
            cfg.update({k: v for k, v in data.items() if v})  # 用文件中的非空值覆盖默认值
            cfg.pop("component_urls", None)  # v1.4 移除手动直链配置（兼容旧版字段）
            return cfg  # 返回合并后的配置
        except Exception:  # 解析失败（文件损坏）
            pass  # 忽略，走默认配置
    return dict(DEFAULT_CONFIG)  # 返回默认配置副本


def save_config(cfg):
    """将配置字典写入 config.json（UTF-8、非 ASCII 保留、缩进 2 空格）。"""
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")  # 序列化并写盘


def auto_detect_config():
    """自动探测配置：优先便携版结构 -> config.json -> E:/AI音乐 常见目录猜测。"""
    p = portable_cfg(app_dir())  # 先探测便携版结构
    if p:  # 命中便携版
        return p  # 直接返回
    if CONFIG_FILE.exists():  # 配置文件存在
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))  # 解析配置
            cfg = dict(DEFAULT_CONFIG)  # 默认配置为基础
            cfg.update({k: v for k, v in data.items() if v})  # 合并文件值
            cfg.pop("component_urls", None)  # 移除旧直链字段
            if Path(cfg.get("msst_dir", "")).exists() and Path(cfg.get("model_dir", "")).exists():  # 关键目录存在
                return cfg  # 返回该配置
        except Exception:  # 解析失败
            pass  # 忽略
    cfg = dict(DEFAULT_CONFIG)  # 回到默认配置
    base = Path("E:/AI音乐")  # 猜测基目录（用户机器上的工具链根目录）
    if not base.exists():  # 基目录不存在
        return cfg  # 返回纯默认配置
    ddsp_candidates = list(base.glob(".DDSP/DDSP-barbara-6.2"))  # 查找 DDSP 固定目录
    if ddsp_candidates and ddsp_candidates[0].exists():  # 找到
        dd = ddsp_candidates[0]  # 取第一个
        py = dd / "env" / "python.exe"  # 其环境解释器
        if py.exists():  # 解释器存在
            cfg["ddsp_dir"], cfg["ddsp_python"] = str(dd), str(py)  # 写入 DDSP 路径配置
    msst_candidates = list(base.glob("分离/MSST-GUI-*"))  # 查找 MSST 目录（通配版本号）
    if msst_candidates:  # 找到候选
        ms = sorted(msst_candidates, key=lambda p: p.name)[-1]  # 取名字排序最后一个（最新版本）
        py = ms / "env" / "python.exe"  # 其环境解释器
        if py.exists():  # 解释器存在
            cfg["msst_dir"], cfg["msst_python"] = str(ms), str(py)  # 写入 MSST 路径配置
    model_dirs = list(base.glob(".DDSP/DDSP模型"))  # 查找模型目录
    if model_dirs:  # 找到
        cfg["model_dir"] = str(model_dirs[0])  # 写入模型目录配置
    return cfg  # 返回自动探测后的配置


def find_models(model_dir):
    """扫描模型目录，返回 [(显示名, 角色名, 模型文件路径)]；自动挑选步数最大的 model_N.pt。"""
    models = []  # 结果列表：每个元素为 (显示名, 角色名, ckpt路径)
    root = Path(model_dir)  # 模型根目录
    if not root.exists():  # 目录不存在
        return models  # 返回空列表
    pat = re.compile(r"(.+?)\s*步数(\d+)")  # 正则：匹配“角色名 步数N”格式的目录名
    for d in sorted(root.iterdir()):  # 遍历根目录下所有子目录（排序保证稳定）
        if not d.is_dir():  # 跳过非目录项
            continue  # 继续下一个
        m = pat.search(d.name)  # 尝试解析目录名
        role = d.name  # 默认角色名为目录名
        if m:  # 匹配成功
            role = m.group(1).strip()  # 提取“步数”前的角色名（去空格）
        ckpt = None  # 模型文件路径变量
        best = (0, None)  # (最大步数, 对应文件路径)
        for p in d.rglob("model_*.pt"):  # 递归查找 model_N.pt
            mm = re.search(r"model_(\d+)\.pt$", p.name)  # 提取文件名中的步数 N
            if not mm:  # 不匹配则跳过
                continue  # 继续
            step = int(mm.group(1))  # 步数转整数
            if step > best[0]:  # 比当前最大步数大
                best = (step, p)  # 更新最佳项
        ckpt = best[1]  # 采用步数最大的模型
        if ckpt is None:  # 没有 model_N.pt
            for p in d.rglob("*.pt"):  # 退而求其次：任意 .pt 文件
                ckpt = p  # 取第一个
                break  # 只取一个
        if ckpt:  # 找到模型文件
            # 下拉显示角色全名（如「丰川祥子」），优先查全名映射，未知角色回退目录名
            display = ROLE_FULL_NAMES.get(role, d.name.strip())  # 全名映射优先，未知角色回退目录名
            models.append((display, role, str(ckpt)))  # 追加 (显示名, 角色名, 模型路径)
    return models  # 返回模型列表

def netease_search(keyword, limit=8):
    """网易云音乐搜索：按歌名/关键词返回歌曲信息列表 [{name,artist,id,duration}]。"""
    url = "https://music.163.com/api/search/get/web?" + urllib.parse.urlencode(  # 构造网易云搜索 API 地址
        {"s": keyword, "type": 1, "limit": limit}  # 参数：s=关键词, type=1 表示歌曲, limit=返回数量
    )
    req = urllib.request.Request(url, headers={"Referer": "https://music.163.com/", "User-Agent": "Mozilla/5.0"})  # 带 Referer 与 UA，模拟浏览器请求
    with urllib.request.urlopen(req, timeout=20) as resp:  # 发起请求，20 秒超时
        data = json.loads(resp.read().decode("utf-8", "replace"))  # 解析返回 JSON
    out = []  # 结果列表
    for s in (data.get("result") or {}).get("songs") or []:  # 遍历搜索结果的歌曲数组（各层缺省时为空）
        artists = "/".join(a.get("name", "") for a in s.get("artists") or [])  # 艺术家名用斜杠拼接（多歌手）
        dur = s.get("duration", 0) / 1000  # 时长毫秒转秒
        out.append({  # 追加歌曲条目
            "name": s.get("name", ""),  # 歌曲名
            "artist": artists,  # 歌手
            "id": s.get("id"),  # 网易云歌曲 ID（后续下载/取歌词用）
            "duration": int(dur),  # 时长秒
        })
    return out  # 返回歌曲信息列表


def netease_download(song_id, dst):
    """按网易云歌曲 ID 下载 mp3 到 dst（外链接口，失败可能返回 404 页面）。"""
    url = f"http://music.163.com/song/media/outer/url?id={song_id}.mp3"  # 网易云外链播放地址（免登录）
    tmp = str(dst) + ".part"  # 先写 .part 临时文件，避免下载中断污染目标文件
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})  # 构造请求
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as f:  # 下载响应体到临时文件
        shutil.copyfileobj(resp, f)  # 流式拷贝
    os.replace(tmp, str(dst))  # 原子替换：临时文件改名为目标文件
    return dst  # 返回目标路径


def netease_lyric(song_id):
    """按网易云歌曲 ID 获取 LRC 格式歌词文本。"""
    url = f"https://music.163.com/api/song/lyric?id={song_id}&lv=1&kv=1&tv=-1"  # lv=1 请求 LRC 歌词，kv/tv 关闭翻译词
    req = urllib.request.Request(url, headers={"Referer": "https://music.163.com/", "User-Agent": "Mozilla/5.0"})  # 模拟浏览器请求
    with urllib.request.urlopen(req, timeout=20) as resp:  # 发起请求
        data = json.loads(resp.read().decode("utf-8", "replace"))  # 解析 JSON
    return (data.get("lrc") or {}).get("lyric") or ""  # 提取 lrc.lyric 字段，缺失返回空字符串


def lrc_to_srt(lrc):
    """把 LRC 歌词文本转为 SRT 字幕文本（供 ffmpeg 烧录字幕）。"""
    def ts(sec):
        """内部函数：把秒数格式化为 SRT 时间戳 HH:MM:SS,mmm。"""
        h = int(sec // 3600); m = int(sec % 3600 // 60); s = int(sec % 60)  # 分解时/分/秒
        ms = int(round((sec - int(sec)) * 1000))  # 计算毫秒部分（四舍五入）
        if ms >= 1000:  # 毫秒进位处理
            s += 1; ms = 0  # 秒进一、毫秒归零
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"  # 返回补零后的时间戳字符串

    lines = []  # 歌词条目列表 [(秒, 歌词文本), ...]
    pat = re.compile(r"\[(\d+):(\d+)(?:\.(\d+))?\]\s*(.*)")  # 正则解析 LRC 时间标签 [mm:ss(.xx)] 歌词
    for raw in lrc.splitlines():  # 逐行处理歌词
        m = pat.match(raw)  # 匹配时间标签
        if not m:  # 不匹配（如元信息行）则跳过
            continue  # 处理下一行
        mm, ss = int(m.group(1)), int(m.group(2))  # 提取分钟与秒
        frac = (m.group(3) or "0").ljust(3, "0")[:3]  # 提取小数秒并补齐/截断为 3 位毫秒表示
        sec = mm * 60 + ss + int(frac) / 1000  # 折算为总秒数（含毫秒）
        text = m.group(4).strip()  # 该行歌词文本（去首尾空白）
        if text:  # 非空歌词才保留
            lines.append((sec, text))  # 加入列表
    lines.sort()  # 按时间排序（保证显示顺序）
    srt = []  # SRT 块列表
    for i, (t, txt) in enumerate(lines):  # 遍历排序后的歌词
        end = lines[i + 1][0] if i + 1 < len(lines) else t + 4  # 结束时间=下一条歌词时间；最后一条+4 秒
        srt.append(f"{i+1}\n{ts(t)} --> {ts(end)}\n{txt}\n")  # 追加 SRT 块：序号/起止时间/文本
    return "\n".join(srt)  # 用空行分隔合并为完整 SRT 文本


# ---------------------------------------------------------------- ffmpeg / 合成
# ffmpeg 下载、音频时长、背景搜索/生成、MP4 合成相关函数

FFMPEG_DL_URLS = [  # 全局列表：ffmpeg 官方下载候选直链（按顺序尝试）
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",  # gyan.dev 官方构建
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",  # GitHub 构建
]

BG_PALETTES = {  # 全局字典：氛围背景色板名 -> 4 个渐变色（十六进制字符串）
    "星河紫": ["0x0d0628", "0x2b1055", "0x4a1a7a", "0x6d28d9"],  # 深紫到亮紫
    "深海蓝": ["0x020617", "0x0b2e4f", "0x1b4965", "0x134e4a"],  # 深蓝到青绿
    "晚霞橙": ["0x2d0a2a", "0x7b2d4e", "0xd95d39", "0xf2a65a"],  # 深紫红到橙黄
    "樱花粉": ["0x2d1b3d", "0x8e5573", "0xc98f9b", "0xf2d5d7"],  # 深紫到浅粉
    "霓虹青": ["0x001514", "0x003844", "0x006a71", "0x00c2a8"],  # 墨绿到青
    "极夜黑金": ["0x0a0a0a", "0x1a1a2e", "0x3d2c6a", "0xc9a227"],  # 黑到金
}
BG_PALETTE_KEYS = list(BG_PALETTES.keys())  # 全局列表：色板名称列表（供下拉框使用）


def find_ffmpeg():
    """查找 ffmpeg 可执行文件：优先程序目录，其次 PATH。返回路径或 None。"""
    for p in [app_dir() / "ffmpeg.exe", app_dir() / "ffmpeg" / "bin" / "ffmpeg.exe"]:  # 检查程序目录下两种常见位置
        if p.exists():  # 文件存在
            return str(p)  # 返回该路径
    p = shutil.which("ffmpeg")  # 在系统 PATH 中查找
    if p:  # 找到
        return p  # 返回路径
    return None  # 都没找到返回 None


def download_ffmpeg(log_cb):
    """下载并解压 ffmpeg 到程序目录，返回 ffmpeg.exe 路径或 None。"""
    tmp = Path(tempfile.gettempdir()) / "ffmpeg_dl.zip"  # 临时 zip：系统临时目录/ffmpeg_dl.zip
    for url in FFMPEG_DL_URLS:  # 依次尝试候选下载地址
        try:  # 单个源失败继续下一个
            log_cb("下载 ffmpeg（%s）…" % url.split("/")[2])  # 日志提示当前源（取 URL 域名部分）
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})  # 构造请求
            with urllib.request.urlopen(req, timeout=900) as r, open(str(tmp), "wb") as f:  # 下载（15 分钟超时）
                shutil.copyfileobj(r, f)  # 流式写入临时 zip
            with zipfile.ZipFile(str(tmp)) as z:  # 打开 zip
                names = [n for n in z.namelist() if n.endswith("bin/ffmpeg.exe")]  # 找出包内 ffmpeg.exe 条目
                if not names:  # 未找到
                    raise RuntimeError("压缩包内未找到 ffmpeg.exe")  # 抛错
                name = names[0]  # 取第一个匹配
                dst = app_dir() / "ffmpeg.exe"  # 目标：程序目录/ffmpeg.exe
                with z.open(name) as src, open(str(dst), "wb") as d:  # 从 zip 读取并写出
                    shutil.copyfileobj(src, d)  # 拷贝内容
            try:  # 清理临时 zip
                tmp.unlink()  # 删除
            except Exception:  # 删除失败不影响主流程
                pass  # 忽略
            log_cb("ffmpeg 就绪: %s" % dst)  # 日志提示就绪
            return str(dst)  # 返回 ffmpeg 路径
        except Exception as e:  # 当前源失败
            log_cb("下载失败: %s" % e)  # 记录失败原因
    return None  # 全部失败返回 None


def audio_duration(path, ffmpeg=None):
    """获取音频时长（秒）。wav 用文件头读取，其他格式用 ffmpeg -i 解析。"""
    if Path(path).suffix.lower() == ".wav":  # wav 文件
        try:  # 尝试读取文件头
            with wave.open(str(path), "rb") as w:  # 打开 wav
                return w.getnframes() / float(w.getframerate())  # 帧数/采样率 = 时长秒
        except Exception:  # 读取失败
            pass  # 走 ffmpeg 兜底
    if not ffmpeg:  # 未指定 ffmpeg
        ffmpeg = find_ffmpeg()  # 自动查找
    if ffmpeg:  # 找到 ffmpeg
        p = subprocess.run([ffmpeg, "-i", str(path)], capture_output=True, text=True,  # 执行 ffmpeg -i 读取媒体信息
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # 不弹黑窗
        m = re.search(r"Duration: (\d+):(\d+):(\d+)\.(\d+)", p.stderr)  # 从 stderr 匹配 Duration 行
        if m:  # 匹配成功
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 100.0  # 折算总秒数
    return 0.0  # 无法获取返回 0


def bing_image_search(keyword, limit=8):
    """必应图片搜索：返回图片直链列表（解析页面内嵌 JSON）。"""
    url = "https://www.bing.com/images/search?q=" + urllib.parse.quote(keyword) + "&form=HDRSC2&first=1"  # 构造搜索地址（关键词 URL 编码）
    req = urllib.request.Request(url, headers={  # 构造请求
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",  # 完整浏览器 UA
        "Accept-Language": "zh-CN,zh;q=0.9",  # 中文优先
    })
    with urllib.request.urlopen(req, timeout=30) as resp:  # 请求页面
        html = resp.read().decode("utf-8", "replace")  # 读取 HTML 文本
    found = []  # 图片直链结果列表
    for m in re.findall(r'm="([^"]+)"', html):  # 正则提取页面内嵌的 JSON 数据块
        txt = unescape(m)  # 反转义 HTML 实体
        try:  # 尝试解析 JSON
            d = json.loads(txt)  # 解析
            u = d.get("murl")  # 取 murl（原图直链）字段
        except Exception:  # 解析失败
            u = None  # 视为无直链
        if u and u.startswith("http") and u not in found:  # 合法且未重复
            found.append(u)  # 加入结果
        if len(found) >= limit:  # 达到数量上限
            break  # 停止解析
    return found  # 返回图片直链列表


def download_image(url, dst, timeout=60):
    """下载网络图片到 dst（校验响应类型确为图片）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})  # 构造请求
    with urllib.request.urlopen(req, timeout=timeout) as r:  # 发起下载
        ctype = r.headers.get("Content-Type", "")  # 读取响应 Content-Type
        if not ctype.startswith("image/") and not ctype.startswith("application/octet-stream"):  # 非图片类型
            raise RuntimeError("非图片内容: %s" % ctype)  # 抛错拒绝写入
        data = r.read()  # 读取全部字节
    Path(dst).write_bytes(data)  # 写入目标文件
    return dst  # 返回目标路径


def gen_gradient_bg(ffmpeg, out_mp4, dur, palette, size="1920x1080", fps=25, seed_text=""):
    """用 lavfi gradients 生成流动渐变背景视频（本地氛围生成，稳定零依赖）。"""
    if not palette:  # 未指定色板
        palette = BG_PALETTES[BG_PALETTE_KEYS[abs(hash(seed_text or "bg")) % len(BG_PALETTE_KEYS)]]  # 按文本哈希选定一个色板（确定性）
    cols = ":".join(f"c{i}=0x{v}" for i, v in enumerate(palette[:4]))  # 生成 gradients 滤镜的 c0~c3 颜色参数
    src = f"gradients=s={size}:{cols}:nb_colors={len(palette[:4])}:d=8:speed=0.03:rate={fps}"  # lavfi 输入源描述（尺寸/颜色/数量/时长/速度/帧率）
    cmd = [ffmpeg, "-y", "-f", "lavfi", "-i", src, "-t", str(max(dur, 1.0)),  # 命令：用 lavfi 生成视频，时长取 dur 与 1 秒较大值
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", str(out_mp4)]  # H.264 编码、中等预设、CRF20、yuv420p 兼容格式
    p = subprocess.run(cmd, capture_output=True, text=True,  # 执行 ffmpeg
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # 不弹窗
    if p.returncode != 0 or not Path(out_mp4).exists():  # 失败或产物缺失
        raise RuntimeError("氛围背景生成失败\n" + p.stderr[-500:])  # 抛错并带 ffmpeg 错误尾部
    return out_mp4  # 返回生成视频路径


def ff_escape_path(p):
    """把路径转为 ffmpeg 字幕滤镜可用的转义形式（反斜杠/冒号转义）。"""
    return str(p).replace("\\", "/").replace(":", "\\:")  # 反斜杠转正斜杠、冒号前加反斜杠


def compose_mp4(ffmpeg, bg, audio, srt, out_mp4, size="1920x1080", fps=25,
                kenburns=True, log_cb=None, cancel=None):
    """背景(图片/GIF/视频) + 音乐 + 字幕 -> MP4"""
    def log(m):
        """内部函数：有回调时输出日志。"""
        if log_cb:
            log_cb(m)

    bg = Path(bg)  # 背景路径转 Path 对象
    ext = bg.suffix.lower()  # 背景文件扩展名（小写）
    is_img = ext in (".jpg", ".jpeg", ".png", ".bmp", ".webp")  # 是否为静态图片
    is_gif = ext == ".gif"  # 是否为 GIF
    w, h = size.split("x")  # 目标尺寸拆分宽/高

    sub_filter = ""  # 字幕滤镜片段（为空则不烧字幕）
    if srt and Path(srt).exists():  # 提供了字幕文件且存在
        srt_s = ff_escape_path(srt)  # 转义字幕路径
        sub_filter = (",subtitles='%s':fontsdir='C\\:/Windows/Fonts':"  # 拼接 subtitles 滤镜：指定字幕文件与字体目录
                      "force_style='FontName=Microsoft YaHei,FontSize=20,Bold=1,Outline=2,Shadow=1'"  # 字幕样式：微软雅黑/20号/加粗/描边/阴影
                      % srt_s)

    cmd = [ffmpeg, "-y"]  # 基础命令：-y 覆盖输出
    if is_img:  # 静态图片背景
        cmd += ["-loop", "1", "-i", str(bg)]  # 循环读图作为无限视频流
    else:  # GIF/视频背景
        cmd += ["-stream_loop", "-1", "-i", str(bg)]  # 无限循环背景媒体流
    cmd += ["-i", str(audio)]  # 追加音频输入流

    base_scale = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"  # 基础滤镜：等比放大填满后裁剪到目标尺寸
    if is_img and kenburns:  # 静态图且启用 Ken Burns 效果
        vf = base_scale + (f",zoompan=z='min(1+0.0012*on,1.2)':"  # 缩放表达式：随帧号缓慢放大至 1.2 倍
                           f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:fps={fps}:s={w}x{h}") + sub_filter + "[v]"  # 居中取景 + 帧率/尺寸 + 字幕滤镜，输出流 [v]
    else:  # 视频/GIF 背景或不启用 Ken Burns
        vf = base_scale + sub_filter + "[v]"  # 仅等比裁剪 + 字幕，输出流 [v]
    cmd += ["-filter_complex", vf, "-map", "[v]", "-map", "1:a",  # 滤镜图 + 映射视频流/音频流
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",  # 视频编码参数
            "-c:a", "aac", "-b:a", "192k", "-shortest", str(out_mp4)]  # AAC 192k 音频 + 以较短流为准截断

    log("合成 MP4：%s" % bg.name)  # 日志：开始合成
    log("命令: " + " ".join(cmd[:4]) + " ...")  # 日志：显示命令开头（避免整行过长）
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,  # 启动 ffmpeg 进程（合并输出流）
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # 不弹窗
    last = ""  # 上次进度行缓存（避免重复刷屏）
    for line in iter(proc.stdout.readline, b""):  # 逐行读取 ffmpeg 输出
        text = line.decode("utf-8", "replace").strip()  # 解码并去空白
        if text:  # 非空行
            m = re.search(r"time=(\d+):(\d+):(\d+)\.(\d+)", text)  # 匹配进度时间
            if m:  # 命中进度
                cur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))  # 折算当前秒数
                if int(cur) % 10 == 0 and text != last:  # 每 10 秒汇报一次且去重
                    last = text  # 更新缓存
                    log("进度 %s" % m.group(0))  # 输出进度时间戳
            elif "Error" in text or "error" in text:  # 错误信息
                log("ffmpeg: " + text[-160:])  # 输出错误尾部
            else:  # 其他输出
                log(text[-160:])  # 输出尾部（防止超长行）
            if cancel and cancel.is_set():  # 用户取消
                proc.terminate()  # 终止 ffmpeg 进程
                raise RuntimeError("已取消")  # 抛“已取消”异常
    proc.wait()  # 等待进程退出
    if proc.returncode != 0 or not Path(out_mp4).exists():  # 失败或产物缺失
        raise RuntimeError("MP4 合成失败")  # 抛错
    log("MP4 完成: %s" % os.path.basename(out_mp4))  # 日志：完成
    return out_mp4  # 返回 MP4 路径

# ---------------------------------------------------------------- 子进程
# 运行外部 python 脚本的统一封装

def run_python(python_path, args, log_cb=None, timeout=None):
    """运行外部 python 脚本（MSST/DDSP/audio_utils），实时回传日志，返回 (返回码, 合并输出)。"""
    cmd = [python_path] + args  # 命令 = 解释器 + 参数
    proc = subprocess.Popen(  # 启动子进程
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,  # stdout 管道，stderr 并入 stdout
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # Windows 不弹黑窗
    )
    buf = []  # 输出行缓存
    if log_cb:  # 需要实时日志
        for line in iter(proc.stdout.readline, b""):  # 逐行读取
            text = line.decode("utf-8", "replace").rstrip()  # 解码去行尾空白
            if text:  # 非空
                log_cb(text)  # 实时回调日志
            buf.append(text)  # 缓存该行
    else:  # 不需要实时日志
        out, _ = proc.communicate(timeout=timeout)  # 一次性等待并取回输出（可设超时）
        buf = out.decode("utf-8", "replace").splitlines()  # 解码并按行拆分
    proc.wait(timeout=timeout)  # 确保进程结束（等待退出）
    return proc.returncode, "\n".join(buf)  # 返回退出码与完整输出文本


# ---------------------------------------------------------------- 流程
# 核心翻唱流水线类 Pipeline：转码 -> 分离 -> 转音 -> 混音 -> 歌词 -> 合成

class Pipeline:
    """翻唱流水线：管理 5 个阶段（分离/转音/混音/歌词/合成），支持取消。"""

    def __init__(self, cfg, log_cb, stage_cb):
        self.cfg = cfg  # 实例变量：配置字典（工具链路径等）
        self.log = log_cb  # 实例变量：日志回调函数（输出到 UI 日志区）
        self.stage = stage_cb  # 实例变量：阶段进度回调函数（更新 UI 阶段文字）
        self.cancel = threading.Event()  # 实例变量：取消事件（线程安全标志位）

    def _log(self, msg):
        """内部快捷方法：输出一条日志。"""
        self.log(msg)  # 调用日志回调

    def to_wav(self, src, dst):
        """把任意音频转成 44100Hz wav（调用 audio_utils.py to_wav）。"""
        self._log("[转码] %s -> %s" % (os.path.basename(src), os.path.basename(dst)))  # 日志：转码来源与目标文件名
        rc, out = run_python(self.cfg["ddsp_python"], [  # 用 DDSP 环境 python 运行 audio_utils
            str(AUDIO_UTILS), "to_wav", str(src), str(dst), "--sr", "44100"  # 子命令 to_wav + 输入输出 + 采样率
        ], self._log)
        if rc != 0 or "TO_WAV_OK" not in out:  # 退出码非 0 或未见成功标记
            raise RuntimeError("音频转码失败\n" + out[-500:])  # 抛错并带输出尾部
        return dst  # 返回转码后路径

    def separate(self, wav, workdir):
        """阶段1：MSST 人声分离。输入 wav，返回 (人声 wav, 伴奏 wav)。"""
        self._log("===== 阶段1/5：人声分离（MSST）=====")  # 日志：阶段标题
        in_dir = workdir / "sep_in"  # 分离输入目录：工作目录/sep_in
        out_dir = workdir / "sep_out"  # 分离输出目录：工作目录/sep_out
        in_dir.mkdir(parents=True, exist_ok=True)  # 创建输入目录（递归）
        out_dir.mkdir(parents=True, exist_ok=True)  # 创建输出目录
        shutil.copy2(wav, in_dir / "input.wav")  # 复制输入音频为 input.wav（保留元数据）
        cfg = self.cfg  # 取配置字典（缩短引用）
        args = [  # MSST inference.py 参数列表
            os.path.join(cfg["msst_dir"], "inference.py"),  # 分离脚本路径
            "--model_type", "mel_band_roformer",  # 模型类型
            "--config_path", os.path.join(cfg["msst_dir"], "configs", cfg["msst_config"]),  # yaml 配置路径
            "--start_check_point", os.path.join(cfg["msst_dir"], "pretrain", cfg["msst_ckpt"]),  # 模型权重路径
            "--input_folder", str(in_dir),  # 输入文件夹
            "--store_dir", str(out_dir),  # 输出文件夹
            "--extract_instrumental",  # 同时提取伴奏（instrumental）
            "--disable_detailed_pbar",  # 关闭详细进度条（配合日志回传）
        ]
        if cfg.get("use_gpu_separate", True):  # 配置允许 GPU
            args += ["--device_ids", "0"]  # 使用 GPU 0
        else:  # 否则
            args += ["--force_cpu"]  # 强制 CPU
        self._log("调用 MSST 人声分离…")  # 日志：开始分离
        rc, out = run_python(cfg["msst_python"], args, self._log, timeout=None)  # 运行分离（无超时，长任务）
        if rc != 0:  # 失败
            raise RuntimeError("人声分离失败\n" + out[-800:])  # 抛错
        vocals = out_dir / "input_vocals.wav"  # 预期人声产物路径
        inst = out_dir / "input_instrumental.wav"  # 预期伴奏产物路径
        if not vocals.exists():  # 预期路径不存在
            for p in out_dir.rglob("*.wav"):  # 递归搜索 wav
                if "vocals" in p.name:  # 文件名含 vocals
                    vocals = p  # 更新人声路径
                if "instrumental" in p.name:  # 文件名含 instrumental
                    inst = p  # 更新伴奏路径
        if not vocals.exists() or not inst.exists():  # 人声或伴奏缺失
            raise RuntimeError("分离产物缺失: %s" % out_dir)  # 抛错并带输出目录
        return vocals, inst  # 返回 (人声, 伴奏)

    def convert(self, vocals, model_ckpt, out_wav, key=0):
        """阶段2：DDSP 转音。输入人声与角色模型，输出转音后 wav。"""
        self._log("===== 阶段2/5：AI 转音（DDSP）=====")  # 日志：阶段标题
        if self.cancel.is_set():  # 已取消
            raise RuntimeError("已取消")  # 中断
        args = [  # main_reflow.py 参数列表
            os.path.join(self.cfg["ddsp_dir"], "main_reflow.py"),  # DDSP 推理脚本
            "-m", model_ckpt,  # 模型文件路径（角色音色模型 .pt）
            "-i", str(vocals),  # 输入人声 wav
            "-o", str(out_wav),  # 输出转音 wav
            "-d", "cpu",  # 强制 CPU（DDSP 转音 CPU 更快稳定）
            "-k", str(key),  # 变调半音数（key）
            "-pe", "rmvpe",  # 音高提取器 rmvpe
        ]
        rc, out = run_python(self.cfg["ddsp_python"], args, self._log, timeout=None)  # 运行 DDSP 推理
        if rc != 0:  # 失败
            raise RuntimeError("AI 转音失败\n" + out[-800:])  # 抛错
        if not out_wav.exists():  # 产物缺失
            raise RuntimeError("转音产物缺失")  # 抛错
        return out_wav  # 返回转音 wav

    def mix(self, vocals, inst, out_wav, vg=1.0, ig=0.7):
        """阶段3：混音。转音人声 + 伴奏按音量比例合成（audio_utils.py mix）。"""
        self._log("===== 阶段3/5：混音 =====")  # 日志：阶段标题
        args = [  # audio_utils mix 参数
            str(AUDIO_UTILS), "mix", str(vocals), str(inst), str(out_wav),  # 子命令 + 人声/伴奏/输出
            "--vg", str(vg), "--ig", str(ig), "--peak", "0.95",  # 人声音量/伴奏音量/峰值归一化到 0.95
        ]
        rc, out = run_python(self.cfg["ddsp_python"], args, self._log, timeout=None)  # 运行混音
        if rc != 0 or "MIX_OK" not in out:  # 失败或未见成功标记
            raise RuntimeError("混音失败\n" + out[-500:])  # 抛错
        return out_wav  # 返回成品 wav

    def make_lyrics(self, song_id, hint, out_srt):
        """阶段4：生成歌词字幕（优先歌曲 ID 取歌词，失败则按文件名搜索）。"""
        self._log("===== 阶段4/5：生成歌词字幕 =====")  # 日志：阶段标题
        lrc = ""  # 歌词文本变量
        if song_id:  # 已知网易云歌曲 ID
            try:  # 尝试取歌词
                lrc = netease_lyric(song_id)  # 拉取 LRC 歌词
            except Exception as e:  # 失败
                self._log("取歌词失败: %s" % e)  # 记录失败
        if not lrc and hint:  # 无歌词但有歌曲名提示
            self._log("尝试按文件名搜索歌词: %s" % hint)  # 日志：按歌名搜索
            try:  # 尝试搜索
                res = netease_search(hint, limit=3)  # 按歌名搜索歌曲
                if res:  # 有结果
                    lrc = netease_lyric(res[0]["id"])  # 取第一条的歌词
                    self._log("命中: %s - %s" % (res[0]["name"], res[0]["artist"]))  # 日志：命中曲目
            except Exception as e:  # 搜索失败
                self._log("歌词搜索失败: %s" % e)  # 记录失败
        if not lrc:  # 最终无歌词
            self._log("未能获取歌词，跳过字幕生成（可手动放置同名 .srt）")  # 提示跳过
            return None  # 返回 None
        srt_text = lrc_to_srt(lrc)  # LRC 转 SRT
        Path(out_srt).write_text(srt_text, encoding="utf-8-sig")  # 写字幕文件（UTF-8 BOM，兼容播放器）
        self._log("字幕已生成: %s" % os.path.basename(out_srt))  # 日志：字幕完成
        return out_srt  # 返回字幕路径

    def compose_video(self, ffmpeg, bg, audio, srt, out_mp4, size, fps, kenburns):
        """阶段5：合成 MP4（调用全局 compose_mp4 并传入取消事件）。"""
        self._log("===== 阶段5/5：合成 MP4 =====")  # 日志：阶段标题
        compose_mp4(ffmpeg, bg, audio, srt, out_mp4, size=size, fps=fps,  # 调用合成函数
                    kenburns=kenburns, log_cb=self._log, cancel=self.cancel)  # 传入日志与取消事件
        return out_mp4  # 返回 MP4 路径

    def run(self, source_audio, model_ckpt, role, workdir, out_wav, key=0, vg=1.0, ig=0.7,
            lyric_song_id=None, lyric_hint=None, lyric_out=None,
            compose_cfg=None):
        """流水线总入口：按顺序执行全部阶段，返回成品 wav 路径。"""
        workdir = Path(workdir)  # 工作目录转 Path 对象
        workdir.mkdir(parents=True, exist_ok=True)  # 创建工作目录
        self.stage("准备音频")  # 阶段提示：准备音频
        wav = workdir / "source.wav"  # 工作目录中的源 wav 路径
        if Path(source_audio).suffix.lower() == ".wav":  # 源文件本身就是 wav
            wav = Path(source_audio)  # 直接使用源文件
        else:  # 其他格式
            self.to_wav(source_audio, wav)  # 先转码为 wav
        if self.cancel.is_set():  # 取消检查点
            raise RuntimeError("已取消")  # 中断
        vocals, inst = self.separate(wav, workdir)  # 阶段1：人声分离
        if self.cancel.is_set():  # 取消检查点
            raise RuntimeError("已取消")  # 中断
        self.stage("AI 转音")  # 阶段提示
        cv = workdir / "converted_vocals.wav"  # 转音人声路径
        self.convert(vocals, model_ckpt, cv, key)  # 阶段2：DDSP 转音
        if self.cancel.is_set():  # 取消检查点
            raise RuntimeError("已取消")  # 中断
        self.stage("混音输出")  # 阶段提示
        self.mix(cv, inst, out_wav, vg, ig)  # 阶段3：混音
        if lyric_out:  # 需要字幕
            self.stage("生成歌词字幕")  # 阶段提示
            self.make_lyrics(lyric_song_id, lyric_hint, lyric_out)  # 阶段4：歌词字幕
        if compose_cfg and compose_cfg.get("enabled"):  # 需要合成视频
            self.stage("合成 MP4")  # 阶段提示
            ffmpeg = compose_cfg["ffmpeg"]  # 取 ffmpeg 路径
            if not ffmpeg:  # 未找到
                raise RuntimeError("未找到 ffmpeg，请先在背景页安装/下载 ffmpeg")  # 抛错
            bg = compose_cfg["bg"]  # 背景路径
            srt = compose_cfg.get("srt") or lyric_out  # 字幕：优先显式指定，其次用刚生成的
            if srt and not Path(srt).exists():  # 字幕文件不存在
                srt = None  # 置空跳过烧录
            out_mp4 = Path(out_wav).with_suffix(".mp4")  # MP4 路径 = 成品 wav 同目录同名前缀
            self.compose_video(ffmpeg, bg, out_wav, srt, out_mp4,  # 阶段5：合成
                               compose_cfg.get("size", "1920x1080"),  # 默认 1080p
                               compose_cfg.get("fps", 25),  # 默认 25 帧
                               compose_cfg.get("kenburns", True))  # 默认启用 Ken Burns
        self.stage("完成")  # 阶段提示：完成
        return out_wav  # 返回成品 wav 路径


# ---------------------------------------------------------------- 便携版
# 一键打包便携版文件夹

def make_portable(cfg, dst, log_cb):
    """把工具链+模型+软件复制为便携版文件夹。"""
    dst = Path(dst)  # 目标文件夹路径
    tk = dst / "toolkit"  # 目标内 toolkit 目录
    tk.mkdir(parents=True, exist_ok=True)  # 创建 toolkit 目录

    def copy(src, dest, label):
        """内部函数：复制目录（跳过缓存/日志），已存在则先删除。"""
        src = Path(src)  # 源目录
        if not src.exists():  # 源不存在
            log_cb("跳过缺失目录: %s" % src)  # 日志提示跳过
            return  # 结束
        log_cb("复制 %s …（%s）" % (label, src))  # 日志：开始复制
        if dest.exists():  # 目标已存在
            shutil.rmtree(str(dest))  # 先删除旧目标
        shutil.copytree(str(src), str(dest),  # 复制目录树
                        ignore=shutil.ignore_patterns("__pycache__", ".git", "*.log", "events.out.*"))  # 忽略缓存/日志等
        log_cb("完成 %s" % label)  # 日志：完成

    copy(cfg.get("msst_dir"), tk / "MSST" / Path(cfg.get("msst_dir")).name, "MSST 工具链")  # 复制 MSST 到 toolkit/MSST/<原名>
    copy(cfg.get("ddsp_dir"), tk / "DDSP" / Path(cfg.get("ddsp_dir")).name, "DDSP 工具链")  # 复制 DDSP 到 toolkit/DDSP/<原名>
    copy(cfg.get("model_dir"), tk / "models", "角色模型")  # 复制模型到 toolkit/models
    md_src = cfg.get("model_dir")  # 模型源目录
    if md_src and Path(md_src).exists():  # 模型目录存在
        copy(md_src, dst / "models", "角色模型（内置到根目录，随包分发）")  # 再复制一份到根目录 models（随包内置）
    for name in ("AI翻唱工坊.exe", "audio_utils.py", "README.md", "config.json", "ffmpeg.exe"):  # 需要随包的文件清单
        p = app_dir() / name  # 程序目录下的文件
        if p.exists():  # 存在
            shutil.copy2(str(p), str(dst / name))  # 复制到便携版根目录
    log_cb("便携版完成：%s" % dst)  # 日志：完成


# ---------------------------------------------------------------- GUI
# 主窗口 App 类：界面构建、事件回调、后台线程管理

class App(tk.Tk):
    """主应用窗口（tkinter 根窗口）。"""

    def __init__(self):
        """初始化窗口、配置、控件、消息轮询与缺失组件自动获取。"""
        super().__init__()  # 初始化 tkinter 根窗口
        self.title(f"{APP_NAME} v{VERSION}")  # 窗口标题
        self.geometry("860x720")  # 窗口初始尺寸
        self.minsize(760, 620)  # 窗口最小尺寸
        self.q = queue.Queue()  # 实例变量：消息队列（后台线程 -> UI 主线程）
        self.worker = None  # 实例变量：翻唱后台线程对象
        self.portable_worker = None  # 实例变量：便携版打包后台线程对象
        self.bg_worker = None  # 实例变量：背景搜索/下载后台线程对象
        self.cfg = self._init_config()  # 实例变量：加载/自动探测配置
        self.bg_path = None  # 实例变量：当前选择的背景文件路径
        self.bg_search_results = []  # 实例变量：背景搜索结果列表（图片直链）
        self.bg_kind = "import"  # 实例变量：背景类型（import/url/bing/gradient）

        style = ttk.Style(self)  # 创建 ttk 样式对象
        if "vista" in style.theme_names():  # Windows 系统自带 vista 主题
            style.theme_use("vista")  # 切换为 vista 主题（更现代外观）

        self._build_ui()  # 构建全部界面控件
        self.after(100, self._poll)  # 每 100ms 轮询一次消息队列
        if self._missing_at_startup:  # 存在缺失组件
            self.after(400, self._auto_fetch_missing_at_startup)  # 延迟启动自动获取流程

    def _init_config(self):
        """加载配置、扫描模型、检测缺失组件，返回配置字典。"""
        cfg = auto_detect_config()  # 自动探测配置
        if not CONFIG_FILE.exists():  # 配置文件不存在
            save_config(cfg)  # 首次启动写入配置
        self._models = find_models(cfg["model_dir"])  # 实例变量：扫描得到的模型列表
        # v1.4.5：模型/组件缺失统一交给首次启动自动获取流程提示并联网安装，不再单独弹 warning
        missing = [k for k, v in component_status(cfg).items() if not v["ready"]]  # 找出未就绪组件 key
        if missing:  # 存在缺失
            names = {"msst": "MSST 工具链", "ddsp": "DDSP 工具链", "models": "角色模型", "ffmpeg": "ffmpeg"}  # 组件显示名映射
            self._missing_at_startup = [names[m] for m in missing]  # 实例变量：缺失组件显示名列表
        else:  # 全部就绪
            self._missing_at_startup = []  # 空列表
        return cfg  # 返回配置

    def _build_ui(self):
        """构建主界面：顶栏、三个标签页、参数行、日志区与按钮。"""
        top = ttk.Frame(self)  # 顶栏容器
        top.pack(fill=tk.X, padx=10, pady=8)  # 水平铺满
        ttk.Label(top, text="AI 翻唱工坊", font=("Microsoft YaHei UI", 16, "bold")).pack(side=tk.LEFT)  # 标题文字
        ttk.Label(top, text="翻唱 + 视频合成（免 PR）", foreground="#666").pack(side=tk.LEFT, padx=10)  # 副标题
        ttk.Button(top, text="组件管理", command=self._open_component_manager).pack(side=tk.RIGHT)  # 组件管理按钮
        ttk.Button(top, text="设置", command=self._open_settings).pack(side=tk.RIGHT)  # 设置按钮

        nb = ttk.Notebook(self)  # 标签页控件
        nb.pack(fill=tk.X, padx=10)  # 水平铺满
        self.tab_file = ttk.Frame(nb)  # 标签页1：导入原音频
        self.tab_search = ttk.Frame(nb)  # 标签页2：搜索歌名
        self.tab_bg = ttk.Frame(nb)  # 标签页3：背景与合成
        nb.add(self.tab_file, text=" ① 导入原音频 ")  # 注册标签1
        nb.add(self.tab_search, text=" ② 搜索歌名 ")  # 注册标签2
        nb.add(self.tab_bg, text=" ③ 背景与合成 ")  # 注册标签3

        row = ttk.Frame(self.tab_file)  # 标签1 内第一行容器
        row.pack(fill=tk.X, padx=8, pady=8)  # 水平铺满
        self.file_path = tk.StringVar()  # 实例变量：原音频路径（绑定输入框）
        ttk.Entry(row, textvariable=self.file_path, width=56).pack(side=tk.LEFT, fill=tk.X, expand=True)  # 路径输入框
        ttk.Button(row, text="选择 mp3/wav", command=self._pick_file).pack(side=tk.LEFT, padx=6)  # 文件选择按钮

        srow = ttk.Frame(self.tab_search)  # 标签2 内搜索行容器
        srow.pack(fill=tk.X, padx=8, pady=8)  # 水平铺满
        self.search_text = tk.StringVar()  # 实例变量：搜索关键词
        ttk.Entry(srow, textvariable=self.search_text, width=40).pack(side=tk.LEFT, fill=tk.X, expand=True)  # 搜索输入框
        ttk.Button(srow, text="搜索", command=self._do_search).pack(side=tk.LEFT, padx=6)  # 搜索按钮
        self.result_var = tk.StringVar(value="输入歌名搜索网易云音乐")  # 实例变量：搜索状态文字
        ttk.Label(self.tab_search, textvariable=self.result_var, foreground="#777").pack(anchor=tk.W, padx=8)  # 状态标签
        self.result_list = tk.Listbox(self.tab_search, height=7)  # 实例变量：搜索结果列表控件
        self.result_list.pack(fill=tk.X, padx=8, pady=4)  # 放置列表
        self._search_results = []  # 实例变量：搜索结果数据列表（与列表控件对应）

        self._build_bg_tab()  # 构建背景标签页

        rrow = ttk.Frame(self)  # 角色选择行
        rrow.pack(fill=tk.X, padx=10, pady=4)  # 水平铺满
        ttk.Label(rrow, text="翻唱角色:").pack(side=tk.LEFT)  # 标签文字
        self.role_var = tk.StringVar()  # 实例变量：当前角色名
        self.role_cb = ttk.Combobox(rrow, textvariable=self.role_var, state="readonly", width=28)  # 角色下拉框
        names = [m[0] for m in self._models] or ["（未发现模型）"]  # 下拉选项 = 模型显示名，无模型则占位
        self.role_cb["values"] = names  # 设置下拉选项
        if names:  # 有选项
            self.role_cb.current(0)  # 默认选第一个
        self.role_cb.pack(side=tk.LEFT, padx=6)  # 放置下拉框

        prow = ttk.Frame(self)  # 参数行容器
        prow.pack(fill=tk.X, padx=10, pady=4)  # 水平铺满
        ttk.Label(prow, text="变调 key:").pack(side=tk.LEFT)  # 变调标签
        self.key_var = tk.StringVar(value="0")  # 实例变量：变调半音数（默认 0）
        ttk.Spinbox(prow, from_=-12, to=12, textvariable=self.key_var, width=4).pack(side=tk.LEFT, padx=6)  # 变调输入框（-12~12）
        ttk.Label(prow, text="伴奏音量:").pack(side=tk.LEFT, padx=(12, 0))  # 伴奏音量标签
        self.ig_var = tk.StringVar(value="0.7")  # 实例变量：伴奏音量比例（默认 0.7）
        ttk.Spinbox(prow, from_=0.0, to=1.0, increment=0.1, textvariable=self.ig_var, width=4).pack(side=tk.LEFT, padx=6)  # 音量输入框
        ttk.Label(prow, text="输出目录:").pack(side=tk.LEFT, padx=(12, 0))  # 输出目录标签
        self.out_var = tk.StringVar(value=self.cfg.get("output_dir", ""))  # 实例变量：输出目录路径
        ttk.Entry(prow, textvariable=self.out_var, width=24).pack(side=tk.LEFT, padx=6)  # 输出目录输入框
        ttk.Button(prow, text="浏览", command=self._pick_outdir).pack(side=tk.LEFT)  # 浏览按钮

        opt = ttk.Frame(self)  # 选项行容器
        opt.pack(fill=tk.X, padx=10, pady=2)  # 水平铺满
        self.lyric_var = tk.BooleanVar(value=True)  # 实例变量：是否输出歌词字幕（默认开）
        ttk.Checkbutton(opt, text="输出歌词字幕（.srt）", variable=self.lyric_var).pack(side=tk.LEFT)  # 歌词勾选框
        self.auto_video_var = tk.BooleanVar(value=False)  # 实例变量：是否自动合成 MP4（默认关）
        ttk.Checkbutton(opt, text="翻唱完成后自动合成 MP4（需先在背景页配好背景）",  # 自动合成勾选框
                        variable=self.auto_video_var).pack(side=tk.LEFT, padx=20)

        self.progress = ttk.Progressbar(self, mode="indeterminate")  # 实例变量：不定进度条
        self.progress.pack(fill=tk.X, padx=10, pady=6)  # 放置进度条
        self.stage_var = tk.StringVar(value="就绪")  # 实例变量：阶段状态文字
        ttk.Label(self, textvariable=self.stage_var).pack(anchor=tk.W, padx=10)  # 阶段状态标签

        self.log_text = tk.Text(self, height=10, state="disabled", font=("Consolas", 9))  # 实例变量：日志文本框（只读）
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 6))  # 日志区铺满剩余空间

        btm = ttk.Frame(self)  # 底部按钮行
        btm.pack(fill=tk.X, padx=10, pady=(0, 8))  # 水平铺满
        self.start_btn = ttk.Button(btm, text="开始翻唱", command=self._start)  # 实例变量：开始按钮
        self.start_btn.pack(side=tk.LEFT)  # 放置
        self.cancel_btn = ttk.Button(btm, text="取消", command=self._cancel, state="disabled")  # 实例变量：取消按钮（初始禁用）

        self.cancel_btn.pack(side=tk.LEFT, padx=6)  # 放置取消按钮
        ttk.Button(btm, text="组件管理", command=self._open_component_manager).pack(side=tk.LEFT, padx=6)  # 底部组件管理按钮
        ttk.Button(btm, text="打开输出目录", command=self._open_outdir).pack(side=tk.RIGHT)  # 底部打开输出目录按钮

    # ---------- 背景页 ----------
    def _build_bg_tab(self):
        """构建「③ 背景与合成」标签页：来源单选、导入/搜索/生成三个面板、当前背景参数、手动合成区。"""
        f = self.tab_bg  # 背景标签页容器
        self.bg_src_var = tk.StringVar(value="import")  # 实例变量：背景来源类型（import/search/gen）
        src = ttk.Frame(f)  # 来源选择行
        src.pack(fill=tk.X, padx=8, pady=6)  # 水平铺满
        ttk.Label(src, text="背景来源:").pack(side=tk.LEFT)  # 标签文字
        for key, label in [("import", "导入文件"), ("search", "网上搜索"), ("gen", "生成背景")]:  # 三种来源选项
            ttk.Radiobutton(src, text=label, value=key, variable=self.bg_src_var,  # 单选按钮绑定 bg_src_var
                            command=self._bg_src_changed).pack(side=tk.LEFT, padx=6)  # 切换时触发重排面板

        self.bg_import_frame = ttk.Frame(f)  # 实例变量：导入面板容器
        self.bg_import_frame.pack(fill=tk.X, padx=8, pady=2)  # 默认显示导入面板
        self.bg_file = tk.StringVar()  # 实例变量：导入背景文件路径
        ttk.Entry(self.bg_import_frame, textvariable=self.bg_file, width=60).pack(side=tk.LEFT, fill=tk.X, expand=True)  # 文件路径输入框
        ttk.Button(self.bg_import_frame, text="选择图片/GIF/视频", command=self._pick_bg).pack(side=tk.LEFT, padx=6)  # 选择按钮

        self.bg_search_frame = ttk.Frame(f)  # 实例变量：网上搜索面板容器
        self.bg_search_frame.pack(fill=tk.X, padx=8, pady=2)  # 放置
        ttk.Label(self.bg_search_frame, text="关键词:").pack(side=tk.LEFT)  # 标签文字
        self.bg_kw = tk.StringVar(value="星空 动漫 壁纸")  # 实例变量：搜索关键词（默认值）
        ttk.Entry(self.bg_search_frame, textvariable=self.bg_kw, width=24).pack(side=tk.LEFT, padx=4)  # 关键词输入框
        ttk.Button(self.bg_search_frame, text="搜索背景", command=self._bg_search).pack(side=tk.LEFT, padx=4)  # 搜索按钮
        self.bg_search_var = tk.StringVar(value="搜索后选中一条即可作为背景")  # 实例变量：搜索结果提示文字
        ttk.Label(f, textvariable=self.bg_search_var, foreground="#777").pack(anchor=tk.W, padx=12)  # 提示标签
        self.bg_list = tk.Listbox(f, height=4)  # 实例变量：搜索结果列表控件
        self.bg_list.pack(fill=tk.X, padx=8, pady=2)  # 放置列表
        self.bg_list.bind("<Double-Button-1>", lambda e: self._bg_pick_result())  # 双击列表选中下载

        self.bg_gen_frame = ttk.Frame(f)  # 实例变量：生成背景面板容器
        self.bg_gen_frame.pack(fill=tk.X, padx=8, pady=2)  # 放置
        ttk.Label(self.bg_gen_frame, text="氛围色板:").pack(side=tk.LEFT)  # 标签文字
        self.bg_palette_var = tk.StringVar(value=BG_PALETTE_KEYS[0])  # 实例变量：选中色板名（默认第一个）
        ttk.Combobox(self.bg_gen_frame, textvariable=self.bg_palette_var, state="readonly",  # 色板下拉框
                     values=BG_PALETTE_KEYS, width=10).pack(side=tk.LEFT, padx=4)  # 选项为全部色板名
        ttk.Button(self.bg_gen_frame, text="生成氛围背景", command=self._bg_generate).pack(side=tk.LEFT, padx=4)  # 生成按钮

        sep = ttk.Separator(f, orient=tk.HORIZONTAL)  # 分隔线
        sep.pack(fill=tk.X, padx=8, pady=6)  # 放置

        cfg = ttk.Frame(f)  # 当前背景参数行
        cfg.pack(fill=tk.X, padx=8)  # 水平铺满
        ttk.Label(cfg, text="当前背景:").pack(side=tk.LEFT)  # 标签文字
        self.bg_status_var = tk.StringVar(value="未选择（将使用黑色底）")  # 实例变量：当前背景状态文字
        ttk.Label(cfg, textvariable=self.bg_status_var, foreground="#2b6").pack(side=tk.LEFT, padx=4)  # 状态标签（绿色）
        ttk.Button(cfg, text="清除", command=self._bg_clear).pack(side=tk.LEFT, padx=6)  # 清除背景按钮
        ttk.Label(cfg, text="分辨率:").pack(side=tk.LEFT, padx=(20, 0))  # 标签文字
        self.bg_size_var = tk.StringVar(value="1920x1080")  # 实例变量：合成分辨率（默认 1080p）
        ttk.Combobox(cfg, textvariable=self.bg_size_var, state="readonly",  # 分辨率下拉框
                     values=["1920x1080", "1280x720", "1080x1920"], width=10).pack(side=tk.LEFT)  # 常用分辨率选项
        self.bg_ken_var = tk.BooleanVar(value=True)  # 实例变量：是否启用 Ken Burns 动态效果
        ttk.Checkbutton(cfg, text="背景轻微动态", variable=self.bg_ken_var).pack(side=tk.LEFT, padx=10)  # 动态勾选框

        mux = ttk.Frame(f)  # 手动合成参数行（音频/字幕）
        mux.pack(fill=tk.X, padx=8, pady=4)  # 水平铺满
        ttk.Label(mux, text="音频(成品):").pack(side=tk.LEFT)  # 标签文字
        self.mux_audio = tk.StringVar(value="")  # 实例变量：手动合成音频路径
        ttk.Entry(mux, textvariable=self.mux_audio, width=38).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)  # 音频输入框
        ttk.Button(mux, text="浏览", command=self._pick_mux_audio).pack(side=tk.LEFT)  # 浏览音频按钮
        ttk.Label(mux, text="字幕:").pack(side=tk.LEFT, padx=(12, 0))  # 标签文字
        self.mux_srt = tk.StringVar(value="")  # 实例变量：手动合成字幕路径
        ttk.Entry(mux, textvariable=self.mux_srt, width=20).pack(side=tk.LEFT, padx=4)  # 字幕输入框
        ttk.Button(mux, text="浏览", command=self._pick_mux_srt).pack(side=tk.LEFT)  # 浏览字幕按钮

        ff = ttk.Frame(f)  # ffmpeg 状态行
        ff.pack(fill=tk.X, padx=8, pady=4)  # 水平铺满
        self.ffmpeg_var = tk.StringVar(value=self._ffmpeg_status())  # 实例变量：ffmpeg 状态文字
        ttk.Label(ff, textvariable=self.ffmpeg_var, foreground="#b50").pack(side=tk.LEFT)  # 状态标签（橙色）
        ttk.Button(ff, text="下载 ffmpeg", command=self._ffmpeg_download).pack(side=tk.LEFT, padx=6)  # 下载按钮
        ttk.Button(ff, text="合成 MP4", command=self._compose_video).pack(side=tk.RIGHT)  # 手动合成按钮

    def _ffmpeg_status(self):
        """返回 ffmpeg 可用状态文字。"""
        p = find_ffmpeg()  # 查找 ffmpeg
        if p:  # 找到
            return "ffmpeg: 可用"  # 可用提示
        return "ffmpeg: 未找到（合成 MP4 需要，点右侧下载）"  # 缺失提示

    def _bg_src_changed(self):
        """背景来源单选切换：隐藏旧面板、显示对应面板并重排。"""
        v = self.bg_src_var.get()  # 当前来源类型
        self.bg_import_frame.pack_forget()  # 隐藏导入面板
        self.bg_search_frame.pack_forget()  # 隐藏搜索面板
        self.bg_gen_frame.pack_forget()  # 隐藏生成面板
        if v == "import":  # 导入模式
            self.bg_import_frame.pack(fill=tk.X, padx=8, pady=2, before=self.bg_search_frame)  # 重新显示导入面板（在搜索面板前）
        elif v == "search":  # 搜索模式
            self.bg_search_frame.pack(fill=tk.X, padx=8, pady=2, before=self.bg_gen_frame)  # 重新显示搜索面板
        else:  # 生成模式
            self.bg_gen_frame.pack(fill=tk.X, padx=8, pady=2, before=sep)  # 重新显示生成面板
        self._refresh_bg_frames()  # 刷新布局

    def _refresh_bg_frames(self):
        # 简化重排：按变量重新 pack 各 frame（pack_forget 后按序重放）
        pass  # 占位空实现（保留接口）

    def _pick_bg(self):
        """文件对话框选择本地背景文件。"""
        p = filedialog.askopenfilename(  # 弹出文件选择框
            title="选择背景", filetypes=[  # 标题与类型过滤
                ("图片/视频", "*.jpg *.jpeg *.png *.bmp *.webp *.gif *.mp4 *.mov *.webm"),  # 支持的图片/视频格式
                ("所有文件", "*.*")])  # 兜底全部文件
        if p:  # 用户选择了文件
            self.bg_file.set(p)  # 同步到输入框
            self.bg_path = p  # 记录为当前背景
            self.bg_status_var.set(Path(p).name)  # 状态显示文件名

    def _bg_search(self):
        """按关键词启动背景搜索（后台线程）。"""
        kw = self.bg_kw.get().strip() or "壁纸"  # 取关键词，空则用默认“壁纸”
        self.bg_search_var.set("搜索中…")  # 状态提示搜索中
        self.bg_list.delete(0, tk.END)  # 清空旧列表
        self._bg_search_kw = kw  # 实例变量：记录本次关键词（备用）
        t = threading.Thread(target=self._bg_search_job, args=(kw,), daemon=True)  # 后台线程执行搜索
        t.start()  # 启动线程

    def _bg_search_job(self, kw):
        """后台线程：调用必应搜索图片直链，结果经消息队列回传 UI。"""
        try:  # 尝试搜索
            urls = bing_image_search(kw, limit=10)  # 搜图取前 10 张
        except Exception as e:  # 失败
            self.q.put(("bg_search_result", [], "搜索失败: %s" % e))  # 队列回传失败提示
            return  # 结束
        self.q.put(("bg_search_result", urls, "找到 %d 张，双击选择下载" % len(urls)))  # 队列回传结果与提示

    def _bg_pick_result(self):
        """双击搜索结果列表：取选中项并启动下载线程。"""
        sel = self.bg_list.curselection()  # 获取选中索引
        if not sel:  # 未选中
            return  # 结束
        idx = sel[0]  # 取第一个选中
        if idx >= len(self.bg_search_results):  # 索引越界保护
            return  # 结束
        url = self.bg_search_results[idx]  # 取对应图片直链
        self.bg_search_var.set("下载中… %s" % url[:60])  # 状态提示下载中
        t = threading.Thread(target=self._bg_download_job, args=(url,), daemon=True)  # 后台线程下载
        t.start()  # 启动线程

    def _bg_download_job(self, url):
        """后台线程：下载选中图片到系统临时目录 cover_bg，结果回传队列。"""
        try:  # 尝试下载
            work = Path(tempfile.gettempdir()) / "cover_bg"  # 系统临时目录/cover_bg
            work.mkdir(exist_ok=True)  # 创建目录（已存在则忽略）
            dst = work / ("search_%d.jpg" % int(time.time()))  # 目标文件：时间戳命名防冲突
            download_image(url, dst)  # 下载图片
            self.q.put(("bg_downloaded", str(dst), Path(dst).name))  # 队列回传下载完成
        except Exception as e:  # 失败
            self.q.put(("bg_search_result", [], "下载失败: %s" % e))  # 队列回传失败提示

    def _bg_generate(self):
        """启动氛围背景生成（需 ffmpeg，后台线程）。"""
        pal = self.bg_palette_var.get()  # 选中色板名
        ffmpeg = find_ffmpeg()  # 查找 ffmpeg
        if not ffmpeg:  # 未找到
            self._append_log("未找到 ffmpeg，请先下载。")  # 日志提示
            messagebox.showwarning(APP_NAME, "未找到 ffmpeg，请先点击「下载 ffmpeg」。")  # 弹窗警告
            return  # 结束
        t = threading.Thread(target=self._bg_generate_job, args=(ffmpeg, pal), daemon=True)  # 后台线程生成
        t.start()  # 启动线程

    def _bg_generate_job(self, ffmpeg, pal):
        """后台线程：用 lavfi gradients 生成 240 秒渐变背景视频。"""
        try:  # 尝试生成
            work = Path(tempfile.gettempdir()) / "cover_bg"  # 输出目录
            work.mkdir(exist_ok=True)  # 创建目录
            out = work / ("bg_gen_%d.mp4" % int(time.time()))  # 输出文件名：时间戳命名
            dur = 240  # 时长 240 秒
            self.q.put(("log", "生成氛围背景（%s，240 秒）…" % pal))  # 日志提示
            gen_gradient_bg(ffmpeg, out, dur, BG_PALETTES[pal])  # 调用渐变生成函数
            self.q.put(("bg_downloaded", str(out), out.name))  # 队列回传生成完成
        except Exception as e:  # 失败
            self.q.put(("bg_search_result", [], "生成失败: %s" % e))  # 队列回传失败提示

    def _bg_clear(self):
        """清除当前背景，恢复黑色底。"""
        self.bg_path = None  # 清空背景路径
        self.bg_status_var.set("未选择（将使用黑色底）")  # 状态提示恢复默认

    def _pick_mux_audio(self):
        """手动合成：选择音频文件。"""
        p = filedialog.askopenfilename(title="选择音频", filetypes=[("音频", "*.wav *.mp3 *.flac"), ("所有文件", "*.*")])  # 文件对话框
        if p:  # 已选择
            self.mux_audio.set(p)  # 同步到输入框

    def _pick_mux_srt(self):
        """手动合成：选择字幕文件。"""
        p = filedialog.askopenfilename(title="选择字幕", filetypes=[("字幕", "*.srt *.ass"), ("所有文件", "*.*")])  # 文件对话框
        if p:  # 已选择
            self.mux_srt.set(p)  # 同步到输入框

    def _ffmpeg_download(self):
        """启动 ffmpeg 下载（后台线程，防止重复下载）。"""
        if self.bg_worker and self.bg_worker.is_alive():  # 下载线程已在运行
            messagebox.showinfo(APP_NAME, "正在下载中…")  # 提示
            return  # 结束
        self.bg_worker = threading.Thread(target=self._ffmpeg_dl_job, daemon=True)  # 实例变量：下载线程
        self.bg_worker.start()  # 启动线程

    def _ffmpeg_dl_job(self):
        """后台线程：下载并解压 ffmpeg，结果回传队列。"""
        self.q.put(("log", "开始下载 ffmpeg（约 90MB）…"))  # 日志提示
        p = download_ffmpeg(lambda m: self.q.put(("log", m)))  # 调用下载函数（日志经队列回传）
        if p:  # 成功
            self.q.put(("ffmpeg_ok", p))  # 队列回传成功与路径
        else:  # 失败
            self.q.put(("bg_search_result", [], "ffmpeg 下载失败，可手动放置 ffmpeg.exe 到软件目录"))  # 队列回传失败提示

    def _compose_video(self):
        """手动合成 MP4 入口：校验 ffmpeg/音频，自动补字幕/背景，启动合成线程。"""
        if self.worker and self.worker.is_alive():  # 已有任务运行
            messagebox.showinfo(APP_NAME, "正在处理中…")  # 提示
            return  # 结束
        ffmpeg = find_ffmpeg()  # 查找 ffmpeg
        if not ffmpeg:  # 未找到
            messagebox.showwarning(APP_NAME, "未找到 ffmpeg，请先点击「下载 ffmpeg」。")  # 弹窗警告
            return  # 结束
        audio = self.mux_audio.get().strip()  # 音频输入框内容
        if not audio or not Path(audio).exists():  # 未指定或文件不存在
            # 自动找输出目录里最近一次成品
            outdir = self.out_var.get() or self.cfg.get("output_dir")  # 输出目录
            cands = sorted(Path(outdir).glob("*_翻唱_带伴奏.wav"), key=lambda p: p.stat().st_mtime, reverse=True) if Path(outdir).exists() else []  # 按修改时间倒序找成品 wav
            if cands:  # 找到候选
                audio = str(cands[0])  # 取最近一次成品
                self.mux_audio.set(audio)  # 同步输入框
                self._append_log("自动选用最近成品: %s" % Path(audio).name)  # 日志提示
            else:  # 无候选
                messagebox.showwarning(APP_NAME, "请先选择要合成的音频（成品 wav）。")  # 弹窗警告
                return  # 结束
        srt = self.mux_srt.get().strip() or None  # 字幕输入框内容
        if not srt and self.lyric_var.get():  # 未指定字幕但开启了歌词
            # 尝试同名 srt
            cand = Path(audio).with_suffix("").name  # 音频去扩展名文件名
            outdir = Path(audio).parent  # 音频所在目录
            for p in outdir.glob(cand + "_歌词.srt"):  # 匹配 原名_歌词.srt
                srt = str(p)  # 命中
                break  # 只取第一个
            if not srt:  # 未命中
                for p in outdir.glob(Path(audio).stem + "*歌词.srt"):  # 模糊匹配含“歌词”的 srt
                    srt = str(p)  # 命中
                    break  # 只取第一个
            if srt:  # 最终命中
                self.mux_srt.set(srt)  # 同步输入框
        bg = self.bg_path  # 当前背景路径
        if not bg:  # 无背景
            messagebox.showinfo(APP_NAME, "未选择背景，将使用纯黑底。\n也可以在背景页选择/生成背景。")  # 提示将用黑底
        out_mp4 = Path(audio).with_suffix(".mp4")  # 输出 MP4：与音频同目录同名前缀
        self._set_running(True)  # 锁定界面（禁用开始/启用取消）
        self._clear_log()  # 清空日志
        self.progress.start(12)  # 启动不定进度条
        self.worker = threading.Thread(  # 实例变量：合成线程
            target=self._compose_job, args=(ffmpeg, bg, audio, srt, out_mp4), daemon=True)  # 线程目标与参数
        self.worker.start()  # 启动线程

    def _compose_job(self, ffmpeg, bg, audio, srt, out_mp4):
        """后台线程：执行 MP4 合成（无背景时用 lavfi 纯黑底，有背景走 compose_mp4）。"""
        try:  # 尝试合成
            if bg is None:  # 纯黑底分支
                # 纯黑底：lavfi color
                w, h = self.bg_size_var.get().split("x")  # 分辨率拆分宽高
                sub = ""  # 字幕滤镜片段（为空则不烧字幕）
                if srt and Path(srt).exists():  # 有字幕文件
                    sub = ",subtitles='%s':fontsdir='C\\:/Windows/Fonts':force_style='FontName=Microsoft YaHei,FontSize=20,Bold=1,Outline=2,Shadow=1'" % ff_escape_path(srt)  # 拼接字幕滤镜（微软雅黑样式）
                cmd = [ffmpeg, "-y", "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r=25",  # 命令：lavfi 生成纯黑视频流
                       "-i", audio, "-filter_complex", f"[0:v]{sub}[v]",  # 音频输入 + 视频流加字幕滤镜
                       "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "medium",  # 映射流与视频编码参数
                       "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out_mp4)]  # 音频编码 + 以短流截断
                p = subprocess.run(cmd, capture_output=True, text=True,  # 执行 ffmpeg
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))  # 不弹窗
                if p.returncode != 0 or not out_mp4.exists():  # 失败或产物缺失
                    raise RuntimeError("MP4 合成失败\n" + p.stderr[-400:])  # 抛错带 stderr 尾部
            else:  # 有背景分支
                compose_mp4(ffmpeg, bg, audio, srt, out_mp4,  # 调用通用合成函数
                            size=self.bg_size_var.get(), kenburns=self.bg_ken_var.get(),  # 分辨率与动态效果
                            log_cb=lambda m: self.q.put(("log", m)))  # 日志经队列回传
            self.q.put(("video_done", str(out_mp4)))  # 队列回传合成完成
        except Exception as e:  # 任何异常
            self.q.put(("error", str(e)))  # 队列回传错误

    # ---------- 通用 ----------
    def _pick_file(self):
        """选择原音频文件（标签1 导入）。"""
        p = filedialog.askopenfilename(title="选择原音频", filetypes=[("音频", "*.mp3 *.wav *.flac *.m4a"), ("所有文件", "*.*")])  # 文件对话框
        if p:  # 已选择
            self.file_path.set(p)  # 同步到输入框

    def _pick_outdir(self):
        """选择输出目录（文件夹对话框）。"""
        p = filedialog.askdirectory(title="选择输出目录")  # 文件夹对话框
        if p:  # 已选择
            self.out_var.set(p)  # 同步到输入框

    def _open_outdir(self):
        """在资源管理器中打开输出目录。"""
        d = self.out_var.get() or self.cfg.get("output_dir")  # 输出目录路径
        if d and os.path.isdir(d):  # 目录存在
            os.startfile(d)  # noqa：用系统默认方式打开文件夹
        else:  # 目录不存在
            messagebox.showinfo(APP_NAME, "输出目录尚不存在，运行一次后即可打开。")  # 提示

    def _do_search(self):
        """标签2 搜索歌名：调用网易云搜索并填充结果列表。"""
        kw = self.search_text.get().strip()  # 关键词
        if not kw:  # 空关键词
            return  # 结束
        self.result_var.set("搜索中…")  # 状态提示
        self.update_idletasks()  # 强制刷新界面（立即显示“搜索中”）
        try:  # 尝试搜索
            res = netease_search(kw)  # 网易云搜索
        except Exception as e:  # 失败
            self.result_var.set("搜索失败: %s" % e)  # 状态显示失败
            return  # 结束
        self._search_results = res  # 实例变量：保存结果数据
        self.result_list.delete(0, tk.END)  # 清空旧列表
        for r in res:  # 遍历结果
            self.result_list.insert(tk.END, "%s - %s（%d秒）" % (r["name"], r["artist"], r["duration"]))  # 列表显示 歌名-歌手（时长）
        self.result_var.set("共 %d 条结果，双击或选中后点开始" % len(res))  # 状态提示

    def _selected_search(self):
        """返回列表中当前选中的搜索结果条目（字典），未选中返回 None。"""
        sel = self.result_list.curselection()  # 选中索引
        if not sel:  # 未选中
            return None  # 返回 None
        return self._search_results[sel[0]]  # 返回对应条目

    def _open_component_manager(self):
        """打开组件管理弹窗：展示组件状态，支持本地安装与自动获取。"""
        w = tk.Toplevel(self)  # 新建顶层窗口（依附主窗口）
        w.title("组件管理 - 按需安装")  # 窗口标题
        w.geometry("720x480")  # 窗口尺寸
        w.transient(self)  # 置顶于主窗口
        body = ttk.Frame(w)  # 主体容器
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)  # 铺满

        ttk.Label(body, text="按需安装：软件本体很小，组件缺失时可自动联网获取或从本地安装。",  # 说明文字
                  foreground="#555").pack(anchor=tk.W, pady=(0, 6))  # 放置

        cols = ttk.Frame(body)  # 组件列表容器
        cols.pack(fill=tk.X)  # 水平铺满
        for t in ("组件", "状态", "大小参考"):  # 表头占位（实际用行内标签布局）
            pass  # 空操作（保留结构）
        self.comp_rows = {}  # 实例变量：组件行控件字典 {key: {"status": 状态标签, "desc": 说明标签}}

        def refresh():
            """内部函数：刷新所有组件行状态与说明。"""
            st = component_status(self.cfg)  # 获取组件状态
            for key, row in self.comp_rows.items():  # 遍历已建行
                info = st[key]  # 该组件状态
                row["status"].config(text="已就绪" if info["ready"] else "缺失",  # 状态文字
                                     foreground="#2b6" if info["ready"] else "#d33")  # 绿色就绪/红色缺失
                row["desc"].config(text=info["desc"])  # 说明文字

        def local_install(key):
            """内部函数：弹出选择框并从本地目录/文件安装组件。"""
            info = {  # 各组件安装提示与类型
                "msst": ("选择 MSST 工具链目录（含 inference.py 与 env\\python.exe）", "dir"),  # MSST：选目录
                "ddsp": ("选择 DDSP 工具链目录（含 main_reflow.py 与 env\\python.exe）", "dir"),  # DDSP：选目录
                "models": ("选择角色模型目录（含“角色名 步数N”子目录）", "dir"),  # 模型：选目录
                "ffmpeg": ("选择 ffmpeg.exe", "file"),  # ffmpeg：选文件
            }[key]  # 按 key 取说明与类型
            if info[1] == "dir":  # 目录类型
                p = filedialog.askdirectory(title=info[0])  # 目录选择框
            else:  # 文件类型
                p = filedialog.askopenfilename(title=info[0], filetypes=[("exe", "*.exe")])  # 文件选择框
            if not p:  # 用户取消
                return  # 结束
            self._append_log("组件管理器：本地安装 %s <- %s" % (key, p))  # 日志
            t = threading.Thread(target=self._comp_install_job, args=(key, p), daemon=True)  # 后台线程安装
            t.start()  # 启动线程
            messagebox.showinfo(APP_NAME, "已开始安装，请留意主界面日志。")  # 提示

        def auto_fetch(key):
            """内部函数：启动该组件自动获取（联网查找官方源）。"""
            self._append_log("组件管理器：自动获取 %s（软件自主联网查找资源）" % key)  # 日志
            t = threading.Thread(target=self._comp_auto_job, args=(key,), daemon=True)  # 后台线程获取
            t.start()  # 启动线程
            messagebox.showinfo(APP_NAME, "已开始自动获取：软件会联网查找官方资源并下载安装，\n耗时取决于组件大小与网速，请留意主界面日志。")  # 提示

        for key, label in (("msst", "MSST 人声分离工具链"),  # 组件 key 与显示名
                           ("ddsp", "DDSP 转音工具链"),  # DDSP
                           ("models", "角色模型"),  # 角色模型
                           ("ffmpeg", "ffmpeg（视频合成）")):  # ffmpeg
            f = ttk.Frame(cols)  # 单组件行
            f.pack(fill=tk.X, pady=3)  # 放置
            ttk.Label(f, text=label, width=26, anchor=tk.W).pack(side=tk.LEFT)  # 组件名
            stv = ttk.Label(f, text="…", width=6, anchor=tk.CENTER)  # 状态标签（初始省略号）
            stv.pack(side=tk.LEFT)  # 放置
            desc = ttk.Label(f, text="", foreground="#888", anchor=tk.W)  # 说明标签
            desc.pack(side=tk.LEFT, fill=tk.X, expand=True)  # 放置（占满剩余宽度）
            ttk.Button(f, text="本地安装", command=lambda k=key: local_install(k)).pack(side=tk.RIGHT, padx=2)  # 本地安装按钮
            ttk.Button(f, text="自动获取", command=lambda k=key: auto_fetch(k)).pack(side=tk.RIGHT, padx=2)  # 自动获取按钮
            self.comp_rows[key] = {"status": stv, "desc": desc}  # 记录该行控件

        sep = ttk.Separator(body, orient=tk.HORIZONTAL)  # 分隔线
        sep.pack(fill=tk.X, pady=8)  # 放置

        tip = ttk.Label(body, text=(  # 底部说明文字块
            "说明：\n"
            "· 自动获取：软件自动联网查找官方资源（GitHub 最新源码/预训练模型、ModelScope、ffmpeg 官方构建），"
            "下载并自动创建运行环境，适合新机器首装。\n"
            "· 本地安装：从本机已有目录复制组件到 exe 同级 toolkit\\，适合已有工具链的机器快速就绪。\n"
            "· 角色模型：随软件包内置，或点「自动获取」从作者仓库下载 8 个角色模型（共约 4.4GB），"
            "解压到 exe 同级 models\\（toolkit\\models）即自动识别；也可用“本地安装”导入自己的模型目录。"
        ), foreground="#666", justify=tk.LEFT, wraplength=680)  # 灰色说明、自动换行
        tip.pack(anchor=tk.W)  # 放置

        btns = ttk.Frame(body)  # 底部按钮行
        btns.pack(fill=tk.X, pady=8)  # 放置
        ttk.Button(btns, text="刷新状态", command=refresh).pack(side=tk.LEFT)  # 刷新按钮
        ttk.Button(btns, text="关闭", command=w.destroy).pack(side=tk.RIGHT)  # 关闭按钮
        refresh()  # 初始刷新一次状态

    # ---------- 组件安装/下载线程 ----------
    def _auto_fetch_missing_at_startup(self):
        """v1.4.5：首次启动检测到缺失组件时自动联网获取，无需用户手动点「组件管理-自动获取」。"""
        label_map = {"MSST 工具链": "msst", "DDSP 工具链": "ddsp", "角色模型": "models", "ffmpeg": "ffmpeg"}  # 显示名->组件 key 映射
        keys = [label_map[n] for n in self._missing_at_startup if n in label_map]  # 缺失组件 key 列表
        if not keys:  # 无缺失
            return  # 结束
        self._append_log("首次启动：检测到缺失组件 %s，自动开始获取…" % "、".join(self._missing_at_startup))  # 日志
        messagebox.showinfo(  # 弹窗告知
            APP_NAME,
            "检测到缺失组件：%s\n已自动开始联网获取并安装，请留意主界面日志。\n"
            "首次获取需下载较多资源（合计数 GB），耗时取决于网速；\n"
            "期间可正常操作，全部就绪后即可开始翻唱。" % "、".join(self._missing_at_startup),  # 提示内容
        )

        def run():
            """内部函数：顺序自动获取全部缺失组件（供后台线程执行）。"""
            for k in keys:  # 遍历缺失组件
                self._comp_auto_job(k)  # 逐个自动获取

        threading.Thread(target=run, daemon=True).start()  # 后台线程启动获取

    def _comp_install_job(self, key, src):
        """后台线程：本地安装组件（复制到 exe 同级 toolkit\\），完成后刷新配置与模型下拉框。"""
        try:  # 尝试安装
            self.q.put(("log", "安装组件 %s（%s）…" % (key, src)))  # 日志
            dst_tk = app_dir() / "toolkit"  # 目标根目录：程序目录/toolkit
            if key == "ffmpeg":  # ffmpeg 分支
                dst = dst_tk / "ffmpeg.exe"  # 目标文件
                dst_tk.mkdir(parents=True, exist_ok=True)  # 创建 toolkit 目录
                shutil.copy2(src, str(dst))  # 复制 exe
            elif key == "models":  # 模型分支
                dst = dst_tk / "models"  # 目标目录
                if dst.exists():  # 已存在
                    shutil.rmtree(str(dst))  # 先删除旧目录
                shutil.copytree(src, str(dst), ignore=shutil.ignore_patterns("__pycache__", ".git", "*.log"))  # 复制模型目录（跳过缓存/日志）
            else:  # msst/ddsp 分支
                src_p = Path(src)  # 源目录
                dst = dst_tk / ("MSST" if key == "msst" else "DDSP") / src_p.name  # 目标：toolkit/MSST或DDSP/源目录名
                if dst.exists():  # 已存在
                    shutil.rmtree(str(dst))  # 先删除旧目标
                dst.parent.mkdir(parents=True, exist_ok=True)  # 创建父目录
                shutil.copytree(str(src_p), str(dst), ignore=shutil.ignore_patterns("__pycache__", ".git", "*.log", "events.out.*"))  # 复制工具链（跳过缓存/日志/事件文件）
            p = portable_cfg(app_dir())  # 重新探测便携配置
            if p:  # 探测成功
                self.cfg = p  # 更新配置
                save_config(self.cfg)  # 持久化配置
                self._models = find_models(self.cfg["model_dir"])  # 重新扫描模型
                names = [m[0] for m in self._models] or ["（未发现模型）"]  # 模型显示名
                self.role_cb["values"] = names  # 更新下拉选项
                if names:  # 有选项
                    self.role_cb.current(0)  # 默认选第一个
            self.q.put(("log", "组件 %s 安装完成。" % key))  # 日志
            self.q.put(("info", "组件 %s 安装完成。" % key))  # 日志（info 类型）
        except Exception as e:  # 任何异常
            self.q.put(("error", "组件 %s 安装失败: %s" % (key, e)))  # 队列回传失败

    def _comp_auto_job(self, key):
        """v1.4：自动获取组件 —— 软件自主联网解析官方源并下载安装，无需用户填直链。"""
        try:  # 尝试获取
            self.q.put(("log", "自动获取组件 %s：正在联网查找资源…" % key))  # 日志
            dst_tk = app_dir() / "toolkit"  # 目标根目录
            dst_tk.mkdir(parents=True, exist_ok=True)  # 确保存在

            def log(m):
                """内部函数：日志经消息队列回传 UI。"""
                self.q.put(("log", m))  # 队列回传

            if key == "ffmpeg":  # ffmpeg 分支
                cands = resolve_component_sources("ffmpeg", log_cb=log)  # 解析官方下载源候选
                done = False  # 是否成功标志
                for c in cands[:1]:  # 只尝试第一个候选（for-else 结构）
                    try:  # 尝试该候选
                        tmp = dst_tk / "_dl_tmp"  # 临时下载目录
                        out = download_component(c["url"], str(tmp), log_cb=log)  # 下载并解压
                        exes = [p for p in out.rglob("ffmpeg*.exe")]  # 在解压产物中找 ffmpeg.exe
                        if not exes:  # 未找到
                            raise RuntimeError("压缩包内未找到 ffmpeg.exe")  # 抛错
                        shutil.copy2(str(exes[0]), str(dst_tk / "ffmpeg.exe"))  # 复制到 toolkit/ffmpeg.exe
                        shutil.rmtree(str(tmp), ignore_errors=True)  # 清理临时目录
                        done = True  # 标记成功
                        break  # 退出循环
                    except Exception as e:  # 该候选失败
                        log("候选 %s 失败：%s" % (c.get("label"), e))  # 日志
                if not done:  # 所有候选均失败
                    raise RuntimeError("ffmpeg 自动获取失败：所有候选源均不可达")  # 抛错
            elif key == "msst":  # MSST 分支
                self._auto_fetch_msst(log)  # 调专用获取流程
            elif key == "ddsp":  # DDSP 分支
                self._auto_fetch_ddsp(log)  # 调专用获取流程
            elif key == "models":  # 角色模型分支
                self._auto_fetch_models(log)  # 调专用获取流程
            p = portable_cfg(app_dir())  # 重新探测便携配置
            if p:  # 探测成功
                self.cfg = p  # 更新配置
                save_config(self.cfg)  # 持久化
                self._models = find_models(self.cfg["model_dir"])  # 重新扫描模型
                names = [m[0] for m in self._models] or ["（未发现模型）"]  # 模型显示名
                self.role_cb["values"] = names  # 更新下拉框
                if names:  # 有选项
                    self.role_cb.current(0)  # 默认选第一个
            self.q.put(("log", "组件 %s 自动获取完成。" % key))  # 日志
            self.q.put(("info", "组件 %s 自动获取完成。" % key))  # 日志（info 类型）
        except Exception as e:  # 任何异常
            self.q.put(("error", "组件 %s 自动获取失败: %s" % (key, e)))  # 队列回传失败

    def _auto_fetch_models(self, log):
        """自动获取角色模型：从作者仓库 v1.4-components Release 下载 8 个角色 zip 并解压到 toolkit\\models。"""
        dst_tk = app_dir() / "toolkit"  # 目标根目录
        dst = dst_tk / "models"  # 模型目录
        dst.mkdir(parents=True, exist_ok=True)  # 确保存在
        cands = resolve_component_sources("models", log_cb=log)  # 解析模型下载源候选
        if not cands:  # 无候选
            raise RuntimeError("角色模型源解析失败（作者仓库不可达？）")  # 抛错
        got = 0  # 成功下载数
        tmp = dst_tk / "_dl_tmp_models"  # 临时下载目录
        for c in cands:  # 遍历候选
            try:  # 尝试该候选
                log("下载 %s" % c.get("label"))  # 日志
                out = download_component(c["url"], str(tmp), log_cb=log)  # 下载并解压
                moved = 0  # 移动成功的模型目录数
                for child in out.iterdir():  # 遍历解压产物
                    if child.is_dir():  # 是目录
                        tgt = dst / child.name  # 目标路径
                        if tgt.exists():  # 已存在
                            shutil.rmtree(str(tgt))  # 先删旧
                        shutil.move(str(child), str(tgt))  # 移动进 models
                        moved += 1  # 计数
                if moved == 0:  # 无模型目录
                    log("候选 %s：压缩包内未发现模型目录" % c.get("label"))  # 日志
                else:  # 有模型
                    got += 1  # 成功数 +1
            except Exception as e:  # 该候选失败
                log("候选 %s 失败：%s" % (c.get("label"), e))  # 日志
        shutil.rmtree(str(tmp), ignore_errors=True)  # 清理临时目录
        if got == 0:  # 全部失败
            raise RuntimeError("角色模型自动获取失败：所有候选源均不可达")  # 抛错
        log("角色模型已就绪：toolkit\\models 下共 %d 个模型目录" % got)  # 日志

    def _auto_fetch_msst(self, log):
        """自动获取 MSST：官方源码 + 人声分离模型 + 自动创建 Python 环境。"""
        dst_tk = app_dir() / "toolkit"  # 目标根目录
        cands = resolve_component_sources("msst", log_cb=log)  # 解析 MSST 源码源
        if not cands:  # 无候选
            raise RuntimeError("MSST 官方源码解析失败（GitHub 不可达？）")  # 抛错
        tmp = dst_tk / "_dl_tmp"  # 临时目录
        out = download_component(cands[0]["url"], str(tmp), log_cb=log)  # 下载源码并解压
        src_p = [p for p in out.rglob("inference.py")][0].parent  # 找含 inference.py 的源码目录
        dst = dst_tk / "MSST" / src_p.name  # 目标目录
        if dst.exists():  # 已存在
            shutil.rmtree(str(dst))  # 先删旧
        dst.parent.mkdir(parents=True, exist_ok=True)  # 创建父目录
        shutil.copytree(str(src_p), str(dst),  # 复制源码
                        ignore=shutil.ignore_patterns("__pycache__", ".git", "events.out.*"))  # 跳过缓存/事件文件
        shutil.rmtree(str(tmp), ignore_errors=True)  # 清理临时目录
        # 人声分离模型（约 870 MB；网络受限时允许失败，可稍后重试或本地安装）
        ckpt_name = self.cfg.get("msst_ckpt", "mel_band_roformer_vocals_becruily.ckpt")  # 分离模型权重文件名（配置优先）
        pretrain = dst / "pretrain"  # 预训练模型目录
        pretrain.mkdir(parents=True, exist_ok=True)  # 确保存在
        if not (pretrain / ckpt_name).exists():  # 权重不存在
            m_cands = resolve_component_sources("msst_model", log_cb=log)  # 解析模型下载源
            got = False  # 成功标志
            for mc in m_cands:  # 遍历候选
                try:  # 尝试
                    log("下载分离模型：%s" % mc.get("label"))  # 日志
                    download_file(mc["url"], pretrain / ckpt_name, log_cb=log)  # 下载权重
                    got = True  # 标记成功
                    break  # 退出
                except Exception as e:  # 该候选失败
                    log("候选失败：%s" % e)  # 日志
            if not got:  # 全部失败
                log("分离模型未下载成功（约 870 MB，网络受限时常见）。可稍后重试，或改用本地安装。")  # 日志（允许失败）
        # 自动创建 Python 环境
        epy = setup_python_env(dst, "requirements.txt", log_cb=log)  # 创建虚拟环境并装依赖
        if epy:  # 环境创建成功
            cfg = self.cfg  # 配置字典
            cfg["msst_dir"] = str(dst)  # 记录 MSST 目录
            cfg["msst_python"] = epy  # 记录 Python 路径
            ccfg = dst / "configs" / cfg.get("msst_config", "")  # 配置 yaml 路径
            if not ccfg.exists():  # 配置不存在
                names = sorted(p.name for p in (dst / "configs").glob("config_vocals*.yaml")) if (dst / "configs").exists() else []  # 找 config_vocals*.yaml
                if not names:  # 未找到
                    names = sorted(p.name for p in (dst / "configs").glob("*.yaml")) if (dst / "configs").exists() else []  # 退而求其次找任意 yaml
                if names:  # 找到
                    cfg["msst_config"] = names[0]  # 选用第一个
                    log("默认分离配置不存在，已自动选用 %s" % names[0])  # 日志
            if not (pretrain / ckpt_name).exists():  # 权重仍缺失
                cks = sorted(p.name for p in pretrain.glob("*.ckpt")) if pretrain.exists() else []  # 找已下载的 ckpt
                if cks:  # 找到
                    cfg["msst_ckpt"] = cks[0]  # 配置选用
                    log("已自动选用分离模型 %s" % cks[0])  # 日志
            save_config(cfg)  # 持久化配置
            log("MSST 环境就绪：%s" % epy)  # 日志

    def _auto_fetch_ddsp(self, log):
        """自动获取 DDSP：官方源码 + 预训练（编码器/音高/vocoder）+ 自动创建 Python 环境。"""
        dst_tk = app_dir() / "toolkit"  # 目标根目录
        cands = resolve_component_sources("ddsp", log_cb=log)  # 解析 DDSP 源码源
        if not cands:  # 无候选
            raise RuntimeError("DDSP 官方源码解析失败（GitHub 不可达？）")  # 抛错
        tmp = dst_tk / "_dl_tmp"  # 临时目录
        out = download_component(cands[0]["url"], str(tmp), log_cb=log)  # 下载源码并解压
        src_p = [p for p in out.rglob("main_reflow.py")][0].parent  # 找含 main_reflow.py 的源码目录
        dst = dst_tk / "DDSP" / src_p.name  # 目标目录
        if dst.exists():  # 已存在
            shutil.rmtree(str(dst))  # 先删旧
        dst.parent.mkdir(parents=True, exist_ok=True)  # 创建父目录
        shutil.copytree(str(src_p), str(dst),  # 复制源码
                        ignore=shutil.ignore_patterns("__pycache__", ".git", "events.out.*", ".*"))  # 跳过缓存/隐藏文件
        shutil.rmtree(str(tmp), ignore_errors=True)  # 清理临时目录
        # 预训练模型（contentvec / hubert / rmvpe / nsf_hifigan）
        pretrain = dst / "pretrain"  # 预训练目录
        pretrain.mkdir(parents=True, exist_ok=True)  # 确保存在
        p_cands = resolve_component_sources("ddsp_pretrain", log_cb=log)  # 解析预训练模型源
        for pc in p_cands:  # 遍历候选
            rel = pc.get("rel_path", "")  # 相对路径
            name = rel.split("/")[-1]  # 文件名
            sub = rel.split("/")[-2] if "/" in rel else ""  # 上级子目录名
            target = (pretrain / sub / name) if sub in ("contentvec", "rmvpe", "nsf_hifigan") else (pretrain / name)  # 特定子目录下的目标路径
            if target.exists():  # 已存在
                continue  # 跳过
            try:  # 尝试下载
                log("下载预训练：%s" % pc.get("label"))  # 日志
                download_file(pc["url"], target, log_cb=log)  # 下载
            except Exception as e:  # 失败
                log("候选失败：%s" % e)  # 日志
        # 可选：GitHub Release 的 model_0.pt（供训练，推理不强制）
        try:  # 尝试下载 model_0.pt
            for c in cands:  # 遍历源码候选
                if "model_0.pt" in c.get("label", ""):  # 标签含 model_0.pt
                    target = pretrain / "model_0.pt"  # 目标路径
                    if not target.exists():  # 未下载过
                        log("下载预训练模型：%s" % c.get("label"))  # 日志
                        download_file(c["url"], target, log_cb=log)  # 下载
                    break  # 退出
        except Exception as e:  # 失败
            log("预训练模型 model_0.pt 下载跳过：%s" % e)  # 日志（可跳过）
        # 自动创建 Python 环境
        epy = setup_python_env(dst, "requirements.txt", log_cb=log)  # 创建虚拟环境并装依赖
        if epy:  # 环境创建成功
            cfg = self.cfg  # 配置字典
            cfg["ddsp_dir"] = str(dst)  # 记录 DDSP 目录
            cfg["ddsp_python"] = epy  # 记录 Python 路径
            save_config(cfg)  # 持久化
            log("DDSP 环境就绪：%s" % epy)  # 日志

    def _open_settings(self):
        """打开设置弹窗：编辑工具链路径与 GPU 开关，保存后刷新配置与模型列表。"""
        w = tk.Toplevel(self)  # 新建顶层窗口
        w.title("设置 - 工具链路径")  # 标题
        w.geometry("620x430")  # 尺寸
        w.transient(self)  # 依附主窗口
        fields = [  # 可编辑字段定义（显示名, 配置 key）
            ("MSST 目录", "msst_dir"),  # MSST 工具链目录
            ("MSST Python", "msst_python"),  # MSST Python 解释器
            ("DDSP 目录", "ddsp_dir"),  # DDSP 工具链目录
            ("DDSP Python", "ddsp_python"),  # DDSP Python 解释器
            ("模型目录", "model_dir"),  # 角色模型根目录
            ("分离模型(yaml)", "msst_config"),  # 分离配置文件
            ("分离权重(ckpt)", "msst_ckpt"),  # 分离权重
        ]
        vars_ = {}  # 字段输入变量字典 {key: StringVar}
        body = ttk.Frame(w)  # 主体容器
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)  # 铺满
        for i, (label, key) in enumerate(fields):  # 逐字段建行
            ttk.Label(body, text=label).grid(row=i, column=0, sticky=tk.W, pady=3)  # 左侧标签
            v = tk.StringVar(value=self.cfg.get(key, ""))  # 输入变量（初始取配置值）
            vars_[key] = v  # 登记
            ent = ttk.Entry(body, textvariable=v, width=60)  # 输入框
            ent.grid(row=i, column=1, sticky=tk.EW, pady=3, padx=4)  # 右侧输入框
        body.columnconfigure(1, weight=1)  # 第二列可伸缩
        gpu_var = tk.BooleanVar(value=self.cfg.get("use_gpu_separate", True))  # GPU 开关变量（默认 True）
        ttk.Checkbutton(body, text="分离使用 GPU（更快；DDSP 转音固定 CPU）", variable=gpu_var).grid(  # GPU 勾选框
            row=len(fields), column=0, columnspan=2, sticky=tk.W, pady=6)  # 放在字段下方

        def save():
            """内部函数：保存设置并刷新主界面模型下拉框。"""
            for k, v in vars_.items():  # 遍历输入变量
                if v.get().strip():  # 非空
                    self.cfg[k] = v.get().strip()  # 写入配置
            self.cfg["use_gpu_separate"] = gpu_var.get()  # GPU 开关写入配置
            save_config(self.cfg)  # 持久化
            self._models = find_models(self.cfg["model_dir"])  # 重新扫描模型
            names = [m[0] for m in self._models] or ["（未发现模型）"]  # 模型显示名
            self.role_cb["values"] = names  # 更新下拉框
            if names:  # 有选项
                self.role_cb.current(0)  # 默认选第一个
            messagebox.showinfo(APP_NAME, "设置已保存")  # 提示
            w.destroy()  # 关闭弹窗

        ttk.Button(w, text="保存", command=save).pack(pady=6)  # 保存按钮

    def _start(self):
        """开始翻唱入口：校验输入、解析参数、构造 Pipeline 并在后台线程执行。"""
        source = None  # 源音频（本地路径或网易云条目字典）
        song_id = None  # 网易云歌曲 ID
        hint = None  # 文件名/歌名提示
        if self.file_path.get().strip():  # 用户导入了本地文件
            source = self.file_path.get().strip()  # 取本地路径
            hint = Path(source).stem  # 提示取文件主名
        sel = self._selected_search()  # 搜索结果选中项
        if sel:  # 有选中（优先级高于本地文件）
            source = sel  # 源为网易云条目
            song_id = sel["id"]  # 歌曲 ID
            hint = sel["name"]  # 歌名
        if source is None:  # 无任何输入
            messagebox.showwarning(APP_NAME, "请先选择本地音频，或搜索并选中一首歌。")  # 弹窗警告
            return  # 结束
        if not self._models or not self.role_var.get() or self.role_var.get().startswith("（"):  # 无模型或未选角色
            messagebox.showwarning(APP_NAME, "未发现可用角色模型，请检查设置中的模型目录。")  # 弹窗警告
            return  # 结束
        idx = self.role_cb.current()  # 下拉框选中索引
        if idx < 0:  # 未选中
            idx = 0  # 兜底取 0
        role = self._models[idx][1]  # 角色名
        ckpt = self._models[idx][2]  # 模型权重路径

        try:  # 解析数值参数
            key = int(self.key_var.get())  # 变调半音（整数）
            ig = float(self.ig_var.get())  # 伴奏音量（浮点）
        except ValueError:  # 解析失败
            messagebox.showerror(APP_NAME, "key 需为整数，伴奏音量需为数字。")  # 弹窗报错
            return  # 结束
        outdir = self.out_var.get().strip() or self.cfg.get("output_dir")  # 输出目录
        os.makedirs(outdir, exist_ok=True)  # 创建输出目录

        workdir = Path(outdir) / ".job"  # 任务中间目录（输出目录/.job）
        source_audio = source  # 实际音频路径（初始为 source）
        if isinstance(source, dict):  # 网易云条目：需先下载
            self.log("下载歌曲: %s - %s" % (source["name"], source["artist"]))  # 日志
            mp3 = workdir / ("source_%s.mp3" % source["id"])  # 下载目标文件
            try:  # 尝试下载
                netease_download(source["id"], mp3)  # 网易云下载
            except Exception as e:  # 失败
                messagebox.showerror(APP_NAME, "下载失败: %s" % e)  # 弹窗报错
                return  # 结束
            source_audio = str(mp3)  # 更新为本地 mp3 路径

        stem = Path(source_audio).stem  # 源音频主文件名
        out_wav = Path(outdir) / ("%s_%s_翻唱_带伴奏.wav" % (stem, role))  # 最终成品 wav 路径
        lyric_out = Path(outdir) / ("%s_%s_翻唱_歌词.srt" % (stem, role)) if self.lyric_var.get() else None  # 歌词字幕输出路径（未勾选则为 None）

        compose_cfg = None  # 自动合成配置（未开启则为 None）
        if self.auto_video_var.get():  # 勾选了自动合成视频
            compose_cfg = {  # 合成配置字典
                "enabled": True,  # 启用标志
                "ffmpeg": find_ffmpeg(),  # ffmpeg 路径
                "bg": self.bg_path,  # 背景路径
                "srt": self.mux_srt.get().strip() or None,  # 字幕路径
                "size": self.bg_size_var.get(),  # 分辨率
                "fps": 25,  # 帧率
                "kenburns": self.bg_ken_var.get(),  # 动态效果开关
            }
            if not compose_cfg["ffmpeg"]:  # ffmpeg 缺失
                self._append_log("未找到 ffmpeg，跳过自动合成，可在背景页下载后手动合成。")  # 日志提示

        self._set_running(True)  # 锁定界面（禁用开始）
        self._clear_log()  # 清空日志
        self.progress.start(12)  # 启动不定进度条
        pipe = Pipeline(self.cfg, self._log, self._set_stage)  # 创建流水线对象（配置、日志回调、阶段回调）
        self.worker = threading.Thread(  # 实例变量：任务线程
            target=self._run_job,  # 线程目标
            args=(pipe, source_audio, ckpt, role, workdir, out_wav, key, ig, song_id, hint, lyric_out, compose_cfg),  # 任务参数
            daemon=True,  # 守护线程
        )
        self.worker.start()  # 启动线程

    def _run_job(self, pipe, source, ckpt, role, workdir, out_wav, key, ig, song_id, hint, lyric_out, compose_cfg):
        """后台线程：执行完整翻唱流水线，结果经队列回传。"""
        try:  # 尝试运行
            pipe.run(source, ckpt, role, workdir, out_wav, key, 1.0, ig, song_id, hint, lyric_out, compose_cfg)  # 调用流水线（人声音量固定 1.0）
        except Exception as e:  # 失败
            self.q.put(("error", str(e)))  # 队列回传错误
            return  # 结束
        self.q.put(("done", str(out_wav)))  # 队列回传完成与成品路径

    def _make_portable(self):
        """制作便携版：选择目标目录，确认后复制全部工具链与模型（后台线程）。"""
        if self.portable_worker and self.portable_worker.is_alive():  # 已在制作
            messagebox.showinfo(APP_NAME, "正在制作便携版，请稍候…")  # 提示
            return  # 结束
        dst = filedialog.askdirectory(title="选择便携版保存位置（将生成 toolkit\\ 子目录）")  # 目标目录选择
        if not dst:  # 用户取消
            return  # 结束
        cfg = self.cfg  # 当前配置
        confirm = messagebox.askyesno(  # 二次确认弹窗
            APP_NAME,
            "将复制以下内容到所选目录（工具链合计约 40+ GB，耗时较久，请确保磁盘空间充足）：\n"
            "  1) MSST 工具链（含分离模型）\n"
            "  2) DDSP 工具链（含 vocoder）\n"
            "  3) 8 个角色模型\n"
            "  4) 软件本体（含 ffmpeg）\n"
            "复制完成后，把整个文件夹拷到新电脑即可直接使用。\n\n确定开始吗？",  # 提示内容
        )
        if not confirm:  # 用户拒绝
            return  # 结束
        self._set_running(True)  # 锁定界面
        self._clear_log()  # 清空日志
        self.progress.start(12)  # 启动进度条
        self.portable_worker = threading.Thread(  # 实例变量：便携版制作线程
            target=self._portable_job, args=(cfg, Path(dst)), daemon=True  # 线程目标与参数
        )
        self.portable_worker.start()  # 启动线程

    def _portable_job(self, cfg, dst):
        """后台线程：执行便携版复制，结果经队列回传。"""
        try:  # 尝试制作
            make_portable(cfg, dst, lambda m: self.q.put(("log", m)))  # 调用复制函数（日志经队列回传）
        except Exception as e:  # 失败
            self.q.put(("error", "制作便携版失败: %s" % e))  # 队列回传错误
            return  # 结束
        self.q.put(("info", "便携版制作完成。将整个文件夹拷贝到新电脑，双击 AI翻唱工坊.exe 即可。"))  # 日志
        self.q.put(("portable_done", str(dst)))  # 队列回传完成与路径

    def _cancel(self):
        """取消任务：当前仅提示（等待子进程自然结束，未真正终止）。"""
        if self.worker and self.worker.is_alive():  # 任务线程运行中
            self.q.put(("info", "正在取消…（等待当前子进程结束）"))  # 队列回传提示

    def _set_running(self, running):
        """切换界面运行态：开始按钮禁用/启用、取消按钮启用/禁用。"""
        self.start_btn.config(state="disabled" if running else "normal")  # 开始按钮状态
        self.cancel_btn.config(state="normal" if running else "disabled")  # 取消按钮状态

    def _set_stage(self, s):
        """阶段回调：把当前阶段文字经队列回传 UI。"""
        self.q.put(("stage", s))  # 队列回传

    def _log(self, msg):
        """日志回调：把日志经队列回传 UI。"""
        self.q.put(("log", msg))  # 队列回传

    def _clear_log(self):
        """清空日志文本框内容。"""
        self.log_text.config(state="normal")  # 临时可编辑
        self.log_text.delete("1.0", tk.END)  # 删除全部内容
        self.log_text.config(state="disabled")  # 恢复只读

    def _poll(self):
        """主线程定时轮询：消费消息队列并更新 UI（每 100ms 一次）。"""
        try:  # 尝试消费队列
            while True:  # 循环取尽当前队列
                kind, *rest = self.q.get_nowait()  # 非阻塞取消息（消息类型 + 剩余参数）
                if kind == "log":  # 日志类型
                    self._append_log(rest[0])  # 追加日志
                elif kind == "stage":  # 阶段类型
                    self.stage_var.set("阶段: " + rest[0])  # 更新阶段状态
                elif kind == "info":  # 信息类型
                    self._append_log(rest[0])  # 追加日志
                elif kind == "bg_search_result":  # 背景搜索结果类型
                    urls, msg = rest[0], rest[1]  # 图片直链列表与提示
                    self.bg_search_results = urls  # 保存结果
                    self.bg_list.delete(0, tk.END)  # 清空列表
                    for i, u in enumerate(urls):  # 遍历结果
                        host = urllib.parse.urlparse(u).netloc  # 提取域名
                        self.bg_list.insert(tk.END, "%d. %s" % (i + 1, host))  # 列表显示序号与域名
                    self.bg_search_var.set(msg)  # 更新提示
                elif kind == "bg_downloaded":  # 背景下载完成类型
                    path, name = rest[0], rest[1]  # 路径与文件名
                    self.bg_path = path  # 设为当前背景
                    self.bg_status_var.set(name)  # 更新状态
                    self.bg_search_var.set("已选用: %s" % name)  # 更新提示
                    self._append_log("背景已就绪: %s" % path)  # 日志
                elif kind == "ffmpeg_ok":  # ffmpeg 就绪类型
                    self.ffmpeg_var.set("ffmpeg: 可用")  # 更新状态
                    self._append_log("ffmpeg 可用: %s" % rest[0])  # 日志
                elif kind == "portable_done":  # 便携版完成类型
                    self.progress.stop()  # 停止进度条
                    self._set_running(False)  # 恢复界面
                    self.stage_var.set("完成")  # 阶段完成
                    messagebox.showinfo(APP_NAME, "便携版制作完成：\n%s" % rest[0])  # 弹窗提示
                elif kind == "video_done":  # 视频合成完成类型
                    self.progress.stop()  # 停止进度条
                    self._set_running(False)  # 恢复界面
                    self.stage_var.set("完成")  # 阶段完成
                    self._append_log("【完成】MP4: %s" % rest[0])  # 日志
                    messagebox.showinfo(APP_NAME, "视频合成完成！\n%s" % rest[0])  # 弹窗提示
                elif kind == "error":  # 错误类型
                    self.progress.stop()  # 停止进度条
                    self._set_running(False)  # 恢复界面
                    self.stage_var.set("失败")  # 阶段失败
                    self._append_log("【失败】" + rest[0])  # 日志
                    messagebox.showerror(APP_NAME, "失败：\n" + rest[0][-600:])  # 弹窗报错（截断 600 字符）
                elif kind == "done":  # 翻唱完成类型
                    self.progress.stop()  # 停止进度条
                    self._set_running(False)  # 恢复界面
                    self.stage_var.set("完成")  # 阶段完成
                    self._append_log("【完成】成品: " + rest[0])  # 日志
                    messagebox.showinfo(APP_NAME, "翻唱完成！\n%s" % rest[0])  # 弹窗提示
        except queue.Empty:  # 队列已空
            pass  # 忽略
        self.after(100, self._poll)  # 100ms 后再次轮询（形成周期循环）

    def _append_log(self, msg):
        """向日志文本框追加一行并滚动到底部。"""
        self.log_text.config(state="normal")  # 临时可编辑
        self.log_text.insert(tk.END, msg + "\n")  # 追加文本
        self.log_text.see(tk.END)  # 滚动到底部
        self.log_text.config(state="disabled")  # 恢复只读


def main():
    app = App()  # 创建主应用实例
    app.mainloop()  # 进入 Tk 事件循环


if __name__ == "__main__":  # 脚本作为主程序运行时
    main()  # 启动应用
