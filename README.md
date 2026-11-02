# BoardRunner

Run AI coding agents from a Notion board. Open a card, pick a machine; Claude Code writes a plan on the card, waits for your approval, does the job and reports back on the same card. Nothing starts without your approval.

> **Status: early (v0.14).** Works on macOS and Linux, one or more machines per board. On Linux, first `apt install bubblewrap socat`; the sandbox there has not run yet. Private until the first public release ([plan](docs/PLAN.md)).

## You need

- Python 3.11 or newer. BoardRunner is one file, `boardrunner.py`, with nothing to install.
- [Claude Code](https://code.claude.com), signed in. Jobs use your Claude account.
- A Notion account.

## Five steps to a first card

1. In Notion, create an integration at <https://www.notion.so/my-integrations> (enable Read, Update and Insert content, and Read user information) and copy its secret. Create an empty page, add the integration under ••• > Connections, and copy the page link.
2. Make a folder such as ~/boardrunner (not under ~/Documents, ~/Desktop or ~/Downloads), put `boardrunner.py` in it and run every command from there. First: `export NOTION_TOKEN=<secret>`
3. `python3 boardrunner.py init --parent "<page link>" --live`
   It creates the board, the config, the work folder `~/work` and a private `.env`, then checks the setup.
4. `python3 boardrunner.py service`, then run the install line it prints. A pass now runs every minute.
5. On the board, create a card yourself: a title, Assignee = the agent `init` printed (`mac` or `linux`), Schedule = Now. After 3 to 5 minutes (a card you are still editing is left alone for 2) its Stage is Plan ready, with the plan in the card. Set Stage to Approved; the report arrives on the card when the job ends.

Jobs run in the Claude Code sandbox: no network, writes only in `~/work`, no access to `~/.ssh` and similar folders.

## More

If a card does not move, run `python3 boardrunner.py doctor`, then `python3 boardrunner.py run --once` by hand; the service logs to `boardrunner.log`. Trying it for someone? See [stranger-test](docs/stranger-test.md). The [guide](docs/guide.md) covers the rest: stages, schedules, revisions, stopping, several machines, safety, development.

## License

[MIT](LICENSE)
