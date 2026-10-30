# DS 麦清理 · DS Mac Cleaner

> 一个通用的 macOS「扫描 → 清理 → 记录」本地工具，不是针对某一次 200GB 的临时脚本。

打开 App 时自动开始一次只读扫描；看清本机隐藏数据后，勾选可重建缓存清理；
每次扫描和清理都写入历史数据库。没有后台常驻、没有定时任务、没有 LaunchAgent。

![DS 麦清理概览](docs/overview.png)

## 产品定位

- **通用**：不依赖当前机器的固定数字或目录。每次扫描重新测量 `~/Library`、系统缓存、
  容器、开发缓存、Docker、VM、个人文件，再按同一套安全规则分类。
- **扫描即启动**：用户打开 App / 页面时自动扫描，不需要先点按钮。
  不打开就不扫描，不驻留后台。
- **清理有边界**：只允许删除白名单内的可重建缓存；聊天记录、Docker.raw、VM swap、
  个人文件只报告不建议自动删。
- **记录可追溯**：每次扫描、每次清理写入 Docker Compose 启动的 PostgreSQL；
  Docker 未启动时降级到本机 JSONL。
- **每次都从当前状态出发**：历史记录用于对比，不会拿上一次报告冒充本次结果。

## 启动即扫描

1. 打开 `/Applications/DS Mac Cleaner.app`（或启动台）。
2. App 启动本地 Python 服务并打开 `http://127.0.0.1:8787`。
3. 页面加载后**自动发起一次只读扫描**，触发标记为 `launch`。
4. 扫描完成后显示本机最新报告；点击「重新扫描」可随时刷新。

没有常驻进程、后台定时器、登录项或 LaunchAgent。关闭页面后服务本身不执行扫描；
下次打开页面会重新扫描。扫描只在本地调用 `du` / `df` / `tmutil` 等只读命令。

## 功能

- **只读扫描**：动态测量隐藏目录，生成分类、Top 10、磁盘估算和安全等级。
- **安全清理**：默认勾选可重建缓存；清理前输入 `DELETE`；服务端二次校验路径白名单、
  符号链接、挂载点、真实路径。
- **历史记录**：扫描与清理存入 PostgreSQL，包含时间、释放量、项目明细、错误和触发方式；
  历史页支持分页，默认每页 10 条，可切换 10 / 25 / 50 条。
- **本机优先**：不联网、无 CDN、无遥测、无外部字体；所有数据留在本机。
- **macOS 启动器**：`.app` 安装到 `/Applications`，出现在启动台。
- **响应式 Web UI**：桌面与手机均可查看。

![清理确认](docs/cleanup-confirm.png)

## 快速开始

### 依赖

- macOS 12+（本机开发环境 macOS 15.7.4）
- Python 3.9+
- Docker Desktop（可选，仅历史数据库需要）
- Pillow 仅用于构建 App 图标，`setup.sh` 会安装到项目虚拟环境

### 1. 初始化

```bash
cd ds-mac-cleaner
./scripts/setup.sh
```

它会创建 `.venv`、安装依赖、复制 `.env.example` 为 `.env`，
并在 Docker 可用时启动 PostgreSQL。

### 2. 启动 Web

```bash
./scripts/start-server.sh
# 打开 http://127.0.0.1:8787
```

打开页面即自动扫描；首次通常需 30–60 秒。扫描是只读的。

### 3. 安装到启动台

```bash
./scripts/install-app.sh
```

会把 `DS Mac Cleaner.app` 复制到 `/Applications` 并注册 LaunchServices。
之后在启动台搜索「DS 麦清理」或「DS Mac Cleaner」即可打开。
如果 `/Applications` 不可写，会自动退回到 `~/Applications`。

App 是轻量启动器：启动项目里的本地服务并打开浏览器，然后退出。
项目目录不要删除或移动，否则启动器会失效（重新运行 `install-app.sh` 可修复路径）。

### 不想用 App？

```bash
# 前台运行
./scripts/start-server.sh

# 历史库
docker compose up -d db
docker compose --profile tools up -d adminer   # 可选，http://127.0.0.1:8088
```

## 安全模型

这是产品的核心约束。

1. **扫描零删除**：扫描器只调用只读系统命令。
2. **服务端不信任客户端路径**：前端只提交 `item_id`；服务端从持久化报告取路径并重新校验。
3. **删除白名单**：只允许以下根目录内的子路径：
   - `~/Library/Caches`
   - `~/Library/Logs/DiagnosticReports`
   - `~/Library/Developer/Xcode/DerivedData`
   - `~/Library/Developer/CoreSimulator/Caches`
   - `~/.npm/_cacache`
4. **二次防御**：拒绝符号链接、挂载点、根目录本身、真实路径逃逸；部分删除失败会记录错误，不谎报释放。
5. **确认词 + 本地令牌**：POST 必须带随机本地会话令牌；手动清理 body 中 `confirm` 必须等于 `DELETE`。
6. **只读展示高风险数据**：微信/QQ 容器、Docker.raw、VM swap、Downloads、废纸篓等永远不可勾选。

## 架构

```text
macOS App (Launchpad)
   │  open http://127.0.0.1:8787
   ▼
Python stdlib HTTP server (server/app.py)
   ├── scanner.py    只读测量 → 报告 JSON
   ├── catalog.py    分类 / 风险 / 删除白名单
   ├── cleanup.py    服务端二次校验 + 删除
   ├── history.py    PostgreSQL（psycopg2） + JSONL fallback
   └── web/          原生 HTML/CSS/JS，无构建步骤
          │
          ▼
Docker Compose: postgres:16-alpine (127.0.0.1:55432)
```

目录：

```text
ds-mac-cleaner/
├── analysis/system-data-report.md   # 一次真实扫描的示例报告（非产品默认数据）
├── db/init.sql                      # PostgreSQL schema
├── docker-compose.yml               # 历史库 + 可选 Adminer
├── launcher/                        # .app 启动脚本模板
├── scripts/                         # setup / start / build / install / icon
├── server/                          # Python 后端
├── tests/                           # 删除安全与本地分页测试
├── web/                             # 单页 Web UI
├── dist/DS Mac Cleaner.app          # 构建产物（安装前）
└── docs/                            # 界面截图
```

## 数据库

`docker compose up -d db` 启动 PostgreSQL 16：

| 配置 | 默认值 |
|---|---|
| 地址 | `127.0.0.1:55432` |
| 数据库 | `dsmac` |
| 用户 / 密码 | `dsmac` / `dsmac_local_dev` |
| 数据卷 | `ds-mac-cleaner-db-data` |

表：

- `scans`：每次扫描的磁盘、估算、**精简报告**（不含逐项文件清单）、`trigger`（launch/manual）；
- `cleanup_runs`：清理项、请求字节、实际释放、错误、dry-run 标记。

历史增长策略：

- 页面只取当前页（默认 10 条，最大 50 条），不会一次性加载全部记录；
- 数据库报告在写入时去掉 `items` 明细，单条约 19KB，而不是完整报告的 100KB+；
- `.runtime/scans/` 只保留最近 **60 份**完整 JSON 报告用于导出，更早的记录仍有历史摘要；
- 本地 JSONL 与数据库都可分页读取，Docker 未启动时不会阻塞历史页。

Adminer 可选：

```bash
docker compose --profile tools up -d adminer
open http://127.0.0.1:8088
# System: PostgreSQL, Server: db, Username: dsmac, Password: dsmac_local_dev, DB: dsmac
```

## API 摘要

服务只绑定 `127.0.0.1`，所有 POST 需要 `X-DSMC-Token`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 版本、DB 状态、Docker 可用性 |
| GET | `/api/latest` | 最近一次扫描报告 |
| POST | `/api/scans` | 启动扫描；`trigger` 为 `launch` 或 `manual` |
| GET | `/api/tasks/{id}` | 轮询任务进度/结果 |
| POST | `/api/cleanups` | 清理指定 `item_ids`，需 `confirm: "DELETE"` |
| GET | `/api/history?page=1&page_size=10` | 分页历史；兼容旧 `limit` 参数 |
| GET | `/api/permissions` | 写入探针检查各缓存根目录的真实删除权限 |
| POST | `/api/db/start` | 启动 Compose 历史库 |
| POST | `/api/reveal` | 在 Finder 中显示报告中的路径 |
| GET | `/api/scans/{id}/export` | 下载 JSON 报告 |

示例：

```bash
TOKEN=$(curl -s http://127.0.0.1:8787/ | sed -n 's/.*name="dsmc-token" content="\([^"]*\)".*/\1/p')
curl -s -X POST -H "X-DSMC-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"trigger":"manual"}' http://127.0.0.1:8787/api/scans
```

## 开发与测试

```bash
# 单元测试（不访问真实系统目录）
make test

# 构建 .app
./scripts/build-app.sh

# 安装并打开
./scripts/install-app.sh
```

测试覆盖：路径白名单、符号链接、挂载点、根目录拒绝、越界路径、只读项、dry-run。

代码刻意保持**标准库优先**：

- 后端无 Web 框架；`http.server.ThreadingHTTPServer` 足够本地单用户场景；
- 前端无构建步骤，直接读 `web/`；
- 运行时只有 PostgreSQL 驱动是外部依赖；缺失时自动降级到本地 JSONL。
  Pillow 只用于构建 App 图标。

## 常见问题

**扫描很慢？**  
首次要遍历 `~/Library` 和系统目录，取决于文件数量与磁盘速度。受 TCC 保护的应用容器
可能返回 0 或权限错误，报告会保留已测到的部分。

**为什么微信/QQ 不能勾选？**  
删除容器会丢聊天记录，这不该由通用清理器决定。请用 App 内存储管理。

**为什么 Docker 占了 10GB，但 `ls` 显示 1TB？**  
`Docker.raw` 是稀疏文件，逻辑上限 1TB，实际占用由 `du` 统计。不要直接删。

**清理结果释放 0 字节 / 报 `Operation not permitted`？**  
说明服务进程没有真实文件写权限。常见原因是服务由受限沙盒、某些自动化终端或 IDE 任务启动，
而不是由 `/Applications/DS Mac Cleaner.app` 启动。先检查：

```bash
curl -s http://127.0.0.1:8787/api/permissions | jq .
```

`writable` 必须全部为 `true`。若为 `false`，退出当前服务后从启动台重新打开 App。
从普通终端启动 `./scripts/start-server.sh` 通常也有权限；关键是不要从受限沙盒进程启动。

**历史越来越多怎么办？**  
历史页已经分页；数据库只保存精简摘要，完整报告文件只保留最近 60 份。
浏览旧记录不会拖慢页面，也不会一次加载全部数据。如果将来需要跨年份归档，
可以基于 `/api/history` 的 `page` 参数导出，或直接查询 PostgreSQL 的 `scans` / `cleanup_runs`。

**历史库一直离线？**  
先确认 Docker Desktop 已启动，再点 UI 的「启动历史数据库」，或手动：

```bash
docker compose up -d db
docker compose ps
```

**8787 端口被占用？**  
在 `.env` 修改 `DSMC_PORT`，App 启动器会读取同一个 `.env`。或者：

```bash
DSMC_PORT=8790 ./scripts/start-server.sh
```

**启动台找不到 App？**  
在启动台搜索「DS 麦清理」；仍找不到时运行：

```bash
open "/Applications/DS Mac Cleaner.app"
```

**如何卸载？**

```bash
rm -rf "/Applications/DS Mac Cleaner.app"
docker compose down            # 保留数据卷
docker compose down -v         # 同时删除历史数据，谨慎
```

## 免责声明

清理缓存是**永久删除**，不进入废纸篓。请先退出相关应用并备份重要数据。
本项目按“只处理可重建缓存”的原则设计，但任何清理工具都无法保证对每个第三方应用零影响；
请逐项确认后再执行。作者不对数据丢失负责。

---

**DS Mac Cleaner v1.0.0** · 本地运行 · 无遥测 · 为 macOS 15 打造
