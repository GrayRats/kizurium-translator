"""Configuration for Kizurium Translator.

Read from ``~/.config/kizurium-translator/config.toml``. The parsed result is
passed explicitly to the engine, so nothing in the library reads the
environment or mutates module globals behind the caller's back.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType

RGBA = tuple[float, float, float, float]


class ConfigError(ValueError):
    """Конфигурация есть, но её нельзя применить.

    Отдельный тип нужен, чтобы отличить «файла нет» - это норма, дефолты - от
    «файл есть и в нём опечатка». Раньше обе ситуации возвращали дефолты, и
    человек с неверным порогом detection получал рабочую программу на
    дефолтных числах и не понимал, почему она ведёт себя не так, как задано.
    """


@dataclass(frozen=True)
class Config:
    """Every field here is actually read by the engine."""

    # translation
    translation_provider: str = "google"
    provider_options: Mapping = field(default_factory=dict, repr=False)
    request_interval: float = 1.8
    ocr_debounce: float = 0.38
    bergamot_model_path: str = ""
    ocr_language: str = "auto"
    ocr_options: Mapping = field(default_factory=dict)
    interface_language: str = "ru"
    target_lang: str = "ru"
    source_lang: str = "auto"
    use_gtx: bool = True
    allow_slow_translation: bool = False
    # never instantiate online backends; local pack or miss.
    offline_only: bool = False
    max_translate_chars: int = 4300
    gtx_cooldown_s: float = 25.0
    cache_max_entries: int = 4000
    # `frozen=True` не замораживает `dict` внутри: поле оставалось обычным
    # словарём, и `config.glossary["X"] = "Y"` менял объект, который все слои
    # считали константой. Тип объявляет отображение, а `__post_init__` кладёт
    # под ним настоящий неизменяемый словарь.
    glossary: Mapping[str, str] = field(default_factory=dict)

    # live loop
    interval: float = 0.55
    interval_sub: float = 0.28
    interval_ui_stable: float = 4.5
    hide_for_capture_s: float = 0.10
    hide_for_sub_s: float = 0.10

    # change detection
    change_mean: float = 10.0
    change_mean_videoish: float = 20.0
    change_tile: float = 2.15
    change_center: float = 1.85
    ocr_key_sim: float = 0.88
    # Longest side of the cheap change-detection thumbnail.
    probe_side: int = 320

    # ocr
    ocr_engines: tuple[str,...] = ("rapid", "meiki", "tesseract")
    max_screenshot_side: int = 4672
    # Re-read only the elements the thumbnail diff flagged.
    incremental: bool = True

    # overlay
    card_bg: RGBA = (0.05, 0.07, 0.12, 0.55)
    card_fg: RGBA = (1.0, 1.0, 1.0, 1.0)
    card_radius: float = 7.0
    card_outline: bool = True

    # optional game profile id (DATA under data/profiles/). Empty = none.
    profile_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "glossary", MappingProxyType({str(k): str(v)
                                                for k, v in self.glossary.items()})
        )
        _validate(self)

    def to_dict(self) -> dict:
        return {
            "general": {"interface_language": self.interface_language},
            "translation": {
                "provider": self.translation_provider,
                "request_interval": self.request_interval,
                "ocr_debounce": self.ocr_debounce,
                "bergamot_model_path": self.bergamot_model_path,
                "target": self.target_lang,
                "source": self.source_lang,
                "use_gtx": self.use_gtx,
                "allow_slow_translation": self.allow_slow_translation,
                "offline_only": self.offline_only,
                "max_chars": self.max_translate_chars,
                "gtx_cooldown_s": self.gtx_cooldown_s,
                "cache_max_entries": self.cache_max_entries,
                "glossary": self.glossary,
            },
            "live": {
                "interval": self.interval,
                "interval_sub": self.interval_sub,
                "interval_ui_stable": self.interval_ui_stable,
                "hide_for_capture_s": self.hide_for_capture_s,
                "hide_for_sub_s": self.hide_for_sub_s,
            },
            "detection": {
                "change_mean": self.change_mean,
                "change_mean_videoish": self.change_mean_videoish,
                "change_tile": self.change_tile,
                "change_center": self.change_center,
                "ocr_key_sim": self.ocr_key_sim,
                "probe_side": self.probe_side,
            },
            "ocr": {
                "language": self.ocr_language,
                "options": self.ocr_options,
                "engines": list(self.ocr_engines),
                "max_screenshot_side": self.max_screenshot_side,
                "incremental": self.incremental,
            },
            "overlay": {
                "card_bg": list(self.card_bg),
                "card_fg": list(self.card_fg),
                "card_radius": self.card_radius,
                "card_outline": self.card_outline,
            },
            "profile": {
                "id": self.profile_id,
            },
        }


def _validate(cfg: Config) -> None:
    """Отсекает значения, с которыми движок работает, но не так, как задумано.

    Проверка живёт в конструкторе, а не в `load`: `Config(...)` собирается и
    тестами, и из кода, и файл - только один из способов. Молчаливый дефолт
    здесь опаснее ошибки: неверный порог detection не падает, а перестаёт
    замечать движение.
    """
    import math

    from .translation.providers import PROVIDERS

    if cfg.translation_provider not in PROVIDERS:
        raise ConfigError("Unknown translation provider")
    for name in ("request_interval", "ocr_debounce"):
        v = getattr(cfg, name)
        if not math.isfinite(v) or v < 0:
            raise ConfigError(f"{name}: must be finite and non-negative")
    positive = {
        "interval": cfg.interval,
        "interval_sub": cfg.interval_sub,
        "interval_ui_stable": cfg.interval_ui_stable,
        "gtx_cooldown_s": cfg.gtx_cooldown_s,
        "max_translate_chars": cfg.max_translate_chars,
        "cache_max_entries": cfg.cache_max_entries,
        "probe_side": cfg.probe_side,
        "max_screenshot_side": cfg.max_screenshot_side,
        "card_radius": cfg.card_radius,
    }
    for name, value in positive.items():
        if not value > 0:
            raise ConfigError(f"{name}: должно быть больше нуля, получено {value!r}")

    unit = {
        "ocr_key_sim": cfg.ocr_key_sim,
        "hide_for_capture_s": cfg.hide_for_capture_s,
        "hide_for_sub_s": cfg.hide_for_sub_s,
    }
    for name, value in unit.items():
        if not 0.0 <= value <= 1.0:
            raise ConfigError(f"{name}: ожидалось 0..1, получено {value!r}")

    for name in ("hide_for_capture_s", "hide_for_sub_s"):
        if getattr(cfg, name) > cfg.interval:
            raise ConfigError(
                f"{name} больше интервала кадра: оверлей прячется дольше, чем "
                "кадр живёт, и снимок всегда будет пустым"
            )
    if not cfg.target_lang.strip():
        raise ConfigError("target_lang: пустой язык перевода")
    if not cfg.ocr_engines:
        raise ConfigError("ocr_engines: пустой список движков")
    for name in ("card_bg", "card_fg"):
        rgba = getattr(cfg, name)
        if len(rgba) != 4:
            raise ConfigError(f"{name}: ожидалось четыре канала, получено {len(rgba)}")
        if any(not 0.0 <= v <= 1.0 for v in rgba):
            raise ConfigError(f"{name}: каналы должны быть в 0..1, получено {rgba!r}")


def _num(changes: dict, field_name: str, section: dict, keys: tuple[str, ...],
         cast) -> None:
    """Кладёт в `changes` число из секции, с именем поля в сообщении.

    `int("быстро")` падает само по себе и без указания, что именно сломалось.
    Имя секции и имя поля в сообщении - это разница между «не работает» и
    «понятно, где чинить».
    """
    for key in keys:
        if key not in section:
            continue
        raw = section[key]
        try:
            changes[field_name] = cast(raw)
        except (TypeError, ValueError):
            raise ConfigError(f"{field_name} = {raw!r}: ожидалось число") from None


def _rgba(value: object, fallback: RGBA) -> RGBA:
    if not isinstance(value, (list, tuple)):
        raise ConfigError(f"ожидался список из четырёх чисел, получено {value!r}")
    vals = list(value)[:4]
    while len(vals) < 4:
        vals.append(1.0)
    try:
        return tuple(max(0.0, min(1.0, float(v))) for v in vals)  # type: ignore[return-value]
    except (TypeError, ValueError):
        raise ConfigError(f"каналы должны быть числами, получено {value!r}") from None


def config_path() -> Path:
    env = os.environ.get("KIZURIUM_TRANSLATOR_CONFIG")
    if env:
        return Path(env).expanduser()
    # XDG_CONFIG_HOME, with the specification's ~/.config fallback. Hardcoding
    # ~/.config ignored a user who keeps their configuration elsewhere.
    config_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(config_home) if config_home else (Path.home() / ".config")
    return base / "kizurium-translator" / "config.toml"


def load(path: str | os.PathLike[str] | None = None) -> Config:
    """Load configuration.

    Отсутствующий файл - это норма, берутся дефолты. Нечитаемый - ошибка:
    иначе человек с одной опечаткой в пороге получает работающую программу на
    дефолтных числах и не понимает, почему она ведёт себя не так, как задано.
    """
    base = Config()
    target = Path(path).expanduser() if path else config_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return base
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{target}: не читается как TOML: {exc}") from None

    tr = data.get("translation", {})
    live = data.get("live", {})
    det = data.get("detection", {})
    ocr = data.get("ocr", {})
    ov = data.get("overlay", {})

    changes: dict = {}
    changes["translation_provider"] = str(tr.get("provider", "google"))
    changes["provider_options"] = tr.get("providers", {})
    changes["request_interval"] = float(tr.get("request_interval", 1.8))
    changes["ocr_debounce"] = float(tr.get("ocr_debounce", 0.38))
    changes["bergamot_model_path"] = str(tr.get("bergamot_model_path", ""))
    changes["ocr_language"] = str(ocr.get("language", "auto"))
    changes["ocr_options"] = ocr.get("options", {})
    changes["interface_language"] = str(data.get("general", {}).get("interface_language", "ru"))
    if "target" in tr:
        changes["target_lang"] = str(tr["target"])
    if "source" in tr:
        changes["source_lang"] = str(tr["source"])
    for key in ("use_gtx", "allow_slow_translation", "offline_only"):
        if key in tr:
            changes[key] = bool(tr[key])
    _num(changes, "max_translate_chars", tr, ("max_chars", "max_translate_chars"), int)
    _num(changes, "cache_max_entries", tr, ("cache_max_entries",), int)
    _num(changes, "gtx_cooldown_s", tr, ("gtx_cooldown_s",), float)
    if isinstance(tr.get("glossary"), dict):
        changes["glossary"] = {str(k): str(v) for k, v in tr["glossary"].items()}

    for key in (
        "interval",
        "interval_sub",
        "interval_ui_stable",
        "hide_for_capture_s",
        "hide_for_sub_s",
    ):
        _num(changes, key, live, (key,), float)
    for key in (
        "change_mean",
        "change_mean_videoish",
        "change_tile",
        "change_center",
        "ocr_key_sim",
    ):
        _num(changes, key, det, (key,), float)
    _num(changes, "probe_side", det, ("probe_side",), int)

    if isinstance(ocr.get("engines"), list) and ocr["engines"]:
        changes["ocr_engines"] = tuple(str(v) for v in ocr["engines"])
    _num(changes, "max_screenshot_side", ocr, ("max_screenshot_side",), int)
    if "incremental" in ocr:
        changes["incremental"] = bool(ocr["incremental"])

    for key in ("card_bg", "card_fg"):
        if key in ov:
            try:
                changes[key] = _rgba(ov[key], base.card_bg)
            except ConfigError as exc:
                # Имя поля в сообщении: иначе в файле на две секции непонятно,
                # какая из них не так.
                raise ConfigError(f"{key}: {exc}") from None
    if "card_radius" in ov:
        changes["card_radius"] = float(ov["card_radius"])
    if "card_outline" in ov:
        changes["card_outline"] = bool(ov["card_outline"])

    prof = data.get("profile", {})
    if isinstance(prof, dict) and "id" in prof:
        changes["profile_id"] = str(prof["id"] or "").strip()

    return replace(base, **changes)


def save(cfg: Config, path=None) -> None:
    """Atomic settings write; includes provider options but never credentials."""
    import json
    import tempfile

    def value(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, Mapping):
            return "{ " + ", ".join(f"{json.dumps(str(k))} = {value(x)}" for k, x in v.items()) + " }"
        if isinstance(v, (tuple, list)):
            return "[" + ", ".join(value(x) for x in v) + "]"
        return json.dumps(v, ensure_ascii=False)

    target = Path(path or config_path()).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.to_dict()
    data["translation"]["providers"] = dict(cfg.provider_options)
    content = "\n\n".join(
        f"[{section}]\n" + "\n".join(f"{key} = {value(v)}" for key, v in values.items())
        for section, values in data.items()) + "\n"
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".config-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, target)
    finally:
        Path(tmp).unlink(missing_ok=True)


def provider_options(cfg: Config) -> dict:
    from .credentials import get_key

    options = dict(cfg.provider_options.get(cfg.translation_provider, {}))
    if cfg.translation_provider not in {"google", "bergamot"} and not cfg.offline_only:
        options["key"] = get_key(cfg.translation_provider)
    return options
