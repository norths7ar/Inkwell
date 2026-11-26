# Inkwell（墨匣）架构与开发指南

本文面向开发者与贡献者，说明 Inkwell（墨匣）的分层架构、核心契约设计原则、本机服务管理方式以及本地开发与验证方法。

## 1. 架构原则与技术栈

### 核心原则

- **轻量、透明、可控**：控制流、模型协议细节、持久化事务与错误分类全部显式可见，避免黑盒框架掩盖底层语义。
- **最小依赖集**：优先使用 Python 标准库与轻量基础组件，不引入与自身控制面重叠的 ORM 或多智能体编排框架。
- **单一事实源**：会话消息、挂起提问、待审变更、修订历史与作品内容统一落入同一个本地 SQLite 数据库，杜绝双写状态漂移。

### 技术栈

- **后端**：Python `3.14` + `uv` + FastAPI + 标准库 `sqlite3`（WAL 模式 + `PRAGMA user_version` + FTS5）+ 自建 `httpx` 协议优先模型层 + 纯 Python `async` Agent Loop
- **前端**：Node.js `26` + `pnpm` + TypeScript + React + REST + 原生 `EventSource`（SSE）
- **本机服务**：独立 `service/` 生命周期 CLI 与 `/health` 探针

## 2. 模型接入层（协议优先）

后端不依赖按厂商封装的第三方 SDK，而是以**网络线协议**作为抽象的最小单位，复用挂载在应用生命周期上的 `httpx.AsyncClient` 连接池：

- **协议驱动**：直接实现薄驱动层——`openai-chat`、`openai-responses` 与 `anthropic-messages`（可按需扩展），统一处理 SSE 流式解码及分层超时（连接超时与流式 chunk 空闲超时）。
- **统一内部表示（IR）**：消息内容显式建模为结构化块（`text`、`reasoning`、`tool_call`、`tool_result`、`image`），完整保留推理链（如 `reasoning_content` / `thinking`）以支持带工具调用的续轮回传。
- **声明式 Preset 与单模型协议覆盖**：提供商配置统一收口基础连接与鉴权元数据；同时支持在单模型维度覆盖传输协议，方便兼容同一聚合服务商下不同系列模型的协议差异。
- **业务域解耦**：模型接入包与小说业务模型、工具逻辑完全隔离。

## 3. Agent Loop 与工具契约

核心运行循环采用纯 Python `async` 实现，不引入外置图状态机或独立 Checkpointer：

- **显式主循环**：按“组装上下文（含模式提示词、全局/单书规则、作用域过滤与压缩摘要覆盖层）→ 协议层流式推理 → 并发执行工具（带并发上限与取消传播）→ 追加结果并检查挂起或终止条件”顺序执行，主循环清晰可控。
- **单点事件扇出**：循环内产出的领域事件（文本增量、思考增量、工具调用、工具结果、提问挂起、错误与重试重置）由单一分发点同时写入 SQLite 与 SSE 通道，保证实时视图与库内历史严格一致。
- **原生挂起、取消与插话**：`ask_user` 等交互挂起直接持久化在业务表中，页面刷新或服务重启后可从库内状态确定性恢复；流式期间原生响应用户取消（及时关闭上游 HTTP 流）与中途插话指令。
- **统一工具契约**：所有工具遵循一致的输入校验与 `ToolFailure` 错误格式，校验失败返回简明可纠错提示而不回显大段正文。工具描述仅说明适用场景、关键参数边界与副作用，工作流纪律统一留在模式提示词中。
- **轻量后台调用**：会话标题生成、长对话上下文压缩（Compaction）等辅助任务直接发起单次后台 LLM 调用，共享后台轻量模型配置与用量记录，不构建 Subagent 调度层。

## 4. 数据存储、检索与备份

全部本地持久化收敛在单个标准库 `sqlite3` 数据库（`inkwell.sqlite3`）及同目录附件存储中：

- **连接与迁移约定**：启用 WAL 模式、`foreign_keys=ON` 与合理的 `busy_timeout`，配合显式手写 SQL Repository 与强类型模型；使用 `PRAGMA user_version` 按有序事务脚本演进 Schema，并保证兼容平滑升级。
- **全文检索（FTS5）**：章节与设定检索基于 SQLite FTS5 虚表，直接服务于用户全书搜索与 AI 上下文检索。
- **软删除回收站**：章节与设定删除优先写入 `deleted_at` 进入回收站，支持在工作台还原或彻底清除。
- **全量备份与双向导入导出**：
  - **本地全量备份**：在设置面板或通过 `/api/backups` 一键生成包含 `inkwell.sqlite3`（一致快照）与 `attachments/` 的完整 ZIP 备份，并支持列表查看、下载与删除。
  - **作品级迁移**：支持按分卷/章节/设定分目录结构的 ZIP 包以及单文件 Markdown / TXT 双向导入导出。

## 5. 前后端通信与错误契约

前后端彻底放弃 Socket.IO，仅使用标准 HTTP REST 与单条全局原生 SSE：

### REST 与单全局 SSE

- **写操作与控制走 REST**：作品/分卷/章节/设定 CRUD、待审应用/拒绝、发送消息启动运行、回答提问、取消、中途插话、备份与导入导出均走标准 REST。
- **单条全局原生 `EventSource("/api/events")`**: 前端全站只维护一条 `EventSource` 连接，事件 `id` 使用字符串形式的递增序号 `seq`。断线重连时由浏览器原生携带 `Last-Event-ID` 申请补发；当超出内存环形缓冲窗口收到 `resync_required` 事件时，前端重拉 REST 状态，并确保不会覆盖浏览器内正在编辑的未保存草稿。

### 统一结构化错误与 422 脱敏

- **模型运行错误契约**：HTTP 响应中的 `error`、SSE `run_done.error` 与持久化运行历史共享同一结构：`category`、`message`、`retryable`、`status_code`、`retry_after_seconds`。常见分类包括 `auth`（鉴权失败）、`rate_limit`（限流）、`context_overflow`（上下文超限）等。`message` 始终是本地生成的安全提示，不直接透传上游原始错误正文；`status_code` 记录上游状态码（模型发现接口失败自身仍返回 HTTP 502）。运行事件与历史同时保留兼容字段 `error_message`（与 `error.message` 一致），重试事件 `attempt_reset` 同样携带结构化 `error` 与脱敏 `reason`。
- **历史兼容与校验脱敏**：旧表自动升级后，历史无分类失败记录统一返回 `unknown` 与通用提示，不回显旧原始错误；FastAPI 422 校验错误响应的 `detail` 数组仅保留 `loc`、`type`、`msg` 用于前端定位字段，不回显用户输入值、校验上下文、自定义请求头键名或未知字段名。

## 6. 前端工作台架构

前端遵循“单外壳常驻、跨页面基元统一”的设计约束，面向桌面浏览器（`1440×900` 及以上视口各面板独立滚动）：

- **单一常驻外壳（Single AppShell）**：全站只维护一套四槽位网格（`48px 一级左轨 | 0↔200~420px 可调宽目录槽 | 内容槽 | 对话槽`）与唯一一个 `<ChatPanel>` 实例，各视图仅切换槽位宽度与展示模式（如进入全屏「讨论」时收起内容槽、展开对话槽），确保流式回复与未发草稿在切栏时不丢失。拖拽分割线调整宽度时临时禁用 CSS 过渡动画以保证跟手。
- **左轨语义分区**：上侧归属作品内容与协作流（`书架`｜`写作`、`角色`、`背景设定`、`提纲`、`笔记`｜分隔线｜`讨论`、`待审变更`、`回收站`、`全书搜索`），底部仅放置`设置`弹窗入口。
- **共享组件基元**：全部二级目录侧栏共用统一 `<DirectoryToolbar>`（无冗余文字标题，搜索框展开后撑满并隐藏右侧动作按钮，统一提供 `+` 与 `...` 槽位）与两行信息列表项；目录/伴随面板折叠按钮对称位于主画布顶栏两端；跨分类多文档标签页栏支持在章节与各类设定间自由切换。
- **贡献者 UI 协作约束**：界面改动与细节对齐准则集中维护在 [AGENTS.md](../AGENTS.md)。

## 7. 本机服务与独立部署约束

### 生命周期 CLI（`service/`）

在仓库根目录通过一致的数据目录、监听地址与端口参数管理后台进程：

```powershell
uv run python -m service start --json
uv run python -m service status --json
uv run python -m service restart --json
uv run python -m service stop --json
```

`status` 输出规范化 JSON；运行状态文件写入数据目录的 `runtime/`，日志写入 `logs/`；`stop` 会校验进程身份后再请求优雅退出，不会误杀同端口的其他未知进程。

### 独立发布预留护栏

为保证项目可平滑打包为单容器或桌面端应用（如 Tauri sidecar / `pywebview`），编码持续遵守以下边界：

- **单一可配置数据目录**：通过 `INKWELL_DATA_DIR` 或 `--data-dir` 指定，默认使用仓库根目录 `data/`；数据库、密钥、附件、备份、运行记录与日志全部收口于此。
- **同源静态托管与相对路径**：前端 API 与 SSE 一律请求相对路径 `/api/...`；当 `frontend/dist` 存在时由 FastAPI 直接同源托管静态页面与前端路由。
- **参数化监听与优雅退出**：支持 `INKWELL_HOST`、`INKWELL_PORT` 及命令行覆盖（支持 `--port 0` 由系统分配端口），关闭时清理 `httpx` 连接池并安全关闭 SQLite WAL。
- **平台逻辑隔离与可插拔凭据加密**：操作系统专属的进程控制逻辑隔离在 `service/` 内，核心后端保持纯跨平台 `pathlib.Path`；凭据加解密通过薄抽象封装，便于在不同宿主环境中替换实现。

## 8. 本地开发与验证

### 后端开发与全套检查

```powershell
uv sync --frozen
uv run ruff check backend service tests
uv run ruff format --check backend service tests
uv run pyright
uv run pytest -q -p no:cacheprovider
```

后端测试采用进程内 ASGI、`httpx.MockTransport` 及 `.tmp/` 下的独立临时数据目录，不发起真实外部模型调用。

### 前端开发与全套检查

```powershell
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

提交或构建发布产物前在 `frontend/` 下运行：

```powershell
pnpm lint
pnpm format:check
pnpm typecheck
pnpm test
pnpm build
```

前端单元测试（Vitest）默认拦截未模拟的 HTTP 请求，核心用例覆盖草稿暂存与显式保存、待审基线冲突（409）、以及单全局 SSE 流式消费与重连重同步。
