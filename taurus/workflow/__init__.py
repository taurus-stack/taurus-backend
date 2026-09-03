"""Taurus Workflow Engine - ExecutableUnit orchestration framework
Implemented per ExecutableUnit Integration Standard v1.0.

Submodules:
- schemas:    RenderedNodeConfig / UnitOutput / ValidationResult
- context:    WorkflowContext (${path} interpolation engine)
- base_adapter: ExecutableUnit ABC (5 must + 3 optional hooks)
- registry:   plugin registry (@register_unit_adapter)
- units._testing_*: Placeholder adapter only for engine testing
"""