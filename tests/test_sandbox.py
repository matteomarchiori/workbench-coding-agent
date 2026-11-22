from types import SimpleNamespace

from fastapi.testclient import TestClient

from sandbox import server


def test_sandbox_checks_require_bearer_token(monkeypatch):
    monkeypatch.setenv("SANDBOX_TOKEN", "t" * 40)
    monkeypatch.setattr(
        server,
        "_run_fixed_checks",
        lambda: [{"name": "pytest", "passed": True, "exit_code": 0}],
    )

    with TestClient(server.app) as client:
        rejected = client.post("/checks")
        accepted = client.post(
            "/checks",
            headers={"Authorization": f"Bearer {'t' * 40}"},
            json={"command": "id"},
        )

    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["passed"] is True


def test_sandbox_rejects_short_or_missing_server_token(monkeypatch):
    monkeypatch.setenv("SANDBOX_TOKEN", "short")

    with TestClient(server.app) as client:
        response = client.post(
            "/checks",
            headers={"Authorization": "Bearer short"},
        )

    assert response.status_code == 401


def test_pytest_check_uses_only_fixed_argv_and_sanitized_environment(
    monkeypatch,
    tmp_path,
):
    captured = {}

    def fake_run(arguments, **kwargs):
        captured["arguments"] = arguments
        captured.update(kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout="2 passed",
            stderr="",
        )

    monkeypatch.setattr(server, "WORKSPACE", tmp_path)
    monkeypatch.setenv("SANDBOX_TOKEN", "s" * 40)
    monkeypatch.setattr(server.subprocess, "run", fake_run)

    result = server._pytest_check()

    assert captured["arguments"] == [
        server.sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    assert captured["cwd"] == tmp_path
    assert "shell" not in captured
    assert captured["timeout"] == server.PYTEST_TIMEOUT_SECONDS
    assert captured["check"] is False
    assert "SANDBOX_TOKEN" not in captured["env"]
    assert result["passed"] is True


def test_python_syntax_check_reports_invalid_source_without_execution(
    monkeypatch,
    tmp_path,
):
    (tmp_path / "valid.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "invalid.py").write_text("def broken(:\n", encoding="utf-8")
    monkeypatch.setattr(server, "WORKSPACE", tmp_path)

    result = server._python_syntax_check()

    assert result["passed"] is False
    assert result["exit_code"] == 1
    assert result["files_checked"] == 2
    assert any("invalid.py" in error for error in result["errors"])


def test_python_syntax_check_reports_empty_workspace_as_skipped(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(server, "WORKSPACE", tmp_path)

    result = server._python_syntax_check()

    assert result["passed"] is True
    assert result["skipped"] is True
    assert result["files_checked"] == 0
