"""Card state machine: for one card, decide the next action.

No network, process or file access happens here. The only side effect is on the `State` passed in.
Three rules shape the order of the checks in `decide`:

1. Ownership comes first, so another agent's card never fails or loses its note here.
2. A revision note comes before stage logic, and a note plus a stage change cannot start a job.
3. Only a human edit can start a job (`human_approved`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Callable
from zoneinfo import ZoneInfo


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


__all__ = ["Action", "Card", "Policy", "Schedule", "Stage", "State", "decide", "human_approved", "is_due",
           "record_bot_write", "text_hash", "view"]
