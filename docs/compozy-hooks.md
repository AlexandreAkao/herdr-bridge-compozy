# Writing a hooks-only CompozyOS extension

Four things cost real debugging time when this extension was built. All were
verified at runtime against CompozyOS `0.3.0-beta.21`.

## 1. `{{config_dir}}` is the extension's own directory

Not `~/.compozy`. In a hook executor, write:

```toml
executor = { kind = "subprocess", command = "{{config_dir}}/bridge.py" }
```

Getting it wrong fails silently from the caller's side — the failure only shows
up in `compozy hooks runs --session <id>`, which reports the resolved path:

```
resolve executable ".../extensions/herdr-bridge/extensions/herdr-bridge/bridge.py": no such file
```

That command is the debugger for anything hook-related. It reports outcome and
error per hook, per session.

## 2. The payload arrives on stdin, as JSON. Nothing is in the environment.

There are no `COMPOZY_*` variables. Read stdin:

```json
{"event":"turn.start","session_id":"sess-...","session_name":"loop goal reviewer main",
 "session_type":"system","agent_name":"reviewer","workspace_id":"ws_...",
 "workspace":"/path","acp_session_id":"...","turn_id":"turn-...","input_class":"user_message"}
```

## 3. `session_type` decides what is a real agent

- `user` — a session a human created
- `system` — **loop agents** (reviewer, review_fixer, publisher…)
- `spawned` — daemon internals, e.g. the memory extractor
- `dream` — daemon internals, e.g. checkpoint/curator sessions

Filtering by `user` alone hides exactly the loop agents you want to watch. Use
an **allowlist** of `{user, system}`: a denylist lets the next internal type the
daemon invents leak straight into your UI.

## 4. A hooks-only extension needs no subprocess host

It installs as `Type: resource` and validates without a `[subprocess]` section.
But `compozy extension dev` refuses it — that path requires `package.json` or
`go.mod`. Use `compozy extension install <path>` instead.

## 5. Async hooks are canceled with the step's context — and loop events are async-only

Measured on a zero-agent loop that emits its five `loop.*` events within
223 ms. The daemon log:

```
x65  WARN  hook.dispatch.async_failed   hook canceled: context canceled
       by event:  loop.node.terminal 26, loop.generation.post 14, loop.terminal 14, loop.started 7
```

An `async` hook is dispatched on a context derived from the emitting step;
when that step finishes, every hook still queued for it is canceled. The
daemon also dispatches one extension's hooks **serially**, so a slow entry point
(a Python interpreter is ~18 ms before it does anything) makes it worse.

Two consequences:

- **The hook entry point must return in milliseconds.** `hook.sh` spools the
  payload and returns in ~16 ms; a detached `bridge.py --drain` does the work.
  A 2 ms `/bin/sh` recorder received every event; a Python entry point received
  one in five.
- **Prefer sync events where they exist.** `loop.generation.pre`,
  `loop.gate.pre` and `coordinator.*` accept `mode = "sync"` — the daemon waits
  for them. `loop.started`, `loop.generation.post`, `loop.gate.post`,
  `loop.node.terminal` and `loop.terminal` are async-only (`compozy hooks
  events` shows the `Sync` column) and the daemon rejects `mode = "sync"` on
  them. `loop.terminal` fires as the run's context closes, so it is the most
  exposed — reconcile terminal state by asking the daemon instead.

`compozy loop status --run-id <id> --workspace <ws> -o json` answers in ~10 s
here (the workspace resolution cost, even with `--workspace`), so reconcile on
demand, not per event.

## Bonus: verifying color in a herdr pane

`pane.read` with `format: "text"` returns the *rendered screen*, so it contains
zero escape sequences even when the pane is fully colored — `strip_ansi: false`
does not change that. Only `format: "ansi"` shows the attributes.
