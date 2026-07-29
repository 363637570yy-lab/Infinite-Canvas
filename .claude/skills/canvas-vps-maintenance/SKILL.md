---
name: canvas-vps-maintenance
description: 仅当 Infinite-Canvas 用户明确要求连接服务器、查看服务器 / 服务 / 容器状态、执行远程命令、部署、重启、备份或回滚时使用。
---

# Infinite-Canvas 服务器维护适配

## 边界

- 本 Skill 只提供 Infinite-Canvas 的服务器目标、profile、项目检查和成功断言；SSH、传输、长任务、备份与回滚由全局 `$vps-maintenance` 执行。
- 全局源唯一，位于 `${CODEX_HOME:-~/.codex}/skills/vps-maintenance/`；不支持技能调用语法时按该路径读取 `SKILL.md`、`references/` 并直接执行 `scripts/vps_runner.py`，不得另存副本。
- 不负责业务审计、浏览器断言、协议字段设计或本地测试范围。
- 未获得本轮服务器授权时不得连接或执行命令；读取服务器事实不受此限。

## 事实源

**本仓库是公开仓库，具体服务器事实一律不写入本文件。** 别名、目录、容器、端口、挂载数、备份与回滚命名全部从下列本地位置读取：

- 项目 profile：`.codex-project/vps-maintenance/maintenance-profiles/infinite-canvas.md`（已被 `.gitignore` 排除）。
- 只读巡检：`.codex-project/vps-maintenance/readonly-checks/infinite-canvas-status.sh`。
- 服务器事实与凭据位置：全局 `SERVER_INFO.md` 档案，允许整文件明文读取；凭据值只在用户明确索取该值时输出。
- profile 缺失或候选冲突时**停止**；不得猜测别名、目录、容器、端口或凭据，也不得把它们补写进仓库内文件。

## 不可触碰（与具体路径无关的硬约束）

- `runtime/`：画布、会话、素材和产物的真实数据，不得覆盖、清理或随部署重建。
- `API/.env`：服务器侧真实上游凭据，不得读出明文、不得覆盖。
- `runtime/data/api_providers.json`：中转站与协议配置，变更前必须单独备份。
- 既有持久化挂载：部署前后 diff 必须为空。

## 操作级别

| 级别 | 范围 |
|---|---|
| `readonly` | 状态、health、容器和脱敏日志查询；不备份、不写维护记录 |
| `mutation` | 配置或重启等写操作；先建立回滚点，完成聚焦复检 |
| `deploy` | 备份、目标提交部署、入口、持久化和脱敏日志完整复检 |

## 执行

1. 确认授权、目标、级别、影响范围和成功条件。
2. 解析 profile，只把任务需要的项目事实传给全局 `$vps-maintenance`。
3. `mutation` / `deploy` 在写入前确认备份或可执行回滚；没有回滚方案则停止。
4. 同一服务器同一时刻只允许一个远程写 owner。
5. 只输出目标别名、命令类别、状态码、数量、必要路径、哈希和脱敏摘要。
6. `mutation` / `deploy` 完成后把实际变更、验证和回滚点写入仓库**外**的维护历史档案；`readonly` 不写维护记录。维护记录不入本仓库。

## 部署断言（含已知陷阱）

必须断言：

- 容器 `running`、重启次数 `0`。
- 持久化挂载数与挂载点 diff 为空（期望值见 profile）。
- 部署文件逐个 git blob 与目标提交相等。
- 部署前先校验线上处于预期的上一个提交。
- `/` 与本次改动到的静态资源返回 `200`；空视频请求返回 `422`。
- 近期日志 `Traceback` / `ERROR` / `500` 计数为 `0`。

**禁止断言**：

- 禁止断言 `/static/*` 引用的 `?v=` 版本号。应用启动时会用 `main.py` 的 cache_version 重写全部静态引用版本串，源码手写值在运行态必然不一致。此坑已重复踩中多次，其中一次触发无谓回滚（历次记录见仓库外 `MAINTENANCE.md`）。该断言不得再写入任何部署脚本。

## 报告

只报告实际执行、验证、跳过项和残余风险；不得输出密码、Token、Cookie、API Key 或连接串。
