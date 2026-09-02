# Changelog

## 0.3.0

**Loop rows.** 0.2.0 advertised loop visibility and it never worked: loop
events carry no `agent_name`, and the bridge dropped every payload without one.
Loops now get their own row, keyed by `(workspace, loop_name)`, with a pane that
follows `compozy loop events --run <id> --follow`. `loop.terminal` with
`status: blocked` turns the row `blocked` and keeps it there.

**Hook entry point is a shell shim.** The daemon dispatches an extension's
hooks serially and cancels async hooks when the emitting step's context ends;
a Python entry point lost four events in five on a fast loop. `hook.sh` spools
the payload in ~16 ms and `bridge.py --drain` processes the spool in timestamp
order, so late drainers never reorder events.

**Sync signals + reconciliation.** `loop.generation.pre` and
`coordinator.decision` are registered as `sync` hooks (the daemon waits for
those) and drive generation and node tokens reliably. Because `loop.terminal`
is async-only and fires as the run closes, `--status` reconciles live loop rows
against `compozy loop status` and fixes their state.

**Blocked never goes stale.** A row waiting on you stays `blocked` past the
30-minute stale cutoff.

Fixtures for the loop tests are real payloads captured from a run.


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
