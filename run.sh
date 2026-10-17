#!/usr/bin/env bash
set -euo pipefail

# Resolve symlinks so this works when called from another directory or via a
# convenience symlink in ~/bin.
script_path="$(readlink -f -- "${BASH_SOURCE[0]}")" || {
    printf 'Не удалось определить путь к run.sh\n' >&2
    exit 1
}
project_dir="$(cd -- "$(dirname -- "$script_path")" && pwd -P)"
cd -- "$project_dir"

resolve_python() {
    local candidate="$1" version major minor
    if [[ "$candidate" != */* ]]; then
        candidate="$(command -v -- "$candidate" 2>/dev/null || true)"
    fi
    [[ -n "$candidate" && -x "$candidate" ]] || return 1
    version="$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)" || return 1
    IFS=. read -r major minor <<< "$version"
    ((major > 3 || (major == 3 && minor >= 11))) || return 1
    python_bin="$candidate"
    python_version="$version"
    return 0
}

python_bin=""
python_version=""
if [[ -n "${KIZURIUM_PYTHON:-}" ]]; then
    if ! resolve_python "$KIZURIUM_PYTHON"; then
        printf 'KIZURIUM_PYTHON должен указывать на исполняемый Python 3.11+: %s\n' "$KIZURIUM_PYTHON" >&2
        exit 2
    fi
else
    state_data="${XDG_DATA_HOME:-$HOME/.local/share}/kizurium-translator/venv/bin/python"
    candidates=(
        "$project_dir/.venv/bin/python"
        "$state_data"
        python3
        python
    )
    for candidate in "${candidates[@]}"; do
        if resolve_python "$candidate"; then
            break
        fi
    done
fi

if [[ -z "$python_bin" ]]; then
    printf 'Подходящий Python не найден. Нужен Python 3.11+ и зависимости Kizurium.\n' >&2
    printf 'Задайте его явно: KIZURIUM_PYTHON=/path/to/python ./run.sh\n' >&2
    exit 127
fi

export PYTHONPATH="$project_dir/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$python_bin" -c 'import sys; from kizurium_translator.cli import main; sys.exit(main(sys.argv[1:]))' "$@"
