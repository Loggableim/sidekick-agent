import pytest

from swarm_core.router import ModelRouter, NoEligibleModel
from swarm_core.models import ModelRegistry


def test_default_cloud_route_pauses_without_flash_instead_of_cross_provider_fallback():
    """The default route must not silently replace DeepSeek Flash with local/GPT-OSS."""
    router = ModelRouter(ModelRegistry(catalog={"deepseek-v4-pro", "gpt-oss:20b", "llama3"}))
    with pytest.raises(NoEligibleModel):
        router.select("default", {"structured-output"})


def test_required_review_pair_stays_on_independent_cloud_families():
    router = ModelRouter(ModelRegistry(catalog={"glm-5.2", "kimi-k2.7-code", "nemotron-3-super"}))
    first, second = router.select_review_pair()
    assert first.models == ("glm-5.2",)
    assert second.models == ("kimi-k2.7-code",)
    assert first.family != second.family
    assert all("gpt-oss" not in model.casefold() for model in (*first.models, *second.models))
