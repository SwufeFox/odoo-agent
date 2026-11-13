# 显微镜企业办公 Agent MVP

基于 **Odoo 20 Community**，复用供应商、RFQ/采购、合同、员工权限、任务和公告。本地工作加速入口已接入：多供应商报价比较与补问、会议接续已有任务、通知准备到公告草稿，以及有具体下一步的事项简报。

本项目用于本地模拟演示。既有业务边界参考 [原执行方案](Agent执行方案_MVP.md)；本轮按用户提供的工作加速方案推进，六个开发子代理分别实现业务切片和公共界面，不重走旧门禁。实际运行记录见 [测试报告](docs/TEST_REPORT.md)。

## 工作加速从哪里开始

登录后打开“显微镜企业办公 → 工作加速”，原有角色首页保留。工作台按当前账号显示实际可用的入口和事项卡：

- **报价准备**：粘贴每家报价原文，点击“整理报价与补问”。核对可编辑比较表、各家缺项和补问；勾选后“采用选中行到草稿”，明确的产品行直接写入原生 RFQ。运费保留在准备记录和说明中，不计入 RFQ 产品行金额。
- **会议接续**：会议中点击“整理并接续工作”，自动区分新增、更新已有任务、仅记录；核对后一次“采纳选中事项”。唯一匹配的参会人和明确日期会预填，同名候选留给该行处理，截止日期可留空。
- **通知准备**：从会议“准备通知”，员工可修改并提交本人准备稿；负责人“生成公告草稿”进入原有公告表单。生成草稿不等于发布。
- **事项简报**：汇合当前报价缺项、会议决定与未完成任务，直接进入对应记录。员工不因此获得报价、采购或合同访问权。

现有环境停止 Web 后执行 `python scripts/dev.py upgrade`、`python scripts/dev.py seed`，再运行 `python scripts/dev.py run`。种子包含两家模拟物镜报价、一个已有采购跟进任务和一场接续会议；完整连续演示见 [演示脚本](docs/DEMO_SCRIPT.md)，共享材料在 [demo_acceleration.json](scripts/demo_acceleration.json)。重复 seed 保留人工编辑与已有关联。

支持 **OpenAI 兼容模型**和本地字段规则两种整理方式。当前材料只支持文字，暂不支持图片 OCR/语音；缺项保持未知，金额由代码计算，规格差异保留人工判断。没有实际使用基线，不宣称客户节省比例。

## 配置运行时模型

环境变量 `OPENAI_BASE_URL`、`OPENAI_API_KEY`、`OPENAI_MODEL` 优先；源码运行也读取忽略目录 `.runtime/model.json`。例如：

```json
{
  "MOPS_AI_MODE": "openai",
  "OPENAI_BASE_URL": "http://127.0.0.1:3425/v1",
  "OPENAI_API_KEY": "填写自己的密钥，仅保存在本地",
  "OPENAI_MODEL": "commandcode/deepseek/deepseek-v4.1-flash",
  "OPENAI_TIMEOUT_SECONDS": 45
}
```

`auto` 在没有模型配置时使用规则示例；`local` 明确关闭模型请求；`openai` 要求完整配置并实际请求模型。模型拒绝访问、超时或返回无效内容时，保留已有工作并显示失败，不自动用规则结果冒充模型成功。报价、会议分别保留当前配置与最近成功来源，重复整理相同原文会复用相同模型的成功结果。

本机已保存用户提供的指定配置，并已用真实模型走通报价、会议、通知与后续补运费的连续场景。初次推理曾返回HTTP 403，随后实际应用请求成功；最新结果见 [测试报告](docs/TEST_REPORT.md)。连接或输出校验失败时保留已保存的工作，不仅凭配置判断生成成功。

Docker 通过 `.env` 转发同名变量；容器访问宿主机网关需填写容器可达的地址，容器内 `127.0.0.1` 不代表宿主机。当前机器未实测 Docker 路径。

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
.runtime/venv/Scripts/python.exe scripts/smoke_acceleration.py
```

- `test` 在独立 `mops_test` 数据库运行真实 Odoo ORM 测试，日志为 `.runtime/test.log`；不使用演示库替代隔离测试库。
- `upgrade` 升级演示库的模块；升级前停止 Web 进程。
- `seed` 按外部 ID 复用固定模拟记录，保留人工确认的进度，不重复增加供应商/订单/任务。
- `smoke_test.py` 要求 Web 已启动，使用真实登录会话，创建带运行标识的模拟账号和业务数据，验证跨角色交接、权限及并发重复确认；输出 JSON 结果。只接受 loopback URL。
- `smoke_acceleration.py` 使用真实账号串联报价、既有任务、研发任务、通知草稿和后续补运费；检查重复采纳、人工修改保留、角色边界与失败后手工继续，结果为 `.runtime/acceleration_smoke.json`。

容器环境可用 `docker compose run --rm odoo test` 测试独立测试库；升级前先 `docker compose stop odoo`，执行 `docker compose run --rm odoo upgrade`，然后 `docker compose up -d odoo`。

## 业务入口

“显微镜企业办公”菜单中：

- 员工：登录进入“我的任务”，打开任务可回到来源会议；完成状态由负责人直接在原生任务中查看。公告列表区分未读/已读，确认阅读后显示时间与反馈。
- 采购：登录进入询报价与采购单；主表直接选择合同，也可从合同“新建询价”带入供应商、公司和合同，保存后双向回查。
- 负责人：登录进入“待我审批”；会议确认后直接进入生成的任务，并可从会议“关联任务”持续跟进；公告上的“已读人数”直接打开该公告回执。
- 系统管理员：登录进入“员工账号”，新建默认普通员工并建立员工目录关联；使用完整原生用户管理表单，在“Access Rights”中的办公权限选择角色，在“Security”设置密码，本地不发送邀请邮件。

新建办公账号自动采用对应角色的首页；调整角色时仅更新办公默认首页，保留已明确选择的其他个人首页。主公司会议默认使用内置“会议任务”项目，主持人可另选有权限的项目；其他公司需选择本公司的任务项目。

会议整理识别明确的“更新任务 / 行动项 / 待办 / 仅记录 / 会议决定：”行；通知准备摘录“通知 / 公告：”行。例如：

```text
更新任务：跟进供应商交期 | 负责人：采购小陈 | 截止：2026-11-20 | 进展：补问含税运费。
行动项：整理验收规格 | 负责人：研发小林
仅记录：先不下单，待运费与规格确认。
通知：协调会调整为2026-11-18 10:00，地点二楼会议室。
```

整理只准备可编辑建议；用户采纳后才创建或更新正式任务。已采纳行保留来源，不重复执行；后续可继续整理新事项。“确认并锁定会议”是单独的结束操作。失败时保留已有工作，也能手工添加和采纳；有权限的任务和会议仍可双向回查。

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
