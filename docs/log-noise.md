# What the colorizer drops, and why

The original measurements below used 400 log summaries from a real
`spec-cycle` loop. Since 0.3.3, agent panes read original session events:
`compozy logs` summaries trim whitespace and truncate text, making them
unsuitable for reconstructing messages. Successful raw tool results are also
hidden; errors remain visible.

| Slice | Share | Verdict |
| --- | --- | --- |
| `usage` | 30% | Dropped. Token accounting, several per second, no signal about the work. |
| `tool_result` | 15% | Dropped when the summary is `[REDACTED]` — which was **100% of them**. Kept when the outcome is a failure. |
| identical consecutive lines | 14% | Collapsed into `×N`. The daemon emits each `tool_call` twice. |
| `skill.shadowed`, `harness.*` | 4.5% | Dropped. Skill-resolution chatter, not agent work. |

Under a third of the stream carried information.

Two shape problems mattered as much as the volume:

**Tool labels arrive as their own event.** `Terminal`, `Edit`, `Task`,
`SendMessage` and `ToolSearch` are emitted as a `tool_call` whose summary is
just the tool name, immediately followed by the real command (221 bare
`Terminal` events in 24 hours). The colorizer holds the label and merges it:
`Terminal▸npx tsc --noEmit`.

**Every command starts with `cd <long path>;`.** The prefix ate half the width
and truncation cut off the part that mattered, so it is stripped.

**`agent_message` arrives in deltas.** One event carried text of a single
character (`'E'`), the next carried `'ight of ten in. Both sweeps still
running.'`. Rendering one line per fragment is unreadable; consecutive
fragments stream together, preserving their original spaces, line breaks, and
indentation. Command cleanup does not apply to message fragments: trimming each
fragment would join separate words, while inserting a space between every
fragment would split words that arrive in pieces.

Original log fixture result: 277 rendered lines became 66.
