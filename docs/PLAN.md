# BoardRunner plan (v0.3)

Goal: anyone with a Notion account and an agent CLI can go from zero to a first finished card in about 15 minutes, on their own machines, with their own agent names.

## Scope

- In: kanban card flow (plan, approve, run, report), several machines on one board, safe defaults, a guided setup.
- Out for v1: web UI, hosted service, boards other than Notion, team permissions.

## Architecture

The core makes decisions and touches nothing. Everything that touches the outside world sits behind a small interface.

```
             config (boardrunner.toml)
                       |
 Board adapter <--> CORE: decide(card) -> action <--> Runner
  (Notion)             |                             (claude-code; later codex, ...)
                       +--> Sandbox policy (macOS, Linux)
                       +--> Notifier (none; telegram, slack, webhook)
                       +--> Context provider (optional: text given to the job)
```

| Part | Job | First implementation |
| --- | --- | --- |
| Core | Card state machine, pure and table-tested | Ported from the source system |
| Board | Read cards, write fields and body, create the schema | Notion REST API, standard library only |
| Runner | Start, watch and stop a detached agent process | `claude -p` |
| Sandbox | Limit network and writes | Per platform, `strict` by default |
| Notifier | One-line event messages | `none` |
| Scheduler | Run one pass per minute | `boardrunner run --once` with generated systemd or launchd files |

## Design rules

1. No names in code: agents, machines, column labels and paths come from config.
2. Column and field names are English by default, and can be remapped.
3. `dry-run` is the default; `live` is a deliberate config change.
4. Only a human edit may start a job, never an edit made by the bot.
5. A job outlives the runner: restarting BoardRunner never kills or duplicates work.
6. All agents behave the same. Any number of agents can share a board, and only their config differs: name, working folder, sandbox, parallel limit, extra instructions. Shared code never branches on an agent name or a platform name.
7. Runtime dependencies stay at zero (Python standard library).

## Milestones

| # | Result | Done when |
| --- | --- | --- |
| M0 | Skeleton and plan | Repository exists, tests pass |
| M1 | Core ported, free of personal names | State machine tests pass with fake board and runner |
| M2 | Notion adapter and `init` | `boardrunner init` creates the board in a fresh workspace |
| M3 | Claude runner, sandbox, `run --once` | A real card goes from plan to done in dry-run, then live |
| M4 | `doctor`, service files, multiple machines | Two machines share a board without touching each other's cards |
| M5 | Stranger test | Someone new reaches the first finished card from the README alone |
| M6 | More runners and notifiers, public release | Decided after M5 |

## Open decisions

| # | Decision | Default until decided |
| --- | --- | --- |
| 1 | When to go public | Private until M5 |
| 2 | Name | BoardRunner |

## Log

- 2026-11-01 v0.1: project opened, skeleton and plan written.
- 2026-11-01 v0.2: licence decided: MIT.
- 2026-11-01 v0.3: same behaviour for every agent; only the environment differs.
