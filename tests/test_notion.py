import pytest

from pathlib import Path

from boardrunner import (NotionBoard, NotionError, SetupError, Schedule, Stage, _same_start, _token, card_from_page,
                         config_text, doctor_checks, init_board, load_config, main, service_files, text_blocks)


def page(**props):
    base = {"Name": {"title": [{"plain_text": " Do it "}]}, "Stage": {"select": {"name": "Approved"}},
            "Assignee": {"select": {"name": "laptop"}}, "Schedule": {"select": {"name": "Now"}},
            "Due": {"date": None}, "Revision note": {"rich_text": []}, "Card No": {"unique_id": {"number": 7}}}
    return {"id": "abcdefgh-1", "created_by": {"id": "h"}, "last_edited_by": {"id": "b"},
            "last_edited_time": "2026-01-01T00:00:00Z", "properties": {**base, **props}}


class Api:
    def __init__(self, replies=None, fail_on=None):
        self.calls, self.replies, self.fail_on = [], replies or {}, fail_on

    def __call__(self, token, method, path, body=None, version=None):
        self.calls.append((method, path, body))
        if self.fail_on and self.fail_on(method, path, len(self.calls)):
            raise NotionError(400, "no")
        reply = self.replies.get((method, path.split("?")[0]), {})
        return reply(body) if callable(reply) else reply


def test_page_becomes_card():
    card = card_from_page(page())
    assert (card.title, card.stage, card.assignee, card.schedule, card.number) == (
        "Do it", Stage.APPROVED, "laptop", Schedule.NOW, "7")
    assert card.edited_by == "b" and card.created_by == "h"


def test_empty_stage_is_inbox_and_unknown_stage_does_not_crash():
    assert card_from_page(page(Stage={"select": None})).stage == Stage.INBOX
    assert card_from_page(page(Stage={"select": {"name": "Renamed"}})).stage == "Renamed"


def test_listing_follows_pages_and_skips_trash():
    first = {"results": [page(), {**page(), "in_trash": True}], "has_more": True, "next_cursor": "n"}
    second = {"results": [page()], "has_more": False}
    api = Api({("POST", "/databases/db/query"): lambda body: second if body.get("start_cursor") else first})
    assert len(NotionBoard("t", "db", api).list_cards()) == 2


def test_write_maps_fields_and_rejects_unknown():
    api = Api()
    board = NotionBoard("t", "db", api)
    board.write("c", stage=Stage.DONE, note="")
    assert api.calls[0][2]["properties"] == {"Stage": {"select": {"name": "Done"}}, "Revision note": {"rich_text": []}}
    with pytest.raises(ValueError):
        board.write("c", nonsense=1)


def test_body_keeps_human_text_only_and_reads_nested_blocks():
    def block(id_, text, by, children=False):
        return {"id": id_, "type": "paragraph", "paragraph": {"rich_text": [{"plain_text": text}]},
                "created_by": {"id": by}, "has_children": children}

    api = Api({("GET", "/blocks/c/children"): {"results": [block("1", "mine", "h", True), block("2", "the plan", "bot")]},
               ("GET", "/blocks/1/children"): {"results": [block("3", "nested", "h")]}})
    assert NotionBoard("t", "db", api, bot_id="bot").read_body("c") == "mine\nnested"


def test_markdown_becomes_notion_blocks():
    blocks = text_blocks("# Title\n\n- one **bold** `code`\n2. two\n```\nx = *1*\n```\nplain **")
    assert [b["type"] for b in blocks] == ["heading_1", "bulleted_list_item", "numbered_list_item", "code", "paragraph"]
    parts = blocks[1]["bulleted_list_item"]["rich_text"]
    assert [p["text"]["content"] for p in parts] == ["one ", "bold", " ", "code"]
    assert parts[1]["annotations"] == {"bold": True} and parts[3]["annotations"] == {"code": True}
    assert blocks[3]["code"]["rich_text"][0]["text"]["content"] == "x = *1*"
    assert blocks[4]["paragraph"]["rich_text"][0]["text"]["content"] == "plain **"


def test_unclosed_code_fence_keeps_the_text():
    assert text_blocks("```\nkept")[0]["code"]["rich_text"][0]["text"]["content"] == "kept"


def test_long_text_is_split_within_notion_limits():
    api = Api()
    NotionBoard("t", "db", api).append("c", "\n".join(["x" * 4500] * 150))
    assert [len(call[2]["children"]) for call in api.calls] == [100, 50]
    assert all(len(part["text"]["content"]) <= 2000 for part in api.calls[0][2]["children"][0]["paragraph"]["rich_text"])


def test_failed_append_removes_what_it_added():
    api = Api({("PATCH", "/blocks/c/children"): {"results": [{"id": "b1"}]}}, fail_on=lambda m, p, n: m == "PATCH" and n == 2)
    with pytest.raises(NotionError):
        NotionBoard("t", "db", api).append("c", "\n".join(["x"] * 150))
    assert api.calls[-1][:2] == ("DELETE", "/blocks/b1")


USERS = {("GET", "/users/me"): {"id": "bot"}, ("GET", "/users"): {"results": [{"id": "h", "type": "person"},
                                                                             {"id": "x", "type": "bot"}]},
         ("POST", "/databases"): {"id": "db", "url": "u"}}


def test_init_creates_the_schema_and_finds_the_only_person():
    api = Api(USERS)
    made = init_board("t", "parent", ["laptop", "server"], "me", "Board", request=api)
    assert (made["database_id"], made["human_id"], made["bot_id"]) == ("db", "h", "bot")
    schema = next(c[2] for c in api.calls if c[:2] == ("POST", "/databases"))["properties"]
    assert {o["name"] for o in schema["Assignee"]["select"]["options"]} == {"laptop", "server", "me"}
    assert {o["name"] for o in schema["Stage"]["select"]["options"]} == set(Stage)


def test_init_asks_when_several_people_exist():
    users = {**USERS, ("GET", "/users"): {"results": [{"id": "a", "type": "person"}, {"id": "b", "type": "person"}]}}
    with pytest.raises(SetupError, match="--human-user"):
        init_board("t", "p", ["laptop"], "me", "B", request=Api(users))
    assert init_board("t", "p", ["laptop"], "me", "B", human_user="a", request=Api(users))["human_id"] == "a"


def test_failed_view_is_only_a_warning():
    api = Api(USERS, fail_on=lambda m, p, n: "/views" in p or m == "PATCH")
    assert len(init_board("t", "p", ["laptop"], "me", "B", request=api)["warnings"]) == 2


def test_generated_config_gives_the_same_policy_on_every_agent(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text(config_text("db", "h", "bot", ["laptop", "server"], "laptop", "me", "NOTION_TOKEN"))
    config, policy = load_config(path)
    assert (policy.me, policy.peers, policy.humans) == ("laptop", frozenset({"server"}), frozenset({"me"}))
    assert config["mode"] == "dry-run"
    assert "secret" not in path.read_text().lower()


def test_bad_config_is_explained(tmp_path):
    with pytest.raises(SetupError, match="init"):
        load_config(tmp_path / "none.toml")
    path = tmp_path / "c.toml"
    path.write_text('me = "ghost"\n[[agents]]\nname = "laptop"\n')
    with pytest.raises(SetupError, match="ghost"):
        load_config(path)


def test_init_refuses_to_overwrite_a_config(tmp_path, capsys, monkeypatch):
    path = tmp_path / "c.toml"
    path.write_text("x")
    monkeypatch.setenv("NOTION_TOKEN", "t")
    assert main(["--config", str(path), "init", "--parent", "p", "--agent", "a"]) == 2
    assert path.read_text() == "x" and "already exists" in capsys.readouterr().err


def test_missing_token_is_explained(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    assert main(["--config", str(tmp_path / "c.toml"), "init", "--parent", "p", "--agent", "a"]) == 2
    assert "NOTION_TOKEN" in capsys.readouterr().err


def doctor_setup(tmp_path, human="h"):
    path = tmp_path / "c.toml"
    (tmp_path / "work").mkdir()
    path.write_text(config_text("db", human, "bot", ["laptop", "server"], "laptop", "me", "NOTION_TOKEN").replace(
        '"~/work"', f'"{tmp_path}/work"').replace('sandbox = "strict"', f'sandbox = "off"\nclaude_path = "/bin/echo"'))
    return load_config(path)


def good_db():
    props = {name: {} for name in ("Stage", "Schedule", "Due", "Revision note", "Archive")}
    props["Assignee"] = {"select": {"options": [{"name": "laptop"}, {"name": "server"}]}}
    return {"properties": props}


def test_doctor_passes_on_a_good_setup(tmp_path):
    config, policy = doctor_setup(tmp_path)
    api = Api({("GET", "/users/me"): {"id": "bot"}, ("GET", "/databases/db"): good_db()})
    assert all(ok for ok, _ in doctor_checks(config, policy, "t", api))


def test_doctor_names_each_problem(tmp_path):
    config, policy = doctor_setup(tmp_path, human="bot")
    db = good_db()
    del db["properties"]["Stage"]
    db["properties"]["Assignee"]["select"]["options"].pop()
    api = Api({("GET", "/users/me"): {"id": "other"}, ("GET", "/databases/db"): db})
    failed = " | ".join(text for ok, text in doctor_checks(config, policy, "t", api) if not ok)
    for phrase in ("does not match", "Stage", "server", "human_id equals bot_id"):
        assert phrase in failed


def test_doctor_survives_notion_being_down(tmp_path):
    config, policy = doctor_setup(tmp_path)
    assert any(not ok and "Notion" in text for ok, text in doctor_checks(config, policy, "t", Api(fail_on=lambda *_: True)))


def test_service_files_repeat_one_pass():
    linux = service_files("linux", "/py", Path("/a/boardrunner.py"), Path("/a/c.toml"))
    assert "ExecStart=/py /a/boardrunner.py --config /a/c.toml run --once" in linux["boardrunner.service"]
    assert "KillMode=process" in linux["boardrunner.service"]
    assert "OnUnitActiveSec=60" in linux["boardrunner.timer"]
    mac = service_files("darwin", "/py", Path("/a/boardrunner.py"), Path("/a/c.toml"))
    assert "<integer>60</integer>" in mac["com.boardrunner.plist"]


def test_token_from_a_file_beside_the_config(tmp_path, monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    (tmp_path / ".env").write_text('NOTION_TOKEN="abc"\n')
    assert _token("NOTION_TOKEN", tmp_path / "c.toml") == "abc"


def test_start_time_comparison():
    assert _same_start("x", "x") and not _same_start(None, None) and not _same_start("x", None)
