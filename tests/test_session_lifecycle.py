#!/usr/bin/env python3
"""Regressoes do fechamento de panes, sem acessar o herdr do usuario."""
import importlib.util
import os
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("bridge", os.path.join(ROOT, "bridge.py"))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class FakeHerdr:
    def __init__(self):
        self.calls = []
        self.panes = set()
        self.created = 0
        self.close_response = None

    def __call__(self, method, params):
        self.calls.append((method, params))
        if method == "tab.create":
            self.created += 1
            pane = f"p{self.created}"
            self.panes.add(pane)
            return {"result": {"root_pane": {"pane_id": pane},
                               "tab": {"tab_id": f"t{self.created}"}}}
        if method == "pane.close" and self.close_response is not None:
            return self.close_response
        if "pane_id" in params and params["pane_id"] not in self.panes:
            return {"error": {"code": "pane_not_found", "message": "Pane not found"}}
        if method == "pane.close":
            self.panes.remove(params["pane_id"])
        return {"result": {"type": "ok"}}


class SessionLifecycleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.herdr = FakeHerdr()
        mocks = patch.multiple(bridge, herdr=self.herdr, STATE_DIR=tmp.name,
                               MAP_PATH=os.path.join(tmp.name, "panes.json"),
                               LOG_PATH=os.path.join(tmp.name, "bridge.log"))
        mocks.start()
        self.addCleanup(mocks.stop)

    def emit(self, event, session="s1", workspace="ws1"):
        bridge.handle({"event": event, "session_id": session,
                       "session_name": session, "session_type": "user",
                       "agent_name": "reviewer", "workspace_id": workspace})

    def test_last_session_closes_on_each_terminal_event(self):
        for event in sorted(bridge.TERMINAL_EVENTS):
            with self.subTest(event=event):
                self.emit("session.post_create")
                self.emit("turn.start")
                self.emit("turn.end")
                pane = bridge.load_map()["ws1/reviewer"]["pane_id"]
                self.assertIn(pane, self.herdr.panes, "turn.end apenas deixa a sessao ociosa")
                self.herdr.calls.clear()
                self.emit(event)
                self.assertNotIn(pane, self.herdr.panes)
                self.assertNotIn("ws1/reviewer", bridge.load_map())
                self.assertIn(("pane.close", {"pane_id": pane}), self.herdr.calls)
                self.assertFalse(any(m.startswith("pane.report") for m, _ in self.herdr.calls))

    def test_untracked_terminal_events_do_not_create_panes(self):
        for event in sorted(bridge.TERMINAL_EVENTS):
            self.emit(event)
        self.assertEqual(self.herdr.calls, [])
        self.assertEqual(bridge.load_map(), {})

    def test_duplicate_stop_does_not_reopen_and_new_session_can_open(self):
        self.emit("turn.start")
        self.emit("agent.stopped")
        self.herdr.calls.clear()
        self.emit("session.post_stop")
        self.emit("agent.stopped")
        self.assertEqual(self.herdr.calls, [])
        self.emit("session.post_create", session="s2")
        self.assertEqual(self.herdr.created, 2)
        self.emit("session.post_stop", session="s1")
        self.assertEqual(self.herdr.panes, {"p2"})

    def test_open_sibling_keeps_pane_in_every_state(self):
        for sibling_event in ("session.post_create", "turn.end", "turn.start", "permission.request"):
            with self.subTest(sibling_event=sibling_event):
                self.emit("turn.start")
                self.emit(sibling_event, session="s2")
                pane = bridge.load_map()["ws1/reviewer"]["pane_id"]
                self.emit("session.post_stop")
                self.assertIn(pane, self.herdr.panes)
                self.assertIn("s2", bridge.load_map()["ws1/reviewer"]["sessions"])
                self.emit("session.post_stop", session="s2")
                self.assertNotIn(pane, self.herdr.panes)

    def test_silent_sibling_is_not_assumed_terminated(self):
        with patch.object(bridge.time, "time", return_value=1000):
            self.emit("turn.start", session="s2")
        with patch.object(bridge.time, "time", return_value=1000 + bridge.STALE_SESSION_SECONDS + 1):
            self.emit("turn.start")
            self.emit("session.post_stop")
            self.assertEqual(self.herdr.panes, {"p1"})
            self.assertIn("s2", bridge.load_map()["ws1/reviewer"]["sessions"])
            self.emit("session.post_stop", session="s2")
            self.assertEqual(self.herdr.panes, set())

    def test_closing_one_workspace_preserves_the_other(self):
        self.emit("turn.start")
        self.emit("turn.start", workspace="ws2")
        self.emit("session.post_stop")
        self.assertEqual(self.herdr.panes, {"p2"})
        self.assertEqual(set(bridge.load_map()), {"ws2/reviewer"})

    def test_close_failure_keeps_mapping_for_retry(self):
        self.emit("turn.start")
        self.herdr.close_response = {"error": {"code": "internal_error", "message": "Try again"}}
        self.emit("agent.stopped")
        self.assertIn("ws1/reviewer", bridge.load_map())
        self.assertEqual(bridge.load_map()["ws1/reviewer"]["sessions"], {})
        self.assertEqual(self.herdr.panes, {"p1"})
        self.herdr.close_response = None
        self.emit("session.post_stop")
        self.assertEqual(bridge.load_map(), {})
        self.assertEqual(self.herdr.panes, set())

    def test_terminal_event_does_not_recreate_manually_closed_pane(self):
        self.emit("turn.start")
        self.herdr.panes.clear()
        self.herdr.calls.clear()
        self.emit("session.post_stop")
        self.assertFalse(any(m == "tab.create" for m, _ in self.herdr.calls))
        self.assertEqual(self.herdr.panes, set())

    def test_unavailable_herdr_keeps_mapping_for_retry(self):
        self.emit("turn.start")
        with patch.object(bridge, "herdr", return_value=None):
            self.emit("agent.stopped")
        self.assertIn("ws1/reviewer", bridge.load_map())
        self.emit("session.post_stop")
        self.assertEqual(bridge.load_map(), {})
        self.assertEqual(self.herdr.panes, set())

    def test_terminal_event_closes_legacy_empty_row(self):
        self.emit("turn.start")
        data = bridge.load_map()
        data["ws1/reviewer"]["sessions"] = {}
        bridge.save_map(data)
        self.emit("session.post_stop")
        self.assertEqual(bridge.load_map(), {})
        self.assertEqual(self.herdr.panes, set())


if __name__ == "__main__":
    unittest.main()
