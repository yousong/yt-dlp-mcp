# yt-dlp MCP Service — 实施计划

## Phase 1: 项目骨架与基础框架

**目标**：建立项目结构、依赖管理、MCP Server 可启动。

**产出**：
- `pyproject.toml` (uv 项目配置)
- `src/yt_dlp_mcp/` 基础结构
- MCP Server 可启动 (SSE transport)
- `get_info` 工具可用

**任务**：
1. 初始化 uv 项目 (`uv init`)
2. 配置 `pyproject.toml` 依赖
3. 实现 `server.py` — MCP Server 定义与 transport 配置
4. 实现 `core/ytdlp.py` — YoutubeDL 封装
   - `extract_info(url, download=False)` 封装
   - format selection 默认值
   - cookie 文件路径注入
5. 实现 `tools/info.py` — `get_info` 工具
6. 编写基础测试

**验证**：
```bash
uv run python -m yt_dlp_mcp
# MCP client 连接后调用 get_info 返回媒体元信息
```

---

## Phase 2: 音频播放

**目标**：通过管道模式实现音频实时播放。

**产出**：
- `core/mpv.py` — mpv 子进程管理
- `tools/playback.py` — play / playback_control / playback_status

**任务**：
1. 实现 `core/mpv.py`
   - mpv 子进程启动 (stdin 管道 + IPC socket)
   - IPC 协议封装 (JSON 命令/响应)
   - 进程生命周期管理 (启动/停止/崩溃检测)
   - 事件监听 (播放进度、结束、错误)
2. 实现管道连接
   - yt-dlp `outtmpl='-'` stdout → mpv stdin
   - 异步管道管理 (asyncio.subprocess)
3. 实现 `tools/playback.py`
   - `play`: 启动 yt-dlp → mpv 管道
   - `playback_control`: 通过 mpv IPC 控制
   - `playback_status`: 查询 mpv IPC 状态
4. PulseAudio 配置验证
   - `PULSE_SERVER` 环境变量传递
   - mpv `--audio-device` 配置

**验证**：
```bash
# 设置 PULSE_SERVER 后
# MCP 调用 play(url) → 音频从 PulseAudio 服务器输出
# MCP 调用 playback_control(action="pause") → 暂停
# MCP 调用 playback_status() → 返回播放位置、状态
```

---

## Phase 3: 搜索与格式选择

**目标**：搜索功能、格式列表。

**产出**：
- `tools/search.py` — search 工具
- `tools/info.py` 补充 — list_formats 工具

**任务**：
1. 实现 `tools/search.py`
   - 封装 `extract_info('ytsearch:N:query', download=False)`
   - 多搜索源映射 (youtube → ytsearch, soundcloud → scsearch, ...)
   - `extract_flat=True` 快速返回
   - 结果格式化 (标题、URL、时长、缩略图)
2. 实现 `list_formats`
   - 从 `extract_info` 返回的 `info['formats']` 提取
   - 标注音频/视频属性、码率、协议

**验证**：
```bash
# MCP 调用 search(query="lofi hip hop", source="youtube", max_results=5)
# → 返回 5 条 YouTube 搜索结果
# MCP 调用 list_formats(url) → 返回所有可用格式列表
```

---

## Phase 4: Cookie 管理

**目标**：cookie 持久化存储与管理。

**产出**：
- `core/cookies.py` — cookie 文件管理
- `tools/cookies.py` — cookie MCP 工具

**任务**：
1. 实现 `core/cookies.py`
   - Netscape cookie 文件读写
   - 按站点域名索引 (`/data/cookies/{site}.txt`)
   - cookie 解析 (域名、名称、过期时间)
2. 实现 `tools/cookies.py`
   - `set_cookies`: 写入 cookie 文件
   - `get_cookies`: 读取并返回 cookie 列表
   - `delete_cookies`: 删除 cookie 文件
   - `list_cookie_sites`: 列出已存站点
3. 集成到 `core/ytdlp.py`
   - 自动根据 URL 域名匹配 cookie 文件
   - 注入 `cookiefile` 到 ydl_opts

**验证**：
```bash
# set_cookies(site="youtube.com", cookies="... Netscape format ...")
# → 写入 /data/cookies/youtube.com.txt
# get_info(url="https://youtube.com/watch?v=...") → 自动使用 cookie
```

---

## Phase 5: 下载与存储

**目标**：实现媒体下载到 store 目录，含元信息持久化。

**产出**：
- `core/download.py` — 下载任务管理、目录校验
- `tools/download.py` — download / download_status / list_downloads / get_download_info

**任务**：
1. 实现 `core/download.py`
   - 目录名校验（禁止路径穿越）
   - 下载任务队列与状态管理 (pending/downloading/completed/failed)
   - 进度回调 (yt-dlp progress_hooks)
   - 任务 ID 生成与索引
2. 实现 `tools/download.py`
   - `download`: 创建下载任务，异步执行
   - `download_status`: 查询任务进度与产物列表
   - `list_downloads`: 扫描 store 目录，列出已下载内容
   - `get_download_info`: 读取并返回 `.info.json` 内容
3. yt-dlp 集成
   - `outtmpl` 设置为 `{STORE_DIR}/{subdir}/%(title)s-%(id)s.%(ext)s`
   - `writeinfojson: True` 写入元信息
   - `writethumbnail: True` 下载缩略图
   - `playlist_items` 支持播放列表范围选择
4. 编写测试
   - 目录名校验测试（合法/非法名称）
   - 下载流程测试（mock yt-dlp）
   - 元信息文件读取测试

**验证**：
```bash
# download(url="https://...", subdir="music/lofi") → {task_id: "abc123"}
# download_status(task_id="abc123") → {status: "completed", files: [...]}
# list_downloads(subdir="music/lofi") → 列出目录下所有条目
# get_download_info(subdir="music/lofi", filename="artist-track-abc123") → info.json 内容
```

---

## Phase 6: 投屏

**目标**：DLNA / AirPlay / Chromecast 设备发现与投屏。

**产出**：
- `core/cast/` — 各协议实现
- `tools/cast.py` — list_devices / cast 工具

**任务**：
1. 实现 `core/cast/dlna.py`
   - SSDP 设备发现 (`async-upnp-client`)
   - 媒体 URL 推送到 DLNA renderer
   - 设备状态查询
2. 实现 `core/cast/airplay.py`
   - mDNS 设备发现 (`pyatv`)
   - AirPlay 媒体推送
3. 实现 `core/cast/chromecast.py`
   - mDNS 设备发现 (`pychromecast`)
   - Cast 媒体推送
4. 实现 `tools/cast.py`
   - `list_devices`: 聚合所有协议的发现结果
   - `cast`: 根据设备协议选择对应推送方式
   - 投屏失败时返回明确错误信息
5. Docker 网络配置
   - `network_mode: host` 配置

**验证**：
```bash
# list_devices() → 返回局域网内 DLNA/AP/CC 设备
# cast(url, device_id) → 设备开始播放
```

---

## Phase 7: 容器化与部署

**目标**：生产就绪的容器镜像。

**产出**：
- `Dockerfile` (多阶段构建)
- `docker-compose.yml`
- 健康检查与日志

**任务**：
1. 优化 Dockerfile
   - 多阶段构建减小镜像体积
   - uv 缓存层优化
   - `.dockerignore` 配置
2. docker-compose 配置
   - 环境变量模板
   - volume 挂载
   - 网络配置
3. 健康检查
   - MCP Server 存活检测
   - mpv 进程健康检测
   - PulseAudio 连接检测
4. 日志
   - 结构化日志 (JSON)
   - 日志级别配置

**验证**：
```bash
docker compose up -d
docker compose logs -f
# MCP client 连接并执行完整功能测试
```

---

## 里程碑总览

| Phase | 名称 | 预估工时 | 关键产出 |
|-------|------|---------|---------|
| 1 | 基础框架 | 1-2 天 | 项目骨架、MCP Server、get_info |
| 2 | 音频播放 | 2-3 天 | mpv 管道播放、播放控制 |
| 3 | 搜索与格式 | 1 天 | search、list_formats |
| 4 | Cookie 管理 | 1 天 | cookie 持久化、自动匹配 |
| 5 | 下载与存储 | 1-2 天 | download、元信息持久化、任务管理 |
| 6 | 投屏 | 2-3 天 | DLNA/AirPlay/Chromecast |
| 7 | 容器化 | 1 天 | Dockerfile、compose、健康检查 |
| **合计** | | **9-13 天** | |

---

## 技术选型汇总

| 领域 | 选型 | 理由 |
|------|------|------|
| 包管理 | uv | 用户指定；快速、现代 |
| yt-dlp 安装 | PyPI `yt-dlp[default,curl-cffi,deno]` | 不从源码构建；功能全面 |
| MCP SDK | `mcp` (Python) | 官方 SDK |
| 音频播放 | mpv (子进程 + IPC) | 全格式支持、PulseAudio 原生支持 |
| 流管道 | yt-dlp stdout → mpv stdin | 兼容所有 yt-dlp 支持的站点/协议 |
| DLNA | `async-upnp-client` | 成熟异步 UPnP 库 |
| AirPlay | `pyatv` | 唯一可用的 Python AirPlay 库 |
| Chromecast | `pychromecast` | Google 官方推荐 Python 库 |
| 容器基础镜像 | `python:3.12-slim` | 体积小、兼容性好 |
