---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 9da29870b7dfe952a0e458cf6f69e3bd_59fd5f40c22611f1a05452540064ee0f
    ReservedCode1: iVDWBhgc98Hm2zA3p/Bor/s84/UsNh8SCZHgFUbj2t/+rqJxZgTwYY7a+hPkGVL10M2D6e1XeS99T/NKLy2SA7ziPu+u6MkN0YrwZEWUhFdPlvhKBdLqEWbMlLa+pHIfw/a9Sm9TDDGToyviW3AMgvEWYvi/qfEUHhFjtjf8vQKVHwvOuk9tTbtpYAc=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 9da29870b7dfe952a0e458cf6f69e3bd_59fd5f40c22611f1a05452540064ee0f
    ReservedCode2: iVDWBhgc98Hm2zA3p/Bor/s84/UsNh8SCZHgFUbj2t/+rqJxZgTwYY7a+hPkGVL10M2D6e1XeS99T/NKLy2SA7ziPu+u6MkN0YrwZEWUhFdPlvhKBdLqEWbMlLa+pHIfw/a9Sm9TDDGToyviW3AMgvEWYvi/qfEUHhFjtjf8vQKVHwvOuk9tTbtpYAc=
---



# AI 翻唱工坊 CoverStudio v1.4

一键 AI 翻唱工具：MSST 人声分离 → DDSP AI 转音 → 混音 → 歌词字幕，输出带伴奏成品 + .srt 字幕，可选合成 MP4 视频（免 PR）。

## 使用方式

双击 `AI翻唱工坊.exe` 启动。两种输入任选其一：

1. **导入原音频**：选择本地 mp3 / wav 文件（歌词按文件名自动搜索网易云）
2. **搜索歌名**：输入歌名搜索网易云音乐，选中一条结果（歌词精确匹配）

然后选择**翻唱角色**（内置 8 个角色模型：祥子 / 素世 / 喵梦 / 高松灯 / 立希 / 初华 / 若叶睦 / 爱音），可调整变调 key 与伴奏音量，勾选「输出歌词字幕（.srt）」后点「开始翻唱」即可。

成品输出到所选目录（默认桌面\AI翻唱成品）：

- `歌曲_角色_翻唱_带伴奏.wav` —— 混音成品
- `歌曲_角色_翻唱_歌词.srt` —— 歌词字幕（可选，UTF-8 编码，适合导入剪辑软件 / 播放器）
- `歌曲_角色_翻唱.mp4` —— 视频合成成品（在背景页配好背景并勾选自动合成）

## 自动分发（v1.4 新方案）

**安装包只有约 11MB**（exe + ffmpeg），不打包工具链；组件缺失时**软件自动联网查找官方资源并安装**，无需用户手动上传或填写下载直链：

| 组件 | 获取来源（软件自动查找） | 说明 |
|------|--------------------------|------|
| MSST 工具链 | GitHub 官方仓库最新源码 + 自动创建 Python 环境 | 人声分离，约 2~6 GB |
| DDSP 工具链 | GitHub 官方仓库最新源码 + ModelScope 预训练权重 + 自动创建 Python 环境 | AI 转音，约 2~6 GB |
| 角色模型 | 随软件包内置（exe 同级 `models\`），无需下载 | 每个角色一个 ckpt，约 300~600 MB |
| ffmpeg | gyan.dev 官方 Windows 构建直链 | 视频合成，约 80 MB |

使用方式：

1. 打开「组件管理」，软件自动检测 MSST / DDSP / ffmpeg / 角色模型四项就绪状态
2. 点「**自动获取**」：软件后台从 GitHub 解析最新 release 源码、ModelScope 拉取预训练模型、ffmpeg 官方构建下载，并自动创建 Python 虚拟环境安装依赖，全程无需手动干预
3. 也支持「**本地安装**」：从本机已有目录复制组件到 exe 同级 `toolkit\`，适合离线机器或复用已有环境

安装完成后软件自动探测 `toolkit\` 并更新配置。工具链只在缺失时才需要安装——替代原 43GB 便携版全量复制逻辑的瘦身方案。

## 文件说明

| 文件 | 说明 |
|------|------|
| AI翻唱工坊.exe | 主程序（无需安装 Python，约 11MB） |
| config.json | 工具链路径配置，可通过软件内「设置」修改（v1.4 已移除手动直链配置） |
| audio_utils.py | 音频转码 / 混音脚本（由工具链 Python 调用，请勿删除） |
| models\ | 内置角色模型目录（随包分发，自动识别） |
| toolkit\ | 软件自动获取后安装的组件目录（MSST / DDSP / ffmpeg.exe） |

## 依赖环境（本机开发模式）

软件依赖本机已安装的 AI 音乐工具链（自动探测或手动配置，也可用组件管理自动获取 / 本地安装到 toolkit）：

- **MSST**（人声分离）：`E:\AI音乐\分离\MSST-GUI-1.4.0`，Python 环境需含 CUDA 版 torch（分离支持 GPU）
- **DDSP-SVC**（AI 转音）：`E:\AI音乐\.DDSP\DDSP-barbara-6.2`，Python 环境需含 librosa / soundfile
- **角色模型**：`E:\AI音乐\.DDSP\DDSP模型\角色名 步数N`，自动扫描目录内最大步数 ckpt

若目录结构不同，打开软件右上角「设置」修改对应路径后保存即可。

## 流程说明

1. 分离：原曲 → 人声 + 伴奏（GPU 约 5~30 分钟，视歌曲长度；无独显可在设置里关闭 GPU）
2. 转音：人声 → 所选角色音色（CPU，约歌长的 1/15 时长）
3. 混音：转音人声 + 伴奏（人声 1.0 / 伴奏 0.7，峰值 0.95）→ 立体声 16bit 44.1kHz WAV
4. 字幕：网易云歌词 → SRT 时间轴（搜索模式精确匹配，导入模式按文件名搜索，失败自动跳过）
5. 视频（可选）：背景（图片/GIF/视频/网上搜索/氛围生成）+ 成品 + 字幕 → MP4

## 说明

- 搜索 / 歌词功能使用网易云公开接口，仅供个人学习研究使用
- 分离使用 GPU 加速；DDSP 转音固定 CPU（GPU 兼容性原因）
- 新机器需安装 **NVIDIA 显卡驱动**（分离用 GPU 加速）
- 「自动获取」需联网访问 GitHub / ModelScope / gyan.dev；网络受限环境建议使用「本地安装」
*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
