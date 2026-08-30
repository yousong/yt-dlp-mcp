# yt-dlp MCP Service — 设计文档

## 1. 概述

将 yt-dlp 的媒体提取能力封装为 MCP (Model Context Protocol) 服务，以容器方式部署。
核心功能：给定 URL，提取并实时播放其音频流；支持搜索、投屏 (DLNA/AirPlay/Chromecast)、下载存储、会话管理。

### 1.1 设计原则

- **独立性**：仅使用 yt-dlp 公开 API (`YoutubeDL` 类、`extract_info`、format string 等)，不修改、不 import yt-dlp 内部模块
- **音频优先**：通过 yt-dlp format selection 优先选取纯音频流，减少带宽与计算开销
- **流不入窗**：MCP 工具仅返回结构化 JSON 元数据，音视频流不经过 MCP context window
- **容器自包含**：单个容器包含所有运行时依赖，通过 volume 持久化状态

---

## 2. 架构

```
┌────────────────────────────────────────────────────────────────┐
│                    yt-dlp-mcp Container                        │
│                                                                │
│  ┌────────────┐     ┌────────────────┐     ┌────────────────┐  │
│  │ MCP Server │◄───►│  Tool Handlers │◄───►│  yt-dlp (lib)  │  │
│  │   (SSE)    │     │                │     │  [PyPI package]│  │
│  └────────────┘     └───────┬────────┘     └────────────────┘  │
│                             │                                  │
│         ┌───────────┬───────┼───────────┬──────────┐           │
│         │           │       │           │          │           │
│  ┌──────▼────┐ ┌────▼───┐ ┌─▼───────┐ ┌─▼──────┐ ┌─▼───────┐   │
│  │ Playback  │ │  Cast  │ │Download │ │ Cookie │ │ Search  │   │
│  │ Engine    │ │ Module │ │ Manager │ │ Store  │ │ Engine  │   │
│  │ (mpv+PA)  │ │(DLNA/  │ │         │ │(files) │ │         │   │
│  │           │ │ AP/CC) │ │         │ │        │ │         │   │
│  └─────┬─────┘ └───┬────┘ └────┬────┘ └───┬────┘ └─────────┘   │
│        │           │           │            │                  │
└────────┼───────────┼───────────┼────────────┼──────────────────┘
         │           │           │            │
    PULSE_SERVER SSDP/mDNS  /data/store/ /data/cookies/
    (TCP socket) (LAN)      (volume)     (volume)
         │           │           │
    ┌────▼─────┐ ┌───▼──────┐ ┌──▼─────────┐
    │PulseAudio│ │DLNA/AP/CC│ │下载产物    │
    │ Server   │ │Devices   │ │+ 元信息文件│
    └──────────┘ └──────────┘ └────────────┘
```

### 2.1 组件职责

| 组件 | 职责 | 依赖 |
|------|------|------|
| **MCP Server** | 协议层，SSE transport | `mcp` SDK |
| **Tool Handlers** | 业务逻辑编排 | — |
| **yt-dlp** | 媒体信息提取、格式选择、流下载 | PyPI: `yt-dlp[default,curl-cffi,deno]` |
| **Playback Engine** | mpv 子进程管理，PulseAudio 输出 | 系统包: `mpv`, `ffmpeg` |
| **Cast Module** | 设备发现与投屏推送 | `async-upnp-client`, `pyatv`, `pychromecast` |
| **Download Manager** | 下载任务管理、目录组织、元信息持久化 | yt-dlp `download()` + infojson |
| **Cookie Store** | Netscape cookie 文件持久化管理 | 标准库 |

---

## 3. 音频播放：管道模式 + 音频优先

### 3.1 格式选择策略

通过 yt-dlp 的 format selection 机制在服务端控制媒体选择，优先纯音频流：

```
优先级链：

1. bestaudio (vcodec=='none')
   → 纯音频流，直接 HTTP/HTTPS URL
   → 无需 ffmpeg 转码，mpv 直接播放
   → 带宽最小

2. bestaudio (回退)
   → 某些站点可能无纯音频流标记
   → yt-dlp 自动选择最优含音频格式

3. best (vcodec!='none' && acodec!='none')
   → 预混合音视频的格式
   → mpv 以 --no-video 忽略视频流
```

**实现方式**：

```python
# 默认：音频优先
ydl_opts = {
    'format': 'bestaudio/best',
    'quiet': True,
    'no_warnings': True,
}
```

用户可通过 `play` 工具的 `format` 参数覆盖此默认值。

### 3.2 管道播放流程

```
yt-dlp (Python)          ffmpeg (pipe)         mpv              PulseAudio
     │                       │                   │                    │
     │ extract_info(URL,     │                   │                    │
     │   download=False)     │                   │                    │
     │──► info_dict          │                   │                    │
     │                       │                   │                    │
     │ outtmpl='-'           │                   │                    │
     │ format='bestaudio'    │                   │                    │
     │──► stdout ───────────►│─── stdin ────────►│─── PULSE_SERVER ──►│
     │                       │  (stream copy     │  (TCP)             │
     │                       │   if codec match) │                    │
```

**关键细节**：

- yt-dlp 的 `outtmpl='-'` 将流输出到 stdout
- stdout 输出时，yt-dlp 内部使用 `FFmpegFD`（唯一支持 stdout 的 downloader）
- 当选定纯音频格式时，ffmpeg 执行 stream copy (`-c copy`)，无重编码开销
- mpv 通过 `--no-video` 忽略视频流（当不得不选择含视频格式时）
- mpv 通过 `PULSE_SERVER` 环境变量连接远程 PulseAudio
- mpv 通过 `--input-ipc-server=/tmp/mpv-socket` 暴露 IPC 接口，用于控制

### 3.3 为什么不直接传 URL 给 mpv

 | 场景                | 直接 URL     | 管道模式           |
 |------               |---------     |---------           |
 | 纯音频 HTTP URL     | 可用         | 可用               |
 | HLS (m3u8)          | mpv 原生支持 | 可用               |
 | DASH (mpd)          | mpv 原生支持 | 可用               |
 | 分段流 (fragments)  | 不可用       | 可用 (ffmpeg 组装) |
 | 需要合并的音视频    | 不可用       | 可用 (ffmpeg 合并) |
 | RTMP                | mpv 支持     | 可用               |
 | 需要 cookie/headers | 需额外传递   | yt-dlp 自动处理    |

管道模式兼容所有 yt-dlp 支持的场景。

---

## 4. 投屏 (Cast)

### 4.1 协议支持

| 协议 | 发现方式 | 推送方式 | Python 库 |
|------|---------|---------|-----------|
| **DLNA** | SSDP (UPnP) | HTTP PUT/POST to renderer | `async-upnp-client` |
| **AirPlay** | mDNS | AirPlay protocol | `pyatv` |
| **Chromecast** | mDNS | Cast protocol | `pychromecast` |

### 4.2 投屏流程

```
MCP Client                yt-dlp-mcp                  投屏设备
    │                         │                           │
    │ cast(url, device_id)    │                           │
    │────────────────────────►│                           │
    │                         │ extract_info(url,         │
    │                         │   download=False)         │
    │                         │──► 获取直接媒体 URL       │
    │                         │                           │
    │                         │ push URL to device        │
    │                         │──────────────────────────►│
    │                         │                           │ 设备自行下载播放
    │                         │                           │
    │◄────────────────────────│                           │
    │  {status, device, url}  │                           │
```

### 4.3 投屏兼容性

投屏要求设备能直接访问媒体 URL。以下情况可能不兼容：

 | 情况                     | 问题             | 处理                        |
 |------                    |------            |------                       |
 | 纯 HTTP URL (有时效签名) | URL 过期         | 告知用户限制                |
 | HLS (m3u8)               | 多数 DLNA 不支持 | AirPlay/Chromecast 通常支持 |
 | DASH (mpd)               | 多数设备不支持   | 同上                        |
 | 分段流 (fragments)       | 设备无法组装     | 返回错误信息                |
 | DRM 内容                 | 无法播放         | 明确告知不支持              |

当投屏不可用时，向调用方返回明确的异常信息，不自动回退。

### 4.4 投屏意图识别

MCP 工具 `cast` 需要用户明确指定目标设备。`list_devices` 工具返回可用设备列表。
MCP client（AI 助手）根据用户指令（"投屏到客厅电视"）选择设备和协议。

---

## 5. 搜索

### 5.1 yt-dlp 搜索能力

yt-dlp 内置 18+ 搜索提取器，通过 URL 前缀语法触发：

 | 前缀                 | 搜索源      |
 |------                |--------     |
 | `ytsearch:N:query`   | YouTube     |
 | `scsearch:N:query`   | SoundCloud  |
 | `bilisearch:N:query` | Bilibili    |
 | `dmsearch:N:query`   | Dailymotion |
 | `nicosearch:N:query` | NicoVideo   |
 | `google:N:query`     | Google      |
 | ...                  | 更多        |

### 5.2 设计决策：搜索作为同一服务的工具

搜索与播放在同一服务内，理由：
- 搜索结果需后续播放，同服务衔接自然
- 共享 cookie/session 状态
- 减少部署复杂度
- 搜索通过 yt-dlp 公开 API (`extract_info` + search prefix) 即可实现，无需额外依赖

### 5.3 搜索实现

```python
ydl_opts = {
    'extract_flat': True,   # 不解析每个视频，快速返回列表
    'quiet': True,
}
with YoutubeDL(ydl_opts) as ydl:
    info = ydl.extract_info(f'ytsearch10:{query}', download=False)
    # info['entries'] 包含搜索结果列表
```

---

## 6. Cookie / 会话管理

### 6.1 容器内限制

yt-dlp 支持两种 cookie 来源：
- `--cookies FILE`：Netscape 格式文件 → **容器内可用**
- `--cookies-from-browser BROWSER`：从浏览器提取 → **容器内不可用**（无浏览器安装）

### 6.2 管理方案

```
/data/cookies/                    (volume mount)
├── youtube.com.txt               (Netscape 格式)
├── bilibili.com.txt
└── ...
```

**MCP 工具**：

| 工具 | 功能 |
|------|------|
| `set_cookies(site, cookies_text)` | 写入 Netscape 格式 cookie 文件 |
| `get_cookies(site)` | 返回 cookie 状态（域名、数量、过期时间） |
| `delete_cookies(site)` | 删除指定站点 cookie |
| `list_cookie_sites()` | 列出已存储 cookie 的站点 |

**使用方式**：
1. 用户通过 MCP client 提供 cookie（从浏览器导出 Netscape 格式）
2. MCP 工具写入 `/data/cookies/{site}.txt`
3. 后续 yt-dlp 调用自动加载对应站点的 cookie

```python
ydl_opts = {
    'cookiefile': f'/data/cookies/{site}.txt',
}
```

### 6.3 会话信息扩展

除 cookie 外，某些站点可能需要额外的会话信息（如 token、签名等）。
yt-dlp 内部通过 extractor 自动处理这些逻辑，MCP 服务无需额外管理。
仅需确保 cookie 正确，yt-dlp 会基于 cookie 完成认证流程。

---

## 7. 下载与存储

### 7.1 存储结构

yt-dlp-mcp 预设一个 store 目录（默认 `/data/store`），所有下载产物存放于此。
用户通过 `subdir` 参数指定子目录，服务在 store 目录下创建该子目录并下载至其中。

```
/data/store/                          (volume mount, STORE_DIR 环境变量)
├── music/
│   ├── lofi-beats/
│   │   ├── artist - track title.m4a           # 下载产物
│   │   ├── artist - track title.info.json     # 元信息 (yt-dlp --write-info-json)
│   │   ├── artist - track title.jpg           # 缩略图 (可选)
│   │   └── ...
│   └── podcast-ep1/
│       └── ...
├── video/
│   └── ...
└── archive/
    └── ...
```

### 7.2 目录命名与安全

- `subdir` 参数：仅允许字母、数字、连字符、下划线、中文，禁止 `..`、`/` 等路径穿越字符
- 服务启动时校验 `subdir`，非法值返回错误
- 子目录不存在时自动创建

### 7.3 元信息文件

通过 yt-dlp 的 `--write-info-json` 选项，下载时同时生成 `.info.json` 文件，包含：
- 标题、描述、上传者、上传日期
- 时长、格式信息、码率
- 缩略图 URL
- 原始页面 URL
- 章节信息（如有）

元信息文件与下载产物同目录，方便后续处理：
- 重命名：读取 info.json 中的标题/上传者重新组织文件名
- 标签嵌入：从 info.json 提取元信息嵌入音频文件标签（mutagen）
- 索引/检索：扫描所有 info.json 建立本地媒体库索引

### 7.4 下载实现

```python
ydl_opts = {
    'format': 'bestaudio/best',
    'outtmpl': f'/data/store/{subdir}/%(title)s-%(id)s.%(ext)s',
    'writeinfojson': True,
    'writethumbnail': True,
    'quiet': True,
    'progress_hooks': [progress_hook],  # 进度回调
}
with YoutubeDL(ydl_opts) as ydl:
    ydl.download([url])
```

### 7.5 列表下载

当 URL 指向播放列表或频道时，yt-dlp 自动处理所有条目。
用户可通过参数控制：
- `playlist_start` / `playlist_end`：指定下载范围
- `playlist_items`：指定具体条目（如 `"1,3,5-10"`）

### 7.6 下载任务管理

下载可能耗时较长，需要异步任务管理：

| 状态 | 说明 |
|------|------|
| `pending` | 任务已创建，等待执行 |
| `downloading` | 正在下载 |
| `completed` | 下载完成 |
| `failed` | 下载失败 |

MCP 工具返回 `task_id`，可通过 `download_status` 查询进度。

---

## 8. 项目结构

```
yt-dlp-mcp/
├── pyproject.toml                # uv 项目配置
├── uv.lock                       # 锁定依赖版本
├── Dockerfile
├── docker-compose.yml
├── docs/
│   ├── design.md                 # 本文档
│   └── implementation-plan.md    # 实施计划
├── src/
│   └── yt_dlp_mcp/
│       ├── __init__.py
│       ├── __main__.py           # 入口: python -m yt_dlp_mcp
│       ├── server.py             # MCP Server 定义与注册
│       ├── config.py             # 配置管理 (env vars)
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── playback.py       # play, playback_control, playback_status
│       │   ├── info.py           # get_info, list_formats
│       │   ├── search.py         # search
│       │   ├── cast.py           # list_devices, cast
│       │   ├── download.py       # download, download_status, list_downloads
│       │   └── cookies.py        # set/get/delete/list_cookies
│       └── core/
│           ├── __init__.py
│           ├── ytdlp.py          # YoutubeDL 封装 (仅公开 API)
│           ├── mpv.py            # mpv 子进程 + IPC 控制
│           ├── download.py       # 下载任务管理、目录校验
│           ├── cookies.py        # cookie 文件管理
│           └── cast/
│               ├── __init__.py
│               ├── dlna.py       # DLNA 发现与推送
│               ├── airplay.py    # AirPlay 发现与推送
│               └── chromecast.py # Chromecast 发现与推送
└── tests/
    ├── test_ytdlp.py
    ├── test_mpv.py
    ├── test_download.py
    ├── test_tools.py
    └── test_cast.py
```

---

## 9. 依赖管理

### 9.1 yt-dlp 安装方式

通过 PyPI 安装，不从源码构建：

```bash
uv pip install "yt-dlp[default,curl-cffi,deno]"
```

| Extra | 用途 |
|-------|------|
| `default` | brotli, certifi, mutagen, pycryptodomex, requests, urllib3, websockets, yt-dlp-ejs |
| `curl-cffi` | 浏览器 TLS 指纹模拟（反爬虫） |
| `deno` | JavaScript 运行时（YouTube 签名解密必需） |

不安装 `secretstorage`（容器内无 Gnome keyring）。

### 9.2 系统依赖

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    mpv \
    pulseaudio-utils \
    && rm -rf /var/lib/apt/lists/*
```

| 包 | 用途 |
|---|------|
| `ffmpeg` | yt-dlp 后处理、流合并、stdout 管道输出 |
| `mpv` | 音频播放、PulseAudio 输出、IPC 控制 |
| `pulseaudio-utils` | `pactl` 等工具（可选，用于 sink 管理） |

### 9.3 Python 依赖

```toml
[project]
dependencies = [
    "mcp>=1.0",
    "async-upnp-client>=0.40",
    "pyatv>=0.14",
    "pychromecast>=14.0",
    "yt-dlp[default,curl-cffi,deno]>=2025.0",
]
```

---

## 10. 容器配置

### 10.1 Dockerfile

```dockerfile
FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    mpv \
    pulseaudio-utils \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src/ src/
RUN uv sync --frozen --no-dev

VOLUME ["/data/cookies", "/data/store"]

ENV PULSE_SERVER="" \
    MCP_TRANSPORT=sse \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8080 \
    COOKIE_DIR=/data/cookies \
    STORE_DIR=/data/store \
    MPV_SOCKET=/tmp/mpv-socket

ENTRYPOINT ["uv", "run", "python", "-m", "yt_dlp_mcp"]
```

### 10.2 docker-compose.yml

```yaml
services:
  yt-dlp-mcp:
    build: .
    ports:
      - "8080:8080"
    environment:
      PULSE_SERVER: "tcp:${PA_HOST:-host.docker.internal}:4713"
      MCP_TRANSPORT: sse
      COOKIE_DIR: /data/cookies
      STORE_DIR: /data/store
    volumes:
      - cookie_data:/data/cookies
      - store_data:/data/store
    network_mode: host  # 投屏设备发现需要 host 网络

volumes:
  cookie_data:
  store_data:
```

> **注意**：`network_mode: host` 是投屏设备发现 (SSDP/mDNS) 所必需的。
> 后续可通过独立的 DLNA/AirPlay bridge 服务消除此依赖，届时可改用 bridge 网络。

### 10.3 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PULSE_SERVER` | (空) | PulseAudio 服务器地址，如 `tcp:host:4713` |
| `MCP_TRANSPORT` | `sse` | MCP transport 类型，当前仅支持 `sse` |
| `MCP_HOST` | `0.0.0.0` | SSE transport 监听地址 |
| `MCP_PORT` | `8080` | SSE transport 监听端口 |
| `COOKIE_DIR` | `/data/cookies` | cookie 文件存储目录 |
| `STORE_DIR` | `/data/store` | 下载产物存储目录 |
| `MPV_SOCKET` | `/tmp/mpv-socket` | mpv IPC socket 路径 |

---

## 11. MCP 工具定义

### 11.1 播放控制

#### `play`

播放指定 URL 的音频。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `url` | string | 是 | 媒体页面 URL |
| `format` | string | 否 | yt-dlp format string，默认 `bestaudio/best` |
| `volume` | integer | 否 | 音量 0-100，默认不改变 |

返回：`{status, title, duration, format_info}`

#### `playback_control`

控制当前播放。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `action` | string | 是 | `pause` / `resume` / `stop` / `seek` / `volume` |
| `value` | number | 否 | seek: 秒数; volume: 0-100 |

返回：`{status, position, duration}`

#### `playback_status`

获取当前播放状态。

返回：`{status, title, url, position, duration, volume, format}`

### 11.2 信息获取

#### `get_info`

获取媒体元信息（不下载）。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `url` | string | 是 | 媒体页面 URL |

返回：`{title, description, uploader, duration, thumbnails, tags, ...}`

#### `list_formats`

列出所有可用格式。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `url` | string | 是 | 媒体页面 URL |

返回：`[{format_id, ext, acodec, vcodec, abr, vbr, filesize, protocol}, ...]`

### 11.3 搜索

#### `search`

搜索媒体。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | 是 | 搜索关键词 |
| `source` | string | 否 | 搜索源: `youtube`(默认), `soundcloud`, `bilibili`, `dailymotion`, `google` |
| `max_results` | integer | 否 | 最大结果数，默认 10 |

返回：`{entries: [{title, url, duration, uploader, thumbnail}, ...]}`

### 11.4 投屏

#### `list_devices`

发现局域网内投屏设备。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `protocol` | string | 否 | 过滤协议: `dlna`, `airplay`, `chromecast`, `all`(默认) |
| `timeout` | number | 否 | 发现超时秒数，默认 5 |

返回：`{devices: [{id, name, protocol, model, ip}, ...]}`

#### `cast`

投屏播放。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `url` | string | 是 | 媒体页面 URL |
| `device_id` | string | 是 | 目标设备 ID (来自 list_devices) |
| `format` | string | 否 | 格式选择，默认选择最佳兼容格式 |

返回：`{status, device, media_url, cast_url}`

### 11.5 Cookie 管理

#### `set_cookies`

设置站点 cookie。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `site` | string | 是 | 站点域名，如 `youtube.com` |
| `cookies` | string | 是 | Netscape 格式的 cookie 文本 |

返回：`{site, cookie_count, expires}`

#### `get_cookies`

查询站点 cookie 状态。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `site` | string | 是 | 站点域名 |

返回：`{site, cookies: [{domain, name, expires}, ...]}`

#### `delete_cookies`

删除站点 cookie。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `site` | string | 是 | 站点域名 |

返回：`{site, deleted: true}`

#### `list_cookie_sites`

列出已存储 cookie 的所有站点。

返回：`{sites: [{site, cookie_count, last_modified}, ...]}`

### 11.6 下载

#### `download`

下载媒体到 store 目录。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `url` | string | 是 | 媒体页面 URL（支持单个视频或播放列表） |
| `subdir` | string | 是 | 子目录名（在 STORE_DIR 下创建） |
| `format` | string | 否 | yt-dlp format string，默认 `bestaudio/best` |
| `playlist_items` | string | 否 | 播放列表条目选择，如 `"1,3,5-10"` |

返回：`{task_id, status, subdir}`

#### `download_status`

查询下载任务状态。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `task_id` | string | 是 | 下载任务 ID |

返回：`{task_id, status, progress, files: [{path, size, info_json}], error?}`

#### `list_downloads`

列出 store 目录中的下载内容。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `subdir` | string | 否 | 筛选子目录，不传则列出所有 |

返回：`{items: [{subdir, files: [{name, size, has_info_json, downloaded_at}]}, ...]}`

#### `get_download_info`

读取已下载条目的元信息文件。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `subdir` | string | 是 | 子目录名 |
| `filename` | string | 是 | 文件名（不含扩展名） |

返回：info.json 的完整内容（标题、描述、上传者、时长、格式等）

---

## 12. 需确认的设计决策

以下事项需要用户决策：

### 12.1 无需修改 yt-dlp 源码

经分析，所有需求均可通过 yt-dlp 公开 API 实现，**无需修改 yt-dlp 源码**：

| 需求 | 公开 API | 说明 |
|------|---------|------|
| 音频优先格式选择 | `format: 'bestaudio/best'` | 内置 format string |
| 获取流 URL | `extract_info(url, download=False)` | 返回 `info['url']` |
| stdout 管道输出 | `outtmpl: '-'` | 内置支持 |
| 搜索 | `extract_info('ytsearch:N:q', download=False)` | 内置搜索提取器 |
| cookie 管理 | `cookiefile: '/path/to/file'` | Netscape 格式文件 |
| 列出所有格式 | `extract_info` 返回 `info['formats']` | 完整格式列表 |
| 下载到指定目录 | `outtmpl: '/path/%(title)s.%(ext)s'` + `download()` | 内置模板 + 下载 |
| 写入元信息 | `writeinfojson: True` | 自动生成 `.info.json` |
| 播放列表范围 | `playlist_items: '1,3,5-10'` | 内置播放列表控制 |

### 12.2 投屏库成熟度评估

#### `async-upnp-client` (DLNA)

> GitHub: https://github.com/StevenLooman/async_upnp_client

| 指标 | 数据 |
|------|------|
| GitHub Stars | 54 |
| 最近发布 | v0.48.1 (2026-08-24)，2026 年已发布 6 个版本 |
| PyPI 下载量 | 351 万总计，月均约 16.8 万 |
| Open Issues | 2 |
| PyPI 状态 | 5 - Production/Stable |
| 维护者 | 1 人 (StevenLooman) |
| 主要使用者 | Home Assistant DLNA 集成 |

评估：社区规模小但维护活跃，仅 2 个 open issue 表明稳定性良好。作者自述"未完整实现 UPnP 设备架构的所有功能"，不同设备固件的兼容性需实测。作为 Home Assistant 的核心依赖，有足够的生产环境验证。

#### `pyatv` (AirPlay)

> GitHub: https://github.com/postlund/pyatv

| 指标 | 数据 |
|------|------|
| GitHub Stars | 1,170 |
| 最近发布 | v0.18.0 (2026-06-19) |
| PyPI 下载量 | 303 万总计，月均约 15 万 |
| Open Issues | 254 |
| PyPI 状态 | 4 - Beta |
| 维护者 | 1 人 (postlund) |
| 文档 | 完整文档站 pyatv.dev |
| 主要使用者 | Home Assistant Apple TV 集成 |

评估：社区规模中等，文档完善，但 254 个 open issue 较多。Apple 协议逆向工程的固有复杂性导致协议变更（tvOS 更新）可能引入兼容性问题。9 年开发仍为 Beta 状态反映 Apple 生态的持续适配挑战。作为 Home Assistant 核心依赖，主流 AirPlay 场景可用。

#### 风险缓解

- 两个库均为 Home Assistant 核心集成的依赖，有大规模生产验证
- 投屏异常时向调用方返回明确错误信息，不影响核心播放体验
- 后续可通过独立的 DLNA/AirPlay bridge 服务解耦，降低对 yt-dlp-mcp 容器的网络要求

### 12.3 潜在风险点

| 风险 | 影响 | 缓解 |
|------|------|------|
| 投屏时媒体 URL 有时效性 | DLNA 设备可能播放失败 | 返回错误信息，由调用方处理 |
| DRM 内容 | 无法播放/投屏 | 明确不支持 |
| PulseAudio TCP 延迟 | 播放体验 | 调整 mpv `--audio-buffer` |
| mpv 子进程崩溃 | 播放中断 | 进程监控 + 自动重启 |
| 投屏需要 host 网络 | 容器安全隔离降低 | 后续通过独立 bridge 服务消除依赖 |
