#!/usr/bin/env python3
"""Testa a logica pura da ponte: chave da linha, auto-cura e consolidacao.

Roda sem dependencias e sem herdr de pe: python3 tests/test_bridge.py
"""
import importlib.util
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("bridge", os.path.join(ROOT, "bridge.py"))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

failures = []


def check(label, condition):
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        failures.append(label)


def test_row_key():
    print("row_key")
    a = bridge.row_key("ws_1", "reviewer")
    b = bridge.row_key("ws_2", "reviewer")
    check("mesmo agente em workspaces diferentes sao linhas diferentes", a != b)
    check("sem workspace nao explode", bridge.row_key(None, "reviewer") == "no-ws/reviewer")
    check("o nome do agente sobrevive na chave", a.endswith("/reviewer"))


def test_drop_stale():
    print("drop_stale")
    now = time.time()
    sessions = {
        "fresca": {"state": "working", "ts": now - 10},
        "velha": {"state": "working", "ts": now - bridge.STALE_SESSION_SECONDS - 1},
        "sem_ts": {"state": "working"},
    }
    kept = bridge.drop_stale(sessions, now=now)
    check("sessao recente fica", "fresca" in kept)
    check("sessao sem evento ha muito tempo sai", "velha" not in kept)
    check("sessao sem carimbo e tratada como velha", "sem_ts" not in kept)


def test_consolidate():
    print("consolidate")
    state, _ = bridge.consolidate({})
    check("sem sessao viva a linha fica idle", state == "idle")

    state, active = bridge.consolidate({
        "a": {"state": "idle", "name": "A"},
        "b": {"state": "working", "name": "B"},
    })
    check("uma sessao terminando nao apaga outra trabalhando", state == "working")
    check("o titulo segue a sessao ativa", active[1]["name"] == "B")

    state, _ = bridge.consolidate({
        "a": {"state": "working", "name": "A"},
        "b": {"state": "blocked", "name": "B"},
    })
    check("blocked ganha de working", state == "blocked")


def test_prune():
    print("prune")
    original = bridge.pane_alive
    bridge.pane_alive = lambda pane_id: pane_id == "viva"
    try:
        data = {"ws/a": {"pane_id": "viva"}, "ws/b": {"pane_id": "morta"}}
        dead = bridge.prune(data)
        check("entrada com pane morto sai do mapa", "ws/b" not in data)
        check("entrada viva permanece", "ws/a" in data)
        check("prune reporta o que removeu", dead == ["ws/b"])
    finally:
        bridge.pane_alive = original


def test_read_attention():
    print("read_attention")
    check("class none nao bloqueia", bridge.read_attention({"class": "none"}) is False)
    check("class finished nao bloqueia", bridge.read_attention({"class": "finished"}) is False)
    check("class clarify bloqueia", bridge.read_attention({"class": "clarify"}) is True)
    check("class vazia nao bloqueia", bridge.read_attention({"class": ""}) is False)
    check("payload sem o campo devolve None (nao chuta)",
          bridge.read_attention({"session_id": "x"}) is None)


class FakeHerdr:
    """Substitui o socket do herdr: grava as chamadas e devolve panes falsos."""

    def __init__(self):
        self.calls = []
        self.n = 0

    def __call__(self, method, params):
        self.calls.append((method, params))
        if method == "tab.create":
            self.n += 1
            return {"result": {"root_pane": {"pane_id": f"p{self.n}"}, "tab": {"tab_id": f"t{self.n}"}}}
        return {"result": {"type": "ok"}}

    def reports(self):
        return [p for m, p in self.calls if m == "pane.report_agent"]

    def last_tokens(self):
        metas = [p for m, p in self.calls if m == "pane.report_metadata"]
        return metas[-1]["tokens"] if metas else {}


def test_loop_rows():
    print("loop rows (payloads reais de um run)")
    import json, tempfile
    fixture = os.path.join(ROOT, "tests", "fixtures", "loop-events.jsonl")
    events = [json.loads(l) for l in open(fixture) if l.strip()]
    fake = FakeHerdr()
    tmp = tempfile.mkdtemp()
    saved = (bridge.herdr, bridge.pane_alive, bridge.STATE_DIR, bridge.MAP_PATH, bridge.LOG_PATH)
    bridge.herdr, bridge.pane_alive = fake, lambda pane_id: True
    bridge.STATE_DIR, bridge.MAP_PATH = tmp, os.path.join(tmp, "panes.json")
    bridge.LOG_PATH = os.path.join(tmp, "bridge.log")
    try:
        by = {}
        for e in events:
            by.setdefault(e["event"], e)          # primeiro de cada tipo
        nodes = [e for e in events if e["event"] == "loop.node.terminal"]
        bridge.handle(by["loop.started"])
        check("loop.started cria uma linha propria (tab.create)",
              any(m == "tab.create" and p["label"].startswith("cz:loop:") for m, p in fake.calls))
        check("o pane segue a timeline do run",
              any(m == "pane.send_text" and "loop events --run" in p["text"] for m, p in fake.calls))
        check("linha nasce working", fake.reports()[-1]["state"] == "working")

        bridge.handle(by["loop.generation.post"])
        check("geracao vira token", fake.last_tokens().get("cz_gen") == "1")

        bridge.handle(nodes[0])
        check("no terminal vira token nome:disposicao (sem loop_name no payload)",
              fake.last_tokens().get("cz_node") == "review.0:succeeded")
        bridge.handle(nodes[1])
        check("o no seguinte substitui o token", fake.last_tokens().get("cz_node") == "fix.0:succeeded")
        check("no terminal nao muda o estado", fake.reports()[-1]["state"] == "working")

        bridge.handle(by["coordinator.decision"])
        check("coordinator.decision (sync, sem loop_run_id) acha a linha pelo task_id",
              fake.last_tokens().get("cz_node") == "fix.0:loop_action")
        check("e mantem a geracao vinda do task_id", fake.last_tokens().get("cz_gen") == "1")

        done = dict(by["loop.terminal"]); done["status"] = "done"
        bridge.handle(done)
        check("terminal done -> idle", fake.reports()[-1]["state"] == "idle")
        check("motivo fica no token", fake.last_tokens().get("cz_status") == "done")

        bridge.handle(by["loop.started"])
        blocked = dict(by["loop.terminal"]); blocked["status"] = "blocked"
        bridge.handle(blocked)
        check("terminal blocked -> linha blocked", fake.reports()[-1]["state"] == "blocked")
        check("o mesmo loop reusa a linha (uma tab so)",
              sum(1 for m, _ in fake.calls if m == "tab.create") == 1)

        entry = next(e for e in bridge.load_map().values() if e.get("kind") == "loop")
        stale = bridge.drop_stale(entry["sessions"], now=time.time() + bridge.STALE_SESSION_SECONDS * 2)
        check("blocked nao envelhece (continua esperando voce)", len(stale) == 1)
    finally:
        bridge.herdr, bridge.pane_alive, bridge.STATE_DIR, bridge.MAP_PATH, bridge.LOG_PATH = saved


def test_drain_order():
    print("drain_spool (ordem por timestamp, nao por chegada)")
    import json, tempfile
    fake = FakeHerdr()
    tmp = tempfile.mkdtemp()
    saved = (bridge.herdr, bridge.pane_alive, bridge.STATE_DIR, bridge.MAP_PATH, bridge.LOG_PATH, bridge.SPOOL_DIR)
    bridge.herdr, bridge.pane_alive = fake, lambda pane_id: True
    bridge.STATE_DIR, bridge.MAP_PATH = tmp, os.path.join(tmp, "panes.json")
    bridge.LOG_PATH, bridge.SPOOL_DIR = os.path.join(tmp, "bridge.log"), os.path.join(tmp, "spool")
    os.makedirs(bridge.SPOOL_DIR)
    try:
        base = {"workspace_id": "ws_t", "loop_run_id": "looprun-x", "loop_name": "probe"}
        # gravados fora de ordem: terminal primeiro, started por ultimo
        spool = [
            ("c.json", {**base, "event": "loop.terminal", "status": "done", "timestamp": "2026-01-01T00:00:00.300Z"}),
            ("b.json", {**base, "event": "loop.generation.post", "generation": 1, "timestamp": "2026-01-01T00:00:00.200Z"}),
            ("a.json", {**base, "event": "loop.started", "status": "running", "timestamp": "2026-01-01T00:00:00.100Z"}),
        ]
        for name, payload in spool:
            with open(os.path.join(bridge.SPOOL_DIR, name), "w") as fh:
                json.dump(payload, fh)
        with open(os.path.join(bridge.SPOOL_DIR, "lixo.json"), "w") as fh:
            fh.write("{ meio escrito")
        n = bridge.drain_spool()
        check("drena os 3 validos", n == 3)
        states = [r["state"] for r in fake.reports()]
        check("processa em ordem de timestamp: working, working, idle", states == ["working", "working", "idle"])
        check("arquivo corrompido e descartado sem quebrar", not os.listdir(bridge.SPOOL_DIR))
        check("segunda drenagem nao encontra nada", bridge.drain_spool() == 0)
    finally:
        (bridge.herdr, bridge.pane_alive, bridge.STATE_DIR, bridge.MAP_PATH, bridge.LOG_PATH, bridge.SPOOL_DIR) = saved


def main():
    for fn in (test_row_key, test_drop_stale, test_consolidate, test_prune,
               test_read_attention, test_loop_rows, test_drain_order):
        fn()
    print()
    if failures:
        print(f"{len(failures)} falha(s)")
        return 1
    print("tudo passou")
    return 0


if __name__ == "__main__":
    sys.exit(main())
