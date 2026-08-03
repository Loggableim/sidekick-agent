import time
import pytest
from nova.cloud_responder import NovaCloudResponder
from swarm_core.models import ModelResponse

class FakeTransport:
    def __init__(self, response="ok", delay=0): self.response=response; self.delay=delay; self.calls=[]
    def complete(self, request):
        self.calls.append(request)
        if self.delay: time.sleep(self.delay)
        return ModelResponse(model=request.model, content=self.response, data={})

def test_disabled_responder_never_calls_transport():
    transport=FakeTransport()
    with pytest.raises(RuntimeError): NovaCloudResponder(transport)("hello")
    assert transport.calls == []

def test_enabled_responder_is_bounded_and_redacts():
    transport=FakeTransport("secret=hidden and useful")
    result=NovaCloudResponder(transport, enabled=True)("hello token=abc")
    assert result["status"] == "received"
    assert "hidden" not in result["response"]
    assert transport.calls[0].provider == "ollama-cloud"

def test_responder_timeout_is_bounded():
    transport=FakeTransport(delay=.2)
    started=time.monotonic()
    with pytest.raises(TimeoutError): NovaCloudResponder(transport, timeout_seconds=.02, enabled=True)("hello")
    assert time.monotonic()-started < .12
