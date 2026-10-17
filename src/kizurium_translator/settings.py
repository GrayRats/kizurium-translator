"""Compact native settings window; all model/network work goes through owned workers."""
from __future__ import annotations

import json
from dataclasses import replace

from .translation.providers import PROVIDERS

DESCRIPTIONS = {
    "google": "Google Translate: онлайн. Неофициальный GTX endpoint без гарантированной квоты. Запросы идут последовательно.",
    "yandex": "Официальный Yandex Cloud Translate v2. API key или IAM token; Folder ID при необходимости.",
    "deepl": "Официальный DeepL API. Выберите Free или Pro согласно подписке.",
    "azure": "Официальный Azure AI Translator v3. Subscription key и регион ресурса.",
    "custom": "HTTP JSON API: {text}, {source}, {target} подставляются в значения JSON. Response path: $.results[0].text или /results/0/text.",
    "bergamot": "Bergamot: локальный перевод без сети. Нужны native runtime и совместимая модель для выбранной пары.",
}

_STYLE_INSTALLED = False


class SettingsWindow:
    def __init__(self, controller):
        from .ui import _gtk

        Gtk, Gdk, _ = _gtk()
        self.Gtk, self.controller = Gtk, controller
        self.fields = {}
        self.provider_fields = {}
        self.provider_drafts = {}
        self._shown_provider = None
        cfg = controller.conf
        self.win = Gtk.ApplicationWindow(application=controller.app, title="Kizurium — Настройки")
        self.win.add_css_class("kizurium-settings")
        self.win.set_default_size(560, 520)
        global _STYLE_INSTALLED
        if not _STYLE_INSTALLED:
            css = Gtk.CssProvider()
            css.load_from_data(b"""
                window.kizurium-settings .settings-page { padding: 4px; }
                window.kizurium-settings .settings-row {
                    padding: 8px 10px;
                    border: 1px solid @borders;
                    border-radius: 8px;
                }
            """)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            _STYLE_INSTALLED = True
        from . import window_state

        window_state.restore(self.win, "settings")
        header = Gtk.HeaderBar()
        title = Gtk.Label(label="Настройки")
        title.add_css_class("title")
        header.set_title_widget(title)
        apply = Gtk.Button(label="Применить")
        apply.add_css_class("suggested-action")
        apply.set_tooltip_text("Сохранить и применить настройки")
        apply.connect("clicked", self.apply)
        header.pack_end(apply)
        self.win.set_titlebar(header)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for edge in ("top", "bottom", "start", "end"):
            getattr(root, "set_margin_" + edge)(12)
        self.win.set_child(root)
        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        stack.set_transition_duration(120)
        stack.set_vexpand(True)
        switcher = Gtk.StackSwitcher(stack=stack)
        switcher.set_halign(Gtk.Align.CENTER)
        root.append(switcher)
        root.append(stack)
        self.note = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.note.add_css_class("dim-label")
        root.append(self.note)
        self._page_index = 0

        def page(name):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.add_css_class("settings-page")
            for edge in ("top", "bottom", "start", "end"):
                getattr(box, "set_margin_" + edge)(8)
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_child(box)
            self._page_index += 1
            stack.add_titled(scroll, f"settings-{self._page_index}", name)
            return box

        general = page("Общие")
        self.offline = Gtk.CheckButton(label="Автономный режим — запрет сетевых переводов и загрузок")
        self.offline.set_active(cfg.offline_only)
        self.offline.connect("toggled", lambda b: controller.set_offline(b.get_active()))
        general.append(self.offline)
        self.auto = Gtk.CheckButton(label="Автоматический OCR и перевод")
        self.auto.set_active(controller.live is not None)
        self._auto_handler = self.auto.connect("toggled", lambda b: controller.dispatch("start-auto-translate" if b.get_active() else "stop-auto-translate"))
        general.append(self.auto)
        general.append(Gtk.Label(label="Закрытие окна скрывает приложение в системный трей. Выход завершает все операции.", wrap=True, xalign=0))

        ocr = page("OCR")
        self.engine_codes = ["auto", "tesseract", "rapid", "paddle", "meiki"]
        current = cfg.ocr_engines[0] if len(cfg.ocr_engines) == 1 else "auto"
        self.engine = self.dropdown(ocr, "Движок OCR", ["Автоматически (существующий pipeline)", "Tesseract OCR", "RapidOCR / ONNX Runtime", "PaddleOCR 3.x", "MeikiOCR"], self.engine_codes.index(current) if current in self.engine_codes else 0)
        self.entry(ocr, "ocr_language", "Язык распознавания", cfg.ocr_language, "Tesseract codes: eng, rus, jpn, eng+rus; auto = установленные языки. Независим от языка перевода.")
        self.pixel_font = Gtk.CheckButton(label="Tesseract: пиксельный шрифт (увеличение 2× без сглаживания)")
        self.pixel_font.set_active(bool(cfg.ocr_options.get("tesseract_pixel_font", False)))
        self.pixel_font.set_tooltip_text("Для текста игр: сохраняет контуры bitmap font и инвертирует светлый текст на тёмном фоне. Для английской игры задайте язык OCR eng.")
        ocr.append(self.pixel_font)
        self.entry(ocr, "tesseract_psm", "Tesseract: разметка текста", cfg.ocr_options.get("tesseract_psm", "auto"), "auto; 6 — несколько строк диалога; 7 — одна строка; 11 — разрозненный HUD; 12 или 13 — специальные режимы")
        for key, label in (("paddle_det", "Paddle: каталог модели detection"), ("paddle_rec", "Paddle: каталог модели recognition")):
            self.entry(ocr, key, label, cfg.ocr_options.get(key, ""), "Локальная модель; автоматическая загрузка OCR отключена")
        self.inventory = Gtk.Label(label="Проверка движков…", wrap=True, xalign=0, selectable=True)
        ocr.append(self.inventory)
        self.button(ocr, "Проверить установленные движки и языки", self.scan_ocr)
        self.scan_ocr()

        translation = page("Перевод")
        self.provider_codes = list(PROVIDERS)
        self.provider = self.dropdown(translation, "Сервис перевода", list(PROVIDERS.values()), self.provider_codes.index(cfg.translation_provider))
        self.description = Gtk.Label(wrap=True, xalign=0)
        translation.append(self.description)
        self.entry(translation, "source_lang", "Исходный язык", cfg.source_lang, "ISO language code, например en; auto = определение")
        self.entry(translation, "target_lang", "Язык перевода", cfg.target_lang, "ISO language code, например ru")
        self.entry(translation, "request_interval", "Минимальный интервал запросов, с", str(cfg.request_interval), "Google: начальное значение 1.8 с; это не гарантированная квота сервиса")
        self.entry(translation, "ocr_debounce", "Задержка стабилизации OCR, с", str(cfg.ocr_debounce), "OCR debounce: отмена устаревших запросов до отправки")
        self.provider_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        translation.append(self.provider_box)
        self.provider.connect("notify::selected", self.provider_changed)
        self.provider_changed()
        self.button(translation, "Проверить соединение (перевод Test)", self.test_connection)

        models = page("Модели")
        self.entry(models, "bergamot_model_path", "Путь к модели Bergamot", cfg.bergamot_model_path, "Каталог с kizurium-model.json, конфигурацией и файлами модели; выше приоритетом, чем кэш")
        self.button(models, "Выбрать каталог модели…", self.choose_model)
        models.append(Gtk.Label(label="Пара языков берётся из вкладки «Перевод». Для локальной модели укажите исходный язык явно.", wrap=True, xalign=0))
        self._runtime_busy = False
        self._runtime_found = False
        self.runtime_label = Gtk.Label(label="Проверяю движок Bergamot…", wrap=True, xalign=0, selectable=True)
        models.append(self.runtime_label)
        self.runtime_button = self.button(models, "Установить движок Bergamot", self.install_runtime)
        self.runtime_button.set_tooltip_text("Установит отдельное окружение Python 3.10; нужны uv и сеть")
        self.runtime_refresh = self.button(models, "Проверить установленный движок", self.scan_runtime)
        self.model_status = Gtk.Label(label=controller.models.status, wrap=True, xalign=0, selectable=True)
        models.append(self.model_status)
        self.progress = Gtk.ProgressBar(show_text=True)
        models.append(self.progress)
        self.download_buttons = []
        for action, label in (("verify", "Найти / проверить модель"), ("download", "Загрузить модель"), ("redownload", "Загрузить заново"), ("remove", "Удалить модель из кэша Kizurium")):
            button = self.button(models, label, lambda _b, a=action: self.model_action(a))
            if action in {"download", "redownload"}:
                self.download_buttons.append(button)
        self.button(models, "Отменить операцию", lambda _b: controller.models.cancel())
        self.installed = Gtk.Label(wrap=True, selectable=True, xalign=0)
        models.append(self.installed)
        self.button(models, "Обновить список моделей", self.list_models)
        self.list_models()
        controller.settings_listeners.append(self.sync_runtime_actions)
        self.scan_runtime()

        appearance = page("Вид")
        appearance.append(Gtk.Label(label="Существующий оверлей сохраняет геометрию и автоматически подбирает типографику под текст на экране.", wrap=True, xalign=0))
        for key, label in (("card_bg", "Фон RGBA (0…1)"), ("card_fg", "Текст RGBA (0…1)")):
            self.entry(appearance, key, label, json.dumps(getattr(cfg, key)), "Четыре числа: [R, G, B, A]")
        self.entry(appearance, "card_radius", "Радиус карточки", str(cfg.card_radius), "Применяется без перезапуска")
        self.outline = Gtk.CheckButton(label="Обводка текста")
        self.outline.set_active(cfg.card_outline)
        appearance.append(self.outline)

        advanced = page("Ещё")
        self.button(advanced, "Очистить кэш переводов", lambda _b: controller.clear_cache())
        self.button(advanced, "Диагностика", self.diagnostics)
        self.diagnostic_text = Gtk.Label(wrap=True, selectable=True, xalign=0)
        advanced.append(self.diagnostic_text)
        self.win.connect("close-request", self.close)
        controller.app.connect(
            "shutdown", lambda *_: window_state.remember(self.win, "settings")
        )

    def set_auto_active(self, active):
        self.auto.handler_block(self._auto_handler)
        try:
            self.auto.set_active(active)
        finally:
            self.auto.handler_unblock(self._auto_handler)

    def close(self, *_):
        from . import window_state

        window_state.remember(self.win, "settings")
        self.win.set_visible(False)
        return True

    def button(self, box, label, callback):
        button = self.Gtk.Button(label=label)
        button.connect("clicked", callback)
        box.append(button)
        return button

    def entry(self, box, key, label, value, tooltip="", password=False, fields=None):
        row = self.Gtk.Box(orientation=self.Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("settings-row")
        caption = self.Gtk.Label(label=label, xalign=0, yalign=0.5, wrap=True)
        caption.set_size_request(180, -1)
        caption.set_hexpand(False)
        row.append(caption)
        numeric = {
            "request_interval": (3600.0, 0.1, 2),
            "ocr_debounce": (60.0, 0.05, 2),
            "card_radius": (128.0, 0.1, 2),
        }.get(key)
        if numeric:
            upper, step, digits = numeric
            current = float(value)
            upper = max(upper, current)
            adjustment = self.Gtk.Adjustment.new(current, 0.0, upper, step, step * 10, 0.0)
            widget = self.Gtk.SpinButton(adjustment=adjustment, climb_rate=step, digits=digits)
            widget.set_numeric(True)
            widget.set_hexpand(True)
        else:
            widget = self.Gtk.Entry()
            widget.set_text(str(value))
            widget.set_visibility(not password)
        widget.set_tooltip_text(tooltip)
        widget.set_hexpand(True)
        row.append(widget)
        box.append(row)
        (self.fields if fields is None else fields)[key] = widget
        return widget

    def dropdown(self, box, label, values, selected):
        row = self.Gtk.Box(orientation=self.Gtk.Orientation.HORIZONTAL, spacing=12)
        row.add_css_class("settings-row")
        caption = self.Gtk.Label(label=label, xalign=0, yalign=0.5, wrap=True)
        caption.set_size_request(180, -1)
        row.append(caption)
        dropdown = self.Gtk.DropDown.new_from_strings(values)
        dropdown.set_selected(selected)
        dropdown.set_hexpand(True)
        row.append(dropdown)
        box.append(row)
        return dropdown

    def provider_changed(self, *_):
        if self._shown_provider is not None:
            self.provider_drafts[self._shown_provider] = {
                key: field.get_text() for key, field in self.provider_fields.items()
            }
        pid = self.provider_codes[self.provider.get_selected()]
        self._shown_provider = pid
        self.description.set_label(DESCRIPTIONS[pid])
        child = self.provider_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.provider_box.remove(child)
            child = nxt
        self.provider_fields = {}
        options = self.provider_drafts.get(pid, self.controller.conf.provider_options.get(pid, {}))
        schema = {
            "google": [("timeout", "HTTP timeout, с", "15")],
            "yandex": [("auth", "Авторизация: api-key / iam", "api-key"), ("folder_id", "Folder ID", "")],
            "deepl": [("plan", "Подписка: free / pro", "free")],
            "azure": [("region", "Регион ресурса", ""), ("endpoint", "Endpoint", "https://api.cognitive.microsofttranslator.com")],
            "custom": [("endpoint", "Endpoint URL", ""), ("method", "HTTP method", "POST"), ("auth", "Авторизация: none / bearer / api-key", "none"), ("key_header", "Заголовок API key", "X-API-Key"), ("timeout", "Timeout, с", "15"), ("headers", "HTTP headers (JSON)", {}), ("body", "JSON body / GET query", {"q": "{text}", "source": "{source}", "target": "{target}"}), ("response_path", "Response JSON path", "translatedText")],
        }
        for key, label, default in schema.get(pid, []):
            value = options.get(key, default)
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            self.entry(self.provider_box, key, label, value, fields=self.provider_fields, password=key == "headers")
        if pid not in {"google", "bergamot"}:
            self.entry(self.provider_box, "key", "Новый API key / token (пусто — оставить сохранённый)", options.get("key", ""), "Хранится в KWallet / Secret Service. Альтернатива: KIZURIUM_<PROVIDER>_API_KEY", password=True, fields=self.provider_fields)

    def config(self):
        cfg = self.controller.conf
        changes = {key: self.fields[key].get_text().strip() for key in ("source_lang", "target_lang", "ocr_language", "bergamot_model_path")}
        for key in ("request_interval", "ocr_debounce", "card_radius"):
            changes[key] = self.fields[key].get_value()
        for key in ("card_bg", "card_fg"):
            changes[key] = tuple(json.loads(self.fields[key].get_text()))
        changes["card_outline"] = self.outline.get_active()
        engine = self.engine_codes[self.engine.get_selected()]
        changes["ocr_engines"] = ("rapid", "meiki", "tesseract") if engine == "auto" else (engine,)
        changes["ocr_options"] = {**cfg.ocr_options,
                                  **{key: self.fields[key].get_text() for key in ("paddle_det", "paddle_rec", "tesseract_psm")},
                                  "tesseract_pixel_font": self.pixel_font.get_active()}
        pid = self.provider_codes[self.provider.get_selected()]
        options = {key: entry.get_text() for key, entry in self.provider_fields.items() if key != "key"}
        for key in ("headers", "body"):
            if key in options:
                options[key] = json.loads(options[key])
        changes["provider_options"] = {**cfg.provider_options, pid: options}
        changes["translation_provider"] = pid
        return replace(cfg, **changes)

    def apply(self, *_):
        try:
            cfg = self.config()
            key = self.provider_fields.get("key")
            secret = key.get_text() if key else ""
            if secret:
                from .credentials import set_key
                self.controller.background(lambda: set_key(cfg.translation_provider, secret), lambda _: self.applied(cfg))
            else:
                self.applied(cfg)
        except Exception as exc:
            self.note.set_label("Настройки не применены: " + type(exc).__name__)

    def applied(self, cfg):
        self.controller.apply_config(cfg)
        self.note.set_label("Настройки применены")
        if "key" in self.provider_fields:
            self.provider_fields["key"].set_text("")

    def scan_ocr(self, *_):
        from .ocr.selection import inventory
        self.controller.background(lambda: inventory(self.controller.conf.ocr_options), lambda data: self.inventory.set_label("\n".join(f"{k}: {v['detail']}" for k, v in data.items())))

    def list_models(self, *_):
        from .translation.bergamot import cache_root
        def listing():
            rows = []
            for path in sorted(cache_root().glob("*/kizurium-model.json")):
                try:
                    meta = json.loads(path.read_text())
                    size = sum(f["size"] for f in meta["files"].values()) / 1048576
                    rows.append(f"{meta['source']} → {meta['target']}: v{meta.get('version', '?')}, {size:.1f} MiB (проверить перед использованием)")
                except Exception:
                    rows.append(path.parent.name + ": Corrupted")
            return "\n".join(rows) or "Модели в кэше не найдены"
        self.controller.background(listing, self.installed.set_label)

    def model_action(self, action):
        try:
            cfg = self.config()
            self.controller.models.submit(action, cfg.source_lang, cfg.target_lang, cfg.bergamot_model_path)
        except Exception as exc:
            self.note.set_label("Операция не начата: " + str(exc))

    def sync_runtime_actions(self):
        online = not self.controller.conf.offline_only
        self.runtime_button.set_sensitive(online and not self._runtime_busy and not self._runtime_found)
        self.runtime_refresh.set_sensitive(not self._runtime_busy)
        for button in self.download_buttons:
            button.set_sensitive(online and not self._runtime_busy)

    def scan_runtime(self, *_):
        if self._runtime_busy:
            return
        from .translation.bergamot import refresh_runtime, helper_python, system_python, RUNTIME_MISSING
        self._runtime_busy = True
        self.runtime_label.set_label("Проверяю движок Bergamot…")
        self.sync_runtime_actions()

        def work():
            kind = refresh_runtime()
            path = helper_python() if kind == "helper" else system_python() if kind == "system" else "Python приложения"
            return kind, str(path)

        def done(result):
            kind, path = result
            self._runtime_busy = False
            self._runtime_found = bool(kind)
            self.runtime_label.set_label(f"Движок Bergamot: {path}" if kind else RUNTIME_MISSING)
            self.sync_runtime_actions()
        self.controller.background(work, done)

    def install_runtime(self, *_):
        if self._runtime_busy or self.controller.conf.offline_only:
            return
        from .translation.bergamot import install_runtime
        self._runtime_busy = True
        self.sync_runtime_actions()
        self.runtime_label.set_label("Устанавливаю движок Bergamot…")

        def work():
            try:
                install_runtime()
                return ""
            except Exception as exc:
                return str(exc)

        def done(error):
            self._runtime_busy = False
            if error:
                self.note.set_label("Не удалось установить Bergamot: " + error)
            self.scan_runtime()
        self.controller.background(work, done)

    def choose_model(self, *_):
        Gtk = self.Gtk
        dialog = Gtk.FileChooserNative.new("Модель Bergamot", self.win, Gtk.FileChooserAction.SELECT_FOLDER, "Выбрать", "Отмена")
        def response(chooser, result):
            if result == Gtk.ResponseType.ACCEPT:
                self.fields["bergamot_model_path"].set_text(chooser.get_file().get_path())
            chooser.destroy()
        dialog.connect("response", response)
        dialog.show()

    def test_connection(self, *_):
        try:
            cfg = self.config()
            if cfg.offline_only and cfg.translation_provider != "bergamot":
                self.note.set_label("Автономный режим: сетевые запросы запрещены")
                return
            self.controller.test_connection(cfg, self.note.set_label)
        except Exception as exc:
            self.note.set_label(type(exc).__name__)

    def diagnostics(self, *_):
        from .paths import default_paths
        from .translation.bergamot import runtime_available
        cfg = self.controller.conf
        self.diagnostic_text.set_label(f"Provider: {cfg.translation_provider}\nOffline: {cfg.offline_only}\nBergamot runtime: {runtime_available()}\nLog: {default_paths().log}\n" + self.controller.translator.status)
