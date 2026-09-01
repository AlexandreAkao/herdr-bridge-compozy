# herdr-bridge

A [CompozyOS](https://compozy.com) extension that makes your agents show up as
agent rows in [herdr](https://herdr.dev).

herdr tracks agents by reading terminal panes. CompozyOS agents run headless in
a daemon, so herdr cannot see them. This extension bridges the two: it
subscribes to CompozyOS hook events and pushes agent state into herdr's socket
API, giving each agent a row that turns `working` / `idle` / `blocked` in real
time — plus a pane tailing that agent's log, colorized.

```
w1:pX  compozy  working  loop goal reviewer main   $cz_agent=reviewer
```

## Requirements

- CompozyOS `>= 0.3.0-beta.1`
- herdr running (the extension talks to `~/.config/herdr/herdr.sock`)
- `python3` (stdlib only — no dependencies)

If herdr is not running, every hook is a silent no-op. The bridge never fails a
hook.

## Install

```bash
compozy extension install AlexandreAkao/herdr-bridge-compozy
```

Or from a local clone:

```bash
compozy extension install ./herdr-bridge-compozy --allow-unverified --yes
```

## What you get

One row per agent name, not per session — loops spin up dozens of short-lived
sessions of the same agent, and a row per session would be unreadable. The row
consolidates every live session of that agent: `blocked` wins over `working`
wins over `idle`, so a session ending never clears a sibling that is still
working.

Each row owns a herdr tab running:

```
compozy logs --follow --agent <name> -o jsonl | colorize.py
```

`colorize.py` exists because the CompozyOS CLI never emits ANSI — there is no
color flag and no `FORCE_COLOR` handling. It colors by event type and outcome,
and cuts the noise: measured on a real loop, 400 raw events rendered as 277
lines before filtering and 66 after (76% less). What it drops and why is in
[docs/log-noise.md](docs/log-noise.md).

## Commands

| Command | What it does |
| --- | --- |
| `bridge.py --status` | Shows the agent→pane map and the live sessions per agent |
| `bridge.py --refresh` | Restarts the tail in existing panes without closing tabs |
| `bridge.py --reset` | Closes every tab the bridge opened and clears the map |

The installed copy lives in `~/.compozy/extensions/herdr-bridge/`.

## Hooks

| Event | Row becomes |
| --- | --- |
| `session.post_create` | `idle` |
| `turn.start` | `working` |
| `turn.end` | `idle` |
| `permission.request` | `blocked` |
| `session.post_stop`, `agent.stopped`, `agent.crashed` | session leaves the row |

Only `user` and `system` sessions get a row. The daemon's own internals
(`spawned` memory extractors, `dream` curators) are filtered out — see
[docs/compozy-hooks.md](docs/compozy-hooks.md).

## License

MIT
