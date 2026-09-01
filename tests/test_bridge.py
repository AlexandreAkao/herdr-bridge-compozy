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


def test_loop_tokens():
    print("loop_tokens")
    t = bridge.loop_tokens({"loop": "pr-review-fix", "generation": 2})
    check("nome do loop vira token", t["cz_loop"] == "pr-review-fix")
    check("geracao vira token", t["cz_gen"] == "2")
    check("geracao zero nao some", bridge.loop_tokens({"generation": 0})["cz_gen"] == "0")
    vazio = bridge.loop_tokens({})
    check("payload sem loop limpa os tokens",
          vazio["cz_loop"] is None and vazio["cz_gen"] is None)


def main():
    for fn in (test_row_key, test_drop_stale, test_consolidate, test_prune,
               test_read_attention, test_loop_tokens):
        fn()
    print()
    if failures:
        print(f"{len(failures)} falha(s)")
        return 1
    print("tudo passou")
    return 0


if __name__ == "__main__":
    sys.exit(main())
