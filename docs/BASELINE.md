# 工程基线

- 初始工作区只有 `Agent执行方案_MVP.md`、`项目工程设计.md`，没有既有应用、Git 仓库、Compose 或 CI。
- 需求优先级：执行方案优先；用户进一步指定 **Odoo 20**，不采用报告建议的 Odoo 18。
- 官方 `odoo/odoo` 存在 `20.0` 分支；本次固定提交 `7d8d985d0b4e5bf758b2a493e0da7f3ddce1383c`。
- 本机初始 Python 为 3.13.14；官方 Windows PDF 依赖在该解释器安装失败。实际运行改用隔离的 Python **3.12.16**，完整安装 Odoo20 官方 requirements。
- Docker 命令不可用，WSL 未安装。既有 PostgreSQL17 目录只有残留 DLL，没有可运行服务端；因此下载并解压官方 EDB **PostgreSQL17.12**，在本项目 `.runtime/pgdata`、`127.0.0.1:55432` 创建独立模拟库，不接触原有数据库。
- 官方源码 release.py 标记为 **20.0 FINAL**，要求 Python3.12+、PostgreSQL16+。已完成真实数据库初始化、业务模块安装与升级。源码、数据库、filestore、虚拟环境和凭据均位于 Git 忽略目录。
- Odoo20 原生安全模型为统一 `ir.access`，用户字段为 `group_ids`，采购锁定为 `locked`，任务截止日期为 Datetime；本实现按实际源码编写，不复用旧版本假设。
- 未读取真实员工、供应商、采购合同；所有种子数据明确为模拟。后续按用户提供的配置启用真实模型处理模拟材料；邮件、短信和 IM 均不启用。

实际安装、升级、权限与浏览器验证结果见 `TEST_REPORT.md`。
