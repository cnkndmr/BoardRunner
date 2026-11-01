from datetime import datetime, timezone

from boardrunner import Action, Card, Policy, Schedule, Stage, State, decide, human_approved, record_bot_write

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
OLD = "2026-01-01T11:00:00Z"
POLICY = Policy(me="laptop", human_id="human", bot_id="bot", peers=frozenset({"server"}), humans=frozenset({"me"}))


def card(**fields) -> Card:
    defaults = dict(id="c1", number="1", title="Do it", assignee="laptop", created_by="human", edited_by="human",
                    edited_at=OLD)
    return Card(**{**defaults, **fields})


def test_new_card_gets_a_plan():
    assert decide(card(), State(), POLICY, NOW)[0] == Action.DRAFT


def test_card_from_someone_else_is_refused():
    assert decide(card(created_by="stranger"), State(), POLICY, NOW)[0] == Action.WARN


def test_title_still_being_typed_waits():
    assert decide(card(edited_at="2026-01-01T11:59:30Z"), State(), POLICY, NOW)[0] == Action.WAIT


def test_plan_ready_waits_for_human():
    state = State(cards={"c1": {"plan_collected": True, "plan_hash": "x"}})
    assert decide(card(stage=Stage.PLAN_READY), state, POLICY, NOW)[0] == Action.WAIT


def test_human_approval_starts_the_job():
    state = State(cards={"c1": {"plan_collected": True, "plan_hash": "x"}})
    approved = card(stage=Stage.APPROVED, schedule=Schedule.NOW)
    assert decide(approved, state, POLICY, NOW)[0] == Action.START


def test_third_party_cannot_approve():
    state = State(cards={"c1": {"plan_collected": True, "plan_hash": "x"}})
    approved = card(stage=Stage.APPROVED, schedule=Schedule.NOW, edited_by="stranger")
    assert decide(approved, state, POLICY, NOW)[0] == Action.WARN


def test_bot_cannot_approve_its_own_plan():
    state = State(cards={"c1": {"plan_collected": True, "plan_hash": "x"}})
    approved = card(stage=Stage.APPROVED, schedule=Schedule.NOW, edited_by="bot")
    assert decide(approved, state, POLICY, NOW)[0] == Action.WARN


def test_peer_card_is_left_alone():
    assert decide(card(assignee="server"), State(), POLICY, NOW)[0] == Action.WAIT


def test_human_card_is_handed_over():
    assert decide(card(assignee="me"), State(), POLICY, NOW)[0] == Action.HAND_TO_HUMAN


def test_reassigned_running_job_stops():
    state = State(jobs={"c1": {}})
    assert decide(card(assignee="server", stage=Stage.RUNNING), state, POLICY, NOW)[0] == Action.STOP_REASSIGNED


def test_bot_write_stays_trusted_and_clears_old_approval():
    state, target, written = State(), card(), []
    record_bot_write(target, state, POLICY, lambda cid, **f: written.append(f), lambda s: None, stage=Stage.PLANNING)
    assert written == [{"stage": Stage.PLANNING}]
    assert target.edited_by == "bot" and "c1" not in state.human_view
    assert human_approved(target, state, POLICY)
