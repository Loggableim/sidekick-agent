"""Regression tests for Sidekick's model-independent prompt baseline."""

from cli.default_soul import DEFAULT_SOUL_MD
from run_agent import AIAgent
from runtime.prompt_builder import CORE_WORK_GUIDANCE


def test_default_soul_sets_a_reliable_working_persona():
    assert "reliable assistant" in DEFAULT_SOUL_MD
    assert "verify the\nresult before calling it complete" in DEFAULT_SOUL_MD


def test_core_work_guidance_covers_evidence_scope_and_verification():
    assert "Ground claims" in CORE_WORK_GUIDANCE
    assert "preserve unrelated user changes" in CORE_WORK_GUIDANCE
    assert "done and verified" in CORE_WORK_GUIDANCE


def test_agent_injects_core_work_guidance_for_custom_souls_too(monkeypatch):
    monkeypatch.setattr("run_agent.load_soul_md", lambda: "custom persona")
    agent = AIAgent(
        model="prompt-guidance-test",
        enabled_toolsets=[],
        quiet_mode=True,
        skip_context_files=True,
        load_soul_identity=True,
    )

    assert "custom persona" in agent._build_system_prompt_parts()["stable"]
    assert CORE_WORK_GUIDANCE in agent._build_system_prompt_parts()["stable"]
