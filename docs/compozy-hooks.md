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

## Bonus: verifying color in a herdr pane

`pane.read` with `format: "text"` returns the *rendered screen*, so it contains
zero escape sequences even when the pane is fully colored — `strip_ansi: false`
does not change that. Only `format: "ansi"` shows the attributes.
