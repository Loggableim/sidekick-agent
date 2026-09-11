"""Mail argument schemas must reach providers under function.parameters."""

import importlib

import pytest

from tools.registry import registry
from tools.schema_sanitizer import sanitize_tool_schemas


@pytest.mark.parametrize("name,required", [
    ("mail_folders", []),
    ("mail_read", ["inbox_id"]),
    ("mail_search", ["inbox_id", "query"]),
    ("mail_send", ["inbox_id", "to", "subject", "body"]),
])
def test_registered_mail_payload_preserves_arguments(name, required):
    module = importlib.import_module("tools." + name)
    definitions = registry.get_definitions({name}, quiet=True)
    assert len(definitions) == 1
    function = definitions[0]["function"]
    assert set(function) == {"name", "description", "parameters"}
    assert function["name"] == name
    assert function["description"]
    assert function["parameters"] == module.SCHEMA
    sanitized = sanitize_tool_schemas(definitions)[0]["function"]
    assert sanitized["parameters"]["properties"] == module.SCHEMA["properties"]
    assert sanitized["parameters"].get("required", []) == required
    assert definitions[0]["function"]["parameters"] == module.SCHEMA
