import json
import httpx
import pytest
from decompcond.agent import DeepSeekPlanner
from decompcond.io import TechnicalFailure
from decompcond.paths import compile_path


def test_api_retry_preserves_request_and_redacts_body(config, monkeypatch):
    config.agent.api_retries = 1
    config.agent.retry_delay = 0
    monkeypatch.setattr("decompcond.agent.read_credential", lambda _: "test-token")
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(503, text="must not persist server body")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]},
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        "decompcond.agent.httpx.Client",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    assert (
        DeepSeekPlanner(config).request([{"role": "user", "content": "test"}])[0]
        == "{}"
    )
    assert len(calls) == 2 and calls[0] == calls[1]

    def denied(request):
        return httpx.Response(401, text="secret server body")

    monkeypatch.setattr(
        "decompcond.agent.httpx.Client",
        lambda **kw: real_client(transport=httpx.MockTransport(denied), **kw),
    )
    with pytest.raises(TechnicalFailure) as error:
        DeepSeekPlanner(config).request([])
    assert "secret" not in str(error.value) and "test-token" not in str(error.value)


def test_json_repair_is_same_complete_batch(config, tmp_path, monkeypatch):
    config.search.paths_per_round = 1
    library = {
        "groups": {"C1": {"atom_ids": [1], "bond_ids": []}},
        "atoms": {},
        "bonds": {},
    }
    proposal = dict(
        id="p1",
        segments=[dict(start=0, end=100, active=[])],
        parents=[],
        evidence=["condition_library"],
        observation="No prior search",
        change="Release",
        hypothesis="Test",
    )
    replies = iter(["invalid json", json.dumps({"paths": [proposal]})])
    messages = []

    def request(self, body):
        messages.append(list(body))
        return next(replies), {}

    monkeypatch.setattr(DeepSeekPlanner, "request", request)
    result = DeepSeekPlanner(config).propose(
        library, {"evidence_ids": ["condition_library"]}, {}, tmp_path
    )
    assert result == [compile_path(proposal, library["groups"], 100, 10)]
    assert len(messages) == 2
    assert messages[1][-2]["role"] == "assistant"
    assert "rejected" in messages[1][-1]["content"]
