# Changelog

## 0.2.0

**Operator attention.** Registers `permission.denied`, `permission.resolved`,
`task.needs_attention` and `session.attention.changed`. A row now goes `blocked`
when the agent is waiting on you — before, that was indistinguishable from
`working`.

**State hygiene.** The map key is `(workspace_id, agent_name)` instead of the
bare agent name, so the same agent in two workspaces no longer shares one row
and one tail. `--status` prunes rows whose pane died. Sessions idle for 30
minutes drop out of the row's state, which self-heals a row pinned at `working`
by a lost `turn.end`.

**Loop visibility.** Registers `loop.started`, `loop.generation.post`,
`loop.gate.post` and `loop.terminal`, exposing `$cz_loop` and `$cz_gen`.

`session.attention.changed` is calibrated against payloads captured at runtime:
the signal is the `class` field (`none` and `finished` are benign). The `loop.*`
shapes were never captured — they are read defensively and logged when
unrecognized, never guessed.

The map format changed, so 0.2.0 starts with an empty map. Existing tabs are
orphaned — run `bridge.py --reset` on 0.1.0 first, or close them by hand.

## 0.1.0

First release. 7 hooks, one row per agent name, colorized log pane.
