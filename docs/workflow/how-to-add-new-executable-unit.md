# 如何新增一个工作流执行单元（ExecutableUnit）

本指南面向需要扩展 Taurus Ops 工作流引擎能力的开发者。通过 6 个步骤即可接入一个自定义适配器。

## 前置知识

- 工作流引擎基于 DAG（有向无环图）调度，每个节点是一个 ExecutableUnit 实例
- 适配器需实现 5 个核心方法：`validate_config` / `dispatch` / `poll` / `cancel` / `on_after_finish`
- 所有适配器通过 `INSTALLED_UNIT_ADAPTERS` 配置自动注册

## Step 1：创建适配器文件

在 `taurus/workflow/units/` 下新建 `<your_unit>.py`，继承 `BaseUnitAdapter`：

```python
from taurus.workflow.engine.base_adapter import BaseUnitAdapter
from taurus.workflow.engine.schemas import (
    RenderedNodeConfig, UnitOutput, ValidationResult, STATUS_RUNNING, STATUS_SUCCESS, STATUS_FAILED,
)


class YourUnitAdapter(BaseUnitAdapter):
    node_type = "your_unit"
    display_name = "自定义单元"
    requires_host = True
    is_asynchronous_human = False

    def validate_config(self, config: dict) -> ValidationResult:
        errors = {}
        if not config.get("target"):
            errors["/params/target"] = "target 不能为空"
        if errors:
            return ValidationResult(ok=False, errors=errors)
        return ValidationResult(ok=True)

    def dispatch(self, cfg: RenderedNodeConfig) -> UnitOutput:
        # 触发远端执行，返回 RUNNING + adapter_state
        return UnitOutput(
            status=STATUS_RUNNING,
            adapter_state={"task_id": "xxx"},
            summary="已触发执行",
        )

    def poll(self, cfg: RenderedNodeConfig, state: dict) -> UnitOutput:
        # 轮询远端状态
        task_id = state.get("task_id")
        if self._is_done(task_id):
            return UnitOutput(status=STATUS_SUCCESS, output={"result": "ok"}, exit_code=0)
        return UnitOutput(status=STATUS_RUNNING)

    def cancel(self, cfg: RenderedNodeConfig, state: dict) -> UnitOutput:
        # 清理远端资源
        self._kill_task(state.get("task_id"))
        return UnitOutput(status=STATUS_FAILED, error_message="E0901: 用户取消")

    def on_after_finish(self, cfg: RenderedNodeConfig, uo: UnitOutput) -> None:
        # 可选：收尾钩子（如清理临时文件）
        pass
```

## Step 2：编写 docstring（含错误码）

在模块顶部和类 docstring 中声明错误码，格式为 `Exxxx 描述`：

```python
"""自定义执行单元。

错误码：
  E0901 用户取消
  E0902 target 不存在
  E0903 执行超时
"""
```

错误码会被 `python manage.py generate_error_code_ref` 自动扫描并生成参考文档。

## Step 3：注册适配器

在 `taurus/settings.py`（或对应环境配置）的 `INSTALLED_UNIT_ADAPTERS` 中添加：

```python
INSTALLED_UNIT_ADAPTERS = [
    # ... 已有适配器
    "taurus.workflow.units.your_unit.YourUnitAdapter",
]
```

启动时 `apps.py` 会自动加载并注册，import 失败会直接 raise（严格模式）。

## Step 4：编写合规测试

在 `tests/workflow/test_compliance.py` 的 `COMPLIANCE_DATA` 中添加条目：

```python
"your_unit": {
    "valid_config": {"target": "host1"},
    "invalid_config": {},  # 缺 target → E0902
},
```

运行合规测试验证：

```bash
python -m pytest -m compliance -v
```

## Step 5：编写前端 manifest

在 `taurus-web/src/views/taurus/workflow/manifests/` 下新建 `your_unit.manifest.ts`：

```typescript
import type { NodeManifest } from '../types'

const manifest: NodeManifest = {
  node_type: 'your_unit',
  display_name: '自定义单元',
  category: 'execution',
  icon: 'icon-tool',
  requires_host: true,
  fields: [
    {
      key: 'target',
      type: 'host-selector',
      label: '目标主机',
      required: true,
    },
  ],
  output_schema: {
    result: { type: 'string', description: '执行结果' },
  },
}

export default manifest
```

并在 `manifests/index.ts` 中注册：

```typescript
import yourUnitManifest from './your_unit.manifest'
registerNodeManifest(yourUnitManifest)
```

## Step 6：提交 PR

使用 `.github/PULL_REQUEST_TEMPLATE/new-adapter.md` 模板创建 PR，确认以下清单全部勾选：

- [ ] 适配器继承 `BaseUnitAdapter`，5 个核心方法已实现
- [ ] docstring 含错误码声明
- [ ] 已加入 `INSTALLED_UNIT_ADAPTERS`
- [ ] `COMPLIANCE_DATA` 已添加 valid/invalid 配置
- [ ] 前端 manifest 已创建并注册
- [ ] `pytest -m compliance` 全通过
- [ ] `python manage.py generate_error_code_ref` 能扫到新错误码

## 验证清单

```bash
# 1. 后端合规测试
cd taurus-backend && python -m pytest -m compliance -v

# 2. 适配器列表
python manage.py workflow_list_adapters

# 3. 错误码文档生成
python manage.py generate_error_code_ref

# 4. 前端构建
cd taurus-web && npm run build
```

## 常见问题

**Q: 适配器需要异步等待人工操作（如审批）怎么办？**

设置 `is_asynchronous_human = True`，dispatch 返回 RUNNING，poll 中检查人工处理结果。

**Q: 如何支持重试？**

在 DAG 节点定义中设置 `max_retries` 和 `retry_delay_ms`，引擎会在失败时自动创建 attempt_no+1 的新行。错误码需属于 E2xxx（主机/网络）或 E4xxx（超时）才会重试。

**Q: 如何支持超时保护？**

在 DAG 节点定义中设置 `timeout_seconds`，引擎会在超时后自动调用 `cancel` 并标记 E4402 失败。