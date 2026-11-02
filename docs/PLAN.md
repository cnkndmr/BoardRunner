# BoardRunner plan (v0.10)

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
| Core | Card state machine, tested with a fake board and runner | Done (M1) |
| Board | Read cards, write fields and body, create the schema | Done (M2): Notion REST API, standard library only |
| Runner | Start, watch and stop a detached agent process | Done (M3): `claude -p` |
| Sandbox | Limit network, writes and secret reads | Done (M3): Claude Code sandbox, `strict` by default; tried on macOS only |
| Notifier | One-line event messages | `none` |
| Scheduler | Run one pass per minute | Done (M4): `run --once` and generated systemd or launchd files; nothing is installed for you |

## Design rules

1. No names in code: agents, machines, column labels and paths come from config.
2. Column and field names are English by default, and can be remapped.
3. `dry-run` is the default; `live` is a deliberate config change.
4. Only a human edit may start a job, never an edit made by the bot.
5. A job outlives the runner: restarting BoardRunner never kills or duplicates work.
6. All agents behave the same. Any number of agents can share a board, and only their config differs: name, working folder, sandbox, parallel limit, extra instructions. Shared code never branches on an agent name or a platform name.
7. Runtime dependencies stay at zero (Python standard library).
8. The product is one file, `boardrunner.py`. Tests stay in the repository. v1 has one runner (Claude Code); new adapters must not push the file far past 3000 lines.

## Milestones

| # | Result | Done when |
| --- | --- | --- |
| M0 | Skeleton and plan | Repository exists, tests pass |
| M1 | Core ported, free of personal names | State machine tests pass with fake board and runner |
| M2 | Notion adapter and `init` | `boardrunner init` creates the board in a fresh workspace |
| M3 | Claude runner, sandbox, `run --once` | A real card goes from plan to done in dry-run, then live |
| M4 | `doctor`, service files, multiple machines | Two machines share a board without touching each other's cards |
| M5 | Stranger test | Someone new reaches the first finished card from the README alone |
| M6 | Webhook trigger: a card edit starts a pass at once | An edited card is picked up in about a minute without waiting for the timer |
| M7 | More runners and notifiers, public release | Decided after M5 |

## M6: webhook trigger

Polling stays as the safety net; the webhook only rings the bell.

- Notion integration webhooks (`page.properties_updated`, `page.created`, `page.content_updated`) on every plan. Property events are batched, typically under a minute.
- A new `boardrunner listen` command checks the signature, ignores events written by its own integration (`authors`), and runs one pass. Still one file, standard library only.
- Notion only posts to a public HTTPS address. The default is Tailscale Funnel on one machine; that machine wakes the others on the private network. A machine with no listener keeps polling.
- Database automations ("Send webhook") are faster but need a paid Notion plan and pause on the first failure, so they are optional.

## Known gaps after M4

- A Mac and a Linux server shared one board (2026-11-02): each planned and ran only its own cards, and reassigning a running card stopped it on the server. Each machine had its own integration; the board must be added to each integration's page access in Notion's developer settings, adding the connection on the page alone was not enough.
- The Linux sandbox itself has not run: the test machine lacks `socat`, and `strict` correctly refused to start.
- Generated systemd files passed `systemd-analyze verify`, but were never installed.
- Service files were generated and validated, but never installed. On macOS, launchd cannot reach `~/Documents`, so keep the config elsewhere.
- The Claude Code sandbox needs `bwrap` and `socat` on Linux; `strict` refuses to run without them.
- Real human approval was tested only with simulated edits, because an integration cannot act as a person.

## Open decisions

| # | Decision | Default until decided |
| --- | --- | --- |
| 1 | When to go public | Private until M5 |
| 2 | Name | BoardRunner |

## Log

- 2026-11-01 v0.1: project opened, skeleton and plan written.
- 2026-11-01 v0.2: licence decided: MIT.
- 2026-11-01 v0.3: same behaviour for every agent; only the environment differs.
- 2026-11-01 v0.4: single file decided; M1 done on a fake board and runner.
- 2026-11-01 v0.5: M2 done: Notion board, config, `init`, `dry-run`; tried on a real workspace. Plain text only on cards so far.
- 2026-11-01 v0.6: M3 done: real agent, sandbox, state file, `run --once`; a card went from plan to done on real Notion and Claude.
- 2026-11-01 v0.7: M4 mostly done: card body, Markdown, `doctor`, `service`, two runners on one board. Two real machines still open.
- 2026-11-02 v0.8: Linux checks; relative paths fixed; `doctor` hints at sharing the board.
- 2026-11-02 v0.9: M6 is the webhook trigger, after M5; the old M6 becomes M7. Jobs end with an outcome line; a missing or negative one marks the card failed. Setup errors are written once on the card.
- 2026-11-02 v0.10: M5 part one: four fresh-eyes walkthroughs of the README found 18 gaps (3 blockers: no "Run" stage, Schedule undocumented, no first-card steps). README rewritten; `--parent` takes a page link, Notion errors say what to do, `init` writes the local timezone, `.env` accepts `export`, `service` needs a valid config. Still open: a real person reaching a finished card from the README alone.
