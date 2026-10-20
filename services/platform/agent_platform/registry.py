"""模块清单是后端装配入口；新增模块只需明确注册及依赖，不修改业务总开关函数。"""

from dataclasses import dataclass
from importlib import import_module


@dataclass(frozen=True, slots=True)
class Module:
    key: str
    name: str
    feature_modules: tuple[str, ...]
    router_module: str
    model_module: str | None = None


MODULES = (
    Module('identity', '身份与组织权限', ('M14',), 'identity.router'),
    Module('workspace', '工作台与任务', ('M01',), 'workspace.router'),
    Module('files', '文件与成果', ('M17',), 'files.router'),
    Module('catalog', '应用与能力目录', ('M12', 'M13'), 'catalog.router'),
    Module('graph', '本体与图谱', ('M09',), 'graph.router', 'graph.models'),
    Module('operations', '运行与运营', ('M16',), 'operations.router'),
    Module('manufacturing', '设备维护与质量', ('M10', 'M11'), 'manufacturing.router', 'manufacturing.models'),
    Module('data', '数据源与分析', ('M05', 'M06'), 'data.router', 'data.models'),
    Module('reports', '报表与大屏', ('M07',), 'reports.router', 'reports.models'),
    Module('chat', '原生对话', ('M02',), 'chat.router'),
    Module('knowledge', '平台知识库', ('M08',), 'knowledge.router', 'knowledge.models'),
    Module('flows', '平台工作流', ('M04',), 'flows.router', 'flows.models'),
    Module('agents', '平台智能体应用', ('M03',), 'agents.router', 'agents.models'),
    Module('integrations', '原生引擎与模型', ('M02', 'M03', 'M04', 'M08', 'M15'),
           'integrations.router', 'integrations.models'),
)


def load_modules(enabled: tuple[str, ...] = ()):
    selected = set(enabled) if enabled else {m.key for m in MODULES}
    if selected - {m.key for m in MODULES}:
        raise ValueError('启用清单包含未知模块')
    selected.add('identity')
    loaded, missing = [], []
    for module in MODULES:
        if module.key not in selected:
            continue
        full_name = 'agent_platform.modules.' + module.router_module
        try:
            router = import_module(full_name).router
            if module.model_module:
                import_module('agent_platform.modules.' + module.model_module)
            loaded.append((module, router))
        except ModuleNotFoundError as exc:
            # 开发中的模块可报告未装配，但模块内部缺依赖必须直接报错。
            if exc.name and (full_name == exc.name or full_name.startswith(exc.name + '.')):
                missing.append(module)
            else:
                raise
    return loaded, missing
