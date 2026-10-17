"""Do not reuse a pre-upgrade Python venv that cannot import distro GTK bindings."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_PYTHON = Path('/usr/bin/python3')


def require_system_gtk():
    probe = subprocess.run(
        [str(SYSTEM_PYTHON), '-c', 'import gi, cairo; gi.require_version("Gtk", "4.0"); gi.require_version("Gtk4LayerShell", "1.0")'],
        capture_output=True,
    )
    if probe.returncode:
        pytest.skip('requires system Python with GTK4 bindings')


def stale_python(path):
    path.parent.mkdir(parents=True)
    path.write_text('''#!/bin/bash
case "$2" in
    *sys.version_info*) echo 3.11 ;;
    *) exit 1 ;;
esac
''')
    path.chmod(0o755)


def test_run_skips_old_local_and_installed_venvs(tmp_path):
    require_system_gtk()
    shutil.copy2(ROOT / 'run.sh', tmp_path / 'run.sh')
    stale_python(tmp_path / '.venv/bin/python')
    stale_python(tmp_path / 'data/kizurium-translator/venv/bin/python')
    package = tmp_path / 'src/kizurium_translator'
    package.mkdir(parents=True)
    (package / '__init__.py').touch()
    (package / 'cli.py').write_text('import sys\ndef main(args):\n print(sys.executable)\n return 0\n')
    env = {**os.environ, 'XDG_DATA_HOME': str(tmp_path / 'data')}
    env.pop('KIZURIUM_PYTHON', None)
    result = subprocess.run(['bash', str(tmp_path / 'run.sh'), '--version'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(SYSTEM_PYTHON)


def test_installer_replaces_old_python_and_pins_sync(tmp_path):
    require_system_gtk()
    venv = tmp_path / 'venv'
    stale_python(venv / 'bin/python')
    # Even a claimed system-site-packages flag does not make a 3.11 venv
    # compatible with GTK extensions built for the current system Python.
    (venv / 'pyvenv.cfg').write_text('include-system-site-packages = true\n')
    source = (ROOT / 'install.sh').read_text()
    block = source.split('step "Python-окружение"', 1)[1].split('launcher="$(mktemp', 1)[0]
    script = '''set -euo pipefail
step() { :; }; ok() { :; }; warn() { :; }; err() { echo "$*" >&2; }
ensure_uv() { :; }
uv() {
    printf '%s\\n' "$*" >> "$CALLS"
    if [[ "$1" == venv ]]; then
        /usr/bin/python3 -m venv --without-pip --system-site-packages "${!#}"
    fi
}
''' + block
    env = {**os.environ, 'VENV_DIR': str(venv), 'STATE_DIR': str(tmp_path),
           'BIN_DIR': str(tmp_path / 'bin'), 'REPO_DIR': str(tmp_path),
           'CALLS': str(tmp_path / 'calls'), 'OFFLINE_PACKS': 'no',
           'MEIKI': 'no', 'EXTRAS': 'rapid'}
    result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / 'calls').read_text()
    assert 'venv --python /usr/bin/python3 --system-site-packages' in calls
    assert 'sync --python /usr/bin/python3 --locked --no-editable --extra rapid' in calls
    assert subprocess.run([str(venv / 'bin/python'), '-c', 'import gi, cairo']).returncode == 0


def test_generated_launcher_uses_system_package_when_venv_is_broken(tmp_path):
    system_command = Path('/usr/bin/kizurium-translator')
    if not system_command.is_file():
        pytest.skip('requires installed system Kizurium package')
    source = (ROOT / 'install.sh').read_text()
    block = 'launcher="$(mktemp' + source.split('launcher="$(mktemp', 1)[1].split('\nensure_path', 1)[0]
    (tmp_path / 'bin').mkdir()
    env = {**os.environ, 'VENV_DIR': str(tmp_path / 'missing-venv'),
           'BIN_DIR': str(tmp_path / 'bin')}
    result = subprocess.run(['bash', '-c', 'set -eu; ok() { :; };\n' + block], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    launcher = tmp_path / 'bin/kizurium-translator'
    subprocess.run(['bash', '-n', str(launcher)], check=True)
    expected = subprocess.check_output([str(system_command), '--version'], text=True)
    result = subprocess.run([str(launcher), '--version'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
