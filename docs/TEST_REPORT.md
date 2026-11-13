# 测试报告

验收记录日期：**2026-11-13**。当前模块版本为 `20.0.1.2.0`；本报告区分工作加速与真实模型验收、此前基础办公验收，不把历史规则结果记成模型成功。

日志、截图、账号和模型配置保存在本地忽略目录 `.runtime/`，不随Git仓库发布。以下是已执行记录，不是自动CI结果；重跑方式见 [运行与配置](RUNNING.md)。

## 验收环境

| 项目 | 实际环境 |
|---|---|
| 平台 | Windows本地源码运行 |
| Odoo | 20.0 FINAL，官方固定提交 `7d8d985d0b4e5bf758b2a493e0da7f3ddce1383c` |
| Python | 项目独立Python 3.12.16 |
| PostgreSQL | 项目独立PostgreSQL 17.12，loopback端口55432 |
| 模型 | 用户提供的OpenAI兼容接口，模型 `commandcode/deepseek/deepseek-v4.1-flash` |
| 材料 | 全部为模拟业务数据；没有邮件、短信或IM外发 |

## 工作加速与真实模型验收

| 检查 | 执行方式 | 结果与证据 |
|---|---|---|
| 完整ORM回归 | `python scripts/dev.py test` | **84项，0失败、0错误**；`.runtime/test.log` |
| 数字文本和原文依据回归 | Odoo定向运行下列两个报价测试 | **2项，0失败、0错误**；`.runtime/quote_model_retry.log` |
| 真实模型连续场景 | `.runtime/venv/Scripts/python.exe scripts/smoke_acceleration.py --output .runtime/acceleration_model_smoke.json` | **6组全部PASS**；报价到RFQ、会议接续、通知转草稿、补运费保留修改、权限隔离、失败后人工继续 |
| 无模板前缀的自然语言材料 | 一次性真实会话脚本，执行后已清理 | **3组全部PASS**；自然报价提取12000元/13%/零运费，纪要更新旧任务并新增研发任务、保留不下单限制，通知转未发布草稿；`.runtime/natural_model_result.json` |
| 负责人和员工页面 | 独立Chrome实际登录并操作页面 | **PASS**；当前模型、实际提取值、报价原生展开、会议关联、员工工作台及本人通知准备；`.runtime/acceleration_ui_result.json` 和 `.runtime/screenshots/` |
| 最后的报价表单修正 | `python scripts/dev.py upgrade`，再操作真实页面 | **exit0，页面通过**；报价行复用业务表单，内部快照/指纹不显示，字段依据默认折叠；`.runtime/upgrade.log` |

定向测试使用的标签：

```text
/microscope_office_mvp:TestQuotePack.test_model_understands_unlabelled_material_with_grounding
/microscope_office_mvp:TestQuotePack.test_model_rejects_unsupported_fields_values_and_sources
```

完整84项运行之后，数字文本规范化通过2项定向回归；最终视图调整通过模块升级和浏览器路径验证，没有将这些不同时间的检查合并描述为一次完整重跑。

ORM覆盖报价准备、会议接续、通知准备、工作台访问、模型输出校验，以及合同附件、商业数据CRUD、原生阈值审批、状态/审批元数据伪造、会议行动项授权、任务原子生成、重复采纳、公告受众和唯一回执。权限的产品定义见 [角色矩阵](ROLE_MATRIX.md)。

## 基础办公验收记录

以下检查在此前基础办公版本完成，作为基线保留；当前新增流程的结果以上一节为准。

| 检查 | 执行方式 | 实际结果 |
|---|---|---|
| 官方源码和依赖 | 获取固定提交并安装官方requirements | 完成，源码标记20.0 FINAL |
| 新数据库安装 | `python scripts/dev.py init` | exit0；68个原生/自定义模块加载成功，业务视图安装通过 |
| 模拟数据及重复初始化 | `python scripts/dev.py seed` 后再次执行 | 四账号、三供应商、两RFQ、一待审批单；外部ID稳定，既有行动项/进度保留 |
| 原有ORM验收 | `python scripts/dev.py test` | 40项，0失败、0错误；后由当前84项完整回归覆盖更新 |
| 基础会话烟测 | `.runtime/venv/Scripts/python.exe scripts/smoke_test.py` | 9组全部PASS；`.runtime/smoke_result.json` |
| 卸载后重装 | 在专用克隆库 `mops_reinstall` 执行原生模块卸载/安装 | 均exit0，之后删除该一次性验证库，演示库未卸载 |
| 数据库与附件重启 | 停止Odoo和项目PostgreSQL后重新启动 | 任务、公告、合同一致，附件SHA-256一致；`.runtime/persistence_result.json` |
| 初始页面操作 | 独立Chrome登录manager/staff | 手工任务、规则草稿、定向公告、员工阅读、参会者只读与本人任务通过；`.runtime/ui_result.json` |
| 四角色交接 | 独立Chrome登录全部四角色 | 开户、角色/密码设置、新账号首次登录、员工目录、合同到询价、任务到会议和公告回执通过；`.runtime/workflow_ui_result.json` |

基础会话烟测包括：四角色登录、采购审批与锁定、员工商业数据拒绝、并发会议确认只生成唯一任务、并发已读只留一条回执，以及负责人发布/分派到新员工阅读/完成的交接。账号升为采购再降为员工后，合同访问权相应获得和撤销。

## 验收中处理的问题

- **Odoo 20适配**：修正BinaryValue读取和服务端关系域的旧版本假设；测试HTTP监听隔离到18069，避免与演示8069冲突。首次失败记录保留在 `.runtime/test_initial.log`。
- **开户字段缓存**：固定基线下，联系人姓名/邮箱已保存但用户委托字段缓存仍为空，导致原生建员工失败。扩展模块先刷新缓存再调用原生员工创建，不修改Odoo核心；浏览器已验证角色、密码和首次登录。
- **模型调用及数值规范化**：初次直接探测返回HTTP403，后续实际应用请求成功。真实响应中的 `13%` 曾被纯数字校验拒绝，现规范化有原文依据的数字文本；非法字段、无依据数值及来源仍拒绝。
- **报价表单入口**：原生展开曾使用自动生成表单，暴露内部快照/指纹；现注册共享业务表单并折叠字段依据，已在真实页面复查。
- **浏览器环境**：默认自动浏览器的PermissionStatus模拟对象与Odoo 20通知服务不兼容，改用独立Chrome CDP验收，没有改动Odoo核心。
- **已知运行日志**：Windows filestore GC可能输出INFO级PermissionError，附件读写另经验证；可选markdown2未安装，不提供Markdown附件渲染。`without_demo` 已使用Odoo 20布尔形式。

## 验证边界与原执行方案门禁

| 门禁 | 当前结论 | 范围 |
|---|---|---|
| G0 基线 | PASS | 固定Odoo 20、依赖、权限假设及工程决策已记录 |
| G1 环境 | PARTIAL | Windows源码路径通过；Docker环境缺失，未执行容器验收 |
| G2 业务闭环 | PASS（模拟数据） | 采购/合同、账号权限、会议任务、公告与回执 |
| G3 AI辅助 | PASS（模拟数据） | 规则和指定真实模型；原文依据、候选校验、人工采纳与失败保留 |
| G4 交付验收 | PASS（Windows源码） | 测试、真实会话、浏览器页面及操作文档 |
| G5 生产试点 | 未启动 | 未验证生产备份恢复、HTTPS、监控、RTO/RPO和真实客户数据 |

Linux源码路径也未实测。本地停止/重启及附件校验不代表生产恢复演练；没有实际采购、客户迁移、邮件/IM外发或生产交付承诺。详细限制见 [已知边界](KNOWN_LIMITATIONS.md)。
