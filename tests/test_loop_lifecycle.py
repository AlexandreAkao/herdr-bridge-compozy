#!/usr/bin/env python3
"""Fechamento de loops e recuperacao de hooks terminais perdidos."""
import fcntl
import importlib.util
import os
import tempfile
import unittest
from unittest.mock import patch

from test_session_lifecycle import FakeHerdr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("bridge", os.path.join(ROOT, "bridge.py"))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class LoopLifecycleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.herdr = FakeHerdr()
        mocks = patch.multiple(bridge, herdr=self.herdr, STATE_DIR=tmp.name,
                               MAP_PATH=os.path.join(tmp.name, "panes.json"),
                               LOG_PATH=os.path.join(tmp.name, "bridge.log"))
        mocks.start()
        self.addCleanup(mocks.stop)

    def emit(self, event, run="looprun-one", status="running", workspace="ws1"):
        bridge.handle({"event": event, "loop_run_id": run, "status": status,
                       "loop_name": "review", "workspace_id": workspace})

    def test_terminal_outcomes_close_the_last_run(self):
        for status in ("done", "no-op", "failed", "exhausted", "stalled", "canceled"):
            with self.subTest(status=status):
                self.emit("loop.started")
                pane = bridge.load_map()["loop/ws1/review"]["pane_id"]
                self.herdr.calls.clear()
                self.emit("loop.terminal", status=status)
                self.assertNotIn(pane, self.herdr.panes)
                self.assertEqual(bridge.load_map(), {})
                self.assertEqual(self.herdr.calls, [("pane.close", {"pane_id": pane})])

    def test_duplicate_or_untracked_terminal_does_not_create_panes(self):
        self.emit("loop.terminal", status="failed")
        self.assertEqual(self.herdr.calls, [])
        self.emit("loop.started")
        self.emit("loop.terminal", status="failed")
        self.herdr.calls.clear()
        self.emit("loop.terminal", status="failed")
        self.assertEqual(self.herdr.calls, [])

    def test_blocked_run_remains_visible(self):
        self.emit("loop.started")
        self.emit("loop.terminal", status="blocked")
        self.assertEqual(self.herdr.panes, {"p1"})
        self.assertEqual(bridge.load_map()["loop/ws1/review"]["sessions"]["looprun-one"]["state"], "blocked")

    def test_terminal_for_old_run_does_not_restart_new_runs_tail(self):
        self.emit("loop.started")
        with patch.object(bridge.time, "sleep"):
            self.emit("loop.started", run="looprun-two")
        self.herdr.calls.clear()
        self.emit("loop.terminal", status="done")
        self.assertEqual(self.herdr.panes, {"p1"})
        entry = bridge.load_map()["loop/ws1/review"]
        self.assertEqual(entry["run_id"], "looprun-two")
        self.assertEqual(set(entry["sessions"]), {"looprun-two"})
        self.assertFalse(any(m.startswith("pane.send") for m, _ in self.herdr.calls))
        self.emit("loop.terminal", run="looprun-two", status="failed")
        self.assertEqual(self.herdr.panes, set())

    def test_silent_sibling_keeps_pane_until_verified_terminal(self):
        with patch.object(bridge.time, "time", return_value=1000):
            self.emit("loop.started", run="looprun-two")
        with patch.object(bridge.time, "time", return_value=1000 + bridge.STALE_SESSION_SECONDS + 1), patch.object(bridge.time, "sleep"):
            self.emit("loop.started")
            self.emit("loop.terminal", status="done")
        self.assertEqual(self.herdr.panes, {"p1"})
        self.assertIn("looprun-two", bridge.load_map()["loop/ws1/review"]["sessions"])

    def test_reconcile_recovers_the_reported_failed_run(self):
        self.emit("loop.started", run="looprun-b4ea47ddc11afd73")
        with patch.object(bridge, "query_loop_status", return_value="failed") as query:
            fixed = bridge.reconcile_loops()
        query.assert_called_once_with("looprun-b4ea47ddc11afd73", "ws1")
        self.assertEqual(fixed, [("loop/ws1/review", "looprun-b4ea47ddc11afd73", "failed")])
        self.assertEqual(self.herdr.panes, set())
        self.assertEqual(bridge.load_map(), {})

    def test_reconcile_preserves_nonterminal_and_unknown_statuses(self):
        self.emit("loop.started")
        for status in ("running", "queued", "watching", "needs-approval", "paused", "blocked", "future-status", None):
            with self.subTest(status=status), patch.object(bridge, "query_loop_status", return_value=status):
                bridge.reconcile_loops()
                self.assertEqual(self.herdr.panes, {"p1"})
                self.assertIn("looprun-one", bridge.load_map()["loop/ws1/review"]["sessions"])

    def test_reconcile_closes_legacy_empty_rows(self):
        self.emit("loop.started")
        data = bridge.load_map()
        data["loop/ws1/review"]["sessions"] = {}
        data["loop/ws1/review"]["last_status"] = "done"
        bridge.save_map(data)
        with patch.object(bridge, "query_loop_status", return_value="failed"):
            bridge.reconcile_loops()
        self.assertEqual(bridge.load_map(), {})
        self.assertEqual(self.herdr.panes, set())

    def test_reconcile_retries_failed_close(self):
        self.emit("loop.started")
        self.herdr.close_response = {"error": {"code": "internal_error"}}
        self.emit("loop.terminal", status="done")
        self.assertIn("loop/ws1/review", bridge.load_map())
        self.herdr.close_response = None
        with patch.object(bridge, "query_loop_status", return_value="done"):
            bridge.reconcile_loops()
        self.assertEqual(bridge.load_map(), {})
        self.assertEqual(self.herdr.panes, set())

    def test_reconcile_does_not_hold_map_lock_during_daemon_query(self):
        self.emit("loop.started")

        def query(run, workspace):
            with open(os.path.join(bridge.STATE_DIR, ".lock"), "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(lock, fcntl.LOCK_UN)
            return "done"

        with patch.object(bridge, "query_loop_status", side_effect=query):
            bridge.reconcile_loops()
        self.assertEqual(bridge.load_map(), {})

    def test_reconcile_preserves_new_run_created_during_query(self):
        self.emit("loop.started")

        def query(run, workspace):
            with patch.object(bridge.time, "sleep"):
                self.emit("loop.started", run="looprun-two")
            return "done"

        with patch.object(bridge, "query_loop_status", side_effect=query):
            bridge.reconcile_loops()
        self.assertEqual(self.herdr.panes, {"p1"})
        self.assertEqual(set(bridge.load_map()["loop/ws1/review"]["sessions"]), {"looprun-two"})

    def test_watcher_closes_run_without_another_hook(self):
        self.emit("loop.started")
        with patch.object(bridge, "query_loop_status", side_effect=["running", "failed"]), patch.object(bridge.time, "sleep"):
            bridge.watch_loops()
        self.assertEqual(self.herdr.panes, set())
        self.assertEqual(bridge.load_map(), {})

    def test_only_one_watcher_runs(self):
        self.emit("loop.started")
        with bridge.Locked(".watch-lock"), patch.object(bridge, "query_loop_status") as query:
            bridge.watch_loops()
        query.assert_not_called()

    def test_tail_uses_current_cli_syntax_and_explicit_workspace(self):
        command = bridge.loop_tail_command("looprun-one", "ws1")
        self.assertIn("loop events looprun-one --follow --workspace ws1", command)
        self.assertNotIn("--run ", command)

    def test_already_closed_pane_is_removed_from_map(self):
        self.emit("loop.started")
        self.herdr.panes.clear()
        self.emit("loop.terminal", status="failed")
        self.assertEqual(bridge.load_map(), {})

    def test_drainer_starts_automatic_reconciliation(self):
        with patch.object(bridge.sys, "argv", ["bridge.py", "--drain"]), patch.object(bridge, "drain_spool") as drain, patch.object(bridge, "watch_loops") as watch:
            bridge.main()
        drain.assert_called_once_with()
        watch.assert_called_once_with()

    def test_daemon_query_uses_briefing_and_closes_connection(self):
        with patch.object(bridge.http.client, "HTTPConnection") as factory, patch.object(bridge.socket, "socket"):
            connection = factory.return_value
            response = connection.getresponse.return_value
            response.status = 200
            response.read.return_value = b'{"status":"failed"}'
            self.assertEqual(bridge.query_loop_status("looprun-one", "ws1"), "failed")
            connection.request.assert_called_once_with("GET", "/api/workspaces/ws1/loop-runs/looprun-one/briefing")
            connection.close.assert_called_once_with()

    def test_daemon_http_failure_is_not_a_terminal_status(self):
        with patch.object(bridge.http.client, "HTTPConnection") as factory, patch.object(bridge.socket, "socket"):
            connection = factory.return_value
            connection.getresponse.return_value.status = 503
            self.assertIsNone(bridge.query_loop_status("looprun-one", "ws1"))
            connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
