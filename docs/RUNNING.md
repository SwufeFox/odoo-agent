# 运行与配置

本指南对应仓库当前实现。已验证环境是 Windows 本地源码运行；Docker Compose 与 Linux 路径尚未实测，结果见 [测试报告](TEST_REPORT.md)。业务操作见 [演示脚本](DEMO_SCRIPT.md)。

## 1. Windows 首次安装

需要 Git、可用的 Python 3.12+ 启动解释器，以及 GitHub、PyPI、EDB 下载源的网络访问。默认 HTTP 端口为 `8069`，项目独立 PostgreSQL 端口为 `55432`，均监听 loopback。

在仓库根目录执行：

```powershell
python scripts/dev.py setup
python scripts/dev.py db-start
python scripts/dev.py init
python scripts/dev.py seed
python scripts/dev.py run
```

`setup` 获取固定 Odoo 20 源码，通过 uv 创建 Python 3.12 虚拟环境、安装依赖，并在 Windows 下载 PostgreSQL 17.12 二进制包。脚本仅为项目初始化 `.runtime/pgdata`，不接管已有数据库。版本与源码提交见 [工程基线](BASELINE.md)。

打开 http://127.0.0.1:8069，查看本地生成的账号密码：

```powershell
Get-Content .runtime/demo_credentials.json
```

四个演示角色的登录名和默认首页见 [README](../README.md)。原生初始化管理员密码也会随机化。不要将生成的凭据、模型密钥、数据库或附件加入版本控制。

### 生成目录

| 路径 | 内容 |
|---|---|
| `.runtime/odoo/` | 固定提交的 Odoo 源码 |
| `.runtime/venv/` | 应用 Python 环境 |
| `.runtime/pgsql/` | Windows PostgreSQL 二进制包 |
| `.runtime/pgdata/` | 项目数据库数据目录 |
| `.runtime/data/` | Odoo 数据与 filestore 附件 |
| `.runtime/odoo.conf` | 本地 Odoo 和数据库连接配置 |
| `.runtime/demo_credentials.json` | 演示账号、数据库名与 Web 地址 |
| `.runtime/model.json` | 可选的源码运行模型配置 |
| `.runtime/*.log`、`.runtime/*result.json`、`.runtime/screenshots/` | 本地验证日志、结果和截图 |
| `.venv/` | 缺少 uv 时使用的引导工具环境 |

### 日常启动与升级

`run` 在前台运行，按 Ctrl+C 停止 Web。数据库仍在运行时，直接再次执行 `python scripts/dev.py run`；数据库已停止时先执行 `db-start`。不需要再次执行 `init`。

更新代码后，先停止 Web，保持数据库运行，再执行：

```powershell
python scripts/dev.py upgrade
python scripts/dev.py seed
python scripts/dev.py run
```

`seed` 通过固定外部 ID 复用模拟记录，保留已采纳任务及业务关联。重复初始化不会恢复演示前的原始进度；需要独立场景时使用新建记录或连续验收脚本。

## 2. 模型配置

客户端调用 OpenAI 兼容的 `POST <OPENAI_BASE_URL>/chat/completions`，要求服务支持消息输入、JSON 对象输出和非流式响应。项目不包含模型网关，也不提供 API Key。

| 配置 | 含义 |
|---|---|
| `MOPS_AI_MODE` | `auto`、`local` 或 `openai`；默认 `auto` |
| `OPENAI_BASE_URL` | API 基础地址，通常包含 `/v1`，不包含 `/chat/completions` |
| `OPENAI_API_KEY` | 服务商或网关要求的密钥 |
| `OPENAI_MODEL` | 该服务可调用的模型名称 |
| `OPENAI_TIMEOUT_SECONDS` | 单次请求超时，默认 45 秒，允许 1–180 秒 |

- `auto`：地址、密钥、模型名称全部未设置时使用规则；只配置一部分会报配置错误。
- `local`：明确关闭模型请求，使用本地规则。
- `openai`：要求三项连接配置完整，并实际请求指定服务。

### 源码运行

在 `.runtime/model.json` 保存配置，或在启动 Web 的终端设置同名环境变量。**环境变量优先于文件，包括模式设置。** `.env` 由 Docker Compose 使用，源码启动脚本不会自动加载它。

以下为本地验收使用的接口和模型示例；`127.0.0.1:3425` 是另行提供的网关，不由本项目启动。使用时替换为自己的接口、密钥和模型：

```json
{
  "MOPS_AI_MODE": "openai",
  "OPENAI_BASE_URL": "http://127.0.0.1:3425/v1",
  "OPENAI_API_KEY": "YOUR_API_KEY",
  "OPENAI_MODEL": "commandcode/deepseek/deepseek-v4.1-flash",
  "OPENAI_TIMEOUT_SECONDS": 45
}
```

文件配置会在后续请求中读取；修改启动环境变量需要重启 Web。若之前在终端强制过规则模式，改用文件配置前先清除该覆盖：

```powershell
Remove-Item Env:MOPS_AI_MODE -ErrorAction SilentlyContinue
python scripts/dev.py run
```

需要明确演示规则模式时，在启动 Web 的终端执行：

```powershell
$env:MOPS_AI_MODE = "local"
python scripts/dev.py run
```

模式取决于 **Web 进程**。仅在运行烟测脚本的另一个终端改环境变量，不会切换已运行服务器的模式。

页面分别展示当前配置和最近成功来源。相同原文、相同处理方式的成功报价/会议结果可复用；模型拒绝、超时、非法 JSON 或原文依据不符会保留已有内容并显示错误，不静默使用规则结果。

### Docker 配置

在生成的 `.env` 中补充模型变量，变量名称见 [.env.example](../.env.example)。容器内 `127.0.0.1` 指向容器自身；连接宿主机网关必须使用容器可达地址，并确保网关监听地址允许该连接。

## 3. Docker Compose

此路径已提供配置，但未在验收机器实际运行。需要 Docker Engine/Compose，以及构建镜像所需的网络访问。

```powershell
python scripts/prepare_env.py
docker compose up -d --build
docker compose logs -f odoo
```

`prepare_env.py` 只在 `.env` 不存在时生成数据库和管理密码；已有文件会保留，不应反复覆盖。模型配置可以在首次启动前加入 `.env`。

镜像从固定 Odoo 20 源码提交构建。入口脚本初始化模块、生成模拟数据和随机密码后启动 Web。只映射 `127.0.0.1:${ODOO_PORT}:8069`，数据库不发布主机端口；数据库和附件使用两个命名卷。源码实例已占用 `8069` 时，在 `.env` 修改 `ODOO_PORT` 或先停止本项目的源码 Web。

读取容器中的凭据：

```powershell
docker compose exec odoo cat /var/lib/odoo/demo_credentials.json
```

更新源码后必须重新构建镜像，因为 [Dockerfile](../Dockerfile) 使用 `COPY` 加入 add-on 和脚本：

```powershell
docker compose stop odoo
docker compose build odoo
docker compose run --rm odoo upgrade
docker compose up -d odoo
```

独立库 ORM 测试命令为 `docker compose run --rm odoo test`；重新准备模拟数据可执行 `docker compose run --rm odoo seed`。配置详见 [compose.yaml](../compose.yaml)。

## 4. 运行验收

### ORM 测试

先启动项目数据库。ORM 测试不要求演示 Web 已启动，使用独立 `mops_test` 数据库和测试 HTTP 端口 `18069`：

```powershell
python scripts/dev.py test
```

日志保存在 `.runtime/test.log`。模块安装、升级日志分别为 `.runtime/init.log` 和 `.runtime/upgrade.log`。

### 真实会话烟测

先完成 `init`、`seed`，在一个终端保持 Web 运行；在另一个终端执行：

```powershell
.\.runtime\venv\Scripts\python.exe scripts/smoke_test.py
.\.runtime\venv\Scripts\python.exe scripts/smoke_acceleration.py
```

| 脚本 | 覆盖内容 | 默认结果文件 |
|---|---|---|
| `smoke_test.py` | 基础采购/合同、并发确认、角色交接、新员工登录与回执 | `.runtime/smoke_result.json` |
| `smoke_acceleration.py` | 报价到RFQ、旧任务接续、新研发任务、通知草稿、补运费、重复采纳与人工恢复 | `.runtime/acceleration_smoke.json` |

两者均读取本地凭据，只接受 loopback 地址及 `mops_demo` 数据库，会创建带运行标识的模拟业务数据。支持 `--credentials` 和 `--output` 参数。要保留一次真实模型验收的独立结果，可在 Web 已按模型模式启动后运行：

```powershell
.\.runtime\venv\Scripts\python.exe scripts/smoke_acceleration.py --output .runtime/acceleration_model_smoke.json
```

看结果中的实际处理方式区分规则与模型运行；不能根据命令名称或模型配置推断调用成功。历史执行结果见 [测试报告](TEST_REPORT.md)。

## 5. 停止与重置

源码方式先 Ctrl+C 停止 Web，再停止项目数据库：

```powershell
python scripts/dev.py db-stop
```

容器方式停止并保留卷：

```powershell
docker compose down
```

完全重置会删除模拟业务数据和附件。确实不再需要这些内容时，停止 Web 和项目数据库，手工删除本项目的 `.runtime/pgdata`、`.runtime/data`、`.runtime/demo_credentials.json`；保留 `.runtime/odoo.conf`、已下载的源码和运行环境，再执行：

```powershell
python scripts/dev.py configure
python scripts/dev.py db-start
python scripts/dev.py init
python scripts/dev.py seed
python scripts/dev.py run
```

不要删除系统 PostgreSQL 或其他项目的数据目录。容器重置对应 `docker compose down -v`，会删除该 Compose 项目的数据库与附件卷。

## 6. 其他平台与常见问题

Linux 需自行提供 PostgreSQL 16+ 的 bin 目录，通过 `MOPS_PG_BIN` 指定，并以普通用户运行；系统编译依赖也需自行准备。Python 路径改为 `.runtime/venv/bin/python`。该路径未实际验证。

| 现象 | 处理 |
|---|---|
| `8069` 已被占用 | 先停止本项目已有 Web；如需改端口，同步修改 `.runtime/odoo.conf` 的 `http_port`，设置同一地址的 `MOPS_BASE_URL` 后重新 `seed`，更新凭据文件中的访问地址 |
| `55432` 已被占用 | 不要接管该实例；为项目选择空闲端口，并同步调整 `scripts/dev.py` 中的 `DB_PORT` 与 `odoo.conf` 的 `db_port` |
| 下载或依赖安装失败 | 修复网络/工具问题后重跑 `setup`；已下载源码会核对固定提交，不会自动覆盖其他版本 |
| 配置了模型仍显示规则模式 | 检查 Web 启动终端中的 `MOPS_AI_MODE`；环境变量可能覆盖 `model.json` |
| 模型返回403、超时或校验失败 | 检查服务授权、地址及模型名称；按页面错误调整或手工继续，已有准备内容保留 |
| 新报价没有自动改写已生成RFQ | 这是当前保留人工修改的行为；采购员在原生RFQ草稿决定如何修改价格或加入运费 |
| Windows filestore GC 输出 INFO 级 PermissionError | 查看实际附件读写和记录是否正常；已知文件句柄行为及验证边界见测试报告 |
