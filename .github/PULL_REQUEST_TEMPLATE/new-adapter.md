---
name: 新增工作流适配器
about: 新增一个 ExecutableUnit 适配器的 PR 模板
title: "[adapter] 新增 <your_unit> 适配器"
labels: adapter
---

## 适配器信息

- **node_type**: `<your_unit>`
- **display_name**: `<自定义单元>`
- **requires_host**: `true / false`
- **is_asynchronous_human**: `true / false`

## 变更清单

### 后端
- [ ] 新建 `taurus/workflow/units/<your_unit>.py`，继承 `BaseUnitAdapter`
- [ ] 实现 5 个核心方法：`validate_config` / `dispatch` / `poll` / `cancel` / `on_after_finish`
- [ ] docstring 含错误码声明（格式：`Exxxx 描述`）
- [ ] 已加入 `INSTALLED_UNIT_ADAPTERS` 配置
- [ ] `tests/workflow/test_compliance.py` 的 `COMPLIANCE_DATA` 已添加 valid/invalid 配置
- [ ] 单元测试已编写（`tests/workflow/test_<your_unit>_adapter.py`）

### 前端
- [ ] 新建 `taurus-web/src/views/taurus/workflow/manifests/<your_unit>.manifest.ts`
- [ ] 在 `manifests/index.ts` 中注册
- [ ] 字段类型与后端 `validate_config` 一一对应
- [ ] `output_schema` 已声明（供 ConditionExprEditor 自动补全）

## 验证步骤

- [ ] `python -m pytest -m compliance -v` 全通过
- [ ] `python manage.py workflow_list_adapters` 能看到新适配器
- [ ] `python manage.py generate_error_code_ref` 能扫到新错误码
- [ ] `cd taurus-web && npm run build` 无报错
- [ ] 前端 NodePalette 能看到新节点，拖拽到画布后 AutoNodeForm 能渲染表单

## 错误码清单

| 错误码 | 说明 | 是否可重试 |
|--------|------|-----------|
| E0901  | xxx  | 否 |
| E0902  | xxx  | 是（E2xxx/E4xxx 自动重试）|

## 设计说明

<!-- 简述适配器的使用场景、依赖的外部服务、状态机设计等 -->