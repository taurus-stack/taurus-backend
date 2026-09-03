"""§S1-03 Executor Submodules单测."""
from __future__ import annotations

import pytest

from taurus.workflow.engine.executor import (
    _ELSE,
    compute_next_runnables,
    evaluate_edge_condition,
    expand_hosts,
    transition_status,
)
from taurus.workflow.engine.schemas import (
    NO_HOST_SENTINEL,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
)


class TestExpandHosts:
    def test_requires_host_false_returns_sentinel(self):
        node = {"requires_host": False}
        assert expand_hosts(node) == [NO_HOST_SENTINEL]

    def test_explicit_host_ids_priority(self):
        node = {"requires_host": True, "host_ids": ["h1", "h2"]}
        hosts = expand_hosts(node, workflow_hosts=["global_a", "global_b"])
        # explicit 优先
        assert hosts == ["h1", "h2"]

    def test_fallback_to_workflow_hosts(self):
        node = {"requires_host": True}
        hosts = expand_hosts(node, workflow_hosts=["w1", "w2"])
        assert hosts == ["w1", "w2"]

    def test_requires_host_true_no_hosts_returns_empty(self):
        # requires_host=True 但无任何Hostconfig → returnEmptylist(upper layer _prep_node_rows 会Generate FAILED 行)
        assert expand_hosts({"requires_host": True}) == []

    def test_implicit_requires_host_unknown_returns_sentinel(self):
        # 未显式声明 requires_host(None / 缺省)→ 视为隐式无Host场景, returnSentinel
        assert expand_hosts({}) == [NO_HOST_SENTINEL]
        assert expand_hosts({"requires_host": None}) == [NO_HOST_SENTINEL]

    def test_explicit_host_ids_filter_blank(self):
        # 常见脏数据:Empty字符串Empty格不Valid host id
        node = {"host_ids": ["h1", "", "  ", "h2"]}
        assert expand_hosts(node) == ["h1", "h2"]


class TestEvaluateEdgeCondition:
    def _ctx(self, statuses=None, outputs=None):
        return {
            "workflow": {"env": {"REGION": "cn", "FLAG": "true"}},
            "trigger": {"name": "t1"},
            "node_statuses": statuses or {"A": STATUS_SUCCESS, "B": STATUS_FAILED},
            "node_outputs": outputs or {"A": {"rc": 0, "data": {"k": 1}}, "B": {"rc": 1}},
        }

    def test_empty_condition_is_true(self):
        assert evaluate_edge_condition("", self._ctx()) is True
        assert evaluate_edge_condition(None, self._ctx()) is True  # type: ignore[arg-type]

    def test_constant_true_false(self):
        assert evaluate_edge_condition("true", self._ctx()) is True
        assert evaluate_edge_condition("false", self._ctx()) is False

    def test_success_failed_predicates(self):
        ctx = self._ctx()
        assert evaluate_edge_condition("success(A)", ctx) is True
        assert evaluate_edge_condition("success(B)", ctx) is False
        assert evaluate_edge_condition("failed(B)", ctx) is True
        assert evaluate_edge_condition("failed(A)", ctx) is False

    def test_status_predicate(self):
        ctx = self._ctx()
        assert evaluate_edge_condition("status(A, 'SUCCESS')", ctx) is True
        assert evaluate_edge_condition("status(B, 'FAILED')", ctx) is True
        assert evaluate_edge_condition("status(A, 'CANCELLED')", ctx) is False

    def test_else_token(self):
        r = evaluate_edge_condition("__else__", self._ctx())
        assert r is _ELSE

    def test_and_or_not_combos(self):
        ctx = self._ctx()
        assert evaluate_edge_condition("AND(success(A), failed(B))", ctx) is True
        assert evaluate_edge_condition("OR(success(A), success(B))", ctx) is True
        assert evaluate_edge_condition("NOT(failed(A))", ctx) is True
        assert evaluate_edge_condition("NOT(success(A))", ctx) is False

    def test_path_comparison_operators(self):
        ctx = self._ctx()
        assert evaluate_edge_condition("A.output.rc == 0", ctx) is True
        assert evaluate_edge_condition("A.output.rc != 9", ctx) is True
        assert evaluate_edge_condition("A.output.data.k > 0", ctx) is True
        assert evaluate_edge_condition("A.output.data.k <= 0", ctx) is False
        # workflow env 下钻
        assert evaluate_edge_condition("workflow.env.REGION == 'cn'", ctx) is True
        # 找不到Fieldreturn None 比较
        assert evaluate_edge_condition("trigger.name == 't1'", ctx) is True

    def test_unbalanced_paren_raises(self):
        with pytest.raises(ValueError, match="E11"):
            evaluate_edge_condition("success(A", self._ctx())

    def test_bad_token_raises(self):
        with pytest.raises(ValueError, match="E11"):
            evaluate_edge_condition("foo!!!bar", self._ctx())

    def test_bad_node_reference_missing_output_field(self):
        # ReferenceNon-existentField, 比较会得到 None 与字面量 => False 但不报错(Avoid阻塞)
        ctx = self._ctx()
        assert evaluate_edge_condition("A.output.missing == None", ctx) is True
        assert evaluate_edge_condition("A.output.missing == 'x'", ctx) is False


class TestTransitionStatus:
    def test_pending_to_running_ok(self):
        assert transition_status(STATUS_PENDING, STATUS_RUNNING) == STATUS_RUNNING

    def test_running_to_success_ok(self):
        assert transition_status(STATUS_RUNNING, STATUS_SUCCESS) == STATUS_SUCCESS

    def test_success_is_terminal_cannot_change(self):
        with pytest.raises(ValueError, match="E1202"):
            transition_status(STATUS_SUCCESS, STATUS_FAILED)
        with pytest.raises(ValueError, match="E1202"):
            transition_status(STATUS_SUCCESS, STATUS_RUNNING)

    def test_illegal_transition_raises(self):
        # PENDING cannot直接 -> SUCCESS(must先 RUNNING)
        with pytest.raises(ValueError, match="E1202"):
            transition_status(STATUS_PENDING, STATUS_SUCCESS)


class TestComputeNextRunnables:
    def _simple_dag(self):
        # s0 -> a1 -> e0
        # s0 -> b1
        nodes = [
            {"node_key": "s0", "node_type": "start"},
            {"node_key": "a1", "node_type": "testing_noop"},
            {"node_key": "b1", "node_type": "testing_noop"},
            {"node_key": "e0", "node_type": "end"},
        ]
        edges = [
            {"from": "s0", "to": "a1"},
            {"from": "s0", "to": "b1"},
            {"from": "a1", "to": "e0"},
            {"from": "b1", "to": "e0"},
        ]
        return nodes, edges

    def test_initial_runnables_are_a1_and_b1(self):
        nodes, edges = self._simple_dag()
        rows: dict = {}
        ctx = {"node_statuses": {}}
        # start 视为Success, 所below游 a1/b1 都 runnable
        r = compute_next_runnables(nodes, edges, rows, ctx)
        assert set(r) == {"a1", "b1"}

    def test_upstream_pending_blocks_downstream(self):
        nodes, edges = self._simple_dag()
        rows = {
            "a1": [{"status": STATUS_RUNNING, "fail_strategy": "fail_fast"}],
            "b1": [{"status": STATUS_RUNNING, "fail_strategy": "fail_fast"}],
        }
        r = compute_next_runnables(nodes, edges, rows, {})
        # a1,b1 都 RUNNING, e0 的上游没齐Terminal state => e0 不 runnable
        assert "e0" not in r

    def test_upstream_success_unlocks_end(self):
        nodes, edges = self._simple_dag()
        rows = {
            "a1": [{"status": STATUS_SUCCESS, "fail_strategy": "fail_fast"}],
            "b1": [{"status": STATUS_SUCCESS, "fail_strategy": "fail_fast"}],
        }
        r = compute_next_runnables(nodes, edges, rows, {})
        assert "e0" in r

    def test_else_branch_hit_when_other_false(self):
        nodes = [
            {"node_key": "s0", "node_type": "start"},
            {"node_key": "a", "node_type": "testing_noop"},
            {"node_key": "b", "node_type": "testing_noop"},
            {"node_key": "fallback", "node_type": "testing_noop"},
            {"node_key": "e0", "node_type": "end"},
        ]
        edges = [
            {"from": "s0", "to": "a"},
            {"from": "a", "to": "b", "condition": "success(a)"},
            {"from": "a", "to": "fallback", "condition": "__else__"},
            {"from": "b", "to": "e0"},
            {"from": "fallback", "to": "e0"},
        ]
        rows: dict = {"a": [{"status": STATUS_FAILED, "fail_strategy": "fail_fast"}]}
        ctx = {
            "node_statuses": {"a": STATUS_FAILED},
            "node_outputs": {"a": {"rc": 1}},
        }
        r = compute_next_runnables(nodes, edges, rows, ctx)
        # a FAILED => success(a) False → __else__ 命中 fallback
        assert "fallback" in r
        assert "b" not in r

    def test_condition_true_wins_over_else(self):
        nodes = [
            {"node_key": "s0", "node_type": "start"},
            {"node_key": "a", "node_type": "testing_noop"},
            {"node_key": "b", "node_type": "testing_noop"},
            {"node_key": "fallback", "node_type": "testing_noop"},
            {"node_key": "e0", "node_type": "end"},
        ]
        edges = [
            {"from": "s0", "to": "a"},
            {"from": "a", "to": "b", "condition": "success(a)"},
            {"from": "a", "to": "fallback", "condition": "__else__"},
            {"from": "b", "to": "e0"},
            {"from": "fallback", "to": "e0"},
        ]
        rows: dict = {"a": [{"status": STATUS_SUCCESS, "fail_strategy": "fail_fast"}]}
        ctx = {
            "node_statuses": {"a": STATUS_SUCCESS},
            "node_outputs": {"a": {"rc": 0}},
        }
        r = compute_next_runnables(nodes, edges, rows, ctx)
        assert "b" in r
        assert "fallback" not in r

    def test_empty_edges_blocked_when_upstream_failed(self):
        """Emptyentries件edge:when源节. FAILED 时, 后续节.不应被标记为可run.

        场景:a 节.Failed → b 和 c 都不应被execute(即使它们的入edgeentries件为Empty).
        """
        nodes = [
            {"node_key": "s0", "node_type": "start"},
            {"node_key": "a", "node_type": "testing_noop"},
            {"node_key": "b", "node_type": "testing_noop"},
            {"node_key": "c", "node_type": "testing_noop"},
            {"node_key": "e0", "node_type": "end"},
        ]
        edges = [
            {"from": "s0", "to": "a"},
            {"from": "a", "to": "b"},  # Emptyentries件
            {"from": "a", "to": "c"},  # Emptyentries件
            {"from": "b", "to": "e0"},
            {"from": "c", "to": "e0"},
        ]
        rows: dict = {"a": [{"status": STATUS_FAILED, "fail_strategy": "fail_fast"}]}
        ctx = {
            "node_statuses": {"a": STATUS_FAILED},
            "node_outputs": {"a": {"rc": 1}},
        }
        r = compute_next_runnables(nodes, edges, rows, ctx)
        # a FAILED 且Emptyentries件 → b 和 c 都不应 runnable
        assert "b" not in r
        assert "c" not in r

    def test_empty_edges_allowed_when_upstream_success(self):
        """Emptyentries件edge:when源节. SUCCESS 时, 后续节.应被标记为可run."""
        nodes = [
            {"node_key": "s0", "node_type": "start"},
            {"node_key": "a", "node_type": "testing_noop"},
            {"node_key": "b", "node_type": "testing_noop"},
            {"node_key": "c", "node_type": "testing_noop"},
            {"node_key": "e0", "node_type": "end"},
        ]
        edges = [
            {"from": "s0", "to": "a"},
            {"from": "a", "to": "b"},  # Emptyentries件
            {"from": "a", "to": "c"},  # Emptyentries件
            {"from": "b", "to": "e0"},
            {"from": "c", "to": "e0"},
        ]
        rows: dict = {"a": [{"status": STATUS_SUCCESS, "fail_strategy": "fail_fast"}]}
        ctx = {
            "node_statuses": {"a": STATUS_SUCCESS},
            "node_outputs": {"a": {"rc": 0}},
        }
        r = compute_next_runnables(nodes, edges, rows, ctx)
        # a SUCCESS 且Emptyentries件 → b 和 c 都应 runnable
        assert "b" in r
        assert "c" in r

    def test_explicit_condition_override_failed_state(self):
        """显式entries件:whenentries件明确指定 failed() 时, 即使源节. FAILED 也能命中."""
        nodes = [
            {"node_key": "s0", "node_type": "start"},
            {"node_key": "a", "node_type": "testing_noop"},
            {"node_key": "b", "node_type": "testing_noop"},
            {"node_key": "c", "node_type": "testing_noop"},
            {"node_key": "e0", "node_type": "end"},
        ]
        edges = [
            {"from": "s0", "to": "a"},
            {"from": "a", "to": "b", "condition": "failed(a)"},  # 显式entries件
            {"from": "a", "to": "c"},  # Emptyentries件
            {"from": "b", "to": "e0"},
            {"from": "c", "to": "e0"},
        ]
        rows: dict = {"a": [{"status": STATUS_FAILED, "fail_strategy": "fail_fast"}]}
        ctx = {
            "node_statuses": {"a": STATUS_FAILED},
            "node_outputs": {"a": {"rc": 1}},
        }
        r = compute_next_runnables(nodes, edges, rows, ctx)
        # a FAILED:failed(a) 显式命中 → b runnable;Emptyentries件 → c 不 runnable
        assert "b" in r
        assert "c" not in r