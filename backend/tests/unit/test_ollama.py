"""Ollama client request shape and provider error mapping tests."""

import json

import httpx
import pytest
from app.config import Settings
from app.llm.base import LLMTimeout, LLMUnavailable, PromptBundle
from app.llm.ollama import OllamaClient


def test_ollama_request_and_usage_metadata():
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "message": {"content": "{}"},
                "prompt_eval_count": 10,
                "eval_count": 4,
            },
        )

    client = OllamaClient(
        Settings(ollama_url="http://ollama.local/", ollama_model="model-x"),
        transport=httpx.MockTransport(handler),
    )
    bundle = PromptBundle(
        "system", "user", {}, {"type": "object"}, 3, [{"role": "assistant", "content": "{}"}]
    )
    result = client.generate(bundle, 1)
    assert captured["format"] == {"type": "object"}
    assert captured["options"] == {"temperature": 0, "seed": 42, "num_ctx": 4096}
    assert captured["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": "{}"},
        {"role": "user", "content": "user"},
    ]
    assert result.raw_text == "{}"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 4


@pytest.mark.parametrize(
    "error,expected",
    [(httpx.ReadTimeout("timeout"), LLMTimeout), (httpx.ConnectError("offline"), LLMUnavailable)],
)
def test_ollama_maps_request_errors(error, expected):
    def handler(_request):
        raise error

    client = OllamaClient(Settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(expected):
        client.generate(PromptBundle("s", "u", {}, {}, 1), 1)


def test_ollama_non_200_and_bad_response_map_to_unavailable():
    bundle = PromptBundle("s", "u", {}, {}, 1)
    for response in (httpx.Response(500), httpx.Response(200, json={"wrong": "shape"})):
        client = OllamaClient(Settings(), transport=httpx.MockTransport(lambda _request: response))
        with pytest.raises(LLMUnavailable):
            client.generate(bundle, 1)
