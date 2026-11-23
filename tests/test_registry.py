import json

from agent.tools import filesystem, registry


def test_write_workspace_file_creates_file(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem, "WORKSPACE", tmp_path)

    arguments = json.dumps(
        {
            "path": "hello.txt",
            "content": "Ciao agente!",
        }
    )

    response = json.loads(registry.execute_tool("write_workspace_file", arguments))

    assert response["ok"] is True
    assert response["result"]["created"] is True
    assert (tmp_path / "hello.txt").read_text() == "Ciao agente!"


def test_write_workspace_file_rejects_existing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem, "WORKSPACE", tmp_path)
    (tmp_path / "hello.txt").write_text("originale")

    arguments = json.dumps(
        {
            "path": "hello.txt",
            "content": "modificato",
        }
    )

    response = json.loads(registry.execute_tool("write_workspace_file", arguments))

    assert response["ok"] is False
    assert (tmp_path / "hello.txt").read_text() == "originale"


def test_write_workspace_file_overwrites_with_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem, "WORKSPACE", tmp_path)
    (tmp_path / "hello.txt").write_text("originale")

    arguments = json.dumps(
        {
            "path": "hello.txt",
            "content": "modificato",
            "overwrite": True,
        }
    )

    response = json.loads(registry.execute_tool("write_workspace_file", arguments))

    assert response["ok"] is True
    assert response["result"]["overwritten"] is True
    assert (tmp_path / "hello.txt").read_text() == "modificato"

    backup = tmp_path / response["result"]["backup"]
    assert backup.read_text() == "originale"


def test_write_workspace_file_accepts_readme_sized_content(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem, "WORKSPACE", tmp_path)
    content = "README section\n" * 800
    arguments = json.dumps(
        {
            "path": "README.md",
            "content": content,
        }
    )

    response = json.loads(registry.execute_tool("write_workspace_file", arguments))

    assert response["ok"] is True
    assert (tmp_path / "README.md").read_text() == content


def test_edit_workspace_file_applies_a_small_patch(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem, "WORKSPACE", tmp_path)
    target = tmp_path / "README.md"
    target.write_text("# Guide\n\nOld section\n")

    response = json.loads(
        registry.execute_tool(
            "edit_workspace_file",
            json.dumps(
                {
                    "path": "README.md",
                    "old_text": "Old section",
                    "new_text": "New section",
                }
            ),
        )
    )

    assert response["ok"] is True
    assert response["result"]["changed"] is True
    assert target.read_text() == "# Guide\n\nNew section\n"
    assert (tmp_path / response["result"]["backup"]).read_text() == (
        "# Guide\n\nOld section\n"
    )


def test_workspace_checks_tool_cannot_be_executed_by_local_registry():
    invalid_arguments = json.loads(
        registry.execute_tool(
            "run_workspace_checks",
            '{"command":"id"}',
        )
    )
    direct_execution = json.loads(
        registry.execute_tool("run_workspace_checks", "{}")
    )

    assert invalid_arguments["ok"] is False
    assert invalid_arguments["error"]["type"] == "validation_error"
    assert direct_execution["ok"] is False
    assert "sandbox" in direct_execution["error"]
