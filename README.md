# Seedance Drama Maker · AI 短剧生成引擎

简体中文文档。English version: `README.en.md`

- 当前版本：`2.0.0`
- 技术栈：`FastAPI`、`WebSocket`、多 Agent 协作、`FFmpeg`
- 产品能力：`TOS`、`Seed-Speech`、`Seed-2.1-turbo`、`SeeDream-5.0-pro`、`SeeDance-2.5`
- AI 开发工具：`Trae.ai`、`DeepSeek-V4-Flash-GA`、`GPT-5.5`

## ✨ Seedance 2.5 核心亮点

由 **SeeDance 2.5** 视频模型驱动，一句话即可产出电影级 AI 短剧：

- 🎬 **单镜头最长 30 秒**：单个分镜时长可达 30 秒（6–30 秒可调），长镜头叙事更连贯，最终成片总时长上限 `600` 秒。
- 🖼️ **最多 50 个参考输入**：单任务可融合最多 50 张人物 / 布景参考图，角色与场景跨镜头高度一致。
- 🌍 **14 种语言原生支持**：原生多语种对白与旁白生成，覆盖中、英、日、西等 14 种语言。
- 🎞️ **电影级视听效果**：原生音画同步、自适应画幅比例，输出高保真 `MOV`（H.264/AAC）成片，Web 端可直接边下边播。
- 🤖 **多 Agent 全自动流水线**：剧本 → 参考图库 → 分镜生成 → AI 审核 → 长视频合成，一站式闭环。

## 项目简介

这是一个聚焦于 AI 长视频生成的多 Agent 系统，支持自动模式与手动模式两种工作方式。在保证生成质量的前提下，系统尽量简化人工操作，把用户从繁琐的脚本拆解、角色整理、布景统一、分镜衔接、质量复审和长视频合成中解放出来。

项目适用场景包括但不限于：

- 社交媒体数分钟广告视频生成
- 一句话生成短剧 / 漫剧
- 基于参考图片与语音输入生成连续叙事长视频
- 多个长视频任务并行运行，开箱即用

系统会自动协同生成剧本、角色设定、布景设定、对白、音效约束与分镜视频，并结合 AI 质量审核、自动/手动重生成、相邻分镜平滑过渡控制，完成从一句话需求到最终长视频成片的完整链路。

当前主流程为：

```text
需求输入 -> 剧本生成 -> 参考图库确认 -> 分镜视频生成与审核 -> 最终合成
```

系统重点解决以下问题：

- 剧本、角色设定、布景设定、分镜脚本的统一生成
- 多参考图协同生成，支持人物/角色与布景分流
- 自动模式与手动模式下的一致流程控制
- 视频审核失败后的自动重试与人工接管
- 多语言前端与多浏览器标签页独立任务隔离

## 项目展示

### 生成过程

#### ScreenRecord-0

<img src="./demo/ScreenRecord-0.png" alt="ScreenRecord-0" width="100%" />

#### ScreenRecord-1

请播放：[demo/ScreenRecord-1.mp4](./demo/ScreenRecord-1.mp4)

#### ScreenRecord-2

请播放：[demo/ScreenRecord-2.mp4](./demo/ScreenRecord-2.mp4)

#### ScreenRecord-3

请播放：[demo/ScreenRecord-3.mp4](./demo/ScreenRecord-3.mp4)

### 生成结果

#### DemoVideo-1

请播放：[demo/DemoVideo-1.mp4](./demo/DemoVideo-1.mp4)

#### DemoVideo-2

请播放：[demo/DemoVideo-2.mp4](./demo/DemoVideo-2.mp4)

#### DemoVideo-3

请播放：[demo/DemoVideo-3.mp4](./demo/DemoVideo-3.mp4)

## 使用的 BytePlus 产品

- `TOS`
  用于上传、存储和分发用户上传素材、参考图、分镜视频、尾帧图和最终视频。
- `Seed-Speech`
  用于语音转文字，支持通过录音输入视频创作需求。
- `Seed-2.1-turbo`
  用于分镜视频审核，评估人物/角色一致性、物理规律和脚本语义一致性。
- `SeeDream-5.0-pro`
  用于人物/角色参考图与布景参考图生成和重生成。
- `SeeDance-2.5`
  用于分镜视频生成，支持单镜头 6–30 秒、最多 50 个参考输入、14 种语言原生对白与原生音画同步。

以上能力来自 [byteplus.com](https://www.byteplus.com/)。

## AI 开发工具

- `Trae.ai`：代码开发、排查、工程整理
- `DeepSeek-V4-Flash-GA`：需求梳理、方案讨论、文档辅助
- `GPT-5.5`：代码修改、逻辑整理、调试、文档重写

## 运行前提准备

### 环境要求

- Python `3.9+`
- 本机可执行 `ffmpeg` 与 `ffprobe`
- 网络可访问 BytePlus / ModelArk / Seed-Speech / TOS

### 服务开通要求

在运行本程序前，需要先准备以下账号和能力：

- 开通 `ModelArk` 对应模型，并获取 `apikey`
- 开通 `Seed-Speech`，并获取 `appid` 与 `apikey`
- 开通 `TOS`，并获取 `AK/SK`
- 在 `TOS` 中建立你自己的公共可读存储桶 `bucket`
- 在 `config.yaml` 中配置这些服务的 `endpoint` 等非敏感项；真实的 `apikey` / `AK` / `SK` / `appid` / `bucket` 一律写入项目根目录的 `.env`

### 凭证治理规则

- 所有模型 `apikey`、对象存储 `AK/SK`、语音识别凭证、`endpoint` / `bucket` 等敏感信息，只允许在 `.env` 中设定。
- `config.yaml` 仅保留 `${VAR}` 占位符，运行时由 `app/config.py` 从 `.env` 注入，业务代码不得硬编码任何真实凭证。
- `.env` 已被 `.gitignore` 忽略，禁止提交到版本库；可复制 `.env.example` 作为模板填写真实值。
- 如果不同模型使用不同 `apikey`，也只能放在 `.env` 的对应变量中。

### 最小配置示例

敏感凭证写入项目根目录的 `.env`（可复制 `.env.example`）：

```bash
# BytePlus TOS 认证
BYTEPLUS_AK=your-byteplus-ak
BYTEPLUS_SK=your-byteplus-sk
TOS_REGION=ap-southeast-1
TOS_BUCKET=your-public-bucket
TOS_ENDPOINT=https://tos-ap-southeast-1.bytepluses.com

# ModelArk
MODELARK_API_KEY=your-modelark-api-key
MODELARK_BASE_URL=https://ark.ap-southeast.bytepluses.com/api/v3

# Seed-Speech ASR
SPEECH_APPID=your-seed-speech-appid
SPEECH_API_KEY=your-seed-speech-api-key

# 大模型 Endpoint / API Key
MAIN_AGENT_ENDPOINT=your-main-agent-endpoint
MAIN_AGENT_API_KEY=your-main-agent-api-key
SCRIPT_ENDPOINT=your-script-endpoint
SCRIPT_API_KEY=your-script-api-key
IMAGE_ENDPOINT=your-image-endpoint
IMAGE_API_KEY=your-image-api-key
VIDEO_ENDPOINT=your-video-endpoint
VIDEO_API_KEY=your-video-api-key
VIDEO_REVIEW_ENDPOINT=your-video-review-endpoint
VIDEO_REVIEW_API_KEY=your-video-review-api-key
```

`config.yaml` 中对应字段保留占位符即可，运行时会自动从 `.env` 注入：

```yaml
byteplus:
  ak: "${BYTEPLUS_AK}"
  sk: "${BYTEPLUS_SK}"

modelark:
  api_key: "${MODELARK_API_KEY}"
  base_url: "${MODELARK_BASE_URL:-https://ark.ap-southeast.bytepluses.com/api/v3}"

server:
  port: 8888

models:
  main_agent:
    endpoint: "${MAIN_AGENT_ENDPOINT}"
    api_key: "${MAIN_AGENT_API_KEY}"
  # 其余模型段同理，均使用 ${VAR} 占位符
```

## 功能与流程介绍

### 1. 需求输入

支持以下输入方式：

- 文字描述
- 上传参考图片
- 录音输入并调用 `Seed-Speech` 转文字

### 2. 剧本生成

`ScriptAgent` 负责生成：

- 标题、风格、背景
- 角色设定
- 布景设定
- 分镜脚本

当前逻辑约束：

- 视频总时长上限：`600` 秒
- 单个分镜时长范围：`6`–`30` 秒（适配 `SeeDance-2.5`）
- 分镜数量上限：`50`
- 角色设定上限：`30`
- 布景设定上限：`40`
- 相邻分镜要求连贯承接，但不得重复复述
- 大模型原始响应会输出到服务端日志

### 3. 参考图库生成

`ImageAgent` 会生成统一参考图库，分为：

- 人物/角色参考图库
- 布景参考图库

当前支持的上传规则：

- 前端一次最多上传 `30` 张参考图
- 其中人物/角色最多 `10` 张
- 布景最多 `20` 张
- 每张上传图片都需要命名

上传后的两种行为：

- 勾选“使用原图”
  不再对上传图和命名做大模型处理，直接按类型进入参考图库。
- 不勾选“使用原图”
  系统会基于上传图和剧本中的角色/布景设定生成统一参考图库。

只有在所有参考图生成或重生成任务都完成后，系统才会进入下一步倒计时或等待继续指令。

### 4. 分镜视频生成

`VideoAgent` 生成每个分镜视频时，会结合：

- 人物/角色参考图
- 布景参考图
- 当前分镜脚本
- 当前用户风格要求

关键规则：

- 自动失败重试次数受 YAML 配置限制
- 人工点击“重新生成”不计入自动失败预算
- 仅在“当前分镜与上一分镜布景重叠且脚本承接成立”时，才会截取上一分镜最后一帧作为分镜首帧参考
- 该首帧会做人脸检测，并对识别出的人脸做纯黑遮挡后再上传到 `TOS`
- 视频提示词默认追加“限定：不生成背景音乐”，除非用户需求中明确指定背景音乐风格

### 5. 视频审核与流程推进

`VideoReviewAgent` 使用 `Seed-2.1-turbo` 做分镜视频审核。

审核维度：

- 人物/角色一致性
- 物理世界规律
- 与分镜脚本语义一致

当前默认审核配置：

- `video_review.pass_threshold = 60`
- `video_review.max_retries = 2`
- `video_review.default_mode = auto`

流程规则：

- 自动模式：审核失败后自动重试；达到上限后自动选择最高分结果继续流程
- 手动模式：审核仍会执行，但不自动重试；用户可以手动继续下一步
- 只要仍有自动或人工“重新生成”任务未完成，系统不会提前进入下一步 countdown

### 6. 合成

`MergeAgent` 与 `FFmpegService` 负责最终合成。

当前默认合成配置：

- 视频输出格式：`MOV`（`video_generation.output_format = mov`）
- `SeeDance-2.5` 生成任务与参考视频统一使用 `MOV`，`FFmpeg` 直接处理 `MOV` 并输出合并后的 `MOV`
- 合成时启用 `-movflags +faststart`，将 `moov atom` 前置，Web 端可边下边播
- `merge.temporary_edge_trim = off`
- `trim_previous_end_frames = 0`
- `trim_next_start_frames = 1`

视频与审核全部完成后：

- 自动模式：先显示倒计时，再自动进入合成
- 手动模式：先显示倒计时提示，随后等待聊天区继续指令进入合成

## 提示词示例

### 自动模式

`以女生heibi为主角，香港中环写字楼场景，生成美漫风格，都市怪谈悬疑恐怖故事。比例9:16，时长30秒。auto`

### 手动模式

`以大学校园为场景，Q版动漫风格，学生的励志故事。比例4:3，时长90秒`

### 支持上传素材

- 可上传 `参考图片`
- 可上传 `语音`
- 可混合上传人物/角色图和布景图

## 系统架构

```text
Browser UI
  |- static/index.html
  |- static/css/style.css
  |- static/js/app.js
  |- static/i18n/*.json

FastAPI App
  |- app/main.py
  |- REST API
  |- WebSocket manager

MainAgent
  |- ScriptAgent
  |- ImageAgent
  |- VideoAgent
  |- VideoReviewAgent
  |- MergeAgent

Services
  |- LLMService
  |- ASRService
  |- TOSService
  |- FFmpegService
```

### 关键设计点

- 所有敏感凭证统一写入 `.env`，`config.yaml` 仅保留 `${VAR}` 占位符，运行时由 `app/config.py` 注入
- 前后端界面支持 `zh-CN`、`zh-TW`、`en`、`ja`、`es`；`SeeDance-2.5` 视频对白原生支持 14 种语言
- 视频链路统一输出 `MOV`，`TOS` 归一化 `Content-Type` 保证 Web 端正常播放
- 项目与浏览器连接绑定，支持多标签页独立运行
- 参考图库、视频生成、审核、合成都采用渐进式前端展示

## 使用说明

### 安装依赖

```bash
pip install -r requirements.txt
```

### 启动服务

```bash
python3 -m app.main
```

默认访问地址：

```text
http://localhost:8888
```

### 常用指令

- `开始生成` / `开始` / `生成`
- `继续` / `下一步` / `确认`
- `重新生成` / `重做` / `regen`
- `退回-剧本` / `退回-参考图` / `退回-视频`
- `一键生成` / `autorun` / `auto`

## 开源使用规则

- Copyright (c) 2026 Alex Wang
- @author Alex Wang <https://github.com/wanglongxiao>
- @contact <https://www.linkedin.com/in/alexwanglx/>
- 转载、分发和二次修改时，请保留项目内的作者与开源说明注释。
- 请勿提交真实 `apikey`、`AK/SK`、私有桶名、私有素材或生成结果到公开仓库。

## 版本与变更记录

- 当前版本说明见 `CHANGELOG.md`
- 英文版本见 `CHANGELOG.en.md`
