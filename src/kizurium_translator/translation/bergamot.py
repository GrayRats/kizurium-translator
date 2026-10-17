"""Native Bergamot runtime and verified model lifecycle. No implicit network access.

Catalog is the browsermt Python binding's own TranslateLocally repository.
Firefox Remote Settings blobs are not assumed to be native Marian models.
"""
from __future__ import annotations

import configparser
import hashlib
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from functools import lru_cache
import tarfile
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from ..paths import default_paths
from .providers import ProviderError
from .scheduler import Cancelled

CATALOG = "https://translatelocally.com/models.json"
# Mozilla's own model repository (what Firefox Translations downloads). Every
# record carries the size and sha256 of its file, which is what is verified.
MOZILLA_SERVER = "https://firefox.settings.services.mozilla.com/v1/"
MOZILLA_RECORDS = MOZILLA_SERVER + "buckets/main/collections/translations-models/records"
MOZILLA_HOST_SUFFIX = ".mozilla.net"
# The decoder settings Firefox itself runs these models with.
FIREFOX_DECODER = {
    "beam-size": 1, "normalize": 1.0, "word-penalty": 0, "max-length-break": 128,
    "mini-batch-words": 1024, "workspace": 128, "max-length-factor": 2.0, "skip-cost": True,
    "cpu-threads": 0, "quiet": True, "quiet-translation": True,
    "gemm-precision": "int8shiftAlphaAll", "alignment": "soft",
}
MAX_DOWNLOAD = 2 * 1024 ** 3
log = logging.getLogger(__name__)


def cache_root():
    return default_paths().data_dir / "bergamot-models"


def pair_name(source, target):
    if not all(re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z]{2,4})?", x) for x in (source, target)):
        raise ProviderError("unsupported_pair")
    return f"{source}-{target}"


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


RUNTIME_MISSING = (
    "Bergamot: нет движка. Установите его: kizurium-translator --bergamot-runtime-install "
    "(или кнопка в Настройки → Локальные модели)")
HELPER_PYTHON = "3.10"  # newest Python with a ready-made bergamot wheel on PyPI


def runtime_dir():
    return default_paths().data_dir / "bergamot-runtime"


def helper_python():
    """Private interpreter with working bindings, including manually installed ones."""
    python = runtime_dir() / "venv" / "bin" / "python"
    if python.is_file() and _probe_python(str(python), python.stat().st_mtime_ns):
        return python
    return None


def in_process_available():
    return (importlib.util.find_spec("bergamot") is not None
            and importlib.util.find_spec("yaml") is not None
            and _probe_python(sys.executable, Path(sys.executable).stat().st_mtime_ns))


@lru_cache(maxsize=32)
def _probe_python(python, modified):
    """Import native bindings in isolation: a present but broken .so is not usable."""
    try:
        result = subprocess.run(
            [python, "-I", "-c", "import bergamot, yaml; assert all(hasattr(bergamot, n) for n in ('Service', 'VectorString', 'ResponseOptions'))"],
            capture_output=True, timeout=15, check=False)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def system_python():
    """Find bindings installed outside the application's virtual environment."""
    candidates = [os.environ.get("KIZURIUM_BERGAMOT_PYTHON", ""),
                  str(Path(sys.base_prefix) / "bin/python3"), "/usr/bin/python3"]
    for directory in (Path("/usr/bin"), Path("/usr/local/bin")):
        candidates.extend(str(p) for p in sorted(directory.glob("python3.*"))
                          if re.fullmatch(r"python3\.\d+", p.name))
    seen = set()
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        try:
            identity = str(path.resolve())
            if identity in seen:
                continue
            seen.add(identity)
            if _probe_python(str(path), path.stat().st_mtime_ns):
                return path
        except OSError:
            continue
    return None


def refresh_runtime():
    """Called by settings workers after an external installation or repair."""
    _probe_python.cache_clear()
    return runtime_kind()


def runtime_kind():
    """"in-process", "helper" or "" — where Bergamot would run."""
    if in_process_available():
        return "in-process"
    if helper_python():
        return "helper"
    return "system" if system_python() else ""


def runtime_available():
    return bool(runtime_kind())


class HelperRuntime:
    """A loaded model living in the helper process (JSON lines over a pipe)."""

    TIMEOUT = 60.0

    def __init__(self, directory, python=None):
        import subprocess

        self._lock = threading.Lock()
        self._next = 0
        # -I: the script's own directory (which holds *our* bergamot.py) must not
        # shadow the real binding on sys.path.
        worker = Path(__file__).with_name("bergamot_worker.py")
        self._proc = subprocess.Popen(
            [str(python or helper_python()), "-I", str(worker)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
            start_new_session=True)
        try:
            self._call({"op": "load", "dir": str(directory)})
        except BaseException:
            self.close()
            raise

    def _call(self, request):
        import select

        with self._lock:
            if self._proc.poll() is not None:
                raise ProviderError("incompatible", detail="Bergamot: вспомогательный процесс остановился")
            self._next += 1
            request["id"] = self._next
            try:
                self._proc.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
            except OSError:
                raise ProviderError("incompatible", detail="Bergamot: вспомогательный процесс не отвечает") from None
            if not select.select([self._proc.stdout], [], [], self.TIMEOUT)[0]:
                self.close()
                raise ProviderError("timeout", detail="Bergamot: перевод не уложился во время")
            line = self._proc.stdout.readline()
        try:
            answer = json.loads(line)
        except ValueError:
            raise ProviderError("incompatible", detail="Bergamot: непонятный ответ движка") from None
        if not answer.get("ok") or answer.get("id") != request["id"]:
            raise ProviderError("incompatible", detail="Bergamot не смог загрузить или применить модель "
                                                       f"({answer.get('error', 'нет ответа')})")
        return answer

    def translate(self, text):
        return self._call({"op": "translate", "text": text})["text"].strip()

    def close(self):
        proc = self._proc
        try:
            if proc.poll() is None:
                proc.stdin.close()
                try:
                    proc.wait(timeout=2)
                except Exception:  # noqa: BLE001
                    proc.kill()
                    proc.wait(timeout=2)
        except OSError:
            pass


class InProcessRuntime:
    def __init__(self, service, model):
        self.service, self.model = service, model

    def translate(self, text):
        import bergamot

        result = self.service.translate(self.model, bergamot.VectorString([text]), bergamot.ResponseOptions())
        return result[0].target.text.strip()

    def close(self):
        self.service = self.model = None


def install_runtime(emit=lambda *_: None):
    """Create the helper environment (own Python 3.10 + bergamot) and verify it.

    Network is used (uv fetches a standalone Python and the wheel from PyPI), so the
    caller must have checked offline mode. The marker file is written only after
    the interpreter has really imported bergamot and PyYAML.
    """
    import subprocess

    uv = shutil.which("uv")
    if not uv:
        raise ProviderError("incompatible", detail="Нужен uv (https://docs.astral.sh/uv/), чтобы подготовить Python 3.10")
    root = runtime_dir()
    venv = root / "venv"
    root.mkdir(parents=True, exist_ok=True)
    (root / "ok").unlink(missing_ok=True)

    def run(*cmd):
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if done.returncode != 0:
            raise ProviderError("incompatible", detail=(done.stderr or done.stdout).strip().splitlines()[-1][:200]
                                if (done.stderr or done.stdout).strip() else "команда завершилась с ошибкой")

    emit("Installing", "runtime", 0.1, f"Python {HELPER_PYTHON}")
    run(uv, "venv", "--clear", "--python", HELPER_PYTHON, str(venv))
    emit("Installing", "runtime", 0.5, "bergamot, PyYAML")
    run(uv, "pip", "install", "--python", str(venv / "bin" / "python"), "--only-binary", ":all:", "bergamot", "pyyaml")
    emit("Validating", "runtime", 0.9, "")
    run(str(venv / "bin" / "python"), "-c", "import bergamot, yaml")
    (root / "ok").write_text(HELPER_PYTHON + "\n", encoding="utf-8")
    return venv


def remove_runtime():
    shutil.rmtree(runtime_dir(), ignore_errors=True)


def safe_file(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ProviderError("corrupted")
    return path


def validate_files(root, source, target):
    """Validate a manifest and every referenced component before native loading."""
    root = Path(root)
    try:
        manifest = json.loads((root / "kizurium-model.json").read_text())
        if manifest["source"] != source or manifest["target"] != target:
            raise ProviderError("incompatible")
        if manifest["runtime"] != "bergamot-native-v1":
            raise ProviderError("incompatible")
        files = manifest["files"]
        if not files:
            raise ProviderError("corrupted")
        for name, meta in files.items():
            path = safe_file(root, name)
            if path.stat().st_size != meta["size"] or path.stat().st_size <= 0:
                raise ProviderError("corrupted")
            if digest(path) != meta["sha256"]:
                raise ProviderError("corrupted")
        import yaml

        config_path = safe_file(root, manifest["config"])
        if manifest["config"] not in files:
            raise ProviderError("corrupted")
        config = yaml.safe_load(config_path.read_text())
        for field in ("models", "vocabs"):
            if not isinstance(config.get(field), list) or not config[field]:
                raise ProviderError("incompatible")
            for name in config[field]:
                component = safe_file(config_path.parent, name)
                if str(component.relative_to(root.resolve())) not in files:
                    raise ProviderError("corrupted")
        shortlist = config.get("shortlist", [])
        if shortlist:
            component = safe_file(config_path.parent, shortlist[0])
            if str(component.relative_to(root.resolve())) not in files:
                raise ProviderError("corrupted")
        return manifest, config_path
    except ProviderError:
        raise
    except ImportError:
        raise ProviderError("incompatible", detail="Install PyYAML and native bergamot bindings") from None
    except (OSError, ValueError, KeyError, TypeError):
        raise ProviderError("corrupted") from None


def load_runtime(root, source, target):
    """A loaded model: in this process when the binding is importable, else in the helper."""
    _, config_path = validate_files(root, source, target)
    kind = runtime_kind()
    if not kind:
        raise ProviderError("incompatible", detail=RUNTIME_MISSING)
    if kind in {"helper", "system"}:
        return HelperRuntime(root, system_python() if kind == "system" else helper_python())
    try:
        import bergamot
        import yaml

        cfg = yaml.safe_load(config_path.read_text())
        for key in ("models", "vocabs"):
            cfg[key] = [str(safe_file(config_path.parent, name)) for name in cfg[key]]
        if cfg.get("shortlist"):
            cfg["shortlist"][0] = str(safe_file(config_path.parent, cfg["shortlist"][0]))
        cfg.update({"ssplit-prefix-file": "", "ssplit-mode": "paragraph", "max-length-break": 128,
                    "mini-batch-words": 512, "workspace": 128, "alignment": "soft"})
        service = bergamot.Service(bergamot.ServiceConfig(numWorkers=1, logLevel="off"))
        # Resolve paths in a private config, never chdir or modify an imported profile.
        with tempfile.TemporaryDirectory(prefix="kizurium-bergamot-") as temp:
            config = Path(temp) / "config.yml"
            config.write_text(yaml.safe_dump(cfg))
            model = service.modelFromConfigPath(str(config))
        runtime = InProcessRuntime(service, model)
        if not runtime.translate("Test."):
            raise ValueError
        return runtime
    except ProviderError:
        raise
    except Exception:
        raise ProviderError("incompatible", detail="Bergamot could not initialize this model") from None


def verify_runtime(root, source, target):
    """Load a model once to prove it runs, then release it."""
    load_runtime(root, source, target).close()


def _base(code):
    return code.split("-")[0].lower()


def mozilla_plan(records, source, target):
    """Newest stable version that has every file the pair needs, or None.

    Alpha/beta versions ("2.1a1") are skipped, and a record without a sha256 and
    a size is not a candidate: nothing unverifiable is downloaded.
    """
    src, dst = _base(source), _base(target)
    versions: dict[str, dict] = {}
    for record in records:
        attachment = record.get("attachment") or {}
        if (record.get("fromLang"), record.get("toLang")) != (src, dst):
            continue
        if not re.fullmatch(r"\d+(?:\.\d+)*", str(record.get("version", ""))):
            continue
        if not re.fullmatch(r"[a-f0-9]{64}", str(attachment.get("hash", ""))):
            continue
        if not isinstance(attachment.get("size"), int) or not attachment.get("location"):
            continue
        versions.setdefault(record["version"], {}).setdefault(record.get("fileType"), record)
    for version in sorted(versions, key=lambda v: tuple(int(p) for p in v.split(".")), reverse=True):
        files = versions[version]
        need = ("model", "lex") + (("vocab",) if "vocab" in files else ("srcvocab", "trgvocab"))
        if all(kind in files for kind in need):
            return version, {kind: files[kind] for kind in need}
    return None


def marian_config(names):
    """The decoder config for a set of Firefox-format files (names by file type)."""
    vocabs = [names["vocab"], names["vocab"]] if "vocab" in names else [names["srcvocab"], names["trgvocab"]]
    return {**FIREFOX_DECODER, "models": [names["model"]], "vocabs": vocabs,
            "shortlist": [names["lex"], False]}


def write_model_dir(stage, source, target, names, version, origin):
    """Config + manifest for a directory that already holds the model files."""
    import yaml

    stage = Path(stage)
    (stage / "config.intgemm8bitalpha.yml").write_text(yaml.safe_dump(marian_config(names)))
    manifest = {"source": source, "target": target, "runtime": "bergamot-native-v1",
                "version": version, "origin": origin, "config": "config.intgemm8bitalpha.yml",
                "files": {}}
    for path in sorted(stage.iterdir()):
        if path.is_file() and path.name != "kizurium-model.json":
            manifest["files"][path.name] = {"size": path.stat().st_size, "sha256": digest(path)}
    (stage / "kizurium-model.json").write_text(json.dumps(manifest, indent=2))


def firefox_file_sets(profile, source, target):
    """Directories in a profile that hold a complete Firefox-format model.

    Firefox keeps most Remote Settings attachments in an opaque IndexedDB, and
    those are not imported. What is found here is files under their real names
    (model.enru.intgemm*.bin, lex.*.enru.s2t.bin, vocab.enru.spm), e.g. an
    exported set. Nothing in the profile is opened for writing.
    """
    pair = _base(source) + _base(target)
    skip = {"cache2", "startupCache", "shader-cache", "storage"}
    for directory, dirs, files in os.walk(profile):
        dirs[:] = [d for d in dirs if d not in skip and not (Path(directory) / d).is_symlink()]
        found = {}
        for name in files:
            if re.fullmatch(rf"model\.{pair}\.intgemm[\w.]*\.bin", name):
                found["model"] = name
            elif re.fullmatch(rf"lex\.[\d.]+\.{pair}\.s2t\.bin", name):
                found["lex"] = name
            elif name == f"vocab.{pair}.spm":
                found["vocab"] = name
            elif name == f"srcvocab.{pair}.spm":
                found["srcvocab"] = name
            elif name == f"trgvocab.{pair}.spm":
                found["trgvocab"] = name
        if {"model", "lex"} <= found.keys() and ("vocab" in found or {"srcvocab", "trgvocab"} <= found.keys()):
            yield Path(directory), found


def firefox_profiles(home=None):
    home = Path(home or Path.home())
    for base in (home / ".mozilla/firefox", home / ".var/app/org.mozilla.firefox/.mozilla/firefox", home / "snap/firefox/common/.mozilla/firefox"):
        parser = configparser.ConfigParser()
        parser.read(base / "profiles.ini")
        for section in parser.sections():
            if parser.has_option(section, "Path"):
                path = Path(parser.get(section, "Path"))
                yield base / path if parser.get(section, "IsRelative", fallback="1") == "1" else path


def firefox_candidates(source, target):
    """Only exportable native bundles qualify; opaque IDB files are never guessed."""
    for profile in firefox_profiles():
        # Firefox changes its storage layout. Inspect both extension storage and
        # profile files for self-describing bundles; do not mutate SQLite/IDB.
        for directory, dirs, files in os.walk(profile):
            dirs[:] = [d for d in dirs if d not in {"cache2", "startupCache", "shader-cache"} and not (Path(directory) / d).is_symlink()]
            if "kizurium-model.json" in files:
                try:
                    validate_files(Path(directory), source, target)
                    yield Path(directory)
                except ProviderError:
                    continue


def import_firefox_files(directory, names, source, target, cached):
    """Copy a Firefox-format file set into Kizurium's cache and verify the copy."""
    cache_root().mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache_root(), prefix=".import-") as tmp:
        staged = Path(tmp) / "model"
        staged.mkdir()
        for name in names.values():
            shutil.copyfile(Path(directory) / name, staged / name)  # read-only on the source
        write_model_dir(staged, source, target, names, "firefox-import", f"firefox:{directory.name}")
        validate_files(staged, source, target)
        os.replace(staged, cached)
    return cached


def discover(source, target, explicit=""):
    pair = pair_name(source, target)
    if explicit:
        path = Path(explicit).expanduser()
        validate_files(path, source, target)
        return path
    cached = cache_root() / pair
    if cached.exists():
        validate_files(cached, source, target)
        return cached
    for candidate in firefox_candidates(source, target):
        validate_files(candidate, source, target)
        cache_root().mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache_root(), prefix=".import-") as tmp:
            staged = Path(tmp) / "model"
            shutil.copytree(candidate, staged)
            validate_files(staged, source, target)
            os.replace(staged, cached)
        return cached
    for profile in firefox_profiles():
        for directory, names in firefox_file_sets(profile, source, target):
            return import_firefox_files(directory, names, source, target, cached)
    raise ProviderError("not_found", detail=f"Translation model for {pair} is missing. Download it in Settings.")


class BergamotProvider:
    provider_id = "bergamot"
    display_name = "Bergamot (Local / Offline)"
    requires_network = False

    def __init__(self, model_path="", status=lambda *_: None):
        self.model_path, self.status = model_path, status
        self._lock = threading.Lock()
        self._runtime = None
        self._pair = None

    def supported_languages(self):
        return None

    def is_available(self):
        return runtime_available()

    def validate_configuration(self):
        if not self.is_available():
            raise ProviderError("incompatible", detail=RUNTIME_MISSING)

    def initialize(self):
        self.validate_configuration()

    def translate(self, _http, text, source, target):
        self.validate_configuration()
        with self._lock:
            pair = (source, target)
            if self._pair != pair:
                self._close_runtime()  # at most one initialized model
                path = discover(source, target, self.model_path)
                self.status("loading", 0)
                self._runtime = load_runtime(path, source, target)
                self._pair = pair
            try:
                return self._runtime.translate(text)
            except ProviderError:
                self._close_runtime()
                raise
            except Exception:
                raise ProviderError("translation") from None

    def _close_runtime(self):
        if self._runtime is not None:
            self._runtime.close()
        self._runtime = None
        self._pair = None

    def cancel(self):
        # Native inference is bounded and synchronous; scheduler drops stale responses.
        pass

    def shutdown(self):
        # Do not free a model while native inference is using it.
        if self._lock.acquire(blocking=False):
            try:
                self._close_runtime()
            finally:
                self._lock.release()


class ModelManager:
    """One bounded worker for discovery, verification and downloads."""

    def __init__(self, offline=lambda: False, notify=lambda *_: None):
        self.offline, self.notify = offline, notify
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bergamot-model")
        self.cancelled = threading.Event()
        self.future = None
        self.status = "Not Found"
        self.progress = 0.0
        self._lock = threading.Lock()

    def emit(self, status, pair, progress=0, detail=""):
        self.status, self.progress = status, progress
        self.notify(status, pair, progress, detail)

    def check(self, network=False):
        if self.cancelled.is_set() or (network and self.offline()):
            raise Cancelled()

    def submit(self, action, source, target, explicit=""):
        with self._lock:
            if self.future and not self.future.done():
                raise ProviderError("busy")
            if action in {"download", "redownload"} and self.offline():
                raise ProviderError("offline")
            self.cancelled.clear()
            self.future = self.pool.submit(self._work, action, source, target, explicit)
            return self.future

    def _work(self, action, source, target, explicit):
        pair = pair_name(source, target)
        try:
            self.check()
            if action == "remove":
                path = cache_root() / pair
                if path.is_symlink():
                    raise ProviderError("incompatible")
                if path.exists():
                    shutil.rmtree(path)
                self.emit("Not Found", pair)
                return
            if action in {"download", "redownload"}:
                path = self.download(source, target, replace=action == "redownload")
            else:
                self.emit("Validating", pair)
                path = discover(source, target, explicit)
                self.emit("Found", pair, detail=str(path))
                self.check()
                verify_runtime(path, source, target)
            note = str(path) if runtime_available() else (
                f"{path} — модель проверена по контрольной сумме, но runtime Bergamot не установлен")
            self.emit("Installed", pair, 1, note)
        except Cancelled:
            self.emit("Not Found", pair, detail="Operation cancelled; existing models preserved")
        except ProviderError as exc:
            state = {"corrupted": "Corrupted", "incompatible": "Incompatible", "not_found": "Not Found"}.get(exc.category, "Error")
            self.emit(state, pair, detail=exc.detail or exc.category)
            log.warning("provider=bergamot operation=%s category=%s HTTP=%s", action, exc.category, exc.status)
        except Exception as exc:
            self.emit("Error", pair, detail=type(exc).__name__)
            log.warning("provider=bergamot operation=%s error=%s", action, type(exc).__name__)

    def _fetch(self, url, path, size, sha256, done, total, pair):
        """One verified file. Progress is reported over the whole model, not per file."""
        import requests

        host = urlsplit(url)
        if host.scheme != "https" or not (host.hostname or "").endswith(MOZILLA_HOST_SUFFIX):
            raise ProviderError("incompatible")
        self.check(network=True)
        received, last = 0, 0.0
        with requests.get(url, stream=True, timeout=(5, 15)) as response, Path(path).open("wb") as out:
            response.raise_for_status()
            for chunk in response.iter_content(256 * 1024):
                self.check(network=True)
                out.write(chunk)
                received += len(chunk)
                if received > size:
                    raise ProviderError("corrupted")
                if time.monotonic() - last >= .2:
                    self.emit("Downloading", pair, (done + received) / total,
                              f"{(done + received) // 1048576} / {total // 1048576} MiB")
                    last = time.monotonic()
        if received != size or digest(path) != sha256:
            raise ProviderError("corrupted")
        return received

    def download_mozilla(self, source, target, destination, replace):
        """Download a model from Mozilla's repository; sha256 and size are checked."""
        import requests

        pair = pair_name(source, target)
        self.emit("Downloading", pair, detail="Reading Mozilla model repository")
        self.check(network=True)
        with requests.get(MOZILLA_RECORDS, timeout=(5, 30)) as response:
            response.raise_for_status()
            records = response.json()["data"]
        plan = mozilla_plan(records, source, target)
        if plan is None:
            return None
        with requests.get(MOZILLA_SERVER, timeout=(5, 20)) as response:
            response.raise_for_status()
            base = response.json()["capabilities"]["attachments"]["base_url"].rstrip("/") + "/"
        version, files = plan
        total = sum(r["attachment"]["size"] for r in files.values())
        if total > MAX_DOWNLOAD:
            raise ProviderError("size")
        cache_root().mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache_root(), prefix=".download-") as temp:
            tmp = Path(temp)
            stage = tmp / "stage"
            stage.mkdir()
            names, done = {}, 0
            for kind, record in files.items():
                name = record["name"]
                if name != Path(name).name:
                    raise ProviderError("corrupted")
                att = record["attachment"]
                done += self._fetch(base + att["location"], stage / name, att["size"], att["hash"], done, total, pair)
                names[kind] = name
            self.emit("Validating", pair)
            write_model_dir(stage, source, target, names, version, "mozilla-remote-settings")
            validate_files(stage, source, target)
            if runtime_available():
                verify_runtime(stage, source, target)
            self.check(network=True)
            if destination.is_symlink():
                raise ProviderError("incompatible")
            backup = tmp / "previous"
            if destination.exists():
                os.replace(destination, backup)
            try:
                os.replace(stage, destination)
            except OSError:
                if backup.exists():
                    os.replace(backup, destination)
                raise
        return destination

    def download(self, source, target, replace=False):
        pair = pair_name(source, target)
        destination = cache_root() / pair
        if destination.exists() and not replace:
            validate_files(destination, source, target)
            return destination
        self.check(network=True)
        got = self.download_mozilla(source, target, destination, replace)
        return got if got is not None else self.download_catalog(source, target, replace)

    def download_catalog(self, source, target, replace=False):
        import requests
        import yaml

        self.check(network=True)
        pair = pair_name(source, target)
        destination = cache_root() / pair
        if destination.exists() and not replace:
            validate_files(destination, source, target)
            return destination
        self.emit("Downloading", pair, detail="Reading verified model catalog")
        with requests.get(CATALOG, timeout=(5, 20)) as response:
            response.raise_for_status()
            entries = response.json()["models"]
        candidates = [m for m in entries if source in m.get("srcTags", {}) and m.get("trgTag") == target
                      and m.get("repository") == "Bergamot" and m.get("API") == 1]
        if not candidates:
            raise ProviderError("unsupported_pair", detail=f"Для пары {pair} нет проверяемой модели ни в репозитории Mozilla, ни в каталоге translatelocally")
        meta = sorted(candidates, key=lambda m: (m.get("type") != "tiny", -int(m.get("version", 0))))[0]
        url = urlsplit(meta["url"])
        if url.scheme != "https" or url.hostname not in {"data.statmt.org", "translatelocally.com"} or not re.fullmatch(r"[a-f0-9]{64}", meta["checksum"]):
            raise ProviderError("incompatible")
        cache_root().mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache_root(), prefix=".download-") as temp:
            tmp = Path(temp)
            archive, stage = tmp / "model.tar.gz", tmp / "stage"
            self.check(network=True)
            with requests.get(meta["url"], stream=True, timeout=(5, 15)) as response:
                response.raise_for_status()
                total = int(response.headers.get("Content-Length", 0))
                if total > MAX_DOWNLOAD:
                    raise ProviderError("size")
                received, last = 0, 0.0
                with archive.open("wb") as stream:
                    for chunk in response.iter_content(256 * 1024):
                        self.check(network=True)
                        stream.write(chunk)
                        received += len(chunk)
                        if received > MAX_DOWNLOAD:
                            raise ProviderError("size")
                        if time.monotonic() - last >= .2:
                            self.emit("Downloading", pair, received / total if total else 0,
                                      f"{received // 1048576} / {total // 1048576 if total else '?'} MiB")
                            last = time.monotonic()
                if total and received != total:
                    raise ProviderError("corrupted")
            self.check(network=True)
            self.emit("Validating", pair)
            if digest(archive) != meta["checksum"]:
                raise ProviderError("corrupted")
            stage.mkdir()
            with tarfile.open(archive) as tar:
                members = tar.getmembers()
                if sum(m.size for m in members) > 4 * MAX_DOWNLOAD:
                    raise ProviderError("size")
                for m in members:
                    self.check()
                    if not (m.isfile() or m.isdir()) or not (stage / m.name).resolve().is_relative_to(stage.resolve()):
                        raise ProviderError("corrupted")
                    tar.extract(m, stage, filter="data")
            configs = list(stage.rglob("config*.yml"))
            if not configs:
                raise ProviderError("incompatible")
            config = next((p for p in configs if "intgemm" in p.name), configs[0])
            cfg = yaml.safe_load(config.read_text())
            cfg.update({"ssplit-prefix-file": "", "ssplit-mode": "paragraph", "max-length-break": 128,
                        "mini-batch-words": 512, "workspace": 128, "alignment": "soft"})
            config.write_text(yaml.safe_dump(cfg))
            manifest = {"source": source, "target": target, "runtime": "bergamot-native-v1",
                        "version": meta["version"], "origin": meta["url"], "archive_sha256": meta["checksum"],
                        "config": str(config.relative_to(stage)), "files": {}}
            for path in stage.rglob("*"):
                if path.is_file():
                    manifest["files"][str(path.relative_to(stage))] = {"size": path.stat().st_size, "sha256": digest(path)}
            (stage / "kizurium-model.json").write_text(json.dumps(manifest, indent=2))
            validate_files(stage, source, target)
            if runtime_available():
                verify_runtime(stage, source, target)
            self.check(network=True)
            backup = tmp / "previous"
            if destination.is_symlink():
                raise ProviderError("incompatible")
            if destination.exists():
                os.replace(destination, backup)
            try:
                os.replace(stage, destination)
            except OSError:
                if backup.exists():
                    os.replace(backup, destination)
                raise
        return destination

    def cancel(self):
        self.cancelled.set()

    def shutdown(self):
        self.cancel()
        self.pool.shutdown(wait=False, cancel_futures=True)
