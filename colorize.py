#!/usr/bin/env python3
"""Colore e enxuga o stream de logs do CompozyOS para o pane do herdr.

Le `compozy logs --follow -o jsonl` no stdin. O CLI nao emite ANSI em
nenhuma circunstancia (nao ha flag de cor nem FORCE_COLOR), entao cor e
filtragem nascem aqui.

Medido num loop real (400 eventos): 30% era `usage`, 15% era `tool_result`
com summary "[REDACTED]" (100% deles), 14% era linha repetida identica e
4% era chatter de resolucao de skill. Menos de um terco tinha conteudo.
"""
import json
import re
import shutil
import sys

RESET = "\033[0m"

# ruido de infraestrutura: nao diz nada sobre o que o agente esta fazendo
SKIP_TYPES = {
    "usage",
    "session_stream_subscribed",
    "available_commands_update",
    "skill.shadowed",
    "skills.exposure.broken_detected",
}
SKIP_TYPE_PREFIXES = ("harness.",)

# resultado de ferramenta vem sempre censurado; a linha so ocupa espaco
EMPTY_SUMMARIES = {"[REDACTED]", "", "null"}

# rotulo de ferramenta ("Terminal", "Edit"...) vem como evento proprio,
# seguido do comando de verdade. Junta os dois numa linha so.
TOOL_LABEL = re.compile(r"^[A-Z][A-Za-z]{2,19}$")

TYPE_STYLE = {
    "agent_message": "1;97",
    "user_message": "33",
    "tool_call": "36",
    "tool_result": "2;36",
    "done": "1;32",
    "session_stopped": "35",
    "synthetic_reentry": "2;35",
    "coordinator.": "1;35",
    "hook.": "2;35",
    "memory.": "34",
    "transcript": "90",
    "runtime_progress": "2;37",
    "system": "2;37",
}

OUTCOME_STYLE = {
    "warning": "1;33",
    "failure": "1;31",
    "error": "1;31",
    "success": "1;32",
}


def style_for(etype, outcome):
    if outcome in OUTCOME_STYLE:
        return OUTCOME_STYLE[outcome]
    if etype in TYPE_STYLE:
        return TYPE_STYLE[etype]
    for prefix, code in TYPE_STYLE.items():
        if prefix.endswith(".") and etype.startswith(prefix):
            return code
    return "37"


def width():
    try:
        return max(60, shutil.get_terminal_size().columns) - 2
    except Exception:
        return 118


# todo comando do agente vem como `cd /caminho/longo && <o que importa>`
CD_PREFIX = re.compile(r"^cd\s+\S+\s*(?:&&|;)\s*")


def flatten(summary):
    """Heredoc de varias linhas vira uma linha; o corpo raramente importa."""
    parts = [x.strip() for x in summary.replace("\r", "").split("\n") if x.strip()]
    if not parts:
        return ""
    head = CD_PREFIX.sub("", parts[0])
    return head + (" ⏎ …" if len(parts) > 1 else "")


class Renderer:
    def __init__(self):
        self.last_text = ""         # ultima linha impressa, pra reescrever igual
        self.pending_label = None   # rotulo de ferramenta esperando o comando
        self.streaming = False      # dentro de uma sequencia de agent_message
        self.last_key = None        # (tipo, texto) da ultima linha impressa
        self.repeat = 1

    def out(self, text, key=None):
        if key is not None and key == self.last_key:
            # repetida: reescreve a propria linha com o contador
            # a linha anterior ja terminou com \n, entao sobe uma linha,
            # limpa e reescreve com o contador
            self.repeat += 1
            sys.stdout.write(f"\033[A\r\033[K{self.last_text} \033[2;90m×{self.repeat}{RESET}\n")
            sys.stdout.flush()
            return
        self.repeat = 1
        self.last_key = key
        self.last_text = text
        sys.stdout.write(text + "\n")
        sys.stdout.flush()

    def close_stream(self):
        if self.streaming:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self.streaming = False
            self.last_key = None

    def feed(self, d):
        etype = str(d.get("type") or "?")
        if etype in SKIP_TYPES or etype.startswith(SKIP_TYPE_PREFIXES):
            return
        outcome = str(d.get("outcome") or "")
        summary = flatten(str(d.get("summary") or ""))

        # resultado vazio nao vira linha (a menos que seja falha)
        if etype == "tool_result" and summary in EMPTY_SUMMARIES and outcome not in OUTCOME_STYLE:
            return

        # rotulo de ferramenta: segura pro proximo evento
        if etype == "tool_call" and TOOL_LABEL.match(summary):
            self.pending_label = summary
            return

        ts = str(d.get("timestamp") or "")[11:19]
        code = style_for(etype, outcome)

        if etype == "agent_message":
            head = f"\033[2;37m{ts}{RESET} \033[{code}m{'msg':<14}{RESET} "
            if self.streaming:
                sys.stdout.write(summary)
            else:
                sys.stdout.write(head + summary)
                self.streaming = True
            sys.stdout.flush()
            return

        self.close_stream()

        label, label_len = "", 0
        if etype == "tool_call" and self.pending_label:
            label = f"\033[2;36m{self.pending_label}▸{RESET}"
            label_len = len(self.pending_label) + 1
        self.pending_label = None

        tag = etype if etype != "tool_call" else "tool"
        head = f"\033[2;37m{ts}{RESET} \033[{code}m{tag:<14}{RESET} {label}"
        room = width() - len(ts) - 17 - label_len
        body = summary if len(summary) <= room else summary[: room - 1] + "…"
        self.out(head + body, key=(etype, summary))


def main():
    r = Renderer()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if not line.startswith("{"):
            r.close_stream()
            r.out(f"\033[2;37m{line}{RESET}")
            continue
        try:
            r.feed(json.loads(line))
        except Exception:
            pass
    r.close_stream()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, BrokenPipeError):
        pass
