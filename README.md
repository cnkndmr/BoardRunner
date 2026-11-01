# BoardRunner

Run AI coding agents from a kanban board. Open a card, choose who does the work; the agent writes a plan on the card, waits for your approval, does the job and reports back on the same card.

> **Status: early. Not usable yet:** the board can be created and read in Notion, but agents do not run yet ([plan](docs/PLAN.md)). The design comes from a private setup that runs daily.

```mermaid
flowchart LR
    You(["You"]) -- "create card, pick assignee, approve" --> Board["Kanban board<br/>(Notion first)"]
    Board <--> R1["BoardRunner<br/>on your laptop"]
    Board <--> R2["BoardRunner<br/>on your server"]
    R1 --> A1["Agent CLI, sandboxed"]
    R2 --> A2["Agent CLI, sandboxed"]
```

## The idea

| You do | BoardRunner does |
| --- | --- |
| Create a card and pick an assignee | Picks it up and writes a plan on the card |
| Move the card to "Run" | Starts the agent in a sandbox |
| Add a revision note | Rewrites the plan |
| Nothing | Waits. Nothing starts without your approval |
| | Writes the result on the card when the job ends |

The board is the only hub: machines never talk to each other, each one handles only its own cards. If one is off, the others keep working.

## Try the setup

1. Create an integration at <https://www.notion.so/my-integrations> and share a page with it.
2. Run:

```sh
export NOTION_TOKEN=...
python3 boardrunner.py init --parent <page-id> --agent laptop --agent server
python3 boardrunner.py dry-run
```

`init` creates the board and writes `boardrunner.toml`. `dry-run` shows what would happen and changes nothing.

## Development

```sh
uv run pytest
python3 boardrunner.py --version
```

## License

[MIT](LICENSE)
