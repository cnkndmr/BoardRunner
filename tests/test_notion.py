import pytest

from boardrunner import (NotionBoard, NotionError, SetupError, Schedule, Stage, card_from_page, config_text, init_board,
                         load_config, main)


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
