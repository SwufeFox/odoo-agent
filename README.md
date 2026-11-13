# 显微镜企业办公 Agent MVP

基于 **Odoo 20 Community**，包含供应商/RFQ/采购合同、四角色权限、会议行动项转原生任务、定向公告与阅读回执，以及无需 API Key 的本地规则草稿助手。

本项目仅供本地模拟演示，执行范围以 [Agent 执行方案](Agent执行方案_MVP.md) 为准。实际验证和平台边界见 [测试报告](docs/TEST_REPORT.md)。

## Windows 本地启动

需要 Git、Python 3.12+、可访问 GitHub/PyPI/EDB 的网络。初始化脚本为项目创建独立 Python 3.12 虚拟环境和 PostgreSQL 17 数据目录，不使用已有数据库。首次下载可能较慢。

```bash
python scripts/dev.py setup
python scripts/dev.py db-start
python scripts/dev.py init
python scripts/dev.py seed
python scripts/dev.py run
```

访问 **http://127.0.0.1:8069**。在 [.runtime/demo_credentials.json](.runtime/demo_credentials.json) 查看四个真实本地账号的随机密码；登录名为 `staff@mops.local`、`procurement@mops.local`、`manager@mops.local`、`admin@mops.local`。密码和数据库配置只保存在忽略目录，不提交到 Git。原生初始化管理员密码也会随机化。

`run` 在前台运行，按 Ctrl+C 停止。再次运行 `run` 即可恢复数据；数据库独立监听 `127.0.0.1:55432`。如果该端口被占用，修改脚本/配置后重新初始化独立目录，不能接管其他 PostgreSQL 实例。

Linux 可设置 `MOPS_PG_BIN` 指向 PostgreSQL 16+ 的 bin 目录，并以普通用户运行相同命令。未实际运行的平台不会列为已验证。

## Docker Compose

```bash
python scripts/prepare_env.py
docker compose up -d --build
docker compose logs -f odoo
```

镜像从固定官方 Odoo20 源码提交构建。首次启动自动初始化模块、创建模拟数据、随机化账号，再启动 Web。仅映射本机 `8069`；数据库没有主机端口。数据和附件使用独立命名卷。读取凭据：

```bash
docker compose exec odoo cat /var/lib/odoo/demo_credentials.json
```

本机若已经运行源码版 8069，请先停止该实例，或在 `.env` 中设置另一个 `ODOO_PORT`。当前机器没有 Docker；Compose 的实际执行状态见测试报告。

## 运行验收与升级

```bash
python scripts/dev.py test
python scripts/dev.py upgrade
python scripts/dev.py seed
.runtime/venv/Scripts/python.exe scripts/smoke_test.py
```

- `test` 在独立 `mops_test` 数据库运行真实 Odoo ORM 测试，日志为 `.runtime/test.log`；不使用演示库替代隔离测试库。
- `upgrade` 升级演示库的模块；升级前停止 Web 进程。
- `seed` 按外部 ID 复用固定模拟记录，保留人工确认的进度，不重复增加供应商/订单/任务。
- `smoke_test.py` 要求 Web 已启动，使用真实登录会话，创建带运行标识的模拟账号和业务数据，验证跨角色交接、权限及并发重复确认；输出 JSON 结果。只接受 loopback URL。

容器环境可用 `docker compose run --rm odoo test` 测试独立测试库；升级前先 `docker compose stop odoo`，执行 `docker compose run --rm odoo upgrade`，然后 `docker compose up -d odoo`。

## 业务入口

“显微镜企业办公”菜单中：

- 员工：登录进入“我的任务”，打开任务可回到来源会议；完成状态由负责人直接在原生任务中查看。公告列表区分未读/已读，确认阅读后显示时间与反馈。
- 采购：登录进入询报价与采购单；主表直接选择合同，也可从合同“新建询价”带入供应商、公司和合同，保存后双向回查。
- 负责人：登录进入“待我审批”；会议确认后直接进入生成的任务，并可从会议“关联任务”持续跟进；公告上的“已读人数”直接打开该公告回执。
- 系统管理员：登录进入“员工账号”，新建默认普通员工并建立员工目录关联；使用完整原生用户管理表单，在“Access Rights”中的办公权限选择角色，在“Security”设置密码，本地不发送邀请邮件。

新建办公账号自动采用对应角色的首页；调整角色时仅更新办公默认首页，保留已明确选择的其他个人首页。主公司会议默认使用内置“会议任务”项目，主持人可另选有权限的项目；其他公司需选择本公司的任务项目。

会议提取只识别显式行动行，例如：

```text
待办：确认物镜交期 | 负责人：采购小陈 | 截止：2026-11-20
行动项：整理验收规格；负责人：研发小林；截止：2026-11-18
```

提取结果只含建议和原文依据。人工选择系统责任人、填写正式截止日期、勾选采纳，再点击“确认并生成任务”，随后直接查看生成的任务。失败时保留既有草稿，仍可手工处理。已确认快照保留不变；参与者只能从“关联任务”打开自己有权访问的任务，任务中的“来源会议”支持返回。

## 停止与重置

```bash
# 停止前台 Odoo：Ctrl+C
python scripts/dev.py db-stop
# 容器停止并保留数据
docker compose down
```

完全重置会删除模拟数据及附件。需要重置时，先停止服务，确认保留不再需要，再手工删除本项目的 `.runtime/pgdata`、`.runtime/data`、`.runtime/demo_credentials.json`，运行 `configure`、`db-start`、`init`、`seed`。保留 `.runtime/odoo.conf`。**不要删除系统 PostgreSQL 的数据目录。** 容器数据重置为明确的 `docker compose down -v`，同样先确认不需要这些模拟卷。

## 实现说明

- [工程基线](docs/BASELINE.md)、[实现决策](docs/DECISIONS.md)、[功能适配](docs/FIT_GAP.md)
- [角色矩阵](docs/ROLE_MATRIX.md)、[演示脚本](docs/DEMO_SCRIPT.md)
- [测试报告](docs/TEST_REPORT.md)、[已知边界](docs/KNOWN_LIMITATIONS.md)、[客户确认问题](docs/CUSTOMER_DISCOVERY.md)

没有启用外部 LLM、真实邮件、短信、IM、实际采购或生产部署。原生采购阈值和经理豁免保持 Odoo20 实际语义，不把未确认的自审政策写成客户规则。
