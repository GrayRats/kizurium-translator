"""KDE StatusNotifierItem + DBusMenu on the existing GLib session connection."""
from __future__ import annotations

ITEM_XML = '''<node><interface name="org.kde.StatusNotifierItem">
<method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<property name="Category" type="s" access="read"/><property name="Id" type="s" access="read"/>
<property name="Title" type="s" access="read"/><property name="Status" type="s" access="read"/>
<property name="IconName" type="s" access="read"/><property name="ItemIsMenu" type="b" access="read"/>
<property name="Menu" type="o" access="read"/><property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
<signal name="NewToolTip"/><signal name="NewStatus"><arg type="s"/></signal>
</interface></node>'''
MENU_XML = '''<node><interface name="com.canonical.dbusmenu">
<method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
<arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/></method>
<method name="GetGroupProperties"><arg type="ai" direction="in"/><arg type="as" direction="in"/><arg type="a(ia{sv})" direction="out"/></method>
<method name="GetProperty"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
<method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/><arg type="u" direction="in"/></method>
<method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
<property name="Version" type="u" access="read"/><property name="TextDirection" type="s" access="read"/>
<property name="Status" type="s" access="read"/>
<signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
</interface></node>'''
ACTIONS = [("select-region", "Открыть окно"), ("show-window", "Окно переводчика"), ("settings", "Настройки"), ("capture-window", "Выбрать окно"),
           ("toggle-offline", "Автономный режим"), ("start-auto-translate", "Запустить автоперевод"),
           ("stop-auto-translate", "Остановить автоперевод"), ("quit", "Выход")]


class Tray:
    def __init__(self, connection, controller):
        from gi.repository import Gio, GLib

        self.GLib, self.bus, self.controller = GLib, connection, controller
        self.description = "Kizurium"
        self.revision = 1
        self.ids = [connection.register_object("/StatusNotifierItem", Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0], self.item_method, self.item_property, None),
                    connection.register_object("/Menu", Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0], self.menu_method, self.menu_property, None)]
        self.watch = Gio.bus_watch_name_on_connection(connection, "org.kde.StatusNotifierWatcher", Gio.BusNameWatcherFlags.NONE, self.register, None)

    def register(self, *_):
        from gi.repository import Gio

        self.bus.call("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher", "org.kde.StatusNotifierWatcher", "RegisterStatusNotifierItem",
                      self.GLib.Variant("(s)", ("/StatusNotifierItem",)), None, Gio.DBusCallFlags.NONE, 3000, None, self.registered)

    def registered(self, bus, result):
        try:
            bus.call_finish(result)
        except self.GLib.Error:
            self.controller.message("System tray unavailable")

    def item_property(self, _bus, _sender, _path, _iface, name):
        v = self.GLib.Variant
        return {"Category": v("s", "ApplicationStatus"), "Id": v("s", "kizurium-translator"), "Title": v("s", "Kizurium"),
                "Status": v("s", "Active"), "IconName": v("s", "kizurium-translator"), "ItemIsMenu": v("b", False),
                "Menu": v("o", "/Menu"), "ToolTip": v("(sa(iiay)ss)", ("kizurium-translator", [], "Kizurium", self.description))}.get(name)

    def item_method(self, _bus, _sender, _path, _iface, method, _params, invocation):
        self.controller.dispatch("settings" if method == "ContextMenu" else "select-region")
        invocation.return_value(None)

    def properties(self, item):
        v = self.GLib.Variant
        if item == 0:
            return {"children-display": v("s", "submenu")}
        if not 1 <= item <= len(ACTIONS):
            return {}
        action, label = ACTIONS[item-1]
        props = {"label": v("s", label), "enabled": v("b", True), "visible": v("b", True)}
        if action == "toggle-offline":
            props.update({"toggle-type": v("s", "checkmark"), "toggle-state": v("i", int(self.controller.conf.offline_only))})
        if action == "stop-auto-translate":
            props["enabled"] = v("b", self.controller.live is not None)
        if action == "start-auto-translate":
            props["enabled"] = v("b", self.controller.live is None)
        return props

    def menu_property(self, _bus, _sender, _path, _iface, name):
        v = self.GLib.Variant
        return {"Version": v("u", 3), "TextDirection": v("s", "ltr"), "Status": v("s", "normal")}.get(name)

    def menu_method(self, _bus, _sender, _path, _iface, method, params, invocation):
        v = self.GLib.Variant
        args = params.unpack()
        if method == "GetLayout":
            parent = args[0]
            children = [v("(ia{sv}av)", (i, self.properties(i), [])) for i in range(1, len(ACTIONS)+1)] if parent == 0 else []
            invocation.return_value(v("(u(ia{sv}av))", (self.revision, (parent, self.properties(parent), children))))
        elif method == "GetGroupProperties":
            invocation.return_value(v("(a(ia{sv}))", ([(i, self.properties(i)) for i in (args[0] or range(1, len(ACTIONS)+1))],)))
        elif method == "GetProperty":
            invocation.return_value(v("(v)", (self.properties(args[0]).get(args[1], v("s", "")),)))
        elif method == "AboutToShow":
            invocation.return_value(v("(b)", (False,)))
        elif method == "Event":
            invocation.return_value(None)
            if args[1] == "clicked" and 1 <= args[0] <= len(ACTIONS):
                self.controller.dispatch(ACTIONS[args[0]-1][0])

    def update(self, description):
        self.description = description
        self.revision += 1
        self.bus.emit_signal(None, "/StatusNotifierItem", "org.kde.StatusNotifierItem", "NewToolTip", None)
        self.bus.emit_signal(None, "/Menu", "com.canonical.dbusmenu", "LayoutUpdated", self.GLib.Variant("(ui)", (self.revision, 0)))

    def shutdown(self):
        from gi.repository import Gio

        Gio.bus_unwatch_name(self.watch)
        for registration in self.ids:
            self.bus.unregister_object(registration)
