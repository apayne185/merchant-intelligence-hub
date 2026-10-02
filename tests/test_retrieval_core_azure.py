"""
Tests for the real embedder backends in src/copilot/retrieval_core.py.

No network: the openai SDK clients are mocked, so these cover backend
selection, construction and the batching contract without credentials.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from src.copilot.retrieval_core import (
    AzureOpenAIEmbedder,
    OpenAIEmbedder,
    _select_real_embedder,
)


@pytest.fixture(autouse=True)
def _clear_azure_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test in this file starts from a clean slate, no test should
    depend on whatever Azure/OpenAI env vars happen to be set (or not) on
    the machine running the suite."""
    for var in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "OPENAI_API_VERSION", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_select_real_embedder_defaults_to_openai_without_azure_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    embedder = _select_real_embedder()
    assert isinstance(embedder, OpenAIEmbedder)


def test_select_real_embedder_picks_azure_when_endpoint_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-azure-key")
    monkeypatch.setenv("OPENAI_API_VERSION", "2024-02-01")
    embedder = _select_real_embedder()
    assert isinstance(embedder, AzureOpenAIEmbedder)


def test_azure_embedder_defaults_to_text_embedding_3_small(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-azure-key")
    monkeypatch.setenv("OPENAI_API_VERSION", "2024-02-01")
    with patch("openai.AzureOpenAI"):
        embedder = AzureOpenAIEmbedder()
    assert embedder._model == "text-embedding-3-small"


def test_azure_embedder_reads_deployment_name_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-azure-key")
    monkeypatch.setenv("OPENAI_API_VERSION", "2024-02-01")
    monkeypatch.setenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "my-custom-deployment")
    with patch("openai.AzureOpenAI"):
        embedder = AzureOpenAIEmbedder()
    assert embedder._model == "my-custom-deployment"


def test_azure_embedder_explicit_model_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-azure-key")
    monkeypatch.setenv("OPENAI_API_VERSION", "2024-02-01")
    monkeypatch.setenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "env-deployment")
    with patch("openai.AzureOpenAI"):
        embedder = AzureOpenAIEmbedder(model="explicit-deployment")
    assert embedder._model == "explicit-deployment"


def test_azure_embedder_embed_calls_client_with_deployment_and_texts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-azure-key")
    monkeypatch.setenv("OPENAI_API_VERSION", "2024-02-01")

    mock_client = MagicMock()
    mock_client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1, 0.2]), MagicMock(embedding=[0.3, 0.4])]
    )
    with patch("openai.AzureOpenAI", return_value=mock_client):
        embedder = AzureOpenAIEmbedder(model="my-deployment")
        vectors = embedder.embed(["hello", "world"])

    mock_client.embeddings.create.assert_called_once_with(model="my-deployment", input=["hello", "world"])
    assert vectors.shape == (2, 2)
