# ADR 0001：Gardener 采用持久化 C/S 服务（SQLite + 独立预览缓存）

日期：2026-10-21 · 状态：已采纳 · 依据：Gardener PRD v0.1、决策 D30、D35、D37（`docs/gardener/`）

## 背景
CLAUDE.md 的产品决策（2026-09-24）规定 BioScan 服务保持无状态，状态只存于用户要求的文件。Gardener 首版要求
可持续查询的素材记录、多任务后台队列、暂停/恢复/重启恢复、全局人工 Flag/Star 与 AI 历史分离、集合整理与审阅进度，
并由 UI、CLI、MCP 共用一个后台服务（D37）。这些在无状态服务上无法成立。

## 决定
- 新增 `bioscan.gardener`：一个本机后台服务（FastAPI + 单工作协程），**持久化** 到数据目录下的 SQLite（WAL，单写锁），
  预览缓存放在独立目录（D30）。客户端（Web UI、`gardener` CLI、MCP）只经 HTTP 访问，不直接打开数据库。
- 原 BioScan `/run` 服务及其 CLI 保持无状态、行为不变；Gardener 在进程内复用 `Engine`、`RunQueue`、stages 与
  `cull` reducers，不复制其逻辑。该决定只对 Gardener 取代“stateless”规则。
- 原片只读：Gardener 永不在来源目录写入、移动、改名或删除；导出默认只写一个 XML。
- PRD 中实施前未决项的 v0.1 默认值见 `docs/gardener/contract.md` §9，均可在后续版本更改。

## 后果
- 需要 schema 版本与迁移（`meta.schema_version`）。
- 重启后运行中的任务记为 interrupted 并自动重新排队；人工判断和集合整理不被机器重跑覆盖。
- 默认只监听 127.0.0.1；开放局域网需要另行设计认证（未实现）。
