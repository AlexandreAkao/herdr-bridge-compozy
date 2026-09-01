#!/usr/bin/env bash
# Monta o diretorio de pacote que o `compozy extension publish` consome.
# Só o que a extensao precisa em runtime, mais a licenca.
set -euo pipefail

dest="${1:?uso: stage-extension.sh <diretorio-destino>}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p "$dest"
for f in extension.toml bridge.py colorize.py LICENSE README.md; do
  cp "$root/$f" "$dest/$f"
done
chmod +x "$dest/bridge.py" "$dest/colorize.py"

echo "empacotado em $dest:"
ls -1 "$dest"
