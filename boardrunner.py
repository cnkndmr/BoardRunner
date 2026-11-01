"""BoardRunner: run AI coding agents from a kanban board. Single file, standard library only.

The state machine (`decide`) touches no network, process or file; its only side effect is on `State`.
`run_pass` applies its decisions through two small interfaces, `Board` and `Runner`.
Three rules shape the order of the checks in `decide`:

1. Ownership comes first, so another agent's card never fails or loses its note here.
2. A revision note comes before stage logic, and a note plus a stage change cannot start a job.
3. Only a human edit can start a job (`human_approved`).
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import socket
import sys
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

__version__ = "0.0.1"


class Stage(StrEnum):
    INBOX = "Inbox"
    PLANNING = "Planning"
    PLAN_READY = "Plan ready"
    APPROVED = "Approved"
    FOR_HUMAN = "For human"
    RUNNING = "Running"
    DONE = "Done"
    FAILED = "Failed"
    STOPPED = "Stopped"


class Schedule(StrEnum):
    NOW = "Now"
    NIGHT = "Night"
    AT_DATE = "At date"
    MANUAL = "Manual"


class Action(StrEnum):
    WAIT = "wait"
    WARN = "warn"  # nothing happens; the reason is shown to the human once
    DRAFT = "draft"
    CANCEL_DRAFT = "cancel_draft"
    DRAFT_BACK = "draft_back"  # the card moved on while its plan is still being written
    BACK_TO_PLAN = "back_to_plan"
    DRAFT_FAILED = "draft_failed"
    REVISE = "revise"
    REVISE_CLOSED = "revise_closed"
    START = "start"
    CLAIM = "claim"  # the process started, but writing the stage failed
    RECOVER = "recover"
    RECOVER_ORPHAN = "recover_orphan"
    STOP = "stop"
    STOP_REASSIGNED = "stop_reassigned"
    HAND_TO_HUMAN = "hand_to_human"


NOT_CREATED_BY_HUMAN = "Card was not created by the board owner"
NOT_APPROVED = "Last edit is not a verified human edit"
START_NOT_APPROVED = "Approved stage or assignee was not set by a human"
PICK_ASSIGNEE = "Assignee is not an agent; the card will not move"
NOTE_ON_HUMAN_CARD = "Revision note on a card assigned to a human"
RUN_ELSEWHERE = "Running, but not on this machine"
PEER_SILENT = "Assigned to another machine, and nobody has picked it up"
PLAN_DROPPED = "Plan dropped: the assignee was changed by an unverified edit"


@dataclass
class Card:
    id: str
    number: str
    title: str
    stage: Stage = Stage.INBOX
    schedule: Schedule | None = None
    due: str | None = None
    revision_note: str = ""
    created_by: str | None = None
    edited_by: str | None = None
    edited_at: str | None = None
    assignee: str | None = None


@dataclass
class State:
    """What must survive between passes. Plain containers, so it serializes to JSON."""

    cards: dict[str, dict] = field(default_factory=dict)
    human_view: dict[str, dict] = field(default_factory=dict)  # last card view a human approved
    bot_view: dict[str, dict] = field(default_factory=dict)  # last card view this runner wrote
    bot_writing: dict[str, dict] = field(default_factory=dict)  # write announced, not yet confirmed
    drafts: dict[str, dict] = field(default_factory=dict)
    jobs: dict[str, dict] = field(default_factory=dict)

    def record(self, card_id: str) -> dict:
        return self.cards.setdefault(card_id, {})


@dataclass(frozen=True)
class Policy:
    me: str  # this machine's assignee name
    human_id: str | None  # the board owner's user id
    bot_id: str | None  # this runner's user id
    peers: frozenset[str] = frozenset()  # other agents' assignee names
    humans: frozenset[str] = frozenset()  # assignee names that mean "a person does this"
    peer_bot_ids: frozenset[str] = frozenset()  # user ids of the other runners
    primary: bool = True  # exactly one machine handles human cards and shared warnings
    max_jobs: int = 3
    max_drafts: int = 2
    max_draft_attempts: int = 3
    quiet_seconds: int = 120  # a title or note this fresh may still be being typed
    peer_silence_seconds: int = 1800
    night_start_hour: int = 1
    night_last_start: tuple[int, int] = (5, 30)
    timezone: str = "UTC"


DRAFT_FLAGS = ("plan_hash", "approved_plan_hash", "plan_collected", "retry_at", "start_attempts", "plan_blocks",
               "foreign_blocks")


def text_hash(text: str | None) -> str:
    return sha256((text or "").encode()).hexdigest()[:16]


def view(card: Card) -> dict:
    """The fields that count for approval. Comparing views shows whether anyone changed the card."""
    return {
        "stage": card.stage,
        "assignee": card.assignee,
        "schedule": card.schedule,
        "due": card.due,
        "title": text_hash(card.title),
        "note": text_hash(card.revision_note.strip()),
    }


def _write_confirmed(card_id: str, state: State) -> None:
    write = state.bot_writing.pop(card_id)
    state.bot_view[card_id] = write["view"]
    if write["stage_changed"]:  # an old approval must not be reusable after the card moved
        state.human_view.pop(card_id, None)
        record = state.cards.get(card_id) or {}
        record.pop("rejected_stage", None)
        record.pop("rejected_notified", None)


def human_approved(card: Card, state: State, policy: Policy) -> bool:
    """True when the current card view can be trusted as human intent.

    A human edit approves the whole view. After a bot edit, the view must equal what the human last approved or
    what the bot itself wrote. Anything else, including an edit by a third party, is rejected.
    """
    if not policy.human_id:
        return False
    current = view(card)
    if card.edited_by == policy.human_id:
        record = state.cards.get(card.id) or {}
        if record.get("rejected_stage") == current["stage"]:
            # Edits are not tracked per field: an unrelated human edit must not approve a rejected stage.
            current = {**current, "stage": (state.human_view.get(card.id) or {}).get("stage")}
        else:
            record.pop("rejected_stage", None)
            record.pop("rejected_notified", None)
        state.human_view[card.id] = current
        state.bot_view.pop(card.id, None)
        state.bot_writing.pop(card.id, None)
        return True
    if policy.bot_id and card.edited_by == policy.bot_id:
        if current == (state.bot_writing.get(card.id) or {}).get("view"):  # crashed after the write: finish the record
            _write_confirmed(card.id, state)
            return True
        return current in (state.human_view.get(card.id), state.bot_view.get(card.id))
    return False


def record_bot_write(card: Card, state: State, policy: Policy, write: Callable[..., None],
                     persist: Callable[[State], None], **fields) -> None:
    """Every field write goes through here: announce the expected view on disk, write, then confirm.

    A crash before the write leaves the old view valid; a crash after it matches `bot_writing`.
    """
    if "title" in fields:
        fields["title"] = fields["title"].strip()[:120]
    changes = {}
    for name, value in fields.items():
        if name == "title":
            changes["title"] = text_hash(value)
        elif name == "note":
            changes["note"] = text_hash((value or "").strip())
        else:
            changes[name] = value
    if human_approved(card, state, policy):
        base = view(card)
    else:
        base = state.bot_view.get(card.id) or state.human_view.get(card.id) or (
            view(card) if card.edited_by == policy.bot_id else {})
    state.bot_writing[card.id] = {"view": {**base, **changes}, "stage_changed": "stage" in fields}
    persist(state)
    write(card.id, **fields)
    _write_confirmed(card.id, state)
    persist(state)
    for name, value in fields.items():  # a second write in the same action must not start from a stale view
        setattr(card, {"note": "revision_note"}.get(name, name), value)
    card.edited_by = policy.bot_id


def _parse_time(text: str | None, policy: Policy) -> datetime | None:
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=ZoneInfo(policy.timezone))


def is_due(card: Card, now: datetime, policy: Policy) -> tuple[bool, str]:
    local = now.astimezone(ZoneInfo(policy.timezone))
    schedule = card.schedule or Schedule.MANUAL
    if schedule == Schedule.NOW:
        return True, "now"
    if schedule == Schedule.NIGHT:
        inside = policy.night_start_hour <= local.hour and (local.hour, local.minute) < policy.night_last_start
        return inside, "night window" if inside else "waiting for the night window"
    if schedule == Schedule.AT_DATE:
        due = _parse_time(card.due, policy)
        if due is None:
            return False, "schedule is a date, but the date is empty"
        return now >= due, f"date reached ({card.due})" if now >= due else f"waiting for {card.due}"
    return False, "manual: never starts by itself"


def _still_being_typed(card: Card, now: datetime, policy: Policy) -> bool:
    edited = _parse_time(card.edited_at, policy)
    return edited is not None and (now - edited).total_seconds() < policy.quiet_seconds


def _peer_silent(card: Card, now: datetime, policy: Policy) -> bool:
    edited = _parse_time(card.edited_at, policy)
    waiting = card.stage == Stage.INBOX or (card.stage == Stage.APPROVED and card.schedule == Schedule.NOW)
    return bool(card.title) and waiting and edited is not None and (
        (now - edited).total_seconds() >= policy.peer_silence_seconds)


def _no_trust_needed(card: Card, policy: Policy) -> bool:
    return (card.stage == Stage.FOR_HUMAN and card.assignee in policy.peers | policy.humans) or (
        card.stage == Stage.DONE and card.assignee in policy.peers)


def _own_process(card: Card, dry_run: bool, has_job_file: Callable[[Card], bool]) -> tuple[Action, str]:
    if card.stage == Stage.RUNNING and not dry_run and has_job_file(card):
        return Action.RECOVER_ORPHAN, "an unrecorded process may exist"
    return Action.WAIT, f"assignee: {card.assignee or 'empty'}"


def _decide_foreign(card: Card, state: State, policy: Policy, now: datetime, dry_run: bool,
                    has_job_file: Callable[[Card], bool]) -> tuple[Action, str]:
    """The card belongs to someone else. Notes and stage changes must not wake this runner."""
    if card.id in state.drafts:
        return Action.CANCEL_DRAFT, "assignee changed"
    record = state.cards.get(card.id) or {}
    if not dry_run:  # if the card comes back, the old plan must not count as approved
        had_plan = record.get("plan_collected")
        for name in (*DRAFT_FLAGS, "attempts", "draft_error"):
            record.pop(name, None)
        if had_plan and card.edited_by not in (policy.human_id, policy.bot_id, *policy.peer_bot_ids):
            return Action.WARN, PLAN_DROPPED
    if not policy.primary:
        return _own_process(card, dry_run, has_job_file)
    if card.stage in (Stage.FOR_HUMAN, Stage.DONE) and not _no_trust_needed(card, policy) and not human_approved(
            card, state, policy):
        return Action.WARN, NOT_APPROVED
    if card.stage == Stage.FOR_HUMAN:
        if card.assignee in policy.humans and card.revision_note.strip():
            return Action.WARN, NOTE_ON_HUMAN_CARD
        return Action.WAIT, "with a human"
    if card.stage == Stage.DONE:
        return Action.WAIT, "done"
    if card.assignee in policy.humans and card.stage == Stage.INBOX:
        if not human_approved(card, state, policy):
            return Action.WARN, NOT_APPROVED
        if not card.title:
            return Action.WAIT, "empty title"
        if _still_being_typed(card, now, policy):
            return Action.WAIT, "title may still be being typed"
        return Action.HAND_TO_HUMAN, "assigned to a human"
    if (card.assignee not in policy.peers and card.stage == Stage.APPROVED) or (
            card.assignee in policy.humans and card.stage in (Stage.PLANNING, Stage.PLAN_READY)):
        return Action.WARN, PICK_ASSIGNEE
    if card.assignee in policy.peers and policy.peer_bot_ids and _peer_silent(card, now, policy):
        return Action.WARN, PEER_SILENT
    return _own_process(card, dry_run, has_job_file)


def decide(card: Card, state: State, policy: Policy, now: datetime, dry_run: bool = False,
           has_job_file: Callable[[Card], bool] = lambda _: False,
           job_alive: Callable[[Card], str] = lambda _: "no") -> tuple[Action, str]:
    """Return (action, reason). `has_job_file` and `job_alive` report what is known about this machine's processes."""
    if not policy.human_id or card.created_by != policy.human_id:
        return Action.WARN, NOT_CREATED_BY_HUMAN
    if card.id in state.jobs:
        job = state.jobs[card.id]
        if job.get("stopping"):
            return Action.WAIT, "stopping"
        if card.assignee != policy.me:  # two machines must never run the same job; stopping never starts anything
            verified = human_approved(card, state, policy) or card.edited_by in policy.peer_bot_ids
            return (Action.STOP_REASSIGNED, "assignee changed") if verified else (Action.WARN, NOT_APPROVED)
        if card.stage == Stage.STOPPED:
            if human_approved(card, state, policy):
                return Action.STOP, "a human stopped it"
            return Action.WARN, NOT_APPROVED
        if job.get("status") == "starting" and card.stage == Stage.APPROVED:
            return Action.CLAIM, "process started, stage not written"
        return Action.WAIT, "running"
    if card.assignee != policy.me:
        return _decide_foreign(card, state, policy, now, dry_run, has_job_file)
    if card.stage == Stage.RUNNING:
        if policy.peer_bot_ids and card.edited_by != policy.bot_id and job_alive(card) == "no":
            return Action.WARN, RUN_ELSEWHERE
        return Action.RECOVER, "board says running, but no job is recorded"
    if policy.peer_bot_ids and card.stage == Stage.STOPPED and card.edited_by in policy.peer_bot_ids:
        return Action.WAIT, "stopped by the previous owner"
    if not human_approved(card, state, policy):
        return Action.WARN, NOT_APPROVED
    if card.revision_note.strip():
        if _still_being_typed(card, now, policy):
            return Action.WAIT, "note may still be being typed"
        if card.stage == Stage.DONE:
            return Action.REVISE_CLOSED, "the job already finished"
        return Action.REVISE, "revision note"

    drafting = card.id in state.drafts
    record = state.cards.get(card.id) or {}
    timestamp = now.timestamp()

    def next_draft() -> tuple[Action, str]:
        if record.get("attempts", 0) >= policy.max_draft_attempts:
            return Action.DRAFT_FAILED, "draft attempts used up"
        if record.get("retry_at", 0) > timestamp:
            return Action.WAIT, f"draft retry at {_clock(record['retry_at'], policy)}"
        if len(state.drafts) >= policy.max_drafts:
            return Action.WAIT, f"draft limit {policy.max_drafts}"
        return Action.DRAFT, "plan to be written"

    if card.stage == Stage.INBOX:
        if not card.title:
            return Action.WAIT, "empty title"
        if drafting:
            return Action.CANCEL_DRAFT, "moved back to inbox"
        if _still_being_typed(card, now, policy):
            return Action.WAIT, "title may still be being typed"
        return next_draft()
    if card.stage == Stage.PLANNING:
        return (Action.WAIT, "plan being written") if drafting else next_draft()
    if card.stage == Stage.PLAN_READY:
        if drafting:
            return Action.DRAFT_BACK, "plan still being written"
        return (Action.WAIT, "waiting for a human") if record.get("plan_collected") else next_draft()
    if card.stage == Stage.APPROVED:
        if drafting:
            return Action.DRAFT_BACK, "plan still being written"
        if not record.get("plan_collected"):
            return next_draft()
        if not record.get("plan_hash") and not dry_run:
            return Action.BACK_TO_PLAN, "no plan"
        approval = state.human_view.get(card.id) or {}
        if (approval.get("stage") != Stage.APPROVED or approval.get("assignee") != policy.me) and not dry_run:
            return Action.WARN, START_NOT_APPROVED
        if record.get("retry_at", 0) > timestamp:
            return Action.WAIT, f"start retry at {_clock(record['retry_at'], policy)}"
        due, reason = is_due(card, now, policy)
        if not due:
            return Action.WAIT, reason
        if len(state.jobs) >= policy.max_jobs:
            return Action.WAIT, f"job limit {policy.max_jobs}"
        return Action.START, reason
    if card.stage == Stage.FOR_HUMAN:
        return (Action.CANCEL_DRAFT, "moved by a human") if drafting else (Action.WAIT, "with a human")
    if card.stage == Stage.DONE:
        return (Action.CANCEL_DRAFT, "moved by a human") if drafting else (Action.WAIT, "done")
    if card.stage in (Stage.FAILED, Stage.STOPPED):
        if drafting:
            return Action.CANCEL_DRAFT, "moved by a human"
        if card.stage == Stage.FAILED and record.get("attempts", 0) >= policy.max_draft_attempts:
            return Action.DRAFT_FAILED, "draft attempts used up"
    return Action.WAIT, str(card.stage)


def _clock(timestamp: float, policy: Policy) -> str:
    return f"{datetime.fromtimestamp(timestamp, ZoneInfo(policy.timezone)):%H:%M}"


RETRY_SECONDS = 600
RUNNING_STATUS = "running"


class Board(Protocol):
    def list_cards(self) -> list[Card]: ...
    def write(self, card_id: str, **fields) -> None: ...
    def append(self, card_id: str, text: str) -> None: ...


class Runner(Protocol):
    def start(self, kind: str, card: Card, prompt: str) -> str: ...
    def status(self, handle: str) -> str: ...  # running | done | failed
    def result(self, handle: str) -> str: ...
    def stop(self, handle: str) -> None: ...


def plan_prompt(card: Card, record: dict) -> str:
    notes = "".join(f"\nRevision note: {note}" for note in record.get("revisions", []))
    return f"Write a plan for this task. Do not do the work yet.\nTask: {card.title}{notes}"


def job_prompt(card: Card, plan: str) -> str:
    return f"Do this task by following the approved plan.\nTask: {card.title}\nPlan:\n{plan}"


def _clear_plan(record: dict) -> None:
    for name in (*DRAFT_FLAGS, "plan", "attempts"):
        record.pop(name, None)


def _collect(cards: dict[str, Card], board: Board, runner: Runner, state: State, policy: Policy, now: datetime,
             persist: Callable[[State], None]) -> list[str]:
    """Harvest finished drafts and jobs. A card a human moved meanwhile keeps its stage."""
    log = []
    for card_id, draft in list(state.drafts.items()):
        status = runner.status(draft["handle"])
        if status == RUNNING_STATUS:
            continue
        del state.drafts[card_id]
        record = state.record(card_id)
        card = cards.get(card_id)
        if status != "done":
            record["attempts"] = record.get("attempts", 0) + 1
            record["retry_at"] = now.timestamp() + RETRY_SECONDS * record["attempts"]
            log.append(f"{card_id}: draft failed")
        elif card and card.stage == Stage.PLANNING and card.assignee == policy.me:
            plan = runner.result(draft["handle"])
            record.update(plan=plan, plan_hash=text_hash(plan), plan_collected=True)
            record.pop("attempts", None)
            board.append(card_id, plan)
            record_bot_write(card, state, policy, board.write, persist, stage=Stage.PLAN_READY)
            log.append(f"{card_id}: plan ready")
        persist(state)
    for card_id, job in list(state.jobs.items()):
        status = runner.status(job["handle"])
        if status == RUNNING_STATUS:
            continue
        del state.jobs[card_id]
        card = cards.get(card_id)
        if not job.get("stopping") and card:
            board.append(card_id, runner.result(job["handle"]))
            final = Stage.DONE if status == "done" else Stage.FAILED
            record_bot_write(card, state, policy, board.write, persist, stage=final)
            log.append(f"{card_id}: {final}")
        persist(state)
    return log


def _apply(action: Action, reason: str, card: Card, board: Board, runner: Runner, state: State, policy: Policy,
           persist: Callable[[State], None]) -> None:
    record = state.record(card.id)

    def move(stage: Stage, **fields) -> None:
        record_bot_write(card, state, policy, board.write, persist, stage=stage, **fields)

    def cancel_draft() -> None:
        draft = state.drafts.pop(card.id, None)
        if draft:
            runner.stop(draft["handle"])

    if action == Action.WARN:
        if record.get("warned") != reason:
            record["warned"] = reason
            board.append(card.id, f"BoardRunner: {reason}")
    elif action == Action.DRAFT:
        state.drafts[card.id] = {"handle": runner.start("plan", card, plan_prompt(card, record))}
        persist(state)
        if card.stage != Stage.PLANNING:
            move(Stage.PLANNING)
    elif action == Action.CANCEL_DRAFT:
        cancel_draft()
    elif action == Action.DRAFT_BACK:
        move(Stage.PLANNING)
    elif action == Action.BACK_TO_PLAN:
        _clear_plan(record)
        move(Stage.PLANNING)
    elif action == Action.DRAFT_FAILED:
        record.pop("attempts", None)
        board.append(card.id, "BoardRunner: the plan could not be written")
        move(Stage.FAILED)
    elif action == Action.REVISE:
        cancel_draft()
        record.setdefault("revisions", []).append(card.revision_note.strip())
        _clear_plan(record)
        move(Stage.PLANNING, note="")
    elif action == Action.REVISE_CLOSED:
        board.append(card.id, "BoardRunner: this job already finished. Open a new card for more work.")
        record_bot_write(card, state, policy, board.write, persist, note="")
    elif action == Action.START:
        handle = runner.start("job", card, job_prompt(card, record.get("plan", "")))
        state.jobs[card.id] = {"handle": handle, "status": "starting"}
        persist(state)
        move(Stage.RUNNING)
        state.jobs[card.id]["status"] = RUNNING_STATUS
    elif action == Action.CLAIM:
        move(Stage.RUNNING)
        state.jobs[card.id]["status"] = RUNNING_STATUS
    elif action == Action.RECOVER:
        board.append(card.id, "BoardRunner: no record of this job, marking it failed")
        move(Stage.FAILED)
    elif action in (Action.STOP, Action.STOP_REASSIGNED):
        job = state.jobs[card.id]
        job["stopping"] = True
        runner.stop(job["handle"])
    elif action == Action.HAND_TO_HUMAN:
        move(Stage.FOR_HUMAN)
    persist(state)


def run_pass(board: Board, runner: Runner, state: State, policy: Policy, now: datetime, dry_run: bool = False,
             persist: Callable[[State], None] = lambda _: None) -> list[str]:
    """One pass over the board. In a dry run nothing is written and nothing starts."""
    cards = {card.id: card for card in board.list_cards()}
    log = [] if dry_run else _collect(cards, board, runner, state, policy, now, persist)
    for card in cards.values():
        action, reason = decide(card, state, policy, now, dry_run)
        if action != Action.WAIT:
            log.append(f"{card.id}: {action} ({reason})")
            if not dry_run:
                _apply(action, reason, card, board, runner, state, policy, persist)
    return log


NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
NOTION_VIEWS_VERSION = "2026-03-11"
MAX_TEXT = 2000  # Notion's limit for one text object
MAX_BLOCKS = 100  # and for one append request
FIELD_NAMES = {"title": "Name", "stage": "Stage", "assignee": "Assignee", "schedule": "Schedule", "note": "Revision note"}
STAGE_COLORS = {Stage.INBOX: "gray", Stage.PLANNING: "purple", Stage.PLAN_READY: "yellow", Stage.APPROVED: "blue",
                Stage.FOR_HUMAN: "orange", Stage.RUNNING: "green", Stage.DONE: "default", Stage.FAILED: "red",
                Stage.STOPPED: "brown"}
SCHEDULE_COLORS = {Schedule.NOW: "red", Schedule.NIGHT: "blue", Schedule.AT_DATE: "orange", Schedule.MANUAL: "gray"}
NOT_ARCHIVED = {"property": "Archive", "checkbox": {"equals": False}}


class SetupError(Exception):
    """A problem the user can fix; shown without a traceback."""


class NotionError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _safe_to_resend(method: str, path: str) -> bool:
    """Resending must not duplicate anything. Appending blocks or creating pages would."""
    if method in ("GET", "DELETE"):
        return True
    if method == "POST":
        return path.endswith("/query")
    return not path.endswith("/children")


def notion_request(token: str, method: str, path: str, body: dict | None = None, version: str = NOTION_VERSION) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(5):
        request = urllib.request.Request(NOTION_API + path, data=data, method=method, headers={
            "Authorization": f"Bearer {token}", "Notion-Version": version, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            # 429 was not processed; after 502 or 503 the server may have processed the request.
            if attempt < 4 and (error.code == 429 or (error.code in (502, 503) and _safe_to_resend(method, path))):
                try:
                    time.sleep(float(error.headers.get("Retry-After") or 2 ** attempt))
                except ValueError:
                    time.sleep(2 ** attempt)
                continue
            raise NotionError(error.code, f"{method} {path}: {error.code} {error.read()[:300]!r}") from error
        except (OSError, http.client.HTTPException, ValueError) as error:
            reason = getattr(error, "reason", error)
            never_sent = isinstance(reason, (socket.gaierror, ConnectionRefusedError))
            if attempt < 4 and (never_sent or _safe_to_resend(method, path)):
                time.sleep(2 ** attempt)
                continue
            raise NotionError(0, f"{method} {path}: {error}") from error
    raise NotionError(0, f"{method} {path}: out of attempts")


def _plain(prop: dict | None):
    value = prop or {}
    for kind in ("title", "rich_text"):
        if kind in value:
            return "".join(part.get("plain_text") or part.get("text", {}).get("content", "") for part in value[kind] or [])
    if "select" in value:
        return (value["select"] or {}).get("name")
    if "date" in value:
        return (value["date"] or {}).get("start")
    if "unique_id" in value:
        unique = value["unique_id"] or {}
        return str(unique["number"]) if unique.get("number") is not None else None
    return None


def _known(kind, value):
    """A renamed or unknown option stays a plain string: `decide` then waits instead of crashing the pass."""
    try:
        return kind(value)
    except ValueError:
        return value


def card_from_page(page: dict) -> Card:
    props = page.get("properties", {})
    title = next((_plain(value) for value in props.values() if "title" in (value or {})), "") or ""
    return Card(id=page["id"], number=_plain(props.get("Card No")) or page["id"][:8], title=title.strip(),
                stage=_known(Stage, _plain(props.get("Stage")) or Stage.INBOX),
                schedule=_known(Schedule, value) if (value := _plain(props.get("Schedule"))) else None,
                due=_plain(props.get("Due")), revision_note=_plain(props.get("Revision note")) or "",
                created_by=(page.get("created_by") or {}).get("id"),
                edited_by=(page.get("last_edited_by") or {}).get("id"), edited_at=page.get("last_edited_time"),
                assignee=_plain(props.get("Assignee")))


def _rich_text(text: str) -> list[dict]:
    pieces = [text[i:i + MAX_TEXT] for i in range(0, len(text), MAX_TEXT)]
    return [{"type": "text", "text": {"content": piece}} for piece in pieces[:100]]


def text_blocks(text: str) -> list[dict]:
    """One paragraph block per line. Keeping Markdown formatting is left for later."""
    return [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rich_text(line)}}
            for line in text.splitlines() if line.strip()]


class NotionBoard:
    def __init__(self, token: str, database_id: str, request: Callable[..., dict] = notion_request):
        self.token, self.database_id, self._request = token, database_id, request

    def _call(self, *args, **kwargs) -> dict:
        return self._request(self.token, *args, **kwargs)

    def list_cards(self) -> list[Card]:
        pages, body = [], {"page_size": 100, "filter": NOT_ARCHIVED}
        while True:
            reply = self._call("POST", f"/databases/{self.database_id}/query", body)
            pages += [page for page in reply.get("results", []) if not page.get("archived") and not page.get("in_trash")]
            if not reply.get("has_more"):
                return [card_from_page(page) for page in pages]
            body = {**body, "start_cursor": reply["next_cursor"]}

    def write(self, card_id: str, **fields) -> None:
        props = {}
        for name, value in fields.items():
            if name not in FIELD_NAMES:
                raise ValueError(name)
            if name == "title":
                props[FIELD_NAMES[name]] = {"title": _rich_text(value)}
            elif name == "note":
                props[FIELD_NAMES[name]] = {"rich_text": _rich_text(value) if value else []}
            else:
                props[FIELD_NAMES[name]] = {"select": {"name": str(value)} if value else None}
        self._call("PATCH", f"/pages/{card_id}", {"properties": props})

    def append(self, card_id: str, text: str) -> None:
        """All or nothing: if a later chunk fails, earlier chunks are removed so a retry cannot duplicate them."""
        blocks, added = text_blocks(text), []
        try:
            for start in range(0, len(blocks), MAX_BLOCKS):
                reply = self._call("PATCH", f"/blocks/{card_id}/children", {"children": blocks[start:start + MAX_BLOCKS]})
                added += [block["id"] for block in reply.get("results", []) if block.get("id")]
        except Exception:
            for block_id in added:
                try:
                    self._call("DELETE", f"/blocks/{block_id}")
                except NotionError:
                    pass  # the original error is the one to report
            raise


def _options(colors: dict) -> dict:
    return {"select": {"options": [{"name": str(name), "color": color} for name, color in colors.items()]}}


def _schema(assignees: list[str]) -> dict:
    return {"Name": {"title": {}}, "Stage": _options(STAGE_COLORS),
            "Assignee": _options({name: "default" for name in assignees}), "Revision note": {"rich_text": {}},
            "Schedule": _options(SCHEDULE_COLORS), "Due": {"date": {}}, "Archive": {"checkbox": {}}}


def _find_human(call: Callable[..., dict], given: str | None) -> str:
    if given:
        return given
    people, path = [], "/users?page_size=100"
    while True:
        reply = call("GET", path)
        people += [user for user in reply.get("results", []) if user.get("type") == "person"]
        if not reply.get("has_more"):
            break
        path = f"/users?page_size=100&start_cursor={reply['next_cursor']}"
    if len(people) != 1:
        listing = "\n".join(f"  {user['id']} {user.get('name', '')}" for user in people)
        raise SetupError(f"Found {len(people)} people in this workspace; choose one with --human-user:\n{listing}")
    return people[0]["id"]


def _create_board_view(call: Callable[..., dict], database_id: str) -> None:
    """Add the kanban view and remove Notion's default table. Only ever called on a database created by init."""
    database = call("GET", f"/databases/{database_id}", version=NOTION_VIEWS_VERSION)
    source = database["data_sources"][0]["id"]
    fields = call("GET", f"/data_sources/{source}", version=NOTION_VIEWS_VERSION)["properties"]
    old = call("GET", f"/views?database_id={database_id}", version=NOTION_VIEWS_VERSION).get("results", [])
    visible = ("Card No", "Assignee", "Schedule", "Due")
    configuration = {"type": "board",
                     "group_by": {"type": "select", "property_id": fields["Stage"]["id"], "sort": {"type": "manual"}},
                     "properties": [{"property_id": field["id"], "visible": name in visible}
                                    for name, field in fields.items() if name != "Name"]}
    call("POST", "/views", {"database_id": database_id, "data_source_id": source, "type": "board", "name": "Board",
                            "configuration": configuration, "filter": NOT_ARCHIVED}, version=NOTION_VIEWS_VERSION)
    for view_ in old:
        call("DELETE", f"/views/{view_['id']}", version=NOTION_VIEWS_VERSION)


def _toml_text(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def config_text(database_id: str, human_id: str, bot_id: str, agents: list[str], me: str, human: str,
                token_env: str) -> str:
    lines = ['mode = "dry-run"  # dry-run | live | off', f"me = {_toml_text(me)}  # this machine's agent", "",
             "[board]", f"database_id = {_toml_text(database_id)}", f"token_env = {_toml_text(token_env)}",
             f"human_id = {_toml_text(human_id)}", f"bot_id = {_toml_text(bot_id)}"]
    for name in agents:
        lines += ["", "[[agents]]", f"name = {_toml_text(name)}", 'workdir = "~/work"', 'sandbox = "strict"', "max_jobs = 3"]
    lines += ["", "[[humans]]", f"name = {_toml_text(human)}"]
    return "\n".join(lines) + "\n"


def init_board(token: str, parent: str, agents: list[str], human: str, title: str, human_user: str | None = None,
               request: Callable[..., dict] = notion_request) -> dict:
    """Create the board and return the ids a config needs. The board view is a convenience: failing it is not fatal."""

    def call(*args, **kwargs) -> dict:
        return request(token, *args, **kwargs)

    bot_id = call("GET", "/users/me").get("id")
    human_id = _find_human(call, human_user)
    database = call("POST", "/databases", {"parent": {"type": "page_id", "page_id": parent},
                                           "title": _rich_text(title), "properties": _schema([*agents, human])})
    warnings = []
    try:
        call("PATCH", f"/databases/{database['id']}", {"properties": {"Card No": {"unique_id": {"prefix": "CARD"}}}})
    except NotionError as error:
        warnings.append(f"Card No field not added ({error.code}); cards use a short id instead")
    try:
        _create_board_view(call, database["id"])
    except (NotionError, KeyError, IndexError) as error:
        warnings.append(f"Board view not created ({error}); add one grouped by Stage in Notion")
    return {"database_id": database["id"], "url": database.get("url", ""), "human_id": human_id, "bot_id": bot_id,
            "warnings": warnings}


def load_config(path: Path) -> tuple[dict, Policy]:
    try:
        config = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise SetupError(f"{path} not found. Run: python3 boardrunner.py init --parent <page-id> --agent <name>") from None
    except tomllib.TOMLDecodeError as error:
        raise SetupError(f"{path}: {error}") from None
    board = config.get("board") or {}
    agents = {agent["name"]: agent for agent in config.get("agents", [])}
    me = config.get("me")
    if me not in agents:
        raise SetupError(f"{path}: me = {me!r} is not one of the agents: {', '.join(agents) or 'none'}")
    missing = [name for name in ("database_id", "human_id", "bot_id") if not board.get(name)]
    if missing:
        raise SetupError(f"{path}: [board] is missing {', '.join(missing)}")
    peers = frozenset(agents) - {me}
    policy = Policy(me=me, human_id=board["human_id"], bot_id=board["bot_id"], peers=peers,
                    humans=frozenset(human["name"] for human in config.get("humans", [])),
                    peer_bot_ids=frozenset(board.get("peer_bot_ids", [])), primary=config.get("primary", True),
                    max_jobs=agents[me].get("max_jobs", 3), timezone=config.get("timezone", "UTC"))
    return config, policy


def _token(name: str) -> str:
    token = os.environ.get(name, "")
    if not token:
        raise SetupError(f"Set your Notion integration token in the {name} environment variable")
    return token


def _cmd_init(args) -> int:
    path = Path(args.config)
    if path.exists():
        raise SetupError(f"{path} already exists, so this machine has a board. Remove it to create another.")
    agents = args.agent
    if args.human in agents or len(set(agents)) != len(agents):
        raise SetupError("Agent and human names must be different from each other")
    created = init_board(_token(args.token_env), args.parent, agents, args.human, args.title, args.human_user)
    path.write_text(config_text(created["database_id"], created["human_id"], created["bot_id"], agents, agents[0],
                                args.human, args.token_env))
    print(f"Board created: {created['url'] or created['database_id']}")
    print(f"Config written: {path} (mode: dry-run)")
    for warning in created["warnings"]:
        print(f"Warning: {warning}")
    return 0


def _cmd_dry_run(args) -> int:
    config, policy = load_config(Path(args.config))
    board = config["board"]
    notion = NotionBoard(_token(board.get("token_env", "NOTION_TOKEN")), board["database_id"])
    log = run_pass(notion, None, State(), policy, datetime.now(ZoneInfo(policy.timezone)), dry_run=True)
    print("\n".join(log) or "Nothing to do.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="boardrunner", description="Run AI coding agents from a kanban board.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", default="boardrunner.toml")
    commands = parser.add_subparsers(dest="command")
    init = commands.add_parser("init", help="create the board in Notion and write the config")
    init.add_argument("--parent", required=True, help="id of a Notion page shared with your integration")
    init.add_argument("--agent", action="append", required=True, help="agent name; repeat for more agents")
    init.add_argument("--human", default="me", help="the Assignee value meaning you do the work yourself")
    init.add_argument("--human-user", help="your Notion user id, needed when the workspace has several people")
    init.add_argument("--title", default="BoardRunner")
    init.add_argument("--token-env", default="NOTION_TOKEN")
    init.set_defaults(handler=_cmd_init)
    dry_run = commands.add_parser("dry-run", help="show what would happen on the board; changes nothing")
    dry_run.set_defaults(handler=_cmd_dry_run)
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        return args.handler(args)
    except SetupError as error:
        print(error, file=sys.stderr)
        return 2
    except NotionError as error:
        print(f"Notion error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
