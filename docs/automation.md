# 命令行与 AI 自动化

项目提供共享同一转换核心的三种调用方式：GUI、无界面 CLI、Python API。
源码 CLI 不需要 PySide6；独立 `FFmpegUI-cli.exe` 不需要安装 Python。
两者均需要可用的 FFmpeg / ffprobe，默认读取应用设置；未配置工具路径时使用 PATH。

## 独立程序

在 PowerShell 中：

```powershell
$cli = "C:\Users\STRIX\Desktop\FFmpegUI\dist\FFmpegUI\FFmpegUI-cli.exe"
& $cli --help
& $cli formats --json
& $cli probe "C:\media\input.mp4" --json
& $cli convert "C:\media\input.mp4" --format webm --video-encoder libvpx-vp9 --audio-encoder libopus --output-dir "C:\media\output" --json
& $cli convert "C:\media\a.mp4" "C:\media\b.mp4" --preset x264-hq --concurrency 2 --output-dir "C:\media\output" --json
```

`FFmpegUI-cli.exe` 和 GUI 共用同一目录的 `_internal`，分发时复制整个 `dist/FFmpegUI` 文件夹。
`FFmpegUI.exe` 是窗口程序；需要机器可读结果时使用控制台入口 `FFmpegUI-cli.exe`。

## 从源码运行

在仓库目录执行：

```powershell
python -m app.cli --help
python -m app.cli formats --json
python -m app.cli convert "C:\media\input.mp4" --format mkv --video-encoder libx265 --audio-encoder aac --quality 24 --json
python -m app.cli test-encoder hevc_qsv --json
```

常用预设 ID：`copy-mp4`、`x264-hq`、`x264-compress`、`scale-1080`、`scale-720`、`audio-mp3`、`gif`。
`--preset` 与自定义格式/编码参数互斥；`--output-dir` 可简写为 `--out-dir`。
未传输出目录时输出到源目录。已有文件自动避让；始终以结果中的 `output_path` 获取真实路径。
CLI 不修改保存的 GUI 设置，显式软件编码也不受全局 GPU 偏好替换。

## 给其他 AI 的 JSON 请求

保存 UTF-8 文件 `job.json`：

```json
{
  "action": "convert",
  "inputs": ["C:/media/input.mp4"],
  "output_dir": "C:/media/output",
  "options": {
    "format_id": "webm",
    "video_encoder": "libvpx-vp9",
    "audio_encoder": "libopus",
    "quality": 30,
    "audio_bitrate": 128
  },
  "concurrency": 1
}
```

```powershell
& $cli run --request "C:\jobs\job.json" --json
python -m app.cli run --request "C:\jobs\job.json" --json
```

也可通过 `run --request -` 从 stdin 传一个 JSON 对象，默认 `run` 就读取 stdin。
所有相对输入/输出路径均相对于调用进程当前工作目录，未按请求文件所在目录重定位。

动作及字段：

| action | 字段 | 成功结果 |
|---|---|---|
| `formats` | 无 | `formats`：当前可用常见输出、兼容编码器；硬件条目不代表驱动可用 |
| `capabilities` | 无 | `capabilities`：实际 FFmpeg 的 muxer / encoder 列表 |
| `probe` | `inputs` 非空路径数组 | `media`：媒体信息数组 |
| `test-encoder` | `encoder`，可选 `kind`：video/audio/subtitle | `ok`、`detail` |
| `convert` | `inputs`，可选 `options` 或 `preset`、`output_dir`、`concurrency` | `results`、`cancelled` |

高级 `options` 字段：

| 字段 | 类型与单位 | 默认 |
|---|---|---|
| `format_id` | `formats` 返回的格式 ID | `mp4` |
| `video_encoder` / `audio_encoder` | 编码器名称，或 `copy` / `none` | 容器适用的软件编码器 |
| `quality` | 整数 0–51，越低质量越高；各编码器采用对应映射 | 23 |
| `video_bitrate` | 整数 kbps，0 使用质量模式 | 0 |
| `audio_bitrate` | 整数 kbps，0 使用编码器默认；具体范围取决于编码器 | 192 |
| `width` / `height` | 偶数像素，0 保持或按比例计算 | 0 |
| `fps` | 数字，0 保持；否则 0.1–240 | 0 |
| `sample_rate` | 整数 Hz，0 保持 | 0 |
| `channels` | 整数声道数，0 保持 | 0 |

`concurrency` 为 1–4，默认 1；`inputs` 不允许重复文件。
未知字段、错误类型、不能组合的容器/编码器会被拒绝。转换前校验整批输入，失败不会只执行半批。
输出目录在校验通过后创建；原文件和已有输出不会被覆盖。
高级转换只输出第一条视频/音轨，不保留字幕、附件或其他轨道；无音轨的视频输出会略过音频。
显式硬件编码会先试编码，通过仍不能保证所有分辨率、像素格式或驱动状态都受支持。

## 输出约定

默认（包括不传 `--json` 时）stdout 只包含一个 UTF-8 JSON 对象。`--help` 为普通帮助文本。
添加 `--progress` 后，进度和日志以逐行 JSON（NDJSON）写入 stderr，stdout 仍只保留最终结果。

成功示例：

```json
{
  "ok": true,
  "action": "convert",
  "results": [{
    "input": "C:/media/input.mp4",
    "status": "done",
    "output_path": "C:/media/output/input.webm",
    "error": "",
    "percent": 100.0,
    "output_size": 123456,
    "took_ms": 1500
  }],
  "cancelled": false
}
```

| 退出码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 工具启动、媒体探测或实际任务失败 |
| 2 | 命令参数、JSON 或请求字段/组合错误 |
| 130 | 用户取消 |

运行中的转换可用 Ctrl+C 请求取消并清理临时文件。API 使用 `threading.Event` 请求取消。
在预检查阶段取消时，尚未创建转换任务，`results` 为空数组，也不会创建输出目录。
部分批量任务可能在取消前完成，结果中会如实保留它们的输出。

## Python 或 AI 工具调用

通过子进程调用独立 CLI，无需导入本项目：

```python
import json
import subprocess

request = {
    "action": "convert",
    "inputs": ["C:/media/input.mp4"],
    "output_dir": "C:/media/output",
    "options": {"format_id": "mkv", "video_encoder": "libx265", "audio_encoder": "aac"}
}
result = subprocess.run(
    [r"C:\Users\STRIX\Desktop\FFmpegUI\dist\FFmpegUI\FFmpegUI-cli.exe", "run"],
    input=json.dumps(request, ensure_ascii=False),
    capture_output=True, encoding="utf-8"
)
payload = json.loads(result.stdout)
if result.returncode != 0:
    raise RuntimeError(payload)
outputs = [item["output_path"] for item in payload["results"]]
```

也可在仓库可导入的 Python 环境中直接调用：

```python
from app.api import execute

result = execute({
    "action": "convert",
    "inputs": ["C:/media/input.mp4"],
    "preset": "copy-mp4"
})
```

`execute()` 同步返回字典；参数错误抛 `ValueError`，工具探测、硬件试编码或输出目录创建失败抛 `RuntimeError`，实际任务失败返回 `ok=False`。
可传 `on_event=callback` 和 `cancel_event=threading.Event()`；回调可能来自工作线程，需要线程安全。
有本地执行工具的 AI 可直接调用 CLI；远程 AI 服务需由其执行环境访问本机文件和程序。
