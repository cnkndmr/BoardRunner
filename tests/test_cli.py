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
