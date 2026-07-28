"""Static deployment contract for the opt-in Nova runtime seam.

This module is deliberately opt-in: it parses and compiles the live source
without importing it, so it cannot initialize Nova, issue a model request, or
start a daemon.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import py_compile
from tempfile import TemporaryDirectory

import pytest


_LIVE_CONTRACT_DISABLED = not (
    os.environ.get("NOVA_LIVE_BRIDGE_CONTRACT") == "1"
    and os.environ.get("NOVA_LIVE_SPACE")
)
_LIVE_CONTRACT_REASON = (
    "set NOVA_LIVE_BRIDGE_CONTRACT=1 and NOVA_LIVE_SPACE "
    "to inspect the live Nova seam"
)


def _execution_scope_nodes(body: list[ast.stmt]) -> list[ast.AST]:
    class ScopeVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.nodes: list[ast.AST] = []

        def generic_visit(self, node):
            self.nodes.append(node)
            super().generic_visit(node)

        def visit_FunctionDef(self, node):
            self.nodes.append(node)

        def visit_AsyncFunctionDef(self, node):
            self.nodes.append(node)

        def visit_ClassDef(self, node):
            self.nodes.append(node)

        def visit_Lambda(self, node):
            self.nodes.append(node)

    visitor = ScopeVisitor()
    for statement in body:
        visitor.visit(statement)
    return visitor.nodes


def _expression_references_effect(node: ast.AST, aliases: set[str]) -> bool:
    if isinstance(node, ast.Lambda):
        return False
    if isinstance(node, ast.Attribute) and node.attr in {"govern", "act"}:
        return True
    if isinstance(node, ast.Name) and node.id in aliases:
        return True
    return any(
        _expression_references_effect(child, aliases)
        for child in ast.iter_child_nodes(node)
    )


def _target_names(target: ast.AST) -> set[str]:
    return {
        node.id
        for node in ast.walk(target)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }


def _simple_effect_aliases(nodes: list[ast.AST]) -> set[str]:
    aliases = {"govern", "act"}
    changed = True
    while changed:
        changed = False
        for node in nodes:
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                value = node.value
                if not _expression_references_effect(value, aliases):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    for alias in _target_names(target) - aliases:
                        aliases.add(alias)
                        changed = True
            elif isinstance(node, ast.ImportFrom):
                for imported in node.names:
                    if imported.name in {"govern", "act"}:
                        alias = imported.asname or imported.name
                        if alias not in aliases:
                            aliases.add(alias)
                            changed = True
    return aliases


def _top_level_name_bindings(tree: ast.Module, name: str) -> list[ast.AST]:
    class BindingVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.bindings: list[ast.AST] = []

        def _definition(self, node):
            if node.name == name:
                self.bindings.append(node)

        def visit_FunctionDef(self, node):
            self._definition(node)

        def visit_AsyncFunctionDef(self, node):
            self._definition(node)

        def visit_ClassDef(self, node):
            self._definition(node)

        def visit_Lambda(self, node):
            return

        def visit_Name(self, node):
            if node.id == name and isinstance(node.ctx, (ast.Store, ast.Del)):
                self.bindings.append(node)

        def visit_Import(self, node):
            if any((item.asname or item.name.split(".")[0]) == name for item in node.names):
                self.bindings.append(node)

        def visit_ImportFrom(self, node):
            if any((item.asname or item.name) == name for item in node.names):
                self.bindings.append(node)

        def visit_ExceptHandler(self, node):
            if node.name == name:
                self.bindings.append(node)
            self.generic_visit(node)

        def visit_Global(self, node):
            if name in node.names:
                self.bindings.append(node)

        def visit_Nonlocal(self, node):
            if name in node.names:
                self.bindings.append(node)

        def visit_MatchAs(self, node):
            if node.name == name:
                self.bindings.append(node)
            self.generic_visit(node)

        def visit_MatchStar(self, node):
            if node.name == name:
                self.bindings.append(node)

        def visit_MatchMapping(self, node):
            if node.rest == name:
                self.bindings.append(node)
            self.generic_visit(node)

    visitor = BindingVisitor()
    visitor.visit(tree)
    return visitor.bindings


def _function_argument_names(function_node: ast.FunctionDef | ast.AsyncFunctionDef):
    arguments = function_node.args
    names = [
        argument.arg
        for argument in (
            list(arguments.posonlyargs)
            + list(arguments.args)
            + list(arguments.kwonlyargs)
        )
    ]
    if arguments.vararg is not None:
        names.append(arguments.vararg.arg)
    if arguments.kwarg is not None:
        names.append(arguments.kwarg.arg)
    return set(names)


def _assert_versioned_live_entry(source: str, *, filename: str = "<live-nova>") -> None:
    tree = ast.parse(source, filename=filename)
    binding_nodes = _top_level_name_bindings(tree, "submit_intent_proposal")
    assert len(binding_nodes) == 1 and isinstance(
        binding_nodes[0],
        (ast.FunctionDef, ast.AsyncFunctionDef),
    ), "live Nova Mind must define exactly one top-level submit_intent_proposal"
    function_node = binding_nodes[0]
    assert "submit_nova_intent" not in _function_argument_names(function_node), (
        "live submit_intent_proposal must not shadow submit_nova_intent "
        "in its parameters"
    )

    module_entry_bindings = _top_level_name_bindings(tree, "submit_nova_intent")
    assert module_entry_bindings == [], (
        "live Nova Mind must not define or rebind submit_nova_intent"
    )
    function_scope = ast.Module(body=function_node.body, type_ignores=[])
    local_entry_bindings = _top_level_name_bindings(
        function_scope,
        "submit_nova_intent",
    )
    assert len(local_entry_bindings) == 1, (
        "live submit_intent_proposal must import exactly one versioned entry"
    )
    versioned_import = local_entry_bindings[0]
    assert isinstance(versioned_import, ast.ImportFrom) and (
        versioned_import.module == "nova.swarm_runtime_bridge"
        and len(versioned_import.names) == 1
        and versioned_import.names[0].name == "submit_nova_intent"
        and versioned_import.names[0].asname is None
    ), "submit_nova_intent must come from nova.swarm_runtime_bridge"
    assert versioned_import in function_node.body, (
        "submit_nova_intent must be imported unconditionally in the seam body"
    )

    seam_nodes = _execution_scope_nodes(function_node.body)
    calls = [node for node in seam_nodes if isinstance(node, ast.Call)]
    versioned_calls = [
        call
        for call in calls
        if (
            isinstance(call.func, ast.Name)
            and call.func.id == "submit_nova_intent"
        )
    ]
    assert len(versioned_calls) == 1, (
        "live submit_intent_proposal must call the versioned "
        "submit_nova_intent exactly once"
    )
    assert (
        versioned_import.lineno,
        versioned_import.col_offset,
    ) < (
        versioned_calls[0].lineno,
        versioned_calls[0].col_offset,
    ), "the versioned submit_nova_intent import must occur before its call"

    module_effect_nodes = _execution_scope_nodes(
        [
            node
            for node in tree.body
            if not isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            )
        ]
    )
    effect_aliases = _simple_effect_aliases(module_effect_nodes + seam_nodes)
    effect_calls = [
        call
        for call in calls
        if _expression_references_effect(call.func, effect_aliases)
    ]
    assert effect_calls == [], (
        "live submit_intent_proposal must not call govern/act directly or by alias"
    )


@pytest.mark.skipif(_LIVE_CONTRACT_DISABLED, reason=_LIVE_CONTRACT_REASON)
def test_live_submit_intent_proposal_delegates_only_to_the_versioned_bridge():
    """Catches a live deployment retaining a direct Nova govern/act fallback."""
    live_space = Path(os.environ["NOVA_LIVE_SPACE"]).expanduser().resolve()
    source_path = live_space / "nova_mind.py"
    # Keep generated bytecode outside the live Space so this remains read-only.
    with TemporaryDirectory() as temporary_directory:
        py_compile.compile(
            str(source_path),
            cfile=str(Path(temporary_directory) / "nova_mind.pyc"),
            doraise=True,
        )

    _assert_versioned_live_entry(
        source_path.read_text(encoding="utf-8"),
        filename=str(source_path),
    )


def test_ast_contract_accepts_only_the_versioned_entry_import():
    source = """
def submit_intent_proposal(proposal):
    from nova.swarm_runtime_bridge import submit_nova_intent
    return submit_nova_intent(EntityKernel(), proposal, source_slot=1)
"""

    _assert_versioned_live_entry(source)


@pytest.mark.parametrize(
    "source",
    [
        """
from other_module import govern as decide
def submit_intent_proposal(proposal):
    from nova.swarm_runtime_bridge import submit_nova_intent
    decide(proposal)
    return submit_nova_intent(EntityKernel(), proposal, source_slot=1)
""",
        """
def submit_nova_intent(*args, **kwargs):
    return {}
def submit_intent_proposal(proposal):
    return submit_nova_intent(EntityKernel(), proposal, source_slot=1)
""",
        """
def submit_intent_proposal(proposal):
    submit_nova_intent = fake_entry
    return submit_nova_intent(EntityKernel(), proposal, source_slot=1)
""",
        """
def submit_intent_proposal(proposal):
    from nova.swarm_runtime_bridge import submit_nova_intent
    def unused_nested(submit_nova_intent):
        return submit_nova_intent(EntityKernel(), proposal, source_slot=1)
    return fake_entry(proposal)
""",
        """
def submit_intent_proposal(proposal):
    from nova.swarm_runtime_bridge import submit_nova_intent
    unused = lambda submit_nova_intent: submit_nova_intent(
        EntityKernel(), proposal, source_slot=1
    )
    return fake_entry(proposal)
""",
        """
def submit_intent_proposal(proposal):
    from nova.swarm_runtime_bridge import submit_nova_intent
    submit_nova_intent(EntityKernel(), proposal, source_slot=1)
    return (decide := EntityKernel().govern)(proposal)
""",
        """
def submit_intent_proposal(
    proposal,
    submit_nova_intent=fake_entry,
):
    result = submit_nova_intent(EntityKernel(), proposal, source_slot=1)
    from nova.swarm_runtime_bridge import submit_nova_intent
    return result
""",
        """
def submit_intent_proposal(proposal):
    from nova.swarm_runtime_bridge import submit_nova_intent
    match fake_entry:
        case submit_nova_intent:
            pass
    return submit_nova_intent(EntityKernel(), proposal, source_slot=1)
""",
        """
def submit_intent_proposal(proposal):
    result = submit_nova_intent(EntityKernel(), proposal, source_slot=1)
    from nova.swarm_runtime_bridge import submit_nova_intent
    return result
""",
    ],
)
def test_ast_contract_rejects_aliases_and_fake_entry_bindings(source: str):
    with pytest.raises(AssertionError):
        _assert_versioned_live_entry(source)
