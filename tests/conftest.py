"""Shared test setup. No network and no real keys:

- A fake `config` module is installed in sys.modules before any project
  module is imported, so the real (git-ignored) config.py and .env are
  never read.
- Every test gets its own SQLite database under tmp_path.
- Outbound socket connections to anything but localhost raise, so a test
  that forgets to mock GitHub / Groq / Hugging Face / Qdrant fails loudly
  instead of calling them.
"""
import socket
import sys
import types

import pytest


class FakeSettings:
    GITHUB_APP_ID = "1"
    GITHUB_PRIVATE_KEY_PATH = "/nonexistent/test-only-key.pem"
    GITHUB_INSTALLATION_ID = "1"
    GITHUB_WEBHOOK_SECRET = "test-webhook-secret"
    GITHUB_OAUTH_CLIENT_ID = "test-client-id"
    GITHUB_OAUTH_CLIENT_SECRET = "test-client-secret"
    GITHUB_OAUTH_REDIRECT_URI = "http://localhost:8000/auth/callback"
    GROQ_API_KEY = "test-groq-key"
    GROQ_MODEL_REASONING = "test-reasoning-model"
    GROQ_MODEL_LIGHT = "test-light-model"
    HF_TOKEN = "test-hf-token"
    EMBEDDING_PROVIDER = "local"
    QDRANT_URL = "http://qdrant.invalid:6333"
    QDRANT_API_KEY = "test-qdrant-key"
    DASHBOARD_BASE_URL = "http://localhost:5173"
    DATABASE_URL = "sqlite:///./should-be-replaced-per-test.db"


_fake_config = types.ModuleType("config")
_fake_config.Settings = FakeSettings
_fake_config.settings = FakeSettings()
sys.modules["config"] = _fake_config


_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _guarded(real):
    # Localhost stays allowed: on Windows, asyncio's event loop builds its
    # internal socketpair by connecting to 127.0.0.1.
    def connect(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if host not in _LOCAL_HOSTS:
            raise RuntimeError(f"network access blocked in tests: {address!r}")
        return real(self, address, *args, **kwargs)
    return connect


socket.socket.connect = _guarded(_real_connect)
socket.socket.connect_ex = _guarded(_real_connect_ex)


@pytest.fixture
def settings():
    return _fake_config.settings


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Point both job_store and session_store at a new database file and
    create the schema, exactly as the app's startup does."""
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(_fake_config.settings, "DATABASE_URL", f"sqlite:///{db_file}")
    from pipeline.job_store import init_db
    from pipeline.session_store import init_session_db
    init_db()
    init_session_db()
    return db_file


@pytest.fixture(autouse=True)
def clear_in_process_caches():
    """api.auth caches read-access and visibility results in-process, and
    github_tools caches installation tokens; reset them between tests."""
    import api.auth
    import tools.github_tools
    api.auth._read_access_cache.clear()
    api.auth._visibility_cache.clear()
    tools.github_tools._token_cache.clear()
    yield
    api.auth._read_access_cache.clear()
    api.auth._visibility_cache.clear()
    tools.github_tools._token_cache.clear()


class FakeSentenceTransformer:
    """Stands in for the real model: deterministic unit vectors per text,
    of whatever dimension the test asks for."""

    def __init__(self, dim: int = 384):
        self.dim = dim
        self.calls: list[list[str]] = []

    def encode(self, texts, **kwargs):
        import zlib
        import numpy as np
        self.calls.append(list(texts))
        rows = []
        for text in texts:
            vec = np.random.default_rng(zlib.crc32(text.encode())).standard_normal(self.dim)
            rows.append(vec / np.linalg.norm(vec) if kwargs.get("normalize_embeddings") else vec)
        return np.array(rows)


@pytest.fixture(autouse=True)
def fake_embedding_model(monkeypatch):
    """No test loads or downloads the real sentence-transformers model, and
    each test starts with no cached embedders."""
    import rag.embeddings
    model = FakeSentenceTransformer()
    monkeypatch.setattr(rag.embeddings, "_load_sentence_transformer", lambda: model)
    monkeypatch.setattr(rag.embeddings, "_embedders", {})
    return model


@pytest.fixture
def make_finding():
    from models.findings import CodeLocation, Finding, FindingCategory, Severity

    def _make(title="A finding", severity="MEDIUM", category="ARCHITECTURE", description=None,
              file="app.py", snippet="x = 1", line_start=1, line_end=1, confidence=0.9):
        return Finding(
            category=FindingCategory(category),
            severity=Severity(severity),
            title=title,
            description=description if description is not None else title,
            location=CodeLocation(file=file, line_start=line_start, line_end=line_end, snippet=snippet),
            confidence=confidence,
        )

    return _make
