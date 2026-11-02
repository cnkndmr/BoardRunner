# Trying BoardRunner for the first time

Thank you for trying it. Use only the [README](../README.md); open the [guide](guide.md) only if the README leaves you stuck, and note when you did. Nobody will help you during the test: where you get stuck is exactly what we want to learn.

You need about 30 minutes, a Mac or Linux machine, Python 3.11+, Claude Code signed in, and a Notion account. Use a throwaway Notion page; the test creates a board inside it.

## What to do

1. Follow the README's five steps until your first card is Done.
2. Then, on a second card, try three more things:
   - Before approving, type a change into **Revision note** (for example "make it shorter"). The plan should be rewritten.
   - Approve it, and while it is Running, move it to **Stopped**. The job should stop.
   - Run `python3 boardrunner.py doctor` once more.
3. Remove what you installed: the uninstall line for your system is in the guide, under "Run it every minute".

## What to send back

Copy this, fill it in, and send it:

```
Machine: macOS / Linux, version:
Minutes to the first Done card:
Steps where I got stuck or guessed (step number, what happened):
Times I opened the guide, and why:
Error messages I did not understand (paste them):
Revision note worked: yes / no / did not try
Stopping a running card worked: yes / no / did not try
Service install line worked: yes / no
Anything that felt unsafe or surprising:
One thing to change first:
```
