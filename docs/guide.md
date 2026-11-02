# BoardRunner guide

The [README](../README.md) gets you to a first card. This page has the rest.

## How a card moves

| You do | BoardRunner does |
| --- | --- |
| Create a card, pick an Assignee and a Schedule | Picks it up and writes a plan on the card |
| Read the plan, move the card from **Plan ready** to **Approved** | Starts the agent in a sandbox when the Schedule allows |
| Type a change into the card's **Revision note** | Rewrites the plan and clears the note |
| Nothing | Waits. Nothing starts without your approval |
| | Writes the report on the card and moves it to **Done** or **Failed** |

The board is the only hub: machines never talk to each other, each one handles only its own cards. If one is off, the others keep working.

**Stages:** Inbox (you create the card) → Planning (the agent writes a plan) → Plan ready (you read it) → Approved (you approve) → Running → Done or Failed. A card assigned to you (`me`) goes to For human. Move a running card to Stopped to stop the job.

**Schedule:** an approved card starts only when its Schedule allows it. Now: on the next pass. Night: between 01:00 and 05:30 in the config's `timezone`. At date: once the Due date is reached. Manual, or empty: never by itself. For your first card, use Now.

## Cards, step by step

1. Create a card on the board yourself: a title, Assignee = your machine's agent (`init` printed it), Schedule = Now.
2. A card edited in the last two minutes counts as still being typed, so nothing happens yet.
3. On the next pass the card moves to Planning; a pass or two later the plan is in the card body and the stage is Plan ready.
4. Read the plan and set the stage to Approved.
5. The next pass starts the job (Running); when it ends, a pass moves the card to Done or Failed with the report in the body.

A pass is one `python3 boardrunner.py run --once`. The service runs one every minute; without it, run it by hand.

Only cards created by you (`human_id` in the config) are handled. A card with a problem gets one note in its body. "Card was not created by the board owner": someone else or an automation created it. "Assignee is not an agent": pick one of your agents. "Last edit is not a verified human edit": make the change yourself in Notion. A job counts as done only when its report ends with `RESULT: done`; otherwise the card goes to Failed.

## Run it every minute

```sh
python3 boardrunner.py service
```

This writes launchd (macOS) or systemd (Linux) files into `./service` (change with `--output`) and prints the install line. It installs nothing. The files hold absolute paths to this Python, `boardrunner.py` and the config, so keep them where they are. Output goes to `boardrunner.log` beside the config.

A service has a short PATH, so `init` writes the full path of `claude` as `claude_path` in your agent block; set it by hand if you move Claude Code. A service does not see your shell either, so it reads the token from `.env` beside `boardrunner.toml`. `init` writes that file for you (readable only by you); keep it out of git. To write it by hand: one line, `NOTION_TOKEN=<secret>`, then `chmod 600 .env`.

- **macOS:** keep the folder outside ~/Documents, ~/Desktop and ~/Downloads; launchd cannot read them. Remove with `launchctl unload ~/Library/LaunchAgents/com.boardrunner.plist`, then delete that file.
- **Linux:** the install line also runs `loginctl enable-linger $USER`, so the timer keeps running after you log out. Remove with `systemctl --user disable --now boardrunner.timer`.

The service follows `mode`: in `dry-run` it only logs.

## More than one machine

Copy `boardrunner.py`, the config and a `.env` to each machine, and sign in to Claude Code there. Add the machine's agent in the board's Assignee field (or run `init` with every `--agent` at the start: `--agent laptop --agent server`). Then on every machine but the first:

- set `me = "<that machine's agent>"`, and `primary = false` at the top level;
- fix that agent's `workdir`.

Using one integration everywhere is simplest. If a machine has its own integration, add the board to that integration's page access (Notion developer settings > the integration > Content access; adding the connection from the page menu may not be enough). Then set `bot_id` under `[board]` to the id that `doctor` prints, and list the other machines' integration ids in `peer_bot_ids`.

`boardrunner.example.toml` lists every config key, including optional ones (`timezone`, `primary`, `peer_bot_ids`, `model`, `max_minutes`, `instructions`, `claude_path`). Do not copy its placeholder ids.

## Safety

- Planning is read-only: the agent may only read, list and search files.
- A job runs with no one watching, with shell commands and file edits, inside the Claude Code sandbox: its commands have no network, write only in the work folder, and cannot read ~/.ssh, ~/.aws, ~/.gnupg, ~/.netrc, ~/.config, the Keychain or any `.env`. Commands such as `git push`, `ssh`, `rsync`, `gh`, `rm` and `sudo` are denied.
- Claude Code still sends the card text and the files it reads to Anthropic and uses your quota. Limits: `max_jobs` per machine, `max_minutes` per job (360 by default).
- `sandbox = "off"` removes the sandbox: jobs can then reach the network and write anywhere you can.
- Only cards you created and approved yourself can start a job. Anyone who can edit the page can still change a card's text, so read the plan before you approve it.

## Requirements in detail

- Python 3.11 or newer. Nothing to install: BoardRunner is one file, `boardrunner.py`.
- [Claude Code](https://code.claude.com) (`claude` on your PATH), signed in as the user that runs BoardRunner. Jobs use your Claude account and quota.
- A Notion account.
- macOS, or Linux with `bwrap` and `socat` for the sandbox (`apt install bubblewrap socat`). Without them, jobs with `sandbox = "strict"` refuse to start. `doctor` tells you what is missing.

## Notion setup in detail

1. **Notion integration.** At <https://www.notion.so/my-integrations>, create an internal integration. Under Capabilities, enable Read, Update and Insert content, and Read user information. Copy its secret: this is your `NOTION_TOKEN`.
2. **A page for the board.** Create an empty page in Notion, open its ••• menu > Connections and add the integration. Copy the page link (••• > Copy link).
3. **Create the board.** In a folder outside ~/Documents (launchd cannot read it), next to `boardrunner.py`:

   ```sh
   export NOTION_TOKEN=<secret>
   python3 boardrunner.py init --parent "<page link>" --live
   ```

   `init` creates the board inside that page and writes `boardrunner.toml`. This machine becomes an agent named `mac` or `linux` (choose another with `--agent <name>`; repeat `--agent` for more machines). The Assignee `me` means you do the card yourself. `init` also creates the agent's work folder (`~/work`, the only folder a job may write to), saves the token in `.env`, and runs the checks that `doctor` runs. Without `--live` the config starts in `dry-run`: passes only print what they would do, until you set `mode = "live"`. If your workspace has several people, `init` lists them: run it again with `--human-user <your id>`.

Run every command from that folder, or put `--config <path>` before the command: `python3 boardrunner.py --config ~/br/boardrunner.toml doctor`. `dry-run` prints what each card would do and changes nothing; it never writes a plan.

## Development

The tests live in the repository (`tests/`): `uv run pytest`.
