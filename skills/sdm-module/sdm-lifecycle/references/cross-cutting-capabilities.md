# 五阶段与横向能力

五阶段11个主步骤与R/W/V横向能力统一维护于[five-phase-orchestration.md](five-phase-orchestration.md)。旧25个阶段检查和9个横向调用ID保留为兼容身份。主步骤由work_packages维护，最终依赖由workflow_definition维护，展示编号由node_registry生成。

资源与预算使用 R.1—R.5，工作区与版本使用 W.1—W.2，评审协作使用 V.1—V.2，均无阶段归属。主流程在适用节点调用这些能力；独立资源调查不要求训练任务。已有调查 companion 负责具体调查步骤，生命周期负责预算与确认、依赖、证据及恢复，项目适配器负责真实平台动作。

能力模块不等于独立安装 Skill；当前工作区和评审模块保留生命周期所有权。拆分必须迁移完整过程与调用方，不按一个节点建立一个 Skill。参见 [skill-composition.md](skill-composition.md)。

当前模块接口及唯一状态写入方见 [module-interfaces.md](module-interfaces.md)；
可执行接口目录由 `capability_interfaces.py` 维护并接入节点注册表。

确认直接来自用户对话，不要求或代发飞书请求。共享资源、报价、实际使用和停止身份分别记录；调查完成不表示资源可调度，也不产生使用权限。
