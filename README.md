# yt-dlp-mcp

MCP (Model Context Protocol) 服务，封装 yt-dlp 能力，支持音频播放、搜索、下载、投屏。

## 能力

- **音频播放**：通过 PulseAudio 实时播放媒体音频
- **搜索**：支持 YouTube、SoundCloud、Bilibili 等多平台搜索
- **下载**：下载媒体到指定目录，自动生成元信息文件
- **投屏**：支持 DLNA、AirPlay、Chromecast 设备
- **Cookie 管理**：持久化存储站点认证信息

## 快速开始

### Docker 部署

```bash
# 构建镜像
docker compose build

# 启动服务
docker compose up -d

# 查看日志
docker compose logs -f

# 停止服务
docker compose down
```

服务启动后监听 `http://0.0.0.0:8080/mcp`（Streamable HTTP transport）。

MCP 客户端连接地址：`http://<host>:8080/mcp`

## 配置

环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PULSE_SERVER` | (空) | PulseAudio 服务器地址，如 `tcp:host:4713` |
| `MPV_SOCKET` | `/tmp/mpv-socket` | mpv IPC socket 路径 |
| `YTDLP_MCP_TRANSPORT` | `streamable-http` | MCP transport 类型（streamable-http/stdio） |
| `YTDLP_MCP_HOST` | `0.0.0.0` | SSE 监听地址 |
| `YTDLP_MCP_PORT` | `8080` | SSE 监听端口 |
| `YTDLP_MCP_COOKIE_DIR` | `/data/cookies` | Cookie 存储目录 |
| `YTDLP_MCP_STORE_DIR` | `/data/store` | 下载存储目录 |
| `YTDLP_MCP_MPV_AUDIO_DEVICE` | (空) | mpv 音频输出设备，如 `pulse/default` 或 `null`（测试用） |

### 音频输出配置

容器中没有默认音频设备。需要配置 `PULSE_SERVER` 连接到宿主机的 PulseAudio 服务器，或设置 `YTDLP_MCP_MPV_AUDIO_DEVICE=null` 进行测试（不实际播放音频）。

**连接到 PulseAudio：**

1. 在宿主机上启动 PulseAudio 网络服务：
   ```bash
   pactl load-module module-native-protocol-tcp auth-anonymous=1
   ```

2. 在 docker-compose.yml 中配置：
   ```yaml
   environment:
     PULSE_SERVER: "tcp:host.docker.internal:4713"
   ```

**测试模式（无音频输出）：**
```yaml
environment:
  YTDLP_MCP_MPV_AUDIO_DEVICE: "null"
```

## MCP 工具

| 工具 | 说明 |
|------|------|
| `play` | 播放媒体音频 |
| `playback_control` | 控制播放（暂停/恢复/停止/跳转/音量） |
| `playback_status` | 获取播放状态 |
| `get_info` | 获取媒体元信息 |
| `list_formats` | 列出可用格式 |
| `search` | 搜索媒体 |
| `download` | 下载媒体 |
| `download_status` | 查询下载进度 |
| `list_downloads` | 列出已下载内容 |
| `get_download_info` | 读取下载元信息 |
| `list_devices` | 发现投屏设备 |
| `cast` | 投屏播放 |
| `set_cookies` | 设置站点 Cookie |
| `get_cookies` | 查询 Cookie |
| `delete_cookies` | 删除 Cookie |
| `list_cookie_sites` | 列出已存 Cookie 站点 |

## 网络说明

投屏功能（DLNA/AirPlay/Chromecast）需要 `network_mode: host` 以发现局域网设备。

## 开发

```bash
# 安装依赖
uv sync

# 运行测试
uv run pytest tests/

# 本地启动
YTDLP_MCP_COOKIE_DIR=/tmp/cookies YTDLP_MCP_STORE_DIR=/tmp/store uv run python -m yt_dlp_mcp
```
