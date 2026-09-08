#!/usr/bin/env python3
"""Exercita o leitor de eventos e o renderer sem depender do daemon real."""
import contextlib
import io
import os
import re
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bridge
import colorize
import tail

ANSI = re.compile(r"\x1b\[[0-9;]*[mAK]")


def event(sequence, text, session="s1", turn="t1", etype="agent_message", **content):
    return {"sequence": sequence, "session_id": session, "turn_id": turn,
            "type": etype, "timestamp": "2026-01-01T10:00:00Z",
            "content": {"schema": "compozy.session.event.v1", "text": text, **content}}


class SessionTailTests(unittest.TestCase):
    def setUp(self):
        self.events = {"s1": [], "s2": []}
        self.requests = []
        self.fail = set()
        self.output = io.StringIO()
        self.reader = tail.SessionTail("ws/agent", colorize.Renderer())

    def request(self, path):
        self.requests.append(path)
        url = urlsplit(path)
        sid = url.path.split("/")[-2]
        if sid in self.fail:
            raise OSError("daemon unavailable")
        query = parse_qs(url.query)
        events = [e for e in self.events[sid]
                  if e["sequence"] > int(query.get("after_sequence", [0])[0])]
        if "limit" in query:
            events = events[-int(query["limit"][0]):]
        return {"events": events}

    def poll(self, sessions):
        data = {"ws/agent": {"sessions": dict.fromkeys(sessions)},
                "other/agent": {"sessions": {"foreign": {}}}}
        with patch.object(tail, "get_json", self.request), contextlib.redirect_stdout(self.output):
            self.reader.poll(data)

    def text(self):
        return ANSI.sub("", self.output.getvalue())

    def test_canonical_text_preserves_spaces_subwords_and_long_messages(self):
        fragments = ["Runn", "ing", " ", "the ", "suite", " now.\n\n", "    code\n", "x" * 300]
        self.events["s1"] = [event(i, fragment) for i, fragment in enumerate(fragments, 1)]
        self.poll(["s1"])
        self.assertEqual(self.text(), f"10:00:00 {'msg':<14} " + "".join(fragments))

    def test_resume_does_not_drop_bursts_or_replay_after_failure(self):
        self.events["s1"] = [event(1, "Start")]
        self.poll(["s1"])
        self.events["s1"] += [event(i, " word") for i in range(2, 303)]
        self.fail.add("s1")
        self.poll(["s1"])
        self.fail.clear()
        self.poll(["s1"])
        self.poll(["s1"])
        self.assertEqual(self.text(), f"10:00:00 {'msg':<14} Start" + " word" * 301)
        self.assertNotIn("limit=", self.requests[-1])

    def test_new_sibling_sessions_are_discovered_and_scoped(self):
        self.events["s1"] = [event(1, "First")]
        self.poll(["s1"])
        self.events["s2"] = [event(1, "Second", session="s2")]
        self.poll(["s1", "s2"])
        self.assertIn("First\n10:00:00", self.text())
        self.assertTrue(self.text().endswith("Second"))
        self.assertTrue(all(p.startswith("/api/workspaces/ws/sessions/") for p in self.requests))
        self.assertFalse(any("foreign" in p for p in self.requests))

    def test_removed_session_is_drained_before_retiring(self):
        self.events["s1"] = [event(1, "First")]
        self.poll(["s1"])
        self.events["s1"].append(event(2, " final"))
        self.poll([])
        self.assertTrue(self.text().endswith("First final"))
        count = len(self.requests)
        self.poll([])
        self.assertEqual(len(self.requests), count)

    def test_empty_initial_read_does_not_limit_next_batch(self):
        self.poll(["s1"])
        self.events["s1"] = [event(i, " word") for i in range(1, 202)]
        self.poll(["s1"])
        self.assertEqual(self.text().count(" word"), 201)

    def test_turn_changes_start_separate_message(self):
        self.events["s1"] = [event(1, "First"), event(2, "Second", turn="t2")]
        self.poll(["s1"])
        self.assertEqual(self.text().count("msg"), 2)
        self.assertIn("First\n10:00:00", self.text())

    def test_raw_tool_results_stay_quiet_but_errors_are_visible(self):
        self.events["s1"] = [event(1, "noisy result", etype="tool_result"),
                             event(2, "", etype="tool_result", error="suite failed"),
                             event(3, "", etype="error", error="agent failed"),
                             event(4, "exit code 1", etype="tool_result", tool_error=True)]
        self.poll(["s1"])
        self.assertNotIn("noisy result", self.text())
        self.assertIn("suite failed", self.text())
        self.assertIn("agent failed", self.text())
        self.assertIn("exit code 1", self.text())
        self.assertIn("\x1b[1;31m", self.output.getvalue())

    def test_bridge_starts_session_reader_for_exact_row(self):
        import shlex
        argv = shlex.split(bridge.tail_command("ws/a tricky;name"))
        self.assertEqual(argv[-1], "ws/a tricky;name")
        self.assertTrue(argv[1].endswith("/tail.py"))
        self.assertNotIn("logs", argv)


if __name__ == "__main__":
    unittest.main()
