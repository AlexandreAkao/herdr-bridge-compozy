#!/usr/bin/env python3
"""Protege os filtros do colorize.py contra regressao.

Roda sem dependencias: python3 tests/test_colorize.py
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "loop-sample.jsonl")
ANSI = re.compile(r"\x1b\[[0-9;]*[mAK]")

failures = []


def check(label, condition):
    print(("  ok   " if condition else "  FAIL ") + label)
    if not condition:
        failures.append(label)


def render():
    """Devolve (linhas_visiveis, saida_crua). Linha que comeca com ESC[A
    reescreve a anterior no terminal, entao nao conta como linha nova."""
    # text=True traduziria o \r da linha de reescrita em \n (universal
    # newlines) e partiria a linha em duas. Le bytes e decodifica na mao.
    with open(FIXTURE) as fh:
        out = subprocess.run(
            [sys.executable, os.path.join(ROOT, "colorize.py")],
            stdin=fh, capture_output=True, timeout=30,
        ).stdout.decode("utf-8")
    visible = []
    for raw in out.split("\n"):
        if not raw:
            continue
        clean = ANSI.sub("", raw)
        if raw.startswith("\x1b[A"):
            if visible:
                visible[-1] = clean
        else:
            visible.append(clean)
    return visible, out


def main():
    visible, raw = render()
    body = "\n".join(visible)
    print("saida renderizada:")
    for line in visible:
        print("   |", line)
    print()

    check("evento usage nao aparece", "usage_update" not in body)
    check("skill.shadowed nao aparece", "shadowed" not in body)
    check("tool_result [REDACTED] nao aparece", "[REDACTED]" not in body)
    check("rotulo da ferramenta funde com o comando", "Terminal▸" in body)
    check("prefixo cd <caminho> e removido", "cd /srv/app" not in body)
    check("comando sobrevive ao corte do prefixo", "npx tsc --noEmit" in body)
    check("duplicata consecutiva colapsa em x2", "×2" in body)
    check("agent_message fragmentado vira uma linha", "Running the suite now." in body)
    check("heredoc e achatado", "⏎ …" in body)
    check("falha sobrevive ao filtro de tool_result", "suite failed: 2 specs" in body)
    check("falha sai em vermelho", "\033[1;31m" in raw)
    check("12 eventos viram 5 linhas visiveis", len(visible) == 5)

    print()
    if failures:
        print(f"{len(failures)} falha(s)")
        return 1
    print("tudo passou")
    return 0


if __name__ == "__main__":
    sys.exit(main())
