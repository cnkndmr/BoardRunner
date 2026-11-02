from boardrunner import main


def test_cli_runs_without_arguments():
    assert main([]) == 0


import pytest

from boardrunner import NotionError, SetupError, _token, config_text, local_timezone, notion_hint, page_id


def test_parent_accepts_a_page_link():
    hex_id = "0123456789abcdef0123456789abcdef"
    assert page_id(hex_id) == hex_id
    assert page_id(f"https://www.notion.so/team/My-page-{hex_id}?pvs=4") == hex_id
    assert page_id("01234567-89ab-cdef-0123-456789abcdef") == "01234567-89ab-cdef-0123-456789abcdef"
    with pytest.raises(SetupError):
        page_id("My page")


def test_env_file_accepts_export_and_single_quotes(tmp_path, monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    (tmp_path / ".env").write_text("# token\nexport NOTION_TOKEN='abc'\n")
    assert _token("NOTION_TOKEN", tmp_path / "c.toml") == "abc"


def test_notion_errors_say_what_to_do():
    assert "copy the integration secret" in notion_hint(NotionError(401, "GET /users/me: 401 API token is invalid."))
    assert "Connections" in notion_hint(NotionError(404, "GET /databases/x: 404 Could not find database"))
    assert notion_hint(NotionError(500, "boom")) == ""


def test_init_writes_a_timezone():
    assert 'timezone = "Europe/Berlin"' in config_text("d", "h", "b", ["laptop"], "laptop", "me", "NOTION_TOKEN", "Europe/Berlin")
    assert local_timezone()


def test_service_needs_a_config(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "missing.toml"), "service", "--output", str(tmp_path / "svc")]) == 2
    assert not (tmp_path / "svc").exists()


def test_init_does_the_setup_steps(tmp_path, monkeypatch, capsys):
    import boardrunner
    work = tmp_path / "work"
    monkeypatch.setenv("NOTION_TOKEN", "secret_abc")
    monkeypatch.setattr(boardrunner, "init_board", lambda *a, **k: {
        "database_id": "db", "url": "", "human_id": "h", "bot_id": "b", "warnings": []})
    monkeypatch.setattr(boardrunner, "doctor_checks", lambda *a, **k: [(True, "all good")])
    monkeypatch.setattr(boardrunner, "machine_name", lambda: "box")
    monkeypatch.setenv("HOME", str(tmp_path))
    config = tmp_path / "boardrunner.toml"
    assert main(["--config", str(config), "init", "--parent", "0123456789abcdef0123456789abcdef", "--live"]) == 0
    text = config.read_text()
    assert 'mode = "live"' in text and 'name = "box"' in text and 'me = "box"' in text
    assert work.is_dir()
    env = tmp_path / ".env"
    assert env.read_text() == "NOTION_TOKEN=secret_abc\n" and oct(env.stat().st_mode)[-3:] == "600"
    out = capsys.readouterr().out
    assert "ok   all good" in out and "Next: python3 boardrunner.py service" in out


def test_default_agent_name_is_readable(monkeypatch):
    import boardrunner
    monkeypatch.setattr(boardrunner.sys, "platform", "darwin")
    assert boardrunner.machine_name() == "mac"
    monkeypatch.setattr(boardrunner.sys, "platform", "linux")
    assert boardrunner.machine_name() == "linux"


def test_init_refuses_a_folder_launchd_cannot_read_before_touching_notion(tmp_path, monkeypatch):
    import boardrunner
    monkeypatch.setattr(boardrunner.sys, "platform", "darwin")
    monkeypatch.setattr(boardrunner.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("NOTION_TOKEN", "secret_abc")
    called = []
    monkeypatch.setattr(boardrunner, "init_board", lambda *a, **k: called.append(1))
    (tmp_path / "Documents").mkdir()
    config = tmp_path / "Documents" / "boardrunner.toml"
    assert main(["--config", str(config), "init", "--parent", "0123456789abcdef0123456789abcdef"]) == 2
    assert not called and not config.exists()


def test_init_writes_the_claude_path_for_services(tmp_path, monkeypatch):
    import boardrunner
    monkeypatch.setenv("NOTION_TOKEN", "secret_abc")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(boardrunner, "init_board", lambda *a, **k: {
        "database_id": "db", "url": "", "human_id": "h", "bot_id": "b", "warnings": []})
    monkeypatch.setattr(boardrunner, "doctor_checks", lambda *a, **k: [(True, "ok")])
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    fake.write_text("")
    monkeypatch.setattr(boardrunner.shutil, "which", lambda name: str(fake) if name == "claude" else None)
    config = tmp_path / "boardrunner.toml"
    assert main(["--config", str(config), "init", "--parent", "0123456789abcdef0123456789abcdef"]) == 0
    assert f'claude_path = "{fake.resolve()}"' in config.read_text()
    boardrunner.load_config(config)
