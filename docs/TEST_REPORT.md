# 测试报告

## 实际环境

- Windows 本地源码运行：Odoo20.0 FINAL，固定提交 `7d8d985d0b4e5bf758b2a493e0da7f3ddce1383c`。
- Python3.12.16（项目隔离环境）；PostgreSQL17.12（项目独立数据目录，loopback55432）。
- 全部使用模拟业务数据。按用户指定配置实际调用OpenAI兼容模型 `commandcode/deepseek/deepseek-v4.1-flash`；没有邮件、短信或IM外发。

## 已执行证据

| 检查 | 实际命令 | 结果 |
|---|---|---|
| 官方版本和依赖 | 拉取固定20.0源码；完整安装官方requirements | 已完成；源码release.py明确为20.0 FINAL |
| 新数据库安装 | `python scripts/dev.py init` | exit0；68个原生/自定义模块加载成功，包含全部业务视图 |
| 模块升级 | `python scripts/dev.py upgrade` | exit0；日志 `.runtime/upgrade.log` |
| 模拟数据与重复初始化 | `python scripts/dev.py seed`，成功后再次执行 | 4账号、3供应商、2RFQ、1待审批单；外部ID保持不变，已有行动项与进度保留 |
| 独立库ORM验收 | `python scripts/dev.py test` | **84项，0失败、0错误**；日志 `.runtime/test.log`。此完整运行后对数字文本规范化另执行下列2项定向回归 |
| 真实会话端到端验收 | `.runtime/venv/Scripts/python.exe scripts/smoke_test.py` | **9组全部PASS**；输出 `.runtime/smoke_result.json` |
| 卸载后重装 | 在专门克隆的 `mops_reinstall` 库执行原生 `odoo-bin module uninstall`，再 `module install` | 均exit0；全部模型、安全配置和视图重新安装成功；随后删除该一次性验证库，未卸载演示库 |
| 数据库与附件重启 | 停止Odoo；`python scripts/dev.py db-stop`；重新启动PostgreSQL与Odoo | PASS；任务、公告、合同记录完全一致，附件SHA-256一致；输出 `.runtime/persistence_result.json` |
| 原生浏览器验收 | 独立Chrome实际登录manager与staff，操作原生页面 | PASS；UI确认2个手工任务、提取2条草稿、发布定向公告、员工阅读、参会者只读与本人任务可见；输出 `.runtime/ui_result.json` 与截图 `.runtime/screenshots/` |
| 角色工作流浏览器验收 | 独立Chrome登录全部四角色并操作原生页面 | PASS；管理员完整表单开户、修改角色、设置密码及新账号首次登录；员工目录关联；合同新建询价预填；任务回到会议及仅显示本人可读的关联任务；公告确认反馈/阅读时间和负责人查看该公告回执；输出 `.runtime/workflow_ui_result.json` 与截图 `.runtime/screenshots/` |
| 数字文本与原文依据回归 | Odoo `--test-tags '/microscope_office_mvp:TestQuotePack.test_model_understands_unlabelled_material_with_grounding,/microscope_office_mvp:TestQuotePack.test_model_rejects_unsupported_fields_values_and_sources'` | **2项，0失败、0错误**；日志 `.runtime/quote_model_retry.log` |
| 真实模型连续工作验收 | `.runtime/venv/Scripts/python.exe scripts/smoke_acceleration.py --output .runtime/acceleration_model_smoke.json` | **6组全部PASS**；实际使用指定DeepSeek模型，覆盖报价到RFQ、会议接续、通知转草稿、补运费保留人工修改、权限隔离和失败后人工继续 |
| 无模板前缀的自然语言材料 | 一次性真实会话脚本，执行后已删除 | **3组全部PASS**；自然报价提取12000元/13%/零运费，纪要接续旧任务并新增研发任务、保留不下单限制，员工通知转未发布草稿；结果 `.runtime/natural_model_result.json` |
| 工作加速浏览器验收 | 独立Chrome实际登录manager和staff，使用页面入口 | **PASS**；当前模型、真实提取值、报价原生展开、会议关联、员工工作台和本人通知准备均验证；结果 `.runtime/acceleration_ui_result.json`，截图位于 `.runtime/screenshots/` |

ORM验收包括合同关联/附件保密、员工商业数据CRUD拒绝、物料成本和供应商报价保密、原生阈值审批、直接状态/审批人/上下文默认值伪造、会议及行动项权限、任务原子生成/重复确认、公告受众/发布元数据/回执权限、AI非法输出/数量与长度/超时/人工恢复。新增跨角色快捷入口、个人阅读状态与回执隔离，以及管理员批量开户后用户/联系人/员工姓名一致的回归验证。

本轮完整ORM回归包含新增报价准备、会议接续、通知准备、工作台权限及模型输出校验。旧版9组会话、四角色页面与重启证据保留为基线；本轮实际模型行为由6组连续场景和3组自然语言材料验证，不将早期规则运行记成真实模型成功。

真实模型初次直接探测返回HTTP403，随后实际应用请求成功。真实响应中的税率 `13%` 曾被纯数字字段校验拒绝，现对原文有依据的数字文本统一规范化，再执行原文依据校验；非法字段、无依据数值及来源仍拒绝。指定模型处理成功的方式记录在各产物上，当前配置本身不代表成功。

页面复查发现报价行原生展开会使用自动生成表单，显示内部快照和处理指纹。已将既有业务表单注册为该模型的共享表单，字段依据默认折叠；最后这次视图调整通过模块升级（exit0）及实际浏览器展开、返回和角色入口验证。

真实HTTP会话验收额外证明：四个账号可登录；采购从待审批到锁定；普通员工看不到合同、底价与他人任务；两次并发会议确认只产生2条唯一正式任务；两次并发已读只有1条回执；本地规则只写建议，人工确认后生成任务；提取失败仍能人工继续。新增使用真实管理员会话创建员工账号，由负责人发布定向公告、分派任务，新员工登录后确认已读并完成任务，负责人读取结果；同一账号升为采购再降为员工后，合同访问权随之获得并撤销。

首次开户回归定位到固定 Odoo20 基线的委托字段缓存：关联联系人已保存姓名/邮箱，用户对象在同一事务中仍读到空值，原生自动建员工因资源姓名缺失失败。扩展模块先完成账号创建、刷新姓名/邮箱缓存，再调用原生员工创建动作；没有修改 Odoo 核心。管理员入口显式绑定原生账号列表和完整用户表单，浏览器已验证角色配置、密码设置及首次登录。

首轮测试为1失败、2错误，主要涉及Odoo20 BinaryValue读取与服务端关系域，已修正适配和不应重复检验的原生域测试；保留 `.runtime/test_initial.log`。后续发现测试HTTP线程与演示8069端口冲突，已将测试监听隔离到loopback18069，最终测试日志无ERROR/CRITICAL。

Windows原生filestore GC会因文件句柄占用输出INFO级PermissionError，测试结果未计入失败；不修改Odoo核心或屏蔽日志。附件的实际读写与重启校验另行验证。

原生Chrome已实际登录后台、打开办公菜单和会议表单、点击提取并看到2条未确认草稿，无页面异常。默认自动浏览器的PermissionStatus模拟对象与Odoo20原生通知服务不兼容，改用独立Chrome CDP验证，未改动业务或Odoo核心。

初次安装时发现旧版 `without_demo=all` 参数警告，已按Odoo20改为布尔True。原生Mail提示可选markdown2未安装；本次不提供Markdown附件渲染，不隐藏该提示。

## 验证边界

当前机器没有Docker或WSL；Dockerfile/Compose已提供，但不能将容器构建/启动描述为已执行。真实模型已用模拟材料验证；生产备份恢复、HTTPS和监控未执行。

## 门禁结果

```text
[Gate] G0
[状态] PASS
[本次修改] BASELINE、FIT_GAP、DECISIONS、ROLE_MATRIX；独立Git工作分支。
[验证证据] 初始目录仅两份需求文档；实际官方20.0源码SHA与固定构建SHA一致。
[尚存问题] 客户真实流程与组织尚未确认，不阻断模拟环境。
[下一步] 本地业务演示。
[待客户确认] 既有ERP、员工规模和真实角色映射。

[Gate] G1
[状态] PARTIAL（源码运行通过；容器环境缺失）
[本次修改] 固定源码Dockerfile/Compose、隔离Python/PostgreSQL、生命周期脚本与模拟数据。
[验证证据] 本机空库安装、升级、隔离库卸载重装、重复seed、启动及重启均实际成功。
[尚存问题] 本机无Docker/WSL，未执行容器构建和compose启动。
[下一步] 在已有Docker的环境按README验证容器路径；当前可直接使用源码实例。
[待客户确认] 正式部署方式；不影响本机演示。

[Gate] G2
[状态] PASS（本地模拟）
[本次修改] 原生供应商/RFQ/采购；合同台账与附件；四角色；会议/任务；公告与阅读回执。
[验证证据] ORM与真实会话权限拒绝、阈值审批、合同关联、人工任务生成及公告发布均通过；浏览器人工路径已走通。
[尚存问题] 原生经理阈值豁免、自审政策及真实部门边界仍待客户决定。
[下一步] 使用固定模拟案例演示业务价值。
[待客户确认] 底价访问、审批与合同基数；本地假设已记录。

[Gate] G3
[状态] PASS（本地规则及指定真实模型）
[本次修改] 统一OpenAI兼容客户端、报价/会议/通知适配、原文依据、候选范围校验和失败保留。
[验证证据] 6组真实模型连续场景和3组自然语言场景通过；规则、非法输出、超时与人工恢复由ORM回归覆盖。
[尚存问题] 模型服务可用性依赖指定上游；目前只验证模拟材料。
[下一步] 按连续演示脚本核对业务价值。
[待客户确认] 真实客户材料的模型授权与数据分类。

[Gate] G4
[状态] PASS（本地源码验收）
[本次修改] 测试、README、演示脚本、限制、客户问题和变更记录。
[验证证据] 本轮84项ORM测试及2项定向回归均0失败0错误，6组真实模型连续场景及3组自然语言场景PASS；原有角色页面、9组会话与重启记录作为基线保留。
[尚存问题] Docker和生产保障未实测，不将其算作完成的生产能力。
[下一步] 按DEMO_SCRIPT演示并记录客户反馈。
[待客户确认] 是否将某个已验证闭环转为独立付费试点。
```

G5生产试点未启动；没有真实采购、邮件/IM外发、真实敏感数据、付费资源或交付承诺。
