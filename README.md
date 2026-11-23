# Inkwell（墨匣）

Inkwell（墨匣）是一个从零构建的个人本地 AI 小说写作工作台。项目独立建仓并长期维护，仅将 OpenFic 作为产品灵感与功能参照对象，不承接其历史代码与架构包袱。

## 技术栈总览

- **后端**：Python 3.14 + FastAPI + `sqlite3`（WAL 模式 + `PRAGMA user_version` + FTS5）+ 自建 `httpx` 协议优先模型层 + 自研 `async` Agent Loop
- **前端**：TypeScript + React（与个人其他项目统一工具链基线）
- **前后端通信**：REST + 原生 SSE（Server-Sent Events）
- **本机服务**：独立 `service/` 生命周期与状态协议（启停查及 `/health` 探针），对接本机 Service Desk

## 当前阶段

已完成产品边界与底层架构的初步定案，项目处于设计冻结与待启动状态，待准备就绪后从后端核心闭环开始搭建。

## 文档

- [产品设计与边界](docs/product-design.md)
- [架构设计与技术铁律](docs/architecture.md)
