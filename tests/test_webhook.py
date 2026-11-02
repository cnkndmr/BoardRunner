import hmac
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from hashlib import sha256
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from boardrunner import (BUSY, Doorbell, _token, listen_service_files, webhook_handler, webhook_path,
                         webhook_signature_ok, webhook_wakes)

HOOK = "/hook/test_path_0123456789abcdef"


def sign(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, sha256).hexdigest()


def test_signature():
    body = b'{"type":"page.created"}'
    assert webhook_signature_ok("s", body, sign("s", body))
    assert not webhook_signature_ok("s", body, sign("other", body))
    assert not webhook_signature_ok("s", body + b" ", sign("s", body))
    assert not webhook_signature_ok("", body, sign("", body))
    assert not webhook_signature_ok("s", body, None)
    assert not webhook_signature_ok("s", body, "sha256=ü")


def test_own_writes_do_not_wake():
    assert not webhook_wakes({"authors": [{"id": "bot", "type": "bot"}]}, "bot")
    assert webhook_wakes({"authors": [{"id": "human", "type": "person"}]}, "bot")
    assert webhook_wakes({"authors": [{"id": "bot"}, {"id": "human"}]}, "bot")
    assert webhook_wakes({"authors": [{"id": "peer-bot", "type": "bot"}]}, "bot")
    assert webhook_wakes({}, "bot")


def test_doorbell_folds_a_burst_into_two_passes():
    started, release = [], threading.Event()

    def run():
        started.append(1)
        release.wait(2)
        return ""

    bell = Doorbell(run, gap=0, log=lambda _: None)
    for _ in range(5):
        bell.ring()
    time.sleep(0.1)
    release.set()
    time.sleep(0.3)
    assert len(started) == 2


def test_doorbell_keeps_passes_apart():
    times = []
    bell = Doorbell(lambda: times.append(time.monotonic()) or "", gap=0.3, log=lambda _: None)
    bell.ring()
    time.sleep(0.05)
    bell.ring()
    time.sleep(0.6)
    assert len(times) == 2 and times[1] - times[0] >= 0.29


def test_doorbell_waits_while_the_timer_holds_the_lock():
    outputs = iter([BUSY, BUSY, "done"])
    calls = []
    bell = Doorbell(lambda: calls.append(1) or next(outputs), gap=0, busy_wait=0, log=lambda _: None)
    bell.ring()
    time.sleep(0.2)
    assert len(calls) == 3


def test_webhook_path_is_created_once_and_kept_private(tmp_path):
    env = tmp_path / ".env"
    env.write_text("NOTION_TOKEN=t\n")
    first = webhook_path(env)
    assert first.startswith("/hook/") and len(first) > 30
    assert webhook_path(env) == first
    assert oct(env.stat().st_mode)[-3:] == "600" and "NOTION_TOKEN=t" in env.read_text()


def test_first_line_wins_in_env(tmp_path, monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    (tmp_path / ".env").write_text("NOTION_TOKEN=good\nNOTION_TOKEN=appended\n")
    assert _token("NOTION_TOKEN", tmp_path / "c.toml") == "good"


def test_listener_service_files():
    mac = listen_service_files("darwin", "/py", Path("/x/boardrunner.py"), Path("/cfg/b.toml"), 9000)
    assert "KeepAlive" in mac["com.boardrunner.listen.plist"] and "<string>9000</string>" in mac["com.boardrunner.listen.plist"]
    linux = listen_service_files("linux", "/py", Path("/x/boardrunner.py"), Path("/cfg/b.toml"))
    assert "Restart=on-failure" in linux["boardrunner-listen.service"] and "listen --port 8787" in linux["boardrunner-listen.service"]


class Bell:
    def __init__(self):
        self.rings = 0

    def ring(self):
        self.rings += 1


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.delenv("NOTION_WEBHOOK_SECRET", raising=False)
    bell, logs = Bell(), []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), webhook_handler(tmp_path / "c.toml", "bot", bell, logs.append, HOOK))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", bell, tmp_path, logs
    getattr(httpd, "shut" + "down")()
    httpd.server_close()


def post(url, body, headers=None, path=HOOK):
    request = urllib.request.Request(url + path, data=body, method="POST",
                                     headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def event(event_id="e1", author="human"):
    return json.dumps({"id": event_id, "type": "page.properties_updated", "authors": [{"id": author}]}).encode()


def test_verification_then_signed_events(server):
    url, bell, folder, logs = server
    token = "secret_verification_0123456789"
    assert post(url, json.dumps({"verification_token": token}).encode()) == 200
    assert f"NOTION_WEBHOOK_SECRET={token}" in (folder / ".env").read_text()
    assert oct((folder / ".env").stat().st_mode)[-3:] == "600"
    assert any(token in line for line in logs)

    body = event()
    assert post(url, body, {"X-Notion-Signature": sign(token, body)}) == 200
    assert bell.rings == 1
    assert post(url, body, {"X-Notion-Signature": sign(token, body)}) == 200  # a replay of the same event
    assert bell.rings == 1
    assert post(url, event("e2"), {"X-Notion-Signature": sign("wrong", event("e2"))}) == 401
    assert post(url, event("e2")) == 401
    assert sum("does not match" in line for line in logs) == 1  # rejections are logged, at most once a minute
    own = event("e3", author="bot")
    assert post(url, own, {"X-Notion-Signature": sign(token, own)}) == 200
    assert bell.rings == 1


def test_wrong_path_is_not_found(server):
    url, bell, folder, logs = server
    assert post(url, json.dumps({"verification_token": "secret_verification_0123456789"}).encode(), path="/") == 404
    assert post(url, b"{}", path="/hook/guess") == 404
    assert not (folder / ".env").exists()


def test_a_second_verification_token_does_not_replace_the_secret(server):
    url, bell, folder, logs = server
    (folder / ".env").write_text("NOTION_TOKEN=t\nNOTION_WEBHOOK_SECRET=first_secret_0123456789\n")
    assert post(url, json.dumps({"verification_token": "second_secret_0123456789"}).encode()) == 200
    text = (folder / ".env").read_text()
    assert "NOTION_WEBHOOK_SECRET=first_secret_0123456789" in text and "second" not in text
    assert any("second_secret_0123456789" in line for line in logs)  # shown, so the owner can choose


def test_verification_token_cannot_inject_lines(server):
    url, bell, folder, logs = server
    assert post(url, json.dumps({"verification_token": "x" * 20 + "\nNOTION_TOKEN=evil"}).encode()) == 400
    assert post(url, json.dumps({"verification_token": 12345678901234567890}).encode()) == 400
    assert not (folder / ".env").exists()


def test_bad_requests(server):
    url, bell, folder, logs = server
    assert post(url, b"not json") == 400
    assert post(url, b"[1]") == 400
    assert post(url, b"{}" + b" " * 70_000) == 413
    host, port = url.removeprefix("http://").split(":")
    with socket.create_connection((host, int(port)), timeout=5) as raw:
        raw.sendall(f"POST {HOOK} HTTP/1.1\r\nHost: x\r\nContent-Length: abc\r\n\r\n".encode())
        assert b" 400 " in raw.recv(100)
    assert bell.rings == 0


def test_a_follow_up_pass_comes_after_the_typing_guard():
    calls = []
    bell = Doorbell(lambda: calls.append(time.monotonic()) or "", gap=0, log=lambda _: None, follow_up=0.3)
    bell.ring()
    time.sleep(0.1)
    bell.ring()  # moves the follow-up later
    time.sleep(0.25)
    assert len(calls) == 2  # the immediate passes, no follow-up yet
    time.sleep(0.3)
    assert len(calls) == 3
    time.sleep(0.4)
    assert len(calls) == 3  # the follow-up does not schedule another


def test_passes_continue_while_work_is_in_flight():
    calls, flight = [], [True, True, False]
    bell = Doorbell(lambda: calls.append(1) or "", gap=0, log=lambda _: None, in_flight=lambda: flight.pop(0), poll=0.1)
    bell.ring(follow=False)
    time.sleep(0.6)
    assert len(calls) == 3
