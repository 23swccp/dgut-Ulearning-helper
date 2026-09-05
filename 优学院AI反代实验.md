# 优学院 AI 反代实验

本实验位于 `codex/ulearning-ai-bridge` 分支，保持与主程序、现有 Agent CLI、签到和刷课逻辑隔离。当前目标是先验证课程 AI 的可复用性；没有把模型输出直接连接到程序操作。

## 已确认的协议

- AI 页面位于 `aijx.dgut.edu.cn` 的跨域 frame 中。
- 对话接口为 `POST /api/kbChat/chat`，响应为 SSE (`text/event-stream`)。
- `sessionId` 与 `requestId` 由网页本地生成，不依赖预创建接口。
- 当前课程使用 `modelId=1`、首次请求 `sessionSign=1`；课程与助手标识从活动 frame 读取。
- 活动 frame 的短期授权、浏览器 Cookie、User-Agent 和 Referer 均只在内存中使用，不写配置或日志。
- 课程暴露的 instruction 与 instructionGraph 当前均为空数组。这不能证明服务端不存在统一的隐藏系统提示。
- `toolsContentDTOS` 是服务器下发工具调用执行后的结果回传字段，并非客户端自定义工具声明入口。

## 运行

先由主程序启动调试浏览器，在优学院课程中打开 AI 助手，然后执行：

```powershell
python tools/ulearning_ai_bridge_server.py
```

服务仅监听 `127.0.0.1:8786`。启动时会输出一次 JSON，其中包含临时 `baseUrl` 和 `apiKey`。关闭进程后该密钥失效。

当前提供：

- `GET /health`
- `POST /v1/chat/completions`
- OpenAI 风格的非流式响应
- `system`、`assistant`、`user` 文本消息折叠为一次课程 AI 请求

当前明确不提供：

- 流式本地响应
- 图片和文件消息
- 客户端自定义 tools
- 模型自动执行签到、刷课或答题操作
- GUI 入口、开机自启或发布包集成

## 安全边界

服务只允许带启动时临时 Bearer 密钥的本机请求。请求正文不会由 HTTP 服务写日志，上游授权值的对象表示也已隐藏。

程序操作应沿用现有 Agent 工具的校验、任务所有权和状态机制。后续若增加工具调度，应先让模型生成候选调用，再由本地白名单解析、参数校验和确认策略决定是否执行；不能把任意模型文本当命令运行。

`yxy_capture_fixed.py` 和它生成的原始抓包可能含 Cookie、Token、用户与课程信息，仅用于本机调查，不属于该实验的提交内容。

## 下一阶段

1. 给本地接口增加 SSE 流式输出。
2. 设计“模型提出工具调用、本地 Agent 层验证并执行、结果回传模型”的独立调度协议。
3. 添加显式实验开关和 GUI 页面；关闭开关时不加载本模块。
4. 验证授权失效后的重新发现与恢复，不保存长期凭据。
