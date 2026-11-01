from datetime import datetime, timedelta, timezone

from boardrunner import Card, Policy, Schedule, Stage, State, run_pass


class FakeBoard:
    """Like a real board: every write or append by the bot makes the bot the last editor."""

    def __init__(self, clock, bot="bot"):
        self.clock, self.bot, self.cards, self.bodies = clock, bot, {}, {}

    def human_edit(self, card_id, **fields):
        card = self.cards[card_id]
        for name, value in fields.items():
            setattr(card, name, value)
        card.edited_by, card.edited_at = "human", self.clock.iso()

    def add(self, **fields):
        card = Card(id=fields.pop("id", "c1"), number="1", created_by="human", edited_by="human",
                    edited_at=self.clock.iso(), **fields)
        self.cards[card.id] = card
        return card

    def list_cards(self):
        return [Card(**vars(card)) for card in self.cards.values()]

    def write(self, card_id, **fields):
        card = self.cards[card_id]
        for name, value in fields.items():
            setattr(card, {"note": "revision_note"}.get(name, name), value)
        card.edited_by, card.edited_at = self.bot, self.clock.iso()

    def append(self, card_id, text):
        self.bodies.setdefault(card_id, []).append(text)
        self.cards[card_id].edited_by, self.cards[card_id].edited_at = self.bot, self.clock.iso()


class FakeRunner:
    def __init__(self):
        self.started, self.stopped, self.statuses = [], [], {}

    def start(self, kind, card, prompt):
        handle = f"{kind}-{len(self.started)}"
        self.started.append((kind, card.id, prompt))
        self.statuses[handle] = "running"
        return handle

    def status(self, handle):
        return self.statuses[handle]

    def result(self, handle):
        return f"output of {handle}"

    def stop(self, handle):
        self.stopped.append(handle)
        self.statuses[handle] = "failed"

    def finish(self, status="done"):
        for handle, value in self.statuses.items():
            if value == "running":
                self.statuses[handle] = status


class Clock:
    def __init__(self):
        self.now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    def iso(self):
        return self.now.isoformat()

    def tick(self, seconds=200):
        self.now += timedelta(seconds=seconds)


class World:
    def __init__(self, me="laptop", peers=("server",), bot="bot", peer_bots=("bot2",), board=None, clock=None):
        self.clock = clock or Clock()
        self.board = board or FakeBoard(self.clock, bot)
        self.runner, self.state = FakeRunner(), State()
        self.policy = Policy(me=me, human_id="human", bot_id=bot, peers=frozenset(peers), humans=frozenset({"me"}),
                             peer_bot_ids=frozenset(peer_bots))

    def turn(self, dry_run=False):
        self.clock.tick()
        return run_pass(self.board, self.runner, self.state, self.policy, self.clock.now, dry_run)

    def stage(self, card_id="c1"):
        return self.board.cards[card_id].stage


def to_plan_ready(world):
    world.turn()
    assert world.stage() == Stage.PLANNING
    world.runner.finish()
    world.turn()
    assert world.stage() == Stage.PLAN_READY


def test_full_journey():
    world = World()
    world.board.add(title="Do it", assignee="laptop", schedule=Schedule.NOW)
    to_plan_ready(world)
    assert "output of plan-0" in world.board.bodies["c1"]

    world.turn()
    assert world.stage() == Stage.PLAN_READY  # nothing starts without a human
    assert [kind for kind, *_ in world.runner.started] == ["plan"]

    world.board.human_edit("c1", stage=Stage.APPROVED)
    world.turn()
    assert world.stage() == Stage.RUNNING
    assert "output of plan-0" in world.runner.started[1][2]  # the job receives the approved plan

    world.turn()
    assert len(world.runner.started) == 2  # no duplicate start
    world.runner.finish()
    world.turn()
    assert world.stage() == Stage.DONE
    assert "output of job-1" in world.board.bodies["c1"]
    world.turn()
    assert world.stage() == Stage.DONE


def test_revision_note_rewrites_the_plan():
    world = World()
    world.board.add(title="Do it", assignee="laptop")
    to_plan_ready(world)
    world.board.human_edit("c1", revision_note="smaller please")
    world.turn()
    card = world.board.cards["c1"]
    assert card.stage == Stage.PLANNING and card.revision_note == ""
    world.turn()  # the next pass writes the new plan
    assert "smaller please" in world.runner.started[-1][2]
    world.runner.finish()
    world.turn()
    assert world.stage() == Stage.PLAN_READY


def test_job_failure_is_reported():
    world = World()
    world.board.add(title="Do it", assignee="laptop", schedule=Schedule.NOW)
    to_plan_ready(world)
    world.board.human_edit("c1", stage=Stage.APPROVED)
    world.turn()
    world.runner.finish("failed")
    world.turn()
    assert world.stage() == Stage.FAILED


def test_draft_retries_then_fails():
    world = World()
    world.board.add(title="Do it", assignee="laptop")
    for _ in range(40):
        world.turn()
        world.runner.finish("failed")
    assert world.stage() == Stage.FAILED
    assert len(world.runner.started) == 3


def test_human_stop_stops_the_process():
    world = World()
    world.board.add(title="Do it", assignee="laptop", schedule=Schedule.NOW)
    to_plan_ready(world)
    world.board.human_edit("c1", stage=Stage.APPROVED)
    world.turn()
    world.board.human_edit("c1", stage=Stage.STOPPED)
    world.turn()
    assert world.runner.stopped == ["job-1"]
    world.turn()
    assert world.stage() == Stage.STOPPED and not world.state.jobs


def test_reassigning_a_running_card_stops_it_here():
    world = World()
    world.board.add(title="Do it", assignee="laptop", schedule=Schedule.NOW)
    to_plan_ready(world)
    world.board.human_edit("c1", stage=Stage.APPROVED)
    world.turn()
    world.board.human_edit("c1", assignee="server")
    world.turn()
    assert world.runner.stopped == ["job-1"]


def test_two_agents_share_a_board_and_behave_the_same():
    clock = Clock()
    board = FakeBoard(clock)
    laptop = World(board=board, clock=clock)
    server = World(me="server", peers=("laptop",), bot="bot", peer_bots=(), board=board, clock=clock)
    board.add(id="a", title="One", assignee="laptop", schedule=Schedule.NOW)
    board.add(id="b", title="Two", assignee="server", schedule=Schedule.NOW)
    for _ in range(2):
        for world in (laptop, server):
            world.turn()
            world.runner.finish()
    assert [c for _, c, _ in laptop.runner.started] == ["a"]
    assert [c for _, c, _ in server.runner.started] == ["b"]
    assert board.cards["a"].stage == board.cards["b"].stage == Stage.PLAN_READY


def test_human_card_goes_to_the_human_with_no_agent():
    world = World()
    world.board.add(title="Mine", assignee="me")
    world.turn()
    assert world.stage() == Stage.FOR_HUMAN and not world.runner.started


def test_dry_run_touches_nothing():
    world = World()
    world.board.add(title="Do it", assignee="laptop")
    log = world.turn(dry_run=True)
    assert "draft" in log[0]
    assert not world.runner.started and world.stage() == Stage.INBOX and "c1" not in world.board.bodies


def test_warning_is_written_once():
    world = World()
    world.board.add(title="Do it", assignee=None, stage=Stage.APPROVED)
    world.turn()
    world.turn()
    assert len(world.board.bodies["c1"]) == 1
