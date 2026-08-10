# AI 视频生成助手计划与实现基线

- 当前版本：`1.1.0`
- 文档目标：记录当前真实逻辑、关键约束、版本治理与后续验证口径
- 说明：当前仓库没有可回溯的 Git 提交历史，本文件和 `CHANGELOG` 基于现有代码状态与已确认的重要功能演进整理

## 目标

围绕以下主链路维持一个可运行、可扩展、可多语言使用的 AI 视频生成系统：

```text
需求输入 -> 剧本生成 -> 参考图库确认 -> 分镜视频生成与审核 -> 最终合成
```

## 当前实现范围

### 后端 Agent 分工

- `MainAgent`
  管理项目状态、步骤推进、自动/手动模式、重试与多标签页隔离。
- `ScriptAgent`
  生成剧本、角色设定、布景设定、分镜脚本，并做 JSON 容错和质量检查。
- `ImageAgent`
  生成统一参考图库，支持单张参考图重生成与“使用原图”直通。
- `VideoAgent`
  生成分镜视频，按场景选择参考图，并决定是否注入上一分镜尾帧。
- `VideoReviewAgent`
  使用视觉理解模型审核分镜视频。
- `MergeAgent`
  负责在最终阶段触发视频合成。

### 基础服务

- `LLMService`：统一封装大模型调用
- `ASRService`：语音识别
- `TOSService`：对象存储上传与访问路径管理
- `FFmpegService`：抽帧、遮挡、拼接、输出

## 当前配置基线

### 服务与凭证

- 所有 `api_key`、`AK/SK`、`appid` 仅允许在 `config.yaml` 中配置
- 代码侧只读取 `config.yaml`，不应硬编码真实凭证
- 发布前必须把真实凭证替换成占位符

### 当前关键阈值

- `video_generation.total_duration_max = 300`
- `script_generation.max_storyboard_scenes = 25`
- `script_generation.max_characters = 20`
- `script_generation.max_setting_definitions = 40`
- `video_generation.reference_images.max_count = 60`
- `video_generation.reference_images.character_max_count = 20`
- `video_generation.reference_images.scene_max_count = 40`
- `video_generation.reference_images.upload_max_count = 10`
- `video_generation.reference_images.upload_character_max_count = 5`
- `video_generation.reference_images.upload_scene_max_count = 5`
- `video_review.pass_threshold = 60`
- `video_review.max_retries = 2`
- `merge.temporary_edge_trim = off`

## 关键业务规则

### 剧本规则

- 相邻分镜必须顺滑承接，但不能重复表达同一剧情推进点
- 角色设定数量限制与分镜真实主要出场角色口径一致
- 布景设定数量限制与分镜真实使用布景口径一致
- `characters_present` 只统计真实角色，不把动作、语气、发声描述误判为角色
- 当模型返回 JSON 不完整时，解析器需尽可能 salvage 有效分镜而不是直接整段失败

### 参考图库规则

- 人物/角色参考图与布景参考图统一归入同一参考图库输出
- 前端上传限制固定为 `10/5/5`
- 用户勾选“使用原图”时，上传图片与命名直接进入参考图库，不再经过大模型重写
- 参考图库任何自动或人工重生成任务未完成时，不进入下一步倒计时

### 分镜视频规则

- 自动失败重生成次数由 YAML 控制
- 人工点击“重新生成”不计入自动失败预算
- 仅在“前后分镜布景重叠 + 剧情承接成立”时，上一分镜尾帧才会作为分镜首帧参考
- 首帧参考图在上传前需做人脸检测并使用纯黑色遮挡人脸
- 视频提示词末尾默认附加“不生成背景音乐”，除非用户明确指定背景音乐风格

### 审核与流程规则

- 自动模式：审核失败自动重试，达到上限后选最高分结果继续
- 手动模式：审核仍执行，但不自动重试；用户可决定继续或人工重生
- 只要仍有任何重生成任务未完成，就不允许提前进入下一步 countdown
- 视频全部生成并审核完成后，先显示 countdown，再进入合成；手动模式仍需聊天区继续命令

## i18n 与前端输出规则

- 前端 UI 统一由 `static/i18n/*.json` 管理
- 后端提示、报错、流程消息统一由 `app/utils/i18n.py` 管理
- 前端只保留必要的技术性 `console.error`，移除无用调试 `console.log`
- 新增用户可见文本时，必须同步补齐前后端多语言文案

## 版本治理策略

- 仅重大功能变化、流程变化、关键质量修复、明显性能优化才增加版本号
- 文案微调、注释整理、局部无行为变化重构不单独升版本
- 当前将版本统一设定为 `1.1.0`
- 变更记录写入：
  - `CHANGELOG.md`
  - `CHANGELOG.en.md`

## 代码治理策略

### 已完成

- 主要代码文件统一加入开发者与开源规则头注释
- 服务端版本号统一收口到 `app/__init__.py`
- 前端静态资源缓存版本提升到 `20260509a`
- 低风险调试输出已清理

### 保持原则

- 不随意删除功能性代码
- 只清理明确无用的调试残留、临时目录和文档失真内容
- 保持配置键兼容，避免无必要的破坏式命名迁移

## 文档治理策略

README 必须长期覆盖以下内容：

- BytePlus 产品说明：`TOS`、`Seed-Speech`、`Seed-2.0`、`SeeDream-5.0`、`SeeDance-2.0`
- AI 开发工具说明：`Trae.ai`、`Kimi-2.6`、`GPT-5.4`
- 运行前提与服务开通要求
- `config.yaml` 凭证治理要求
- 自动模式与手动模式流程差异
- 上传参考图片 / 语音的使用方法
- 系统架构、使用说明、开源规则
- 自动模式与手动模式示例提示词

## 验证清单

### 静态检查

```bash
python3 -m compileall app
```

### 测试

```bash
python3 -m pytest tests/ -v
```

### 手工验证重点

1. 创建项目并完成剧本生成
2. 验证角色设定、布景设定、分镜数量受 YAML 限制
3. 验证参考图上传 `10/5/5` 限制
4. 验证“使用原图”直通逻辑
5. 验证自动/手动模式下的继续、重生成、countdown 行为
6. 验证上一分镜尾帧仅在符合条件时作为首帧参考
7. 验证视频审核失败与自动重试逻辑
8. 验证最终合成仅在所有任务完成后触发
