"""S3 Context & interpolation engine used by ExecutableUnit.

Public methods:
- WorkflowContext.render(template: str) -> str           — String interpolation `${path.to.key}` / `${arr[0]}`
- WorkflowContext.render_structure(value: Any) -> Any    — Recursively interpolate arbitrary containers
- WorkflowContext.view_masked() -> dict                  — Debug view (secrets masked as ***)

Interpolation root paths:
- workflow.env.XXX           — Workflow global environment variables
- workflow.secrets.XXX       — Sensitive variables (normally returned on render, masked in view_masked)
- trigger.XXX                — Parameters passed at trigger time
- node_<key>.output.XXX      — Upstream node output
- node_<key>.status          — Upstream node's final status int
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


_TOKEN_RE = re.compile(r"\$\{([^}]+)\}|\$\$")

# Max steps per single path (prevent circular references / excessive nesting)
_MAX_PATH_STEPS = 32


class InterpolationError(ValueError):
    """Spec S2.4 E0xxx: config/interpolation error, default not retried."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "E0400",
        variable: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.variable = variable


@dataclass
class WorkflowContext:
    workflow_env: dict[str, Any] = field(default_factory=dict)
    secrets: dict[str, Any] = field(default_factory=dict)
    trigger_params: dict[str, Any] = field(default_factory=dict)
    node_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ------------------------------------------------------------------ public API
    def render(self, template: str) -> str:
        """Execute one interpolation for all ${path} in the string."""
        if not isinstance(template, str):
            raise TypeError(f"render() requires a string, got {type(template).__name__}")
        if "${" not in template and "$$" not in template:
            return template

        out: list[str] = []
        cursor = 0
        for m in _TOKEN_RE.finditer(template):
            start, end = m.span()
            out.append(template[cursor:start])
            if m.group(0) == "$$":
                out.append("$")
            else:
                expr = m.group(1).strip()
                try:
                    value = self._resolve_path(expr)
                except InterpolationError as e:
                    raise InterpolationError(
                        f"Interpolation failed: fragment '{m.group(0)}' — {e}",
                        error_code=e.error_code,
                        variable=expr,
                    ) from e
                out.append("" if value is None else str(value))
            cursor = end
        out.append(template[cursor:])
        return "".join(out)

    def render_structure(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.render(value)
        if isinstance(value, dict):
            return {k: self.render_structure(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.render_structure(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self.render_structure(v) for v in value)
        return value

    def view_masked(self) -> dict[str, Any]:
        return {
            "workflow": {
                "env": dict(self.workflow_env),
                "secrets": {k: "***" for k in self.secrets},
            },
            "trigger": dict(self.trigger_params),
            "node_outputs": {k: self._mask_node(v) for k, v in self.node_outputs.items()},
        }

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _mask_node(node: dict[str, Any]) -> dict[str, Any]:
        masked = dict(node)
        output = masked.get("output") if isinstance(masked.get("output"), dict) else {}
        for key in list(output.keys()):
            low = key.lower()
            if any(s in low for s in ("password", "token", "secret", "private_key", "apikey")):
                output[key] = "***"
        masked["output"] = output
        return masked

    # -------- path parsing: "workflow.env.TOKEN" / "node_a.output.tags[0]" --------
    _STEP_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)|\[(-?\d+)\]")

    @classmethod
    def _split_path(cls, path: str) -> list[str | int]:
        steps: list[str | int] = []
        pos = 0
        while pos < len(path):
            if path[pos] == ".":
                pos += 1
                continue
            m = cls._STEP_RE.match(path, pos)
            if not m:
                raise InterpolationError(
                    f"Path parse failed (position {pos}): '${{{path}}}'",
                    error_code="E0407",
                    variable=path,
                )
            name, idx = m.groups()
            steps.append(name if name is not None else int(idx))
            pos = m.end()
        return steps

    def _resolve_path(self, path: str) -> Any:
        steps = self._split_path(path)
        if len(steps) > _MAX_PATH_STEPS:
            raise InterpolationError(
                f"Path steps exceed {_MAX_PATH_STEPS}: '${{{path}}}'",
                error_code="E0405",
                variable=path,
            )
        if not steps or not isinstance(steps[0], str):
            raise InterpolationError(
                f"Invalid expression '${{{path}}}'",
                error_code="E0401",
                variable=path,
            )

        root_name = steps[0]
        if root_name == "workflow":
            current: Any = {"env": self.workflow_env, "secrets": self.secrets}
        elif root_name == "trigger":
            current = self.trigger_params
        elif root_name.startswith("node_"):
            if root_name not in self.node_outputs:
                raise InterpolationError(
                    f"Upstream node '{root_name}' has not yet provided output",
                    error_code="E0403",
                    variable=path,
                )
            current = self.node_outputs[root_name]
        elif root_name == "nodes":
            # Alias: nodes.<node_key>.output.xxx is equivalent to node_<node_key>.output.xxx
            current = {}
            for k, v in self.node_outputs.items():
                if k.startswith("node_"):
                    current[k[5:]] = v
        else:
            raise InterpolationError(
                f"Unknown scope '{root_name}', valid: workflow/trigger/node_<id>",
                error_code="E0404",
                variable=path,
            )

        for s in steps[1:]:
            try:
                if isinstance(s, int):
                    current = current[s]
                elif isinstance(current, dict):
                    if s not in current:
                        raise KeyError(s)
                    current = current[s]
                elif isinstance(current, (list, tuple)):
                    # Allow string-form index on lists (e.g. miswritten in path, but tolerable)
                    try:
                        current = current[int(s)]
                    except (TypeError, ValueError) as e:
                        raise KeyError(s) from e
                else:
                    # Attempt getattr to access object attributes (compatible with non-dict containers)
                    try:
                        current = getattr(current, s)
                    except AttributeError as e:
                        raise KeyError(s) from e
            except (KeyError, IndexError, TypeError) as e:
                raise InterpolationError(
                    f"Field '{s}' does not exist in path '${{{path}}}': {e!r}",
                    error_code="E0406",
                    variable=path,
                ) from e
        return current