#!/usr/bin/env python3
"""Ponte compozy -> herdr.

Recebe payload de hook do CompozyOS no stdin e reflete o estado do agente
como uma linha de agente no herdr. Uma linha por agent_name (estavel),
nao por sessao (as sessoes de loop sao efemeras e se repetem).

Nunca falha o hook: qualquer erro vira exit 0.
"""
import fcntl
import json
import os
import socket
import sys
import time

HERDR_SOCK = os.path.expanduser("~/.config/herdr/herdr.sock")
STATE_DIR = os.path.expanduser("~/.local/state/herdr-bridge")
MAP_PATH = os.path.join(STATE_DIR, "panes.json")
LOG_PATH = os.path.join(STATE_DIR, "bridge.log")
SOURCE = "compozy-bridge"
AGENT_ID = "compozy"

# allowlist: so agente de verdade vira linha.
#   user   = sessao que a pessoa criou
#   system = agentes de loop (reviewer, review_fixer, publisher...)
# fora daqui ficam as sessoes internas do daemon: spawned (memory extractor),
# dream (checkpoint/curator) e o que mais o daemon inventar depois.
ALLOW_SESSION_TYPES = {"user", "system"}
SKIP_INPUT_CLASSES = {"synthetic_reentry"}

# eventos que tiram a sessao da lista de ativas (em vez de marcar um estado)
TERMINAL_EVENTS = {"session.post_stop", "agent.stopped", "agent.crashed"}
# prioridade ao consolidar varias sessoes do mesmo agente numa linha so
STATE_RANK = {"blocked": 3, "working": 2, "idle": 1}

EVENT_STATE = {
    "session.post_create": "idle",
    "turn.start": "working",
    "message.start": "working",
    "turn.end": "idle",
    "permission.request": "blocked",
    "task.needs_attention": "blocked",
    "task.blocked": "blocked",
    "session.post_stop": "idle",
    "agent.stopped": "idle",
    "agent.crashed": "idle",
}


def log(msg):
    try:
        with open(LOG_PATH, "a") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}\n")
    except Exception:
        pass


def herdr(method, params):
    """Uma chamada JSON-RPC no socket do herdr. None se o herdr nao estiver de pe."""
    if not os.path.exists(HERDR_SOCK):
        return None
    req = {"id": f"{SOURCE}:{time.time_ns()}", "method": method, "params": params}
    try:
        cli = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        cli.settimeout(2.0)
        cli.connect(HERDR_SOCK)
        cli.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = cli.recv(65536)
            if not chunk:
                break
            buf += chunk
        cli.close()
        line = buf.split(b"\n")[0]
        return json.loads(line) if line else None
    except Exception as exc:
        log(f"herdr {method} falhou: {exc}")
        return None


class Locked:
    """Lock de arquivo: hooks async podem disparar em paralelo."""

    def __enter__(self):
        os.makedirs(STATE_DIR, exist_ok=True)
        self.fh = open(os.path.join(STATE_DIR, ".lock"), "w")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def load_map():
    try:
        with open(MAP_PATH) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_map(data):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = MAP_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, MAP_PATH)


def pane_alive(pane_id):
    res = herdr("pane.get", {"pane_id": pane_id})
    return bool(res and "result" in res)


def tail_command(agent_name):
    """Comando que roda dentro do pane: log do agente, colorido."""
    here = os.path.dirname(os.path.abspath(__file__))
    colorizer = os.path.join(here, "colorize.py")
    return (f"compozy logs --follow --agent {agent_name} -o jsonl"
            f" | python3 {colorizer}\n")


def ensure_row(data, agent_name):
    """Entrada do mapa para esse agente, criando a aba no herdr se preciso.
    Precisa ser chamada com o lock ja segurado."""
    entry = data.get(agent_name)
    if entry and pane_alive(entry.get("pane_id", "")):
        return entry

    res = herdr("tab.create", {"label": f"cz:{agent_name}", "focus": False})
    if not res or "result" not in res:
        return None
    pane_id = res["result"]["root_pane"]["pane_id"]
    tab_id = res["result"]["tab"]["tab_id"]
    # o pane vira um tail util em vez de um shell vazio.
    # o CLI do compozy nao emite ANSI, entao o jsonl passa pelo colorize.py
    herdr("pane.send_text", {"pane_id": pane_id, "text": tail_command(agent_name)})
    entry = {"pane_id": pane_id, "tab_id": tab_id, "sessions": {}}
    data[agent_name] = entry
    log(f"linha criada para {agent_name}: {pane_id}")
    return entry


def consolidate(sessions):
    """Estado da linha a partir de todas as sessoes vivas do agente.
    Uma sessao que termina nao pode apagar outra que ainda trabalha."""
    if not sessions:
        return "idle", None
    best_id, best = None, None
    for sid, info in sessions.items():
        rank = STATE_RANK.get(info.get("state"), 0)
        if best is None or rank > STATE_RANK.get(best.get("state"), 0):
            best_id, best = sid, info
    return best.get("state", "idle"), (best_id, best)


def handle(payload):
    event = payload.get("event") or ""
    state = EVENT_STATE.get(event)
    if not state:
        return
    if payload.get("session_type") not in ALLOW_SESSION_TYPES:
        return
    if payload.get("input_class") in SKIP_INPUT_CLASSES:
        return
    agent_name = payload.get("agent_name")
    if not agent_name:
        return
    session_id = payload.get("session_id") or "?"

    with Locked():
        data = load_map()
        entry = ensure_row(data, agent_name)
        if not entry:
            return
        sessions = entry.setdefault("sessions", {})
        if event in TERMINAL_EVENTS:
            sessions.pop(session_id, None)
        else:
            sessions[session_id] = {
                "state": state,
                "name": payload.get("session_name"),
                "type": payload.get("session_type"),
                "turn": payload.get("turn_id"),
            }
        row_state, active = consolidate(sessions)
        if active and active[1].get("name"):
            entry["last_title"] = active[1]["name"]
        # sessao ociosa nao precisa ficar no mapa: o proximo evento dela readiciona.
        # sem isso o contador de sessoes vivas so cresce.
        entry["sessions"] = {s: i for s, i in sessions.items() if i.get("state") != "idle"}
        live = len(entry["sessions"])
        last_title = entry.get("last_title")
        data[agent_name] = entry
        save_map(data)
        pane_id = entry["pane_id"]

    seq = time.time_ns()
    active_id, active_info = active if active else (None, {})
    herdr("pane.report_agent", {
        "pane_id": pane_id, "source": SOURCE, "agent": AGENT_ID,
        "state": row_state, "seq": seq,
        "agent_session_id": active_id,
        "message": f"{event} · {payload.get('session_name') or ''}".strip(" ·"),
    })
    herdr("pane.report_metadata", {
        "pane_id": pane_id, "source": SOURCE, "seq": seq,
        "display_agent": AGENT_ID,
        "title": (active_info.get("name") if active_info else None) or last_title or agent_name,
        "tokens": {
            "cz_agent": agent_name,
            "cz_session": (active_id or "")[:24] or None,
            "cz_type": active_info.get("type") if active_info else None,
            "cz_live": str(live) if live else None,
        },
    })


def cmd_status():
    data = load_map()
    print(f"linhas mapeadas: {len(data)}")
    for agent, entry in sorted(data.items()):
        alive = "viva" if pane_alive(entry["pane_id"]) else "MORTA"
        live = entry.get("sessions") or {}
        print(f"  {agent:24} {entry['pane_id']:8} {entry['tab_id']:8} {alive}  sessoes={len(live)}")
        for sid, info in live.items():
            print(f"       {sid:26} {info.get('state')}")
    res = herdr("agent.list", {})
    if res and "result" in res:
        print("\nherdr agent list (linhas do compozy):")
        for a in res["result"]["agents"]:
            if a.get("agent") == AGENT_ID:
                print(f"  {a['pane_id']:8} {a.get('agent_status'):8} {a.get('title') or ''}")


def cmd_reset():
    with Locked():
        data = load_map()
        for agent, entry in data.items():
            herdr("pane.release_agent", {
                "pane_id": entry["pane_id"], "source": SOURCE,
                "agent": AGENT_ID, "seq": time.time_ns()})
            herdr("tab.close", {"tab_id": entry["tab_id"]})
            print(f"removida: {agent} ({entry['tab_id']})")
        save_map({})


def cmd_refresh():
    """Reinicia o tail dos panes existentes com o comando novo."""
    for agent, entry in load_map().items():
        pane_id = entry["pane_id"]
        if not pane_alive(pane_id):
            print(f"  {agent}: pane morto, pulando")
            continue
        herdr("pane.send_keys", {"pane_id": pane_id, "keys": ["ctrl+c"]})
        time.sleep(0.3)
        herdr("pane.send_text", {"pane_id": pane_id, "text": tail_command(agent)})
        print(f"  {agent}: tail reiniciado em {pane_id}")


def main():
    if len(sys.argv) > 1:
        if sys.argv[1] == "--refresh":
            return cmd_refresh()
        if sys.argv[1] == "--status":
            return cmd_status()
        if sys.argv[1] == "--reset":
            return cmd_reset()
        print("uso: bridge.py [--status|--reset]   (sem args: modo hook, payload no stdin)")
        return
    try:
        raw = sys.stdin.read()
        if raw.strip():
            handle(json.loads(raw))
    except Exception as exc:
        log(f"erro: {exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
