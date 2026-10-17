#!/usr/bin/env bash
set -euo pipefail

# Запуск текущей копии проекта из любого рабочего каталога.
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$project_dir"

if [[ -n "${KIZURIUM_PYTHON:-}" ]]; then
    python_bin="$KIZURIUM_PYTHON"
elif [[ -x "$project_dir/.venv/bin/python" ]]; then
    python_bin="$project_dir/.venv/bin/python"
else
    python_bin=python3
fi

export PYTHONPATH="$project_dir/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$python_bin" -c 'import sys; from kizurium_translator.cli import main; sys.exit(main(sys.argv[1:]))' "$@"
