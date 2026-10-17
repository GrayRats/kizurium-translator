"""One GApplication owns windows, live OCR, tray and desktop actions (session D-Bus)."""
from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from . import APP_ID
from .config import Config, config_path, save
from .translation.bergamot import ModelManager

ACTIONS = ("settings", "select-region", "show-window", "capture-window", "toggle-offline", "start-auto-translate", "stop-auto-translate", "quit")
log = logging.getLogger(__name__)


class DesktopController:
    def __init__(self, app, conf, path=None):
        from gi.repository import Gio, GLib

        from . import live
        from .tray import Tray

        self.app, self.conf, self.path = app, conf, path or config_path()
        self.GLib, self.Gio = GLib, Gio
        self.live = None
        self.stopping = None
        self.selecting = False
        self.closing = False
        self.geometry = ""
        self.settings = None
        self.text_window = None
        self.status_listeners = []
        self.settings_listeners = []
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="desktop")
        self.test_translators = []
        self.selection_cancel = Gio.Cancellable()
        self.pending_start = False
        self._starting = False
        self._selection_revision = 0
        self.models = ModelManager(lambda: self.conf.offline_only, self.model_changed)
        live.configure(conf)
        self.translator = live.runtime.translator()
        self.translator.status_callback = self.message
        for name in ACTIONS:
            action = Gio.SimpleAction.new_stateful(name, None, GLib.Variant("b", conf.offline_only)) if name == "toggle-offline" else Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, n=name: self.dispatch(n))
            app.add_action(action)
        self.tray = Tray(app.get_dbus_connection(), self)
        self.message("Kizurium")

    def background(self, operation, done=lambda _: None):
        if self.closing:
            return None
        future = self.pool.submit(operation)
        def completed(fut):
            try:
                result = fut.result()
            except Exception as exc:
                note = str(exc) if type(exc) is RuntimeError and str(exc).startswith("KWallet") else type(exc).__name__
                self.message("Operation failed: " + note)
                log.warning("operation=desktop error=%s", type(exc).__name__)
                return
            def publish():
                if not self.closing:
                    done(result)
                return False
            self.GLib.idle_add(publish)
        future.add_done_callback(completed)
        return future

    def message(self, text):
        def publish():
            if self.closing:
                return False
            self.tray.update(text)
            for callback in self.status_listeners:
                callback(text)
            if self.settings:
                self.settings.note.set_label(text)
            return False
        self.GLib.idle_add(publish)

    def model_changed(self, status, pair, progress, detail):
        def publish():
            if self.closing:
                return False
            text = f"Kizurium: {status} {pair} — {progress:.0%}" if status == "Downloading" else f"{pair}: {status} — {detail}"
            self.message(text)
            if self.settings:
                self.settings.model_status.set_label(text)
                self.settings.progress.set_fraction(min(1, max(0, progress)))
                self.settings.progress.set_text(f"{progress:.0%}" if progress else status)
            return False
        self.GLib.idle_add(publish)

    def apply_config(self, cfg, persist=True):
        from .cli import make_translator
        from .core import active, scripts
        from .live import runtime
        from .translation.scheduler import SCHEDULER

        if persist:
            save(cfg, self.path)
        old = self.translator
        old.cancel()
        SCHEDULER.bump()
        self.conf = cfg
        if cfg.offline_only:
            self.models.cancel()
            for test in self.test_translators:
                test.cancel()
        self.translator = make_translator(cfg)
        self.translator.status_callback = self.message
        runtime.TRANSLATION.translator = self.translator
        runtime.TRANSLATION.target_lang = cfg.target_lang
        runtime.TRANSLATION.source_lang = cfg.source_lang
        runtime.TRANSLATION.offline_only = cfg.offline_only
        runtime.SETTINGS.target_lang = cfg.target_lang
        runtime.SETTINGS.ocr_engines = cfg.ocr_engines
        scripts.set_target_language(cfg.target_lang)
        active.apply_frame(cfg)
        self.background(old.shutdown)
        action = self.app.lookup_action("toggle-offline")
        action.set_state(self.GLib.Variant("b", cfg.offline_only))
        if self.settings and self.settings.offline.get_active() != cfg.offline_only:
            self.settings.offline.set_active(cfg.offline_only)
        for callback in self.settings_listeners:
            callback()
        self.message("Offline mode" if cfg.offline_only else "Settings applied")

    def set_offline(self, enabled):
        if enabled != self.conf.offline_only:
            self.apply_config(replace(self.conf, offline_only=enabled))

    def dispatch(self, action, initial="", geometry=""):
        if self.closing:
            return
        try:
            if action == "quit":
                self.quit()
            elif action == "settings":
                if self.settings is None:
                    from .settings import SettingsWindow
                    self.settings = SettingsWindow(self)
                self.settings.win.present()
            elif action == "select-region":
                self.open_selector()
            elif action == "show-window":
                if self.text_window is None:
                    from .ui import text_window
                    self.text_window = text_window(translator=self.translator, initial=initial, application=self.app, controller=self)
                else:
                    self.text_window.present()
            elif action == "toggle-offline":
                self.set_offline(not self.conf.offline_only)
            elif action == "capture-window":
                self.select_window(start=False)
            elif action == "start-auto-translate":
                if geometry:
                    self.geometry = geometry
                if self.live is None and not self.selecting:
                    if self.geometry:
                        self.start_live()
                    else:
                        self.select_window(start=True)
            elif action == "stop-auto-translate":
                self.pending_start = False
                self.selection_cancel.cancel()
                self.stop_live()
            elif action == "toggle":
                self.dispatch("stop-auto-translate" if self.live else "start-auto-translate")
        except Exception as exc:
            self.message("Action failed: " + type(exc).__name__)
            log.exception("operation=desktop-action action=%s", action)
        finally:
            self.sync_auto()

    def activate(self):
        """An incidental app activation must not interrupt an active region session."""
        if (self.closing or self.live is not None or self.pending_start
                or self._starting or self.selecting):
            return
        self.dispatch("show-window")

    def open_selector(self):
        """The normal region selector, answered with the action chosen on its toolbar."""
        if self.selecting or self.closing:
            return
        self.selecting = True
        revision = self._selection_revision
        from .selector import SelectorError, select_region

        def pick():
            try:
                return select_region()
            except SelectorError as exc:
                return "error", str(exc)

        def done(result):
            self.selecting = False
            if revision != self._selection_revision:
                return
            action, geom = result
            if action == "error":
                self.message("Выбор области недоступен: " + geom)
            elif action == "settings":
                self.dispatch("settings")
            elif action == "text":
                self.dispatch("show-window")
            elif action == "live" and geom:
                self.dispatch("start-auto-translate", geometry=geom)
            elif action == "ocr" and geom:
                self.copy_text(geom)

        self.background(pick, done)

    def copy_text(self, geom):
        """OCR a region and put the text on the clipboard (the toolbar's copy button)."""
        from . import cli

        scoped = cli.build_parser().parse_args([])
        scoped.region, scoped.geom = geom, None
        conf = self.conf
        self.message("Распознаю текст…")

        def work():
            return cli.cmd_ocr_copy(scoped, conf)

        self.background(work, lambda code: self.message(
            "Текст скопирован" if code == 0 else "Не удалось распознать или скопировать текст"))

    def select_window(self, start=False):
        if self.selecting:
            return
        self.selecting, self.pending_start = True, start
        self.selection_cancel = self.Gio.Cancellable()
        if "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", ""):
            self.message("Выберите окно для захвата (Esc — отмена)")
            bus = self.app.get_dbus_connection()
            def selected(connection, result):
                self.selecting = False
                try:
                    info = connection.call_finish(result).unpack()[0]
                    x, y, w, h = (int(info[k]) for k in ("x", "y", "width", "height"))
                    if w <= 0 or h <= 0:
                        raise ValueError
                    self.geometry = f"{x},{y} {w}x{h}"
                    self.message("Capture window: " + self.geometry)
                    if self.pending_start:
                        self.pending_start = False
                        self.start_live()
                except Exception as exc:
                    self.pending_start = False
                    self.message("Window selection cancelled or unavailable: " + type(exc).__name__)
                finally:
                    self.sync_auto()
            bus.call("org.kde.KWin", "/KWin", "org.kde.KWin", "queryWindowInfo", None,
                     self.GLib.VariantType.new("(a{sv})"), self.Gio.DBusCallFlags.NONE, 60000, self.selection_cancel, selected)
        else:
            # Reuse the shipped QML selector on supported non-KWin compositors.
            from .selector import select_region
            def selected(result):
                self.selecting = False
                _, self.geometry = result
                start = self.pending_start and self.geometry
                self.pending_start = False
                if start:
                    self.start_live()
                self.sync_auto()
            self.background(lambda: select_region(auto_action="live"), selected)

    def start_live(self):
        if self.live or self.closing or self._starting:
            return
        if self.stopping and self.stopping.is_alive():
            self.message("Waiting for previous OCR worker to stop")
            self.pending_start = True
            self.GLib.timeout_add(100, self.retry_start)
            return
        from . import capture
        if "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "") and not capture.portal_ready():
            # Portal consent can be requested without a parent window. Starting
            # a region must never create/present the text translator as a side effect.
            self._starting = self.pending_start = True
            capture.set_portal_parent("")

            def prepared(result):
                self._starting = False
                if not self.pending_start or self.closing:
                    return
                self.pending_start = False
                self.sync_auto()
                ok, error = result
                if not ok:
                    self.message("Захват недоступен: " + error)
                    return
                self._start_live_now()

            def warmup():
                try:
                    capture.kde_capture(self.geometry)
                    return True, ""
                except capture.CaptureError as exc:
                    return False, str(exc)

            self.background(warmup, prepared)
            return
        self._start_live_now()

    def _start_live_now(self):
        from .live.app import main
        self.live = main(["--geom", self.geometry], application=self.app)
        self.sync_auto()
        self.message("Automatic translation started")

    def retry_start(self):
        if not self.pending_start or self.closing:
            return False
        if self.stopping and self.stopping.is_alive():
            return True
        self.pending_start = False
        self.start_live()
        return False

    def stop_live(self):
        from .capture import cancel_capture_request
        self.pending_start = False
        self._selection_revision += 1
        cancel_capture_request()
        if self.live:
            self.live["close"]()
            self.stopping = self.live["thread"]
            self.live = None
        self.translator.cancel_pending()
        self.sync_auto()
        self.message("Automatic translation stopped")

    def sync_auto(self):
        if self.settings:
            active = self.live is not None or self.pending_start
            if self.settings.auto.get_active() != active:
                self.settings.set_auto_active(active)

    def test_connection(self, cfg, done):
        from .cli import make_translator
        tr = make_translator(cfg)
        self.test_translators.append(tr)
        def run():
            try:
                result = tr.via_provider("Test", "en" if cfg.source_lang == "auto" else cfg.source_lang)
                return "Connection OK: " + result if result else tr.status
            finally:
                tr.shutdown()
        def complete(note):
            self.test_translators.remove(tr)
            done(note)
        self.background(run, complete)

    def clear_cache(self):
        tr = self.translator
        def clear():
            tr.cancel_pending()
            cache = tr._cache
            with cache._lock:
                cache.load()
                for key in list(cache._data):
                    cache.forget(key)
                cache.flush(force=True)
            from .translation.scheduler import SCHEDULER
            SCHEDULER.clear_results()
        self.background(clear, lambda _: self.message("Translation cache cleared"))

    def quit(self):
        if self.closing:
            return
        self.stop_live()
        self.closing = True
        self.selection_cancel.cancel()
        self.models.shutdown()
        for tr in self.test_translators:
            tr.shutdown()
        self.translator.shutdown()
        from .live.runtime import shutdown
        shutdown()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.tray.shutdown()
        self.app.quit()


def run(action, conf: Config, *, initial="", geometry="", path=None):
    from .ui import _gtk
    Gtk, _, GLib = _gtk()
    from gi.repository import Gio

    app = Gtk.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
    holder = []
    def startup(application):
        application.hold()
        holder.append(DesktopController(application, conf, path))
    def command_line(_app, command):
        args = command.get_arguments()
        payload = json.loads(args[1]) if len(args) > 1 else {"action": "show-window"}
        holder[0].dispatch(payload["action"], payload.get("initial", ""), payload.get("geometry", ""))
        return 0
    app.connect("startup", startup)
    app.connect("command-line", command_line)
    app.connect("activate", lambda _: holder[0].activate())
    app.connect("shutdown", lambda _: holder and holder[0].quit())
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 15, lambda: (holder[0].quit(), False)[1] if holder else False)
    return app.run(["kizurium-translator", json.dumps({"action": action, "initial": initial, "geometry": geometry})])


def forward(action):
    """Forward legacy stop without starting a resident application."""
    from gi.repository import Gio, GLib
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        path = "/" + APP_ID.replace(".", "/")
        bus.call_sync(APP_ID, path, "org.gtk.Actions", "Activate",
                      GLib.Variant("(sava{sv})", (action, [], {})), None,
                      Gio.DBusCallFlags.NO_AUTO_START, 1500, None)
        return True
    except GLib.Error:
        return False
