# 显微镜企业办公 Agent

基于 **Odoo 20 Community** 的本地办公与 AI 辅助演示项目。复用 Odoo 的供应商、采购、员工和任务，扩展合同台账、会议接续、内部公告及按权限显示的工作台。

面向模拟业务演示：AI 整理可编辑的准备内容，用户核对并采纳后，才创建或更新业务记录。Odoo ORM 负责权限、状态和业务数据。

## 已实现的流程

| 入口 | 能完成的工作 | 采纳后的结果 |
|---|---|---|
| 报价准备 | 整理多家报价、比较缺项和规格、分别准备补问 | 创建原生 RFQ 草稿；重复采用保留既有采购内容 |
| 会议与行动项 | 区分新增任务、更新已有任务、仅记录决定 | 创建或更新 `project.task`；明确责任人和日期可预填，日期允许留空 |
| 通知准备 | 员工从会议起草、核对接收人并提交 | 负责人转为公告草稿，再另行发布；支持个人阅读回执 |
| 工作加速 | 汇总有权查看的报价缺项、会议决定和未完成任务 | 直接进入对应业务记录继续处理 |
| 基础办公 | 供应商、采购审批、合同附件、员工账号及角色配置 | 复用原生业务模型和已有权限，不另建采购或任务引擎 |

补充报价会保留人工修改与既有关联；运费保存在准备记录及说明中，**不自动计入 RFQ 产品行金额**。员工不能通过工作台读取报价、采购或合同。

## 快速开始：Windows 源码运行

需要 Git、可用的 Python 3.12+ 启动解释器，以及访问 GitHub、PyPI、EDB 下载源的网络。初始化脚本通过 uv 准备独立 Python 3.12 环境，并在项目目录下初始化 PostgreSQL；默认使用本机端口 `8069` 和 `55432`。

```powershell
git clone --branch main https://github.com/SwufeFox/odoo-agent.git
cd odoo-agent
python scripts/dev.py setup
python scripts/dev.py db-start
python scripts/dev.py init
python scripts/dev.py seed
python scripts/dev.py run
```

打开 **http://127.0.0.1:8069**。随机密码保存在本地生成的 `.runtime/demo_credentials.json`，该文件不随仓库提供。Web 在前台运行，按 Ctrl+C 停止；再次使用时不需要重新初始化数据库。

| 角色 | 演示登录名 | 默认首页 |
|---|---|---|
| 员工 | `staff@mops.local` | 我的任务 |
| 采购 | `procurement@mops.local` | 询报价与采购单 |
| 负责人 | `manager@mops.local` | 待我审批 |
| 管理员 | `admin@mops.local` | 员工账号 |

登录后，从“显微镜企业办公 → 工作加速”开始。种子数据包含模拟供应商、采购单、两份物镜报价、已有采购任务和接续会议。按 [演示脚本](docs/DEMO_SCRIPT.md) 可连续走通报价、会议、通知和补运费场景。

升级现有实例、模型配置、Docker、测试、停止及重置操作统一见 [运行与配置](docs/RUNNING.md)。

## AI 整理方式

支持 OpenAI 兼容的 `/chat/completions` 接口。通过环境变量或本地 `.runtime/model.json` 配置，环境变量优先。

| `MOPS_AI_MODE` | 行为 |
|---|---|
| `auto`（默认） | 完全没有模型配置时使用本地规则；有配置时要求地址、密钥和模型名称完整 |
| `local` | 明确使用本地规则，不发起模型请求 |
| `openai` | 要求完整模型配置，实际调用指定服务 |

真实模型可整理自然语言材料；本地规则示例采用明确字段和前缀，输入样式见演示脚本。页面分别显示当前配置和最近成功来源。模型拒绝、超时或输出校验失败时保留已有工作，不静默切换为规则结果；用户仍能手工编辑和采纳。

本地验收已使用 `commandcode/deepseek/deepseek-v4.1-flash` 完成连续业务与自然语言场景。项目不提供模型网关或 API Key，接入步骤见 [运行与配置](docs/RUNNING.md)。

## 验证状态与边界

已在 Windows 源码环境验证 Odoo ORM、真实会话、实际模型请求及负责人/员工页面。**Docker Compose 和 Linux 源码路径未实测**。各次检查的范围、结果和本地证据位置见 [测试报告](docs/TEST_REPORT.md)；这些是已执行记录，不代表每次提交都会自动运行 CI。

当前仅处理文字材料，不提供 OCR、语音转写或邮件/IM 推送。所有验收使用模拟数据；生产部署、真实采购及生产运维不属于本地演示交付。详细约束见 [已知边界](docs/KNOWN_LIMITATIONS.md)。

## 文档导航

| 文档 | 用途 |
|---|---|
| [运行与配置](docs/RUNNING.md) | 安装、升级、模型、Docker、测试和重置 |
| [演示脚本](docs/DEMO_SCRIPT.md) | 连续业务演示与各角色操作 |
| [角色矩阵](docs/ROLE_MATRIX.md) | 模型访问、记录隔离和业务方法权限 |
| [测试报告](docs/TEST_REPORT.md) | 当前验收、历史基线和验证边界 |
| [工程基线](docs/BASELINE.md) | 固定版本、依赖及平台适配 |
| [实现决策](docs/DECISIONS.md) | 模型复用、人工采纳、幂等与 AI 边界 |
| [功能适配与差距](docs/FIT_GAP.md) | 已实现能力、开发分工及后续候选 |
| [已知边界](docs/KNOWN_LIMITATIONS.md) | 功能、权限、模型和运行限制 |
| [客户范围确认](docs/CUSTOMER_DISCOVERY.md) | 进入真实业务试点前的待确认事项 |
| [变更记录](CHANGELOG.md) | 按模块版本记录的变化 |

代码位于 `addons/microscope_office_mvp/`，生命周期和验收脚本位于 `scripts/`；模拟连续演示材料为 [demo_acceleration.json](scripts/demo_acceleration.json)。运行时源码、数据库、附件、凭据和验证产物保存在 Git 忽略目录 `.runtime/`。

原始 [Agent 执行方案](docs/reference/Agent执行方案_MVP.md) 与 [项目工程设计](docs/reference/项目工程设计.md) 作为需求来源归档。当前实现以本 README、实现决策和测试报告为准；原始资料中的版本候选、报价和工期不是当前交付承诺。
