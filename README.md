# breeze-qq-bot

一个**可以用语音回你私聊**的 QQ AI 机器人：NapCat + OneBot11 接消息，本地 ollama 或任意 OpenAI 兼容 API 生成回复，再用 **BreezeBlue 官方 TTS** 合成你克隆的音色，转成 QQ 能播的 silk 语音发回去。

- 只处理**私聊文字消息**，好友/用户白名单外的消息一律不理
- 回复自动限长（默认 500 字），TTS 文本再单独限长（默认 80 字）
- TTS 或 silk 转码失败时**自动降级为纯文字**，不会把消息弄丢
- 单进程 asyncio 常驻，依赖只有 `websockets` / `httpx` / `python-dotenv`（+ 可选 `pilk`）

## 效果链路

```
QQ 好友发消息
   ↓ NapCat 反向 WebSocket（ws://127.0.0.1:8080/onebot/v11/ws）
本程序收到 OneBot11 事件（post_type=message, message_type=private）
   ↓ 白名单 / 去重 / 限速检查 → 提取 text 消息段
对话模型（ollama /api/chat 或 OpenAI 兼容 /chat/completions）
   ↓ 回复按句截断到 MAX_REPLY_CHARS
BreezeBlue TTS（POST /v1/text-to-speech/{voice_id}）→ mp3
   ↓ pilk --tencent 转码
send_private_msg 发 record 段（file:///…silk）
   ↓ 任一步失败
降级为纯文字 send_private_msg
```

## 目录结构

| 文件 | 作用 |
| --- | --- |
| `main.py` | 入口：串起事件处理、对话、TTS、语音发送、定时清理、信号退出 |
| `onebot_ws.py` | OneBot11 反向 WebSocket **服务端**，收事件、发动作（echo 配对） |
| `chat_client.py` | 对话层，`ollama` / `openai` 两种 provider 可切换，带每用户上下文 |
| `tts_client.py` | BreezeBlue TTS 调用：并发信号量、429 退避、402/422 不重试 |
| `silk.py` | mp3 → silk（pilk `--tencent`）转码 + `data/voice/` 旧文件清理 |
| `text_utils.py` | 提取 text 段、按句截断、TTS 文本清洗、去重、单用户限速 |
| `config.py` | 从 `.env` / 环境变量读全部配置（只读 dataclass） |
| `logger_setup.py` | 控制台 + `logs/` 按天滚动日志 |
| `test_tts.py` | 单独验证 API Key 和 voice_id 能不能出 mp3 |
| `.env.example` | 配置模板（每项都有中文注释） |
| `requirements.txt` | Python 依赖 |

运行时自动生成：`logs/`（日志）、`data/voice/`（mp3 与 silk，默认保留 6 小时后清理）。

## 一、Windows 从零部署

### 1. 装 Python 3.10+

到 <https://www.python.org/downloads/> 下载安装包，安装时**务必勾选 “Add python.exe to PATH”**。
装完打开 PowerShell 验证：

```powershell
python --version
```

能打印 `Python 3.10.x` 及以上就行。

### 2. 装 ffmpeg

silk 转码和部分音频处理需要 ffmpeg。

1. 到 <https://www.gyan.dev/ffmpeg/builds/> 下载 `ffmpeg-release-essentials.zip`
2. 解压到例如 `C:\ffmpeg`，确认存在 `C:\ffmpeg\bin\ffmpeg.exe`
3. 把 `C:\ffmpeg\bin` 加进系统环境变量 Path（设置 → 系统 → 关于 → 高级系统设置 → 环境变量 → Path → 新建）
4. 新开一个 PowerShell 窗口执行 `ffmpeg -version`，能打印版本号即成功

### 3. 装 pilk（mp3 → silk）

`pilk` 是带 C 扩展的包，Windows 上有时 `pip install` 会失败。按下面顺序试：

```powershell
pip install pilk
```

**如果上面报错**（缺编译工具、`Microsoft Visual C++ 14.0 required` 之类），二选一：

- 方案 A：装编译器后重试
  ```powershell
  # 以管理员身份打开 PowerShell
  winget install Microsoft.VisualStudio.2022.BuildTools --override "--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
  pip install --upgrade setuptools wheel
  pip install pilk
  ```
- 方案 B：直接用官方预编译的 pilk 命令行
  1. 到 <https://github.com/foyoux/pilk/releases> 下载 `pilk.exe`
  2. 放到例如 `C:\pilk\pilk.exe`，并把 `C:\pilk` 加入 Path
  3. 执行 `pilk --help` 验证

本程序两种方式都支持：先找 PATH 里的 `pilk` 命令，找不到再用 `python -m pilk`。
**彻底装不上也能跑**：只是语音会转码失败 → 自动降级成纯文字，机器人照常工作。

### 4. 装项目依赖

```powershell
cd C:\path\to\breeze-qq-bot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

激活后命令行前面会出现 `(.venv)`，以后每次启动机器人前都要先 `activate`。

### 5. 配置 NapCat 反向 WebSocket

1. 装好并登录 NapCat（QQ 需保持登录状态）。
2. 打开 NapCat 的 WebUI（默认 <http://127.0.0.1:6099>），进 **网络配置 → 新建 → WebSocket 服务器**（有的版本叫「反向 WebSocket」）。
3. 填：
   - **地址/主机**：`127.0.0.1`
   - **端口**：`8080`（和 `.env` 的 `WS_PORT` 一致）
   - **路径**：`/onebot/v11/ws`（和 `.env` 的 `WS_PATH` 一致，**少一个斜杠都会连不上**）
   - **消息格式**：`array`
   - 如配置了 Token，本程序不带 token，建议**留空**
4. 保存并启用。本程序启动后会看到日志类似：
   ```
   NapCat 已连接：ws://127.0.0.1:8080/onebot/v11/ws
   ```
   没这条就去 NapCat 那边点「重连」，或检查端口/路径是否一致。

> 本程序是 **ws 服务端**，NapCat 主动来连它，所以要先启动本程序再让 NapCat 连接（NapCat 会自己重试）。

### 6. 填 .env

```powershell
copy .env.example .env
notepad .env
```

至少确认这几项：

| 配置项 | 说明 |
| --- | --- |
| `ALLOWED_USERS` | **必填**，你自己的 QQ 号，多个用英文逗号分隔。留空 = 拒绝所有人（日志会打 WARNING） |
| `CHAT_PROVIDER` | `ollama` 或 `openai` |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | 用 ollama 时填，先 `ollama pull qwen2.5:7b` |
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL` | 用云端时填，base_url 要带 `/v1` |
| `BREEZE_API_KEY` | BreezeBlue 控制台的 API Key |
| `BREEZE_VOICE_ID` | 克隆音色后得到的 voice_id |
| `ENABLE_TTS` | `true` 发语音；`false` 只发文字（省额度） |

### 7. 启动

```powershell
.venv\Scripts\activate
python main.py
```

看到 `breeze-qq-bot 启动` 和 `NapCat 已连接` 就可以用白名单里的号去私聊它了。按 `Ctrl+C` 优雅退出。

想后台常驻可用 `nssm`、Windows 任务计划，或 `pythonw main.py`（注意 `pythonw` 没有控制台，看日志要去 `logs/`）。

## 二、Linux / Mac 简述

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
sudo apt install -y ffmpeg          # Mac: brew install ffmpeg
pip install pilk                    # 失败也不影响纯文字运行
cp .env.example .env && nano .env
python main.py
```

路径都对之后同样让 NapCat 连 `ws://127.0.0.1:8080/onebot/v11/ws` 即可。

## 三、克隆音色拿 voice_id

1. 登录 <https://breeze.blue> 控制台（`BREEZE_API_BASE` 默认指向 `https://api.breeze.blue/v1`）。
2. 进 **音色 / Voices → 克隆音色（Clone Voice）**。
3. 上传一段**干净人声**：10~30 秒、无背景音乐、无混音、44.1kHz 左右最佳；多人对话录音会克隆出「混合音」。
4. 克隆完成后列表里会有一个 **voice_id**（以控制台实际显示为准），复制到 `.env` 的 `BREEZE_VOICE_ID`。
5. 保存后先跑一次验证脚本（见下节），听到 mp3 就是成功。

## 四、单独验证 TTS（test_tts.py）

不用启动 QQ 也能验证 Key 和音色：

```powershell
python test_tts.py
python test_tts.py "自定义测试文本，随便说点什么。"
```

它会：

1. 读取 `.env` 里的 `BREEZE_API_KEY` / `BREEZE_VOICE_ID`，缺了直接报错退出；
2. 调一次 TTS，结果存到 `data/voice/test_tts_<时间戳>.mp3`；
3. 顺便试一次 pilk 转码，成功会额外生成 `.silk`（说明 QQ 语音链路通）。

播放器打开 mp3 能听到你的音色就万事俱备了。

## 五、常见报错对照表

| 现象 / 日志关键字 | 原因 | 处理 |
| --- | --- | --- |
| `ALLOWED_USERS 为空` 且机器人不理人 | 白名单没填 | 在 `.env` 填自己的 QQ 号后重启 |
| 一直没出现 `NapCat 已连接` | 端口/路径不一致，或 NapCat 没启用反向 WS | 核对 `WS_PORT`=8080、`WS_PATH`=`/onebot/v11/ws`、消息格式为 `array` |
| `路径不匹配，已拒绝连接` | NapCat 填的路径和 `WS_PATH` 不同 | 两边改成完全一致（含开头 `/`） |
| `端口被占用` / `address already in use` | 8080 已被别的程序占用 | 改 `.env` 的 `WS_PORT`（如 8081），NapCat 同步改 |
| `TTS 鉴权失败（401/403）` | API Key 错、过期或无权限 | 重新从控制台复制 `BREEZE_API_KEY` |
| `余额不足（HTTP 402）` | BreezeBlue 额度用完 | 去控制台充值；期间可 `ENABLE_TTS=false` 只用文字 |
| `参数或音色异常（422）` | voice_id 不存在、克隆音频质量差、文本异常 | 换成列表里真实的 voice_id 或重新克隆；本条不重试 |
| `触发限流 429` 反复出现 | 请求太密集 | 保持 `TTS_CONCURRENCY=1`，把 `MIN_INTERVAL_SEC` 调大（5~10） |
| `未找到 pilk，请先执行 pip install pilk` | 没装 pilk 或不在 PATH | 见上文「装 pilk」；装不上也能跑，只是发纯文字 |
| `pilk 转码失败` / `未生成有效 silk 文件` | 缺 ffmpeg 或 mp3 损坏 | 装 ffmpeg 并确认 `ffmpeg -version` 可用 |
| 语音提示「文件已过期/无法播放」 | 保留时间太短被清理，或 NapCat 读不到本地文件 | 调大 `VOICE_KEEP_HOURS`；确认 NapCat 与本程序同一台机器 |
| `连接 ollama 失败` | ollama 没启动或地址不对 | 执行 `ollama serve`，核对 `OLLAMA_BASE_URL` 与 `ollama list` |
| `对话 API 返回 HTTP 401` | 云端 Key 错 | 检查 `OPENAI_API_KEY` 与 `OPENAI_BASE_URL`（要带 `/v1`） |
| 回复被截断得太短 | 字数上限太小 | 调大 `MAX_REPLY_CHARS`（默认 500）、`TTS_MAX_CHARS`（默认 80） |

## 六、注意事项

- `.env` 里有密钥，**不要提交到 git**（仓库已用 `.gitignore` 排除）。
- 语音按「句号」截断：回复很长时语音只念前 80 字，剩余不再补发文字，避免语音+长文字同时刷屏。
- `MIN_INTERVAL_SEC`（防刷）与 `TTS_CONCURRENCY`（防限流）是两道闸，不建议同时调小/调大。
- 日志按天切分在 `logs/bot.log`，保留 30 天。
- 本程序用 NapCat 登录的 QQ 号发消息，请遵守平台规则，只与自己的号或已授权的人私聊。
