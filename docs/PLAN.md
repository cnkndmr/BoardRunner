# BoardRunner plan (v0.14)

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
| Sandbox | Limit network, writes and secret reads | Done (M3): Claude Code sandbox, `strict` by default; tried on macOS; on Linux it refused correctly without `socat`, never ran |
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

9. Five steps to a first card, as the README shows them. A change that adds a step, a command or a long-running process needs a decision first; details go to `docs/guide.md`, not the README.

## Milestones

| # | Result | Done when |
| --- | --- | --- |
| M0 | Skeleton and plan | Repository exists, tests pass |
| M1 | Core ported, free of personal names | State machine tests pass with fake board and runner |
| M2 | Notion adapter and `init` | `boardrunner init` creates the board in a fresh workspace |
| M3 | Claude runner, sandbox, `run --once` | A real card goes from plan to done in dry-run, then live |
| M4 | `doctor`, service files, multiple machines | Two machines share a board without touching each other's cards |
| M5 | Stranger test | Someone new reaches the first finished card from the README alone. Run on 2026-11-02 from the kit by the maintainer's agent, not a stranger: passed, five fixes in v0.14 |
| M6 | Webhook trigger (parked on branch `webhook`) | Built and measured; kept out of main because it saved about 30 s for a public endpoint, a tunnel and a second service |
| M7 | More runners and notifiers, public release | Decided after M5 |

## M6: webhook trigger (parked)

Built, reviewed and measured on 2026-11-02, then moved to the `webhook` branch the same day. With a pass every minute, a new card is picked up in 2 to 3 minutes either way (the two-minute typing guard dominates) and an approval starts within a minute either way; the webhook only cut Notion API calls. It needed a public HTTPS endpoint, a tunnel, a second service and about 300 lines, against design rule 9. Worth reviving only for many machines or boards, or close to Notion's rate limit. Its design notes and measurements are in the branch's PLAN.

## Known gaps after M4

- A Mac and a Linux server shared one board (2026-11-02): each planned and ran only its own cards, and reassigning a running card stopped it on the server. Each machine had its own integration; the board must be added to each integration's page access in Notion's developer settings, adding the connection on the page alone was not enough.
- The Linux sandbox itself has not run: the test machine lacks `socat`, and `strict` correctly refused to start.
- Generated systemd files passed `systemd-analyze verify`, but were never installed.
- The macOS service command ran under launchd's conditions (empty environment, PATH /usr/bin:/bin:/usr/sbin:/sbin, its working folder and log) for a full card: plan, approval, job, Done, with `claude_path` from init and the token from `.env`. The plist itself was not loaded then; M5b loaded it (below).
- M5b (2026-11-02): from the kit and README only, `init --live` passed all checks and the launchd service was installed for real. Two cards were created and edited by a person in the Notion UI: plan in about 4 minutes, approval to Done in about 1.5 minutes, a Revision note rewrote the plan, Stopped ended a running job within a minute. The service waited out a network outage and carried on. Fixed in v0.14: README step 2 wording, plan time stated as 3 to 5 minutes, times in the service log, a note when a revised plan replaces the old one, a note when a job is stopped.
- systemd files were generated and validated, but never installed. On macOS, launchd cannot reach `~/Documents`, so keep the config elsewhere.
- The Claude Code sandbox needs `bwrap` and `socat` on Linux; `strict` refuses to run without them.

## Open decisions

| # | Decision | Default until decided |
| --- | --- | --- |
| 1 | When to go public | Private until M5 |
| 3 | Name before going public | Checked 2026-11-02 in USPTO TSDR and TMview (70+ offices incl. USPTO, EUIPO, UKIPO): no live BoardRunner mark in software classes 9 or 42. The 2024 US filing "BOARD RUNNER" (98560614) was abandoned on 2025-03-07; "BOARDRUNNER" (1999, US and CA) is dead; a UK "Board Runner" (UK00003740961) is registered for hand tools (class 8) only. boardrunner.dev looks free; .com, .io, .app are taken; github.com/boardrunner is an empty account. Keep the name. Fallback if needed: cardrun (no mark anywhere in TMview) |
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
- 2026-11-02 v0.11: M6 done: `listen` with secret path, signature check, follow-up and in-flight passes; `service --listen`, `--every`. Three-lens review (17 findings kept) fixed before the live test.
- 2026-11-02 v0.12: back to the simple setup. Webhook parked on branch `webhook`. `init` now names the agent after the machine, creates the work folder, writes `.env`, takes `--live` and runs the checks. README cut to five steps (34 lines); the rest moved to `docs/guide.md`. Design rule 9: five steps to a first card. A second fresh-eyes walk (3 walkers + verifier) found 12 gaps, all fixed without lengthening the README (30 lines): `init` refuses folders launchd cannot read before touching Notion and writes `claude_path`; the Linux install line enables linger; `--version` is 0.12.
- 2026-11-02 v0.13: `dry-run` reads the state earlier live passes saved (read only), so it no longer calls an approved card unverified. `init` writes the `claude` link, not the versioned file behind it, which changes on every Claude Code update. Service run checked under launchd's conditions.
- 2026-11-02 v0.13: `docs/stranger-test.md` for M5b testers (what to do, what to send back). Name check before M7 recorded under open decisions.
- 2026-11-02 v0.13: name check done: BoardRunner has no live software trademark; the US filing that worried us was abandoned. Fallback: cardrun.
- 2026-11-02 v0.14: M5b run end to end with a real launchd service and real human edits. Service log lines carry a time; a revised plan and a stopped job leave a note on the card; README step 2 and plan time fixed.
