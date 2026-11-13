# 工程基线

运行步骤见 [运行与配置](RUNNING.md)，已执行结果见 [测试报告](TEST_REPORT.md)。本文件记录版本和适配事实。

## 固定版本

| 组件 | 当前基线 |
|---|---|
| Odoo | Community 20.0 FINAL；官方提交 `7d8d985d0b4e5bf758b2a493e0da7f3ddce1383c` |
| 自定义模块 | `microscope_office_mvp`，版本以 [manifest](../addons/microscope_office_mvp/__manifest__.py) 为准 |
| 应用Python | 3.12；实际验收3.12.16，运行环境独立于引导解释器 |
| PostgreSQL | Windows验收17.12；Compose固定 `postgres:17.12-bookworm` |
| 本地端口 | Odoo 8069、PostgreSQL 55432、测试HTTP 18069，均为loopback |

源码启动脚本和Dockerfile固定同一个Odoo提交。Odoo 20要求Python 3.12+、PostgreSQL 16+；本项目不承诺其他Odoo主版本兼容。

## 平台与适配事实

- 初始工作区只有两份需求资料，没有应用、Git、Compose或CI。版本随后明确选为Odoo 20，而非原方案中的18。
- 初始Python 3.13.14安装官方Windows PDF依赖失败，实际应用改用隔离Python 3.12.16并安装官方requirements。
- 验收机器无Docker/WSL，既有PostgreSQL目录没有可运行服务端，因此在 `.runtime/` 下载项目专用二进制包并初始化数据目录。
- 已完成真实数据库初始化、模块安装和升级。源码、数据库、附件、虚拟环境、模型配置与凭据均在Git忽略目录。
- Odoo 20安全模型为统一 `ir.access`，用户组字段为 `group_ids`，采购锁定字段为 `locked`，任务截止日期为Datetime；按该基线适配。
- 所有业务种子和验收材料为模拟数据。实际模型调用使用明确配置的接口；邮件、短信和IM不启用。

## 资料来源

原始资料归档为 [Agent执行方案](reference/Agent执行方案_MVP.md) 与 [项目工程设计](reference/项目工程设计.md)，保留其原文。版本、模型接入和交互流程以当前 [README](../README.md)、[实现决策](DECISIONS.md) 及测试报告为准。
