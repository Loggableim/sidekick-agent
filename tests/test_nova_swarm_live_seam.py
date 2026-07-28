"""Static deployment contract for the opt-in Nova runtime seam.

This module is deliberately opt-in: it parses and compiles the live source
without importing it, so it cannot initialize Nova, issue a model request, or
start a daemon.
"""

from __future__ import annotations

import ast
import builtins
import os
from pathlib import Path
import py_compile
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest


_LIVE_CONTRACT_DISABLED = not (
    os.environ.get("NOVA_LIVE_BRIDGE_CONTRACT") == "1"
    and os.environ.get("NOVA_LIVE_SPACE")
)
_LIVE_CONTRACT_REASON = (
    "set NOVA_LIVE_BRIDGE_CONTRACT=1 and NOVA_LIVE_SPACE "
    "to inspect the live Nova seam"
)


def _argument_definition_expressions(arguments: ast.arguments) -> list[ast.AST]:
    expressions: list[ast.AST] = list(arguments.defaults)
    expressions.extend(
        default for default in arguments.kw_defaults if default is not None
    )
    all_arguments = (
        list(arguments.posonlyargs)
        + list(arguments.args)
        + list(arguments.kwonlyargs)
    )
    if arguments.vararg is not None:
        all_arguments.append(arguments.vararg)
    if arguments.kwarg is not None:
        all_arguments.append(arguments.kwarg)
    expressions.extend(
        argument.annotation
        for argument in all_arguments
        if argument.annotation is not None
    )
    return expressions


def _definition_time_expressions(node: ast.AST) -> list[ast.AST]:
    expressions: list[ast.AST] = []
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        expressions.extend(node.decorator_list)
        expressions.extend(_argument_definition_expressions(node.args))
        if node.returns is not None:
            expressions.append(node.returns)
    elif isinstance(node, ast.Lambda):
        expressions.extend(_argument_definition_expressions(node.args))
    elif isinstance(node, ast.ClassDef):
        expressions.extend(node.decorator_list)
        expressions.extend(node.bases)
        expressions.extend(node.keywords)
    expressions.extend(getattr(node, "type_params", ()))
    return expressions


def _is_exact_import(node: ast.AST, *, module: str, name: str) -> bool:
    return (
        isinstance(node, ast.ImportFrom)
        and node.module == module
        and node.level == 0
        and len(node.names) == 1
        and node.names[0].name == name
        and node.names[0].asname is None
    )


def _is_entity_kernel_fallback(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Try)
        and len(node.body) == 1
        and _is_exact_import(
            node.body[0],
            module="nova.entity_kernel",
            name="EntityKernel",
        )
        and len(node.handlers) == 1
        and isinstance(node.handlers[0].type, ast.Name)
        and node.handlers[0].type.id == "ImportError"
        and node.handlers[0].name is None
        and len(node.handlers[0].body) == 1
        and _is_exact_import(
            node.handlers[0].body[0],
            module="entity_kernel",
            name="EntityKernel",
        )
        and node.orelse == []
        and node.finalbody == []
    )


def _is_source_slot_expression(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return type(node.value) is int
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "int"
        and len(node.args) == 1
        and node.keywords == []
        and isinstance(node.args[0], ast.BinOp)
        and isinstance(node.args[0].op, ast.FloorDiv)
        and isinstance(node.args[0].left, ast.Call)
        and isinstance(node.args[0].left.func, ast.Attribute)
        and isinstance(node.args[0].left.func.value, ast.Name)
        and node.args[0].left.func.value.id == "time"
        and node.args[0].left.func.attr == "time"
        and node.args[0].left.args == []
        and node.args[0].left.keywords == []
        and isinstance(node.args[0].right, ast.Name)
        and node.args[0].right.id == "DECIDE_INTERVAL"
    )


def _is_direct_time_import(node: ast.AST) -> bool:
    return isinstance(node, ast.Import) and any(
        imported.name == "time" and imported.asname is None
        for imported in node.names
    )


def _is_versioned_return(node: ast.AST) -> bool:
    if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Call):
        return False
    call = node.value
    return (
        isinstance(call.func, ast.Name)
        and call.func.id == "submit_nova_intent"
        and len(call.args) == 2
        and isinstance(call.args[0], ast.Call)
        and isinstance(call.args[0].func, ast.Name)
        and call.args[0].func.id == "EntityKernel"
        and call.args[0].args == []
        and call.args[0].keywords == []
        and isinstance(call.args[1], ast.Name)
        and call.args[1].id == "proposal"
        and len(call.keywords) == 1
        and call.keywords[0].arg == "source_slot"
        and _is_source_slot_expression(call.keywords[0].value)
    )


def _top_level_name_bindings(tree: ast.Module, name: str) -> list[ast.AST]:
    class BindingVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.bindings: list[ast.AST] = []

        def _definition(self, node):
            if node.name == name:
                self.bindings.append(node)

        def visit_FunctionDef(self, node):
            self._definition(node)
            for expression in _definition_time_expressions(node):
                self.visit(expression)

        def visit_AsyncFunctionDef(self, node):
            self._definition(node)
            for expression in _definition_time_expressions(node):
                self.visit(expression)

        def visit_ClassDef(self, node):
            self._definition(node)
            for expression in _definition_time_expressions(node):
                self.visit(expression)

        def visit_Lambda(self, node):
            for expression in _definition_time_expressions(node):
                self.visit(expression)

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


def _assert_versioned_live_entry(source: str, *, filename: str = "<live-nova>") -> None:
    tree = ast.parse(source, filename=filename)
    binding_nodes = _top_level_name_bindings(tree, "submit_intent_proposal")
    assert (
        len(binding_nodes) == 1
        and isinstance(binding_nodes[0], ast.FunctionDef)
        and binding_nodes[0] in tree.body
    ), (
        "live Nova Mind must define exactly one synchronous unconditional "
        "top-level submit_intent_proposal"
    )
    function_node = binding_nodes[0]
    arguments = function_node.args
    assert (
        arguments.posonlyargs == []
        and len(arguments.args) == 1
        and arguments.args[0].arg == "proposal"
        and (
            arguments.args[0].annotation is None
            or (
                isinstance(arguments.args[0].annotation, ast.Name)
                and arguments.args[0].annotation.id == "dict"
            )
        )
        and arguments.vararg is None
        and arguments.kwonlyargs == []
        and arguments.kw_defaults == []
        and arguments.kwarg is None
        and arguments.defaults == []
        and (
            function_node.returns is None
            or (
                isinstance(function_node.returns, ast.Name)
                and function_node.returns.id == "dict"
            )
        )
        and function_node.decorator_list == []
        and list(getattr(function_node, "type_params", ())) == []
    ), (
        "live submit_intent_proposal must retain the canonical proposal-only "
        "signature"
    )

    module_entry_bindings = _top_level_name_bindings(tree, "submit_nova_intent")
    assert module_entry_bindings == [], (
        "live Nova Mind must not define or rebind submit_nova_intent"
    )

    body = list(function_node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body.pop(0)
    assert body and _is_entity_kernel_fallback(body[0]), (
        "live submit_intent_proposal must use the prescribed "
        "EntityKernel import fallback"
    )
    body.pop(0)

    assert len(body) == 2, (
        "live submit_intent_proposal must contain only its bridge import "
        "and direct return"
    )
    assert _is_exact_import(
        body[0],
        module="nova.swarm_runtime_bridge",
        name="submit_nova_intent",
    ), (
        "submit_nova_intent must be imported unconditionally from "
        "nova.swarm_runtime_bridge"
    )
    entry_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "nova.swarm_runtime_bridge"
        and any(imported.name == "submit_nova_intent" for imported in node.names)
    ]
    assert entry_imports == [body[0]], (
        "live Nova Mind must contain exactly one import of submit_nova_intent"
    )
    assert _is_versioned_return(body[1]), (
        "live submit_intent_proposal must directly return exactly one "
        "canonical submit_nova_intent call"
    )
    source_slot = body[1].value.keywords[0].value
    if not isinstance(source_slot, ast.Constant):
        assert _top_level_name_bindings(tree, "int") == [], (
            "live Nova Mind must not replace built-in int used by source_slot"
        )
        time_bindings = _top_level_name_bindings(tree, "time")
        assert (
            len(time_bindings) == 1
            and time_bindings[0] in tree.body
            and _is_direct_time_import(time_bindings[0])
        ), "live Nova Mind must source time from a direct standard-library import"


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
    try:
        from nova.entity_kernel import EntityKernel
    except ImportError:
        from entity_kernel import EntityKernel
    from nova.swarm_runtime_bridge import submit_nova_intent
    return submit_nova_intent(EntityKernel(), proposal, source_slot=1)
"""

    _assert_versioned_live_entry(source)


def _execute_synthetic_live_entry(source: str) -> tuple[int, int]:
    entry_calls = []
    other_calls = []

    def versioned_entry(*args, **kwargs):
        entry_calls.append((args, kwargs))

    def fake_entry(*args, **kwargs):
        other_calls.append((args, kwargs))

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "nova.swarm_runtime_bridge":
            return SimpleNamespace(submit_nova_intent=versioned_entry)
        if name in {"nova.entity_kernel", "entity_kernel"}:
            return SimpleNamespace(EntityKernel=object)
        if name == "nova" and "swarm_runtime_bridge" in fromlist:
            return SimpleNamespace(
                swarm_runtime_bridge=SimpleNamespace(
                    submit_nova_intent=versioned_entry
                )
            )
        return builtins.__import__(name, globals, locals, fromlist, level)

    namespace = {
        "__builtins__": {**vars(builtins), "__import__": fake_import},
        "DECIDE_INTERVAL": 1,
        "EntityKernel": object,
        "consume": lambda value: type,
        "fake_entry": fake_entry,
        "remember": lambda value: object,
        "time": SimpleNamespace(time=lambda: 1),
    }
    exec(compile(source, "<synthetic-live-nova>", "exec"), namespace)
    namespace["submit_intent_proposal"](object())
    return len(entry_calls), len(other_calls)


@pytest.mark.parametrize(
    "definition",
    [
        """
    def helper(
        bound=(alias := submit_nova_intent),
        hidden=alias(EntityKernel(), proposal, source_slot=1),
    ):
        return hidden
""",
        """
    async def helper(
        bound=(alias := submit_nova_intent),
        hidden=alias(EntityKernel(), proposal, source_slot=1),
    ):
        return hidden
""",
        """
    helper = lambda bound=(alias := submit_nova_intent), hidden=alias(
        EntityKernel(), proposal, source_slot=1
    ): hidden
""",
        """
    class helper(
        remember(alias := submit_nova_intent),
        metaclass=consume(
            alias(EntityKernel(), proposal, source_slot=1)
        ),
    ):
        pass
""",
        """
    alias = submit_nova_intent
    if False:
        alias = fake_entry
    def helper(
        hidden=alias(EntityKernel(), proposal, source_slot=1),
    ):
        return hidden
""",
        """
    (alias,) = (submit_nova_intent,)
    def helper(
        hidden=alias(EntityKernel(), proposal, source_slot=1),
    ):
        return hidden
""",
        """
    def helper(
        bound=(
            (alias := submit_nova_intent)
            if True
            else (alias := fake_entry)
        ),
        hidden=alias(EntityKernel(), proposal, source_slot=1),
    ):
        return hidden
""",
        """
    def helper(
        bound=(alias := submit_nova_intent),
        preserved=True or (alias := fake_entry),
        hidden=alias(EntityKernel(), proposal, source_slot=1),
    ):
        return hidden
""",
        """
    alias = submit_nova_intent
    while False:
        alias = fake_entry
    def helper(
        hidden=alias(EntityKernel(), proposal, source_slot=1),
    ):
        return hidden
""",
        """
    alias = submit_nova_intent
    try:
        pass
    except Exception:
        alias = fake_entry
    def helper(
        hidden=alias(EntityKernel(), proposal, source_slot=1),
    ):
        return hidden
""",
        """
    for alias in (submit_nova_intent, fake_entry):
        break
    def helper(
        hidden=alias(EntityKernel(), proposal, source_slot=1),
    ):
        return hidden
""",
    ],
    ids=[
        "function",
        "async-function",
        "lambda",
        "class",
        "dead-rebind",
        "destructured-alias",
        "conditional-expression",
        "short-circuit-expression",
        "dead-while-rebind",
        "unreached-except-rebind",
        "loop-break",
    ],
)
def test_ast_contract_rejects_definition_time_calls_through_entry_alias(
    definition: str,
):
    source = f"""
def submit_intent_proposal(proposal):
    try:
        from nova.entity_kernel import EntityKernel
    except ImportError:
        from entity_kernel import EntityKernel
    from nova.swarm_runtime_bridge import submit_nova_intent
{definition}
    return submit_nova_intent(EntityKernel(), proposal, source_slot=1)
"""

    assert _execute_synthetic_live_entry(source) == (2, 0)
    with pytest.raises(AssertionError):
        _assert_versioned_live_entry(source)


def test_ast_contract_accepts_the_planned_live_seam_shape():
    source = """
import time

def submit_intent_proposal(proposal: dict) -> dict:
    \"\"\"Submit one Nova Mind intent through the versioned Swarm bridge.\"\"\"
    try:
        from nova.entity_kernel import EntityKernel
    except ImportError:
        from entity_kernel import EntityKernel
    from nova.swarm_runtime_bridge import submit_nova_intent

    return submit_nova_intent(
        EntityKernel(),
        proposal,
        source_slot=int(time.time() // DECIDE_INTERVAL),
    )
"""

    _assert_versioned_live_entry(source)


def test_ast_contract_rejects_an_additional_module_alias_of_the_entry():
    source = """
from nova.swarm_runtime_bridge import submit_nova_intent as int

def submit_intent_proposal(proposal):
    try:
        from nova.entity_kernel import EntityKernel
    except ImportError:
        from entity_kernel import EntityKernel
    from nova.swarm_runtime_bridge import submit_nova_intent
    return submit_nova_intent(
        EntityKernel(),
        proposal,
        source_slot=int(time.time() // DECIDE_INTERVAL),
    )
"""

    assert _execute_synthetic_live_entry(source) == (2, 0)
    with pytest.raises(AssertionError):
        _assert_versioned_live_entry(source)


def test_ast_contract_rejects_a_parent_package_entry_alias():
    source = """
from nova import swarm_runtime_bridge as bridge

def int(value):
    bridge.submit_nova_intent(EntityKernel(), {}, source_slot=0)
    return value

def submit_intent_proposal(proposal):
    try:
        from nova.entity_kernel import EntityKernel
    except ImportError:
        from entity_kernel import EntityKernel
    from nova.swarm_runtime_bridge import submit_nova_intent
    return submit_nova_intent(
        EntityKernel(),
        proposal,
        source_slot=int(time.time() // DECIDE_INTERVAL),
    )
"""

    assert _execute_synthetic_live_entry(source) == (2, 0)
    with pytest.raises(AssertionError):
        _assert_versioned_live_entry(source)


def test_ast_contract_rejects_a_parent_package_entity_kernel_alias():
    source = """
from nova import swarm_runtime_bridge as bridge
EntityKernel = bridge.submit_nova_intent

def submit_intent_proposal(proposal):
    from nova.swarm_runtime_bridge import submit_nova_intent
    return submit_nova_intent(EntityKernel(), proposal, source_slot=1)
"""

    assert _execute_synthetic_live_entry(source) == (2, 0)
    with pytest.raises(AssertionError):
        _assert_versioned_live_entry(source)


def test_ast_contract_rejects_a_conditionally_defined_canonical_seam():
    source = """
if False:
    def submit_intent_proposal(proposal):
        try:
            from nova.entity_kernel import EntityKernel
        except ImportError:
            from entity_kernel import EntityKernel
        from nova.swarm_runtime_bridge import submit_nova_intent
        return submit_nova_intent(EntityKernel(), proposal, source_slot=1)
"""
    namespace = {}
    exec(compile(source, "<synthetic-live-nova>", "exec"), namespace)

    assert "submit_intent_proposal" not in namespace
    with pytest.raises(AssertionError):
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
        """
def submit_intent_proposal(proposal):
    from nova.swarm_runtime_bridge import submit_nova_intent
    def helper(
        ignored=submit_nova_intent(EntityKernel(), proposal, source_slot=1),
    ):
        return ignored
    return submit_nova_intent(EntityKernel(), proposal, source_slot=1)
""",
        """
def submit_intent_proposal(proposal):
    from nova.swarm_runtime_bridge import submit_nova_intent
    def helper(ignored=(submit_nova_intent := fake_entry)):
        return ignored
    return submit_nova_intent(EntityKernel(), proposal, source_slot=1)
""",
    ],
)
def test_ast_contract_rejects_aliases_and_fake_entry_bindings(source: str):
    with pytest.raises(AssertionError):
        _assert_versioned_live_entry(source)
