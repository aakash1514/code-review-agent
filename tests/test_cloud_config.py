"""Cloud-ready configuration: the GitHub App key from an environment
variable, and runtime files under DATA_DIR."""
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from rag.knowledge_graph import KnowledgeGraph, kg_path_for_repo
from tools.github_tools import generate_jwt, load_private_key


@pytest.fixture(scope="module")
def rsa_key():
    """A throwaway RSA key: (private PEM, public key)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return pem, key.public_key()


def test_key_from_env_var_with_real_newlines_signs_a_valid_jwt(monkeypatch, settings, rsa_key):
    pem, public_key = rsa_key
    monkeypatch.setattr(settings, "GITHUB_PRIVATE_KEY", pem, raising=False)

    token = generate_jwt(app_id="42")

    claims = jwt.decode(token, public_key, algorithms=["RS256"])
    assert claims["iss"] == "42"


def test_key_kept_on_one_line_with_escaped_newlines_is_restored(monkeypatch, settings, rsa_key):
    pem, _ = rsa_key
    one_line = pem.strip().replace("\n", "\\n")  # how it looks in a .env file
    monkeypatch.setattr(settings, "GITHUB_PRIVATE_KEY", one_line, raising=False)

    assert load_private_key() == pem.strip()


def test_env_var_wins_over_the_key_file(monkeypatch, settings, rsa_key, tmp_path):
    pem, _ = rsa_key
    key_file = tmp_path / "other.pem"
    key_file.write_text("-----BEGIN FILE KEY-----\n", encoding="utf-8")
    monkeypatch.setattr(settings, "GITHUB_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setattr(settings, "GITHUB_PRIVATE_KEY", pem, raising=False)

    assert load_private_key() == pem.strip()


def test_falls_back_to_the_key_file_when_env_var_is_empty(monkeypatch, settings, tmp_path):
    key_file = tmp_path / "app.pem"
    key_file.write_text("-----BEGIN FILE KEY-----\nabc\n", encoding="utf-8")
    monkeypatch.setattr(settings, "GITHUB_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setattr(settings, "GITHUB_PRIVATE_KEY", "   ", raising=False)

    assert load_private_key() == "-----BEGIN FILE KEY-----\nabc\n"


def test_explicit_path_argument_still_wins(monkeypatch, settings, rsa_key, tmp_path):
    pem, _ = rsa_key
    key_file = tmp_path / "explicit.pem"
    key_file.write_text("-----BEGIN EXPLICIT-----\n", encoding="utf-8")
    monkeypatch.setattr(settings, "GITHUB_PRIVATE_KEY", pem, raising=False)

    assert load_private_key(str(key_file)) == "-----BEGIN EXPLICIT-----\n"


def test_malformed_env_key_fails_without_echoing_it(monkeypatch, settings):
    monkeypatch.setattr(settings, "GITHUB_PRIVATE_KEY", "not-a-key-super-secret-value", raising=False)
    with pytest.raises(ValueError, match="GITHUB_PRIVATE_KEY") as excinfo:
        load_private_key()
    assert "super-secret" not in str(excinfo.value)


def test_knowledge_graph_path_is_under_data_dir(monkeypatch, settings, tmp_path):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path), raising=False)
    path = kg_path_for_repo("octo/my-repo")
    assert Path(path) == tmp_path / "knowledge_graph_octo_my-repo.json"


def test_explicit_base_dir_still_wins(monkeypatch, settings, tmp_path):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "ignored"), raising=False)
    assert Path(kg_path_for_repo("o/r", base_dir=str(tmp_path))) == tmp_path / "knowledge_graph_o_r.json"


def test_knowledge_graph_round_trips_through_data_dir(monkeypatch, settings, tmp_path):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path), raising=False)
    path = kg_path_for_repo("o/r")
    KnowledgeGraph().save(path)
    KnowledgeGraph().load(path)
    assert Path(path).parent == tmp_path