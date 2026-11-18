# 罕见病多中心登记数据受控分析与结果审查平台

六家医院协作的罕见病登记数据分析平台。各中心数据**不出院**：统计请求被编排到
中心本地代理执行，只有聚合统计量进入受控工作区；输出表经小样本抑制与结果审查
后才可发布，发布报告引用完整运行版本（规则版本、批次快照、审查决定、内容哈希）。

## 架构

```
┌────────────┐   ┌─────────────────────────────────────────────┐
│ React 前端  │──▶│ FastAPI 后端（编排运行 / 审查 / 发布）        │
└────────────┘   │  PostgreSQL：元数据、运行快照、审查记录        │
                 └───────┬─────────────────────────────────────┘
                         │ HTTP（仅下发规则+统计请求，仅回传聚合量）
        ┌────────┬───────┼────────┬────────┬────────┐
        ▼        ▼       ▼        ▼        ▼        ▼
     agent-C1 agent-C2 agent-C3 agent-C4 agent-C5 agent-C6   ← 中心本地执行模拟器
     （每个 agent 只读本中心 registry.csv；C2 结局用本地编码、
       C5 缺实验室指标字段、C6 为小样本中心）
```

- **数据管理员**：登记中心批次、变量字典与可用范围，锁定批次（锁定后不可变）。
- **研究者**：编辑队列规则（并发编辑走条件级三方合并）、提交统计请求、创建输出表。
- **结果审查员**：审批规则版本；对小样本/可差分推回的输出做合并、抑制或拒绝；
  批准后输出即发布，报告含完整溯源与哈希。

关键语义：

- 运行启动时**快照各中心已锁定批次**；运行中替换批次只影响新运行。
- 中心执行失败或缺变量映射 → 标记为**覆盖缺口**（`missing_mapping`/`failed`），
  不计为零例，总计只汇总成功中心并在结果中显式列出未纳入范围。
- 输出表任一单元格 `0 < n < k`（默认 k=5）或**唯一隐藏格可由合计差分推回**时，
  审查队列阻止放行，须合并分组、抑制单元格（必要时连合计一起抑制）或拒绝。
- 两名研究者并发改同一规则：后到保存收到 **409 + 条件级差异 + 自动合并建议**，
  确认合并后才能落库；已进入审核的排除条件不能被后一次保存静默抹掉。

## 一键启动（Docker Compose）

```bash
docker compose up --build
```

- 前端：http://localhost:8080 （后端 API：http://localhost:8000）
- 首次启动自动：建库建表 → 生成六中心虚构数据 → 注册并锁定首批次 → 写入演示账号与一条已批准规则。

C2 批次更新演示（替换数据 → 登记新批次 → 新运行自动引用）：

```bash
docker compose run --rm datagen python -m center_agent.datagen --root /data --center C2 --seed 43
# 然后用 admin 登录 → 数据与批次 → C2「从中心同步」→「锁定」
```

## 本地开发启动

预装运行时：Python 3.12（`/opt/python-3.12`）、Node 22（`/opt/node-22`）、
PostgreSQL 18（`127.0.0.1:5432`，库/用户/密码均为 `ccdev`）。

```bash
# 1) 依赖（首次）
/opt/python-3.12/bin/python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt -r center_agent/requirements.txt
cd frontend && npm install && cd ..

# 2) 生成虚构中心数据（首次）
PYTHONPATH=center_agent .venv/bin/python -m center_agent.datagen --root data/centers

# 3) 一键启动 6 个中心代理 + 后端（:8000）
bash scripts/dev.sh            # 追加 --frontend 可同时起 vite 开发服务器(:5173)

# 4) 停止
bash scripts/stop.sh
```

后端启动时自动建表并种子（用户/中心/字典/映射/锁定批次/一条已批准规则）。
前端开发模式 `npm run dev`（代理 /api 到 8000）；或 `npm run build` 后由后端直接托管。

## 测试账号

| 账号 | 密码 | 角色 |
| --- | --- | --- |
| `admin` | `admin123` | 数据管理员 |
| `researcher1` / `researcher2` | `research123` | 研究者 |
| `reviewer` | `review123` | 结果审查员 |

## 验证方式（本题验收场景）

### 自动化验证（已实际执行）

```bash
.venv/bin/python -m pytest backend/tests -q     # 17 项单元/接口测试
.venv/bin/python scripts/smoke.py               # 16 项端到端冒烟（需平台已启动）
```

冒烟覆盖：四角色登录、六中心锁定批次、全量运行、C5 缺字段→覆盖缺口而非零例、
批次替换后旧运行快照不变/新运行用新批次、运行对比、并发保存 409+条件级差异+
确认合并、在审排除条件删除需确认、小样本输出被拦截、合并+抑制后放行、
发布报告溯源完整且哈希校验一致。

### 手工核对路径

1. **中心批次更新**：`admin` 登录 → 数据与批次 → C2「从中心同步」→「锁定」；
   旧运行详情批次快照不变，新运行 C2 引用新批次；运行对比页可见差异。
2. **并发改规则**：两个浏览器分别用 `researcher1`/`researcher2` 打开同一规则，
   先后保存 → 后者看到条件级差异弹窗 →「采用自动合并结果」→ 再保存；
   若合并结果删掉了在审排除条件，会再要求显式确认，且原在审版本保留。
3. **单中心缺字段**：新建运行勾选「实验室指标均值」→ 运行状态为
   「完成（有覆盖缺口）」，C5 标记 `missing_mapping`，总计 = 5 家中心之和。
4. **小样本输出**：用规则「RD03 且出生年 ≥ 2005」按 路径+性别 分组运行
   （C/F=4、C/M=2 小于 k=5）→ 提交输出表 → `reviewer` 审查：直接批准被拒 →
   合并 C 两组 → 抑制剩余小格 → 批准 → 已发布报告中可见完整溯源与哈希。

## 目录结构

```
backend/          FastAPI 后端（app/：模型、编排、隐私分析、路由；tests/：pytest）
center_agent/     中心本地执行模拟器（datagen 虚构数据生成、executor 本地计算、server HTTP 封装）
frontend/         React + Vite（规则编辑、运行对比、审查、报告页面）
scripts/          dev.sh / stop.sh 本地启停、smoke.py 端到端验证
docker-compose.yml 一键部署（db + 6×agent + backend + frontend）
```

## 环境变量（后端）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | 本地 ccdev 库 | SQLAlchemy DSN |
| `SECRET_KEY` | dev 值 | 令牌 HMAC 密钥，生产必改 |
| `SMALL_CELL_K` | `5` | 小样本抑制阈值 |
| `RUN_EXECUTION` | `sync` | `sync` 同步执行；`async` 后台执行 |
| `CENTER_AGENTS` | 本地 9101-9106 | 中心代理地址映射（JSON） |
| `SEED_ON_STARTUP` | `1` | 空库时自动种子 |
