# Inkwell（墨匣）

Inkwell（墨匣）是一个从零构建的个人本地 AI 小说写作工作台。项目独立建仓并长期维护，仅将 OpenFic 作为产品灵感与功能参照对象，不承接其历史代码与架构包袱。

## 技术栈总览

- **后端**：Python 3.14 + FastAPI + `sqlite3`（WAL 模式 + `PRAGMA user_version` + FTS5）+ 自建 `httpx` 协议优先模型层 + 自研 `async` Agent Loop
- **前端**：TypeScript + React（与个人其他项目统一工具链基线）
- **前后端通信**：REST + 原生 SSE（Server-Sent Events）
- **本机服务**：独立 `service/` 生命周期与状态协议（启停查及 `/health` 探针），对接本机 Service Desk

## 当前阶段

已完成 M0 后端核心与 M1-A HTTP/SSE 服务层。作品内容、待审变更、会话运行、提供商与全局设置可通过本地 API 使用；前端工作台尚未实现。

## 本地启动

在仓库根目录执行：

```powershell
uv sync --frozen
uv run python -m backend serve --host 127.0.0.1 --port 8765
```

打开 `http://127.0.0.1:8765/docs` 查看交互式 API 文档，`/health` 返回就绪状态。`--port 0` 可由操作系统分配端口，实际地址见启动日志。`frontend/dist` 存在时同源托管静态文件与前端页面路由；不存在时正常运行纯 API 服务。

数据目录可通过 `INKWELL_DATA_DIR` 或 `--data-dir` 指定，默认使用当前用户的数据目录（Windows 为 `%LOCALAPPDATA%/Inkwell`）。数据库、加密密钥、附件、服务运行记录和日志均存于该目录。`INKWELL_HOST` 与 `INKWELL_PORT` 可设置默认监听参数；命令行参数优先。默认监听回环地址，当前服务没有远程用户认证，不应直接暴露到公网。

后台生命周期管理使用固定端口，所有命令须使用同一组数据目录、host 和 port 参数：

```powershell
uv run python -m service start --json
uv run python -m service status --json
uv run python -m service restart --json
uv run python -m service stop --json
```

`status` 始终输出 Service Desk v1 JSON。运行记录位于数据目录的 `runtime/`，日志位于 `logs/`；`stop` 校验进程身份后请求后端优雅退出，不接管同端口的未知进程。此阶段不提供备份与恢复命令。

## 错误信息

模型调用失败时，HTTP 响应的 `error`、SSE `run_done.error` 与运行历史中的 `error` 使用相同结构：`category`、`message`、`retryable`、`status_code`、`retry_after_seconds`。例如 `auth` 表示鉴权失败，`rate_limit` 表示限流，`context_overflow` 表示上下文超限；`message` 是本地生成的安全提示，不透传提供商原始错误正文。`status_code` 是上游状态码，模型发现接口自身仍返回 HTTP 502。运行事件与历史保留兼容字段 `error_message`，内容与 `error.message` 一致；重试事件 `attempt_reset` 同样携带 `error`，`reason` 使用安全提示。

分类随运行持久化，刷新或重启后仍可查询。数据库会自动升级旧表；旧失败记录没有分类时返回 `unknown` 和通用提示，不回显旧原始错误。422 响应的 `detail` 数组保留 `loc`、`type`、`msg`，便于前端定位字段；输入值、校验上下文、自定义请求头键和未知字段名不回显。

## 开发验证

```powershell
uv run ruff check backend service tests
uv run ruff format --check backend service tests
uv run pyright
uv run pytest -q -p no:cacheprovider
```

端到端测试使用进程内 ASGI、`httpx.MockTransport` 和 `.tmp/20261126-tests` 下的独立临时数据目录，不调用真实模型或启动服务子进程。

## 文档

- [产品设计与边界](docs/product-design.md)
- [架构设计与技术铁律](docs/architecture.md)
