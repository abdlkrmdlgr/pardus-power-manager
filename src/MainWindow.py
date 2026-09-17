#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Nov 15 14:53:01 2024

@author: fatihaltun
"""
import glob
import grp
import os
import re
import subprocess
import xml.etree.ElementTree as ET

import dbus
import dbus.mainloop.glib
import gi

gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GObject, GLib, Gdk, Gio, GdkPixbuf

try:
    gi.require_version('AppIndicator3', '0.1')
    from gi.repository import AppIndicator3 as appindicator
except:
    # fall back to Ayatana
    gi.require_version('AyatanaAppIndicator3', '0.1')
    from gi.repository import AyatanaAppIndicator3 as appindicator

# from UserSettings import UserSettings
from Utils import ErrorDialog
from Notification import Notification

import locale
from locale import gettext as _system_gettext

local_locale = os.path.dirname(os.path.abspath(__file__)) + "/../po"
if os.path.isdir(local_locale):
    locale.bindtextdomain('pardus-power-manager', local_locale)
locale.bindtextdomain('pardus-power-manager', '/usr/share/locale')
locale.textdomain('pardus-power-manager')


def _(msg):
    translations = {
        "Power Saver": "Tasarruf",
        "Balanced": "Dengeli",
        "Performance": "Performans",
        "Built-in Display": "Dahili Ekran",
        "External Display": "Harici Ekran",
        "Display": "Ekran",
        "Primary": "Birincil",
        "Power Mode": "Güç Modu",
        "Choose system performance and power consumption mode": "Sistem performansı ve enerji tüketim tercihini belirleyin",
        "Maximizes battery life and limits system performance": "Pil ömrünü uzatmak için sistem performansını sınırlar",
        "Standard performance and energy consumption balance (Recommended)": "Standart performans ve enerji tüketimi dengesi sağlar (Önerilen)",
        "High computing power and maximum responsiveness": "Yüksek işlem gücü ve maksimum hız sağlar",
        "Screen Brightness": "Ekran Parlaklığı",
        "Adjust brightness level of connected displays": "Bağlı ekranların parlaklık düzeyini ayarlayın",
        "<b>Power Saver:</b> Maximizes battery life and limits system performance.": "<b>Tasarruf:</b> Pil ömrünü uzatmak için donanım performansı ve enerji tüketimi sınırlandırılır.",
        "<b>Balanced:</b> Standard performance and energy consumption balance (Recommended).": "<b>Dengeli:</b> Standart performans ve enerji tüketimi arasında ideal dengeyi sağlar.",
        "<b>Performance:</b> Delivers high computing power and maximum responsiveness.": "<b>Performans:</b> Yüksek işlem gücü ve maksimum hız sağlar. Enerji tüketimi artabilir.",
    }
    lang = os.environ.get("LANG", "")
    if "tr" in lang.lower() and msg in translations:
        return translations[msg]
    translated = _system_gettext(msg)
    if translated == msg and "tr" in lang.lower():
        return translations.get(msg, msg)
    return translated


def getenv(env_name):
    env = os.environ.get(env_name)
    return env if env else ""


xfce_desktop = False
if "xfce" in getenv("SESSION").lower() or "xfce" in getenv("XDG_CURRENT_DESKTOP").lower():
    xfce_desktop = True


class MainWindow(object):
    def __init__(self, application):
        self.Application = application

        self.main_window_ui_filename = os.path.dirname(os.path.abspath(__file__)) + "/../ui/MainWindow.glade"
        try:
            self.GtkBuilder = Gtk.Builder.new_from_file(self.main_window_ui_filename)
            self.GtkBuilder.connect_signals(self)
        except GObject.GError:
            print("Error reading GUI file: " + self.main_window_ui_filename)
            raise

        self.init_power_profiles_dbus()

        self.define_components()
        self.define_variables()

        self.main_window.set_application(application)
        self.main_window.set_size_request(530, -1)
        self.main_window.set_resizable(False)

        # self.user_settings()
        # self.set_autostart()

        self.init_indicator()
        self.mark_current_profile()

        self.control_brightness()
        self.add_brightness_devices()

        self.monitor_brightness_devices()

        self.about_dialog.set_program_name(_("Pardus Power Manager"))
        if self.about_dialog.get_titlebar() is None:
            about_headerbar = Gtk.HeaderBar.new()
            about_headerbar.set_show_close_button(True)
            about_headerbar.set_title(_("About Pardus Power Manager"))
            about_headerbar.pack_start(Gtk.Image.new_from_icon_name("pardus-power-manager", Gtk.IconSize.LARGE_TOOLBAR))
            about_headerbar.show_all()
            self.about_dialog.set_titlebar(about_headerbar)

        # Set version
        # If not getted from __version__ file then accept version in MainWindow.glade file
        try:
            version = open(os.path.dirname(os.path.abspath(__file__)) + "/__version__").readline()
            self.about_dialog.set_version(version)
        except:
            pass

        cssProvider = Gtk.CssProvider()
        cssProvider.load_from_path(os.path.dirname(os.path.abspath(__file__)) + "/../data/style.css")
        screen = Gdk.Screen.get_default()
        if screen:
            screen.connect("monitors-changed", self.on_monitors_changed)
        styleContext = Gtk.StyleContext()
        styleContext.add_provider_for_screen(screen, cssProvider,
                                             Gtk.STYLE_PROVIDER_PRIORITY_USER)

        if "tray" in self.Application.args.keys():
            self.main_window.set_visible(False)
        else:
            self.main_window.set_visible(True)
            self.main_window.show_all()

        self.set_indicator()

        self.hide_widgets()

    def init_power_profiles_dbus(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        try:
            self.ppd_bus = dbus.SystemBus()
            self.ppd_proxy = self.ppd_bus.get_object("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles")
        except dbus.exceptions.DBusException as e:
            print("{}".format(e))
            ErrorDialog(_("Error"), "<b>{}</b>\n\n{}".format(_("power-profiles-daemon not found."), e))
            exit(1)
        self.ppd_interface = dbus.Interface(self.ppd_proxy, "net.hadess.PowerProfiles")

        self.ppd_bus.add_signal_receiver(
            self.on_profile_changed,
            dbus_interface="org.freedesktop.DBus.Properties",
            signal_name="PropertiesChanged",
            path="/net/hadess/PowerProfiles",
        )

    def on_profile_changed(self, interface_name, changed_properties, invalidated_properties):
        if "ActiveProfile" in changed_properties:
            new_profile = changed_properties["ActiveProfile"]
            print("signal: current_profile: {}".format(self.current_profile))
            print("signal: new_profile: {}".format(new_profile))
            if self.current_profile != new_profile:
                self.set_profile(new_profile)

    def define_components(self):
        self.main_window = self.GtkBuilder.get_object("ui_main_window")
        self.about_dialog = self.GtkBuilder.get_object("ui_about_dialog")

        self.ui_powersaver_button = self.GtkBuilder.get_object("ui_powersaver_button")
        self.ui_balanced_button = self.GtkBuilder.get_object("ui_balanced_button")
        self.ui_performance_button = self.GtkBuilder.get_object("ui_performance_button")

        self.ui_power_title_label = self.GtkBuilder.get_object("ui_power_title_label")
        self.ui_power_subtitle_label = self.GtkBuilder.get_object("ui_power_subtitle_label")
        self.ui_powersaver_label = self.GtkBuilder.get_object("ui_powersaver_label")
        self.ui_balanced_label = self.GtkBuilder.get_object("ui_balanced_label")
        self.ui_performance_label = self.GtkBuilder.get_object("ui_performance_label")

        self.ui_profile_info_box = self.GtkBuilder.get_object("ui_profile_info_box")
        self.ui_profile_info_label = self.GtkBuilder.get_object("ui_profile_info_label")

        self.ui_brightness_header_box = self.GtkBuilder.get_object("ui_brightness_header_box")
        self.ui_brightness_title_label = self.GtkBuilder.get_object("ui_brightness_title_label")
        self.ui_brightness_subtitle_label = self.GtkBuilder.get_object("ui_brightness_subtitle_label")
        self.ui_brightness_box = self.GtkBuilder.get_object("ui_brightness_box")

        self.ui_permission_dialog = self.GtkBuilder.get_object("ui_permission_dialog")
        self.ui_permission_info_label = self.GtkBuilder.get_object("ui_permission_info_label")

        self.ui_powersaver_image = self.GtkBuilder.get_object("ui_powersaver_image")
        self.ui_balanced_image = self.GtkBuilder.get_object("ui_balanced_image")
        self.ui_performance_image = self.GtkBuilder.get_object("ui_performance_image")

        self.ui_power_title_image = self.GtkBuilder.get_object("ui_power_title_image")
        if self.ui_power_title_image:
            data_dir = os.path.dirname(os.path.abspath(__file__)) + "/../data/"
            lightning_svg = os.path.join(data_dir, "power-lightning-symbolic.svg")
            if os.path.isfile(lightning_svg):
                self.ui_power_title_image.set_from_file(lightning_svg)
            else:
                self.ui_power_title_image.set_from_icon_name("thunderbolt-symbolic", Gtk.IconSize.LARGE_TOOLBAR)

        if self.ui_power_title_label:
            self.ui_power_title_label.set_text(_("Power Mode"))
        if self.ui_power_subtitle_label:
            self.ui_power_subtitle_label.set_no_show_all(True)
            self.ui_power_subtitle_label.hide()
        if self.ui_powersaver_label:
            self.ui_powersaver_label.set_text(_("Power Saver"))
        if self.ui_balanced_label:
            self.ui_balanced_label.set_text(_("Balanced"))
        if self.ui_performance_label:
            self.ui_performance_label.set_text(_("Performance"))

        if self.ui_powersaver_button:
            self.ui_powersaver_button.set_tooltip_text(_("Maximizes battery life and limits system performance"))
        if self.ui_balanced_button:
            self.ui_balanced_button.set_tooltip_text(_("Standard performance and energy consumption balance (Recommended)"))
        if self.ui_performance_button:
            self.ui_performance_button.set_tooltip_text(_("High computing power and maximum responsiveness"))

        if self.ui_brightness_title_label:
            self.ui_brightness_title_label.set_text(_("Screen Brightness"))
        if self.ui_brightness_subtitle_label:
            self.ui_brightness_subtitle_label.set_text(_("Adjust brightness level of connected displays"))

        self.set_hand_cursor(self.ui_powersaver_button)
        self.set_hand_cursor(self.ui_balanced_button)
        self.set_hand_cursor(self.ui_performance_button)

    def set_hand_cursor(self, widget):
        if not widget:
            return

        def on_enter(w, event):
            win = w.get_window()
            if win:
                cursor = Gdk.Cursor.new_from_name(w.get_display(), "pointer")
                if not cursor:
                    cursor = Gdk.Cursor.new_for_display(w.get_display(), Gdk.CursorType.HAND2)
                win.set_cursor(cursor)
            return False

        def on_leave(w, event):
            win = w.get_window()
            if win:
                win.set_cursor(None)
            return False

        widget.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        widget.connect("enter-notify-event", on_enter)
        widget.connect("leave-notify-event", on_leave)
        widget.connect("realize", lambda w: on_enter(w, None))

    def define_variables(self):
        self.dbus_power_profiles = self.ppd_interface.Get("net.hadess.PowerProfiles", "Profiles",
                                                          dbus_interface="org.freedesktop.DBus.Properties")
        self.power_profiles = ["{}".format(entry['Profile']) for entry in self.dbus_power_profiles]
        self.current_profile = self.ppd_interface.Get("net.hadess.PowerProfiles", "ActiveProfile",
                                                      dbus_interface="org.freedesktop.DBus.Properties")

        self.profile_descriptions = {
            "power-saver": _("<b>Power Saver:</b> Maximizes battery life and limits system performance."),
            "balanced": _("<b>Balanced:</b> Standard performance and energy consumption balance (Recommended)."),
            "performance": _("<b>Performance:</b> Delivers high computing power and maximum responsiveness.")
        }

        self.indicator_icon = "pardus-power-manager"
        system_wide = "usr/share" in os.path.dirname(os.path.abspath(__file__))
        if not system_wide and self.ui_powersaver_image:
            data_dir = os.path.dirname(os.path.abspath(__file__)) + "/../data/"
            pb_ps = GdkPixbuf.Pixbuf.new_from_file_at_scale(data_dir + "pardus-power-manager-power-saver.svg", 20, 20, True)
            pb_bal = GdkPixbuf.Pixbuf.new_from_file_at_scale(data_dir + "pardus-power-manager-balanced.svg", 20, 20, True)
            pb_perf = GdkPixbuf.Pixbuf.new_from_file_at_scale(data_dir + "pardus-power-manager-performance.svg", 20, 20, True)
            self.ui_powersaver_image.set_from_pixbuf(pb_ps)
            self.ui_balanced_image.set_from_pixbuf(pb_bal)
            self.ui_performance_image.set_from_pixbuf(pb_perf)

        self._blanked_connectors = set()

        self.brightness_available = False
        self.brightness_devices = {}
        self.brightness_adjustments = {}
        self.brightness_percent_labels = {}
        self.mdir = {}

        self.brightness_error_message = ""

        self.device = ""
        self.value = ""

        self.user_name = GLib.get_user_name()
        self.brightness_group = "video"

        print("Available profiles: {}".format(self.power_profiles))
        print("Current profile: {}".format(self.current_profile))

    # def user_settings(self):
    #     self.UserSettings = UserSettings()
    #     self.UserSettings.createDefaultConfig()
    #     self.UserSettings.readConfig()

    # def set_autostart(self):
    #     self.UserSettings.set_autostart(self.UserSettings.config_autostart)

    def init_indicator(self):
        self.indicator = appindicator.Indicator.new(
            "pardus-power-manager", self.indicator_icon, appindicator.IndicatorCategory.APPLICATION_STATUS)
        data_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
        if hasattr(self.indicator, "set_icon_theme_path"):
            self.indicator.set_icon_theme_path(data_dir)
        self.indicator.set_status(appindicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title(_("Pardus Power Manager"))

        self.menu = Gtk.Menu()

        self.item_sh_app = Gtk.MenuItem()
        self.item_sh_app.connect("activate", self.on_menu_show_app)
        self.menu.append(self.item_sh_app)

        self.item_separator = Gtk.SeparatorMenuItem()
        self.menu.append(self.item_separator)

        self.item_powersaver = Gtk.MenuItem()
        if "power-saver" in self.power_profiles:
            self.item_powersaver.set_label(_("Power Saver"))
            self.item_powersaver.connect("activate", self.on_ui_powersaver_button_clicked)
            self.menu.append(self.item_powersaver)

        self.item_balanced = Gtk.MenuItem()
        if "balanced" in self.power_profiles:
            self.item_balanced.set_label(_("Balanced"))
            self.item_balanced.connect("activate", self.on_ui_balanced_button_clicked)
            self.menu.append(self.item_balanced)

            self.item_performance = Gtk.MenuItem()
            if "performance" in self.power_profiles:
                self.item_performance.set_label(_("Performance"))
                self.item_performance.connect("activate", self.on_ui_performance_button_clicked)
                self.menu.append(self.item_performance)

        self.item_separator1 = Gtk.SeparatorMenuItem()
        self.menu.append(self.item_separator1)

        self.item_quit = Gtk.MenuItem()
        self.item_quit.set_label(_("Quit"))
        self.item_quit.connect('activate', self.on_menu_quit_app)
        self.menu.append(self.item_quit)

        self.menu.show_all()
        self.indicator.set_menu(self.menu)

    def set_indicator(self):

        if self.main_window.is_visible():
            self.item_sh_app.set_label(_("Hide App"))
        else:
            self.item_sh_app.set_label(_("Show App"))

    def hide_widgets(self):
        self.ui_powersaver_button.set_visible("power-saver" in self.power_profiles)
        self.ui_balanced_button.set_visible("balanced" in self.power_profiles)
        self.ui_performance_button.set_visible("performance" in self.power_profiles)
        self.ui_brightness_box.set_visible(self.brightness_available)
        if hasattr(self, "ui_brightness_header_box") and self.ui_brightness_header_box:
            self.ui_brightness_header_box.set_visible(self.brightness_available)

    def mark_current_profile(self):
        if hasattr(self, "ui_profile_info_label") and self.ui_profile_info_label and hasattr(self, "profile_descriptions"):
            if self.current_profile in self.profile_descriptions:
                self.ui_profile_info_label.set_markup(self.profile_descriptions[self.current_profile])

        clean_ps = self.item_powersaver.get_label().replace("✓", "").replace("[", "").replace("]", "").strip() if hasattr(self, "item_powersaver") else ""
        clean_bal = self.item_balanced.get_label().replace("✓", "").replace("[", "").replace("]", "").strip() if hasattr(self, "item_balanced") else ""
        clean_perf = self.item_performance.get_label().replace("✓", "").replace("[", "").replace("]", "").strip() if hasattr(self, "item_performance") else ""

        if self.current_profile == "power-saver":
            self.ui_powersaver_button.get_style_context().add_class("suggested-action")
            self.ui_balanced_button.get_style_context().remove_class("suggested-action")
            self.ui_performance_button.get_style_context().remove_class("suggested-action")
            if hasattr(self, "item_powersaver"):
                self.item_powersaver.set_label("✓ {}".format(clean_ps))
            if hasattr(self, "item_balanced"):
                self.item_balanced.set_label(clean_bal)
            if hasattr(self, "item_performance"):
                self.item_performance.set_label(clean_perf)
        elif self.current_profile == "balanced":
            self.ui_balanced_button.get_style_context().add_class("suggested-action")
            self.ui_powersaver_button.get_style_context().remove_class("suggested-action")
            self.ui_performance_button.get_style_context().remove_class("suggested-action")
            if hasattr(self, "item_balanced"):
                self.item_balanced.set_label("✓ {}".format(clean_bal))
            if hasattr(self, "item_powersaver"):
                self.item_powersaver.set_label(clean_ps)
            if hasattr(self, "item_performance"):
                self.item_performance.set_label(clean_perf)
        elif self.current_profile == "performance":
            self.ui_performance_button.get_style_context().add_class("suggested-action")
            self.ui_powersaver_button.get_style_context().remove_class("suggested-action")
            self.ui_balanced_button.get_style_context().remove_class("suggested-action")
            if hasattr(self, "item_performance"):
                self.item_performance.set_label("✓ {}".format(clean_perf))
            if hasattr(self, "item_powersaver"):
                self.item_powersaver.set_label(clean_ps)
            if hasattr(self, "item_balanced"):
                self.item_balanced.set_label(clean_bal)

    def set_profile(self, profile_name):

        self.ppd_interface.Set("net.hadess.PowerProfiles", "ActiveProfile", profile_name,
                               dbus_interface="org.freedesktop.DBus.Properties")

        self.current_profile = self.ppd_interface.Get("net.hadess.PowerProfiles", "ActiveProfile",
                                                      dbus_interface="org.freedesktop.DBus.Properties")

        self.mark_current_profile()
        print("profile setted to: {}".format(profile_name))

    def on_monitors_changed(self, screen):
        print("Monitors changed, updating brightness controls...")
        self.control_brightness()
        self.add_brightness_devices()
        self.monitor_brightness_devices()
        self.ui_brightness_box.set_visible(self.brightness_available)
        if hasattr(self, "ui_brightness_header_box") and self.ui_brightness_header_box:
            self.ui_brightness_header_box.set_visible(self.brightness_available)

    def parse_edid(self, data):
        if not data or len(data) < 128:
            return ""
        for offset in (54, 72, 90, 108):
            block = data[offset:offset + 18]
            if len(block) == 18 and block[:3] == b"\x00\x00\x00":
                if block[3] in (0xfc, 0xfe):
                    raw = block[5:].decode("latin1", errors="ignore")
                    clean = "".join(c for c in raw if 32 <= ord(c) <= 126).strip()
                    if clean:
                        return clean
        return ""

    def get_xfce_display_name(self, conn_name):
        xml_path = os.path.expanduser("~/.config/xfce4/xfconf/xfce-perchannel-xml/displays.xml")
        if os.path.isfile(xml_path):
            try:
                tree = ET.parse(xml_path)
                for prop in tree.iter("property"):
                    if prop.get("name") == conn_name and prop.get("value"):
                        return prop.get("value")
            except Exception:
                pass
        return ""

    def get_drm_edid_names(self):
        edid_names = {}
        if os.path.isdir("/sys/class/drm"):
            for item in os.listdir("/sys/class/drm"):
                edid_file = os.path.join("/sys/class/drm", item, "edid")
                if os.path.isfile(edid_file):
                    try:
                        with open(edid_file, "rb") as f:
                            data = f.read()
                        name = self.parse_edid(data)
                        if name:
                            edid_names[item] = name
                            if "-" in item:
                                base = item.split("-", 1)[1]
                                edid_names[base] = name
                    except Exception:
                        pass
        return edid_names

    def get_xrandr_display_info(self):
        outputs = {}
        drm_edids = self.get_drm_edid_names()
        try:
            out = subprocess.check_output(["xrandr", "--prop", "--verbose"], stderr=subprocess.DEVNULL).decode("utf-8", errors="ignore")
            curr = None
            in_edid = False
            edid_hex = ""
            for line in out.splitlines():
                m = re.match(r"^(\S+)\s+(connected|disconnected)", line)
                if m:
                    if curr and edid_hex:
                        try:
                            outputs[curr]["edid_name"] = self.parse_edid(bytes.fromhex(edid_hex))
                        except Exception:
                            pass
                    curr = m.group(1)
                    is_conn = m.group(2) == "connected"
                    outputs[curr] = {
                        "name": curr,
                        "connected": is_conn,
                        "active": False,
                        "brightness": 1.0,
                        "geom": "",
                        "edid_name": "",
                    }
                    in_edid = False
                    edid_hex = ""
                    geom_m = re.search(r"(\d+x\d+\+\d+\+\d+)", line)
                    if geom_m and is_conn:
                        outputs[curr]["active"] = True
                        outputs[curr]["geom"] = geom_m.group(1).split("+")[0]
                elif curr and "\tEDID:" in line:
                    in_edid = True
                    edid_hex = ""
                elif curr and in_edid:
                    if line.startswith("\t\t"):
                        edid_hex += line.strip()
                    else:
                        in_edid = False
                elif curr and "Brightness:" in line:
                    b_m = re.search(r"Brightness:\s+([\d\.]+)", line)
                    if b_m:
                        try:
                            outputs[curr]["brightness"] = float(b_m.group(1))
                        except ValueError:
                            pass

            if curr and edid_hex:
                try:
                    outputs[curr]["edid_name"] = self.parse_edid(bytes.fromhex(edid_hex))
                except Exception:
                    pass
        except Exception as e:
            print("xrandr detection error: {}".format(e))

        for name, data in outputs.items():
            if not data.get("edid_name"):
                if name in drm_edids:
                    data["edid_name"] = drm_edids[name]
                else:
                    xfce_name = self.get_xfce_display_name(name)
                    if xfce_name:
                        data["edid_name"] = xfce_name

        return outputs

    def monitor_brightness_devices(self):
        if hasattr(self, 'mdir') and self.mdir:
            for mon in self.mdir.values():
                try:
                    mon.cancel()
                except Exception:
                    pass
        self.mdir = {}
        if self.brightness_available:
            for device, value in self.brightness_devices.items():
                if not value.get("is_xrandr"):
                    bpath = "/sys/class/backlight/{}/brightness".format(device)
                    if os.path.isfile(bpath):
                        mon = Gio.file_new_for_path(bpath).monitor_file(0, None)
                        mon.connect('changed', self.on_brightness_changed_from_monitoring)
                        self.mdir[device] = mon

    def on_brightness_changed_from_monitoring(self, file_monitor, file, other_file, event_type):
        if event_type in [Gio.FileMonitorEvent.CHANGES_DONE_HINT]:
            device = "{}".format(file.get_path()).split("/")[-2]
            raw_b = self.get_current_brightness(device)
            dev_info = self.brightness_devices.get(device, {})
            max_b = dev_info.get("max_brightness", 100)
            pct = int(round((raw_b / max_b) * 100)) if max_b > 0 else 0
            print("trigger: device: {}, raw_b: {}, pct: {}".format(device, raw_b, pct))
            self.set_brightness(device, pct, from_monitoring=True)

    def get_gdk_monitors(self):
        monitors = {}
        display = Gdk.Display.get_default()
        if display:
            for i in range(display.get_n_monitors()):
                m = display.get_monitor(i)
                geom = m.get_geometry()
                model = m.get_model() or ""
                manufacturer = m.get_manufacturer() or ""
                monitors[model] = {
                    "index": i,
                    "model": model,
                    "manufacturer": manufacturer,
                    "geometry": "{}x{}".format(geom.width, geom.height),
                    "is_primary": m.is_primary(),
                }
        return monitors

    def find_drm_connectors_for_backlight(self, device):
        bpath = "/sys/class/backlight/{}".format(device)
        real_bpath = os.path.realpath(bpath)
        connectors = []

        parent = os.path.dirname(real_bpath)
        if os.path.isfile(os.path.join(parent, "status")):
            connectors.append(parent)
        else:
            dev_link = os.path.join(bpath, "device")
            if os.path.exists(dev_link):
                real_dev = os.path.realpath(dev_link)
                curr = real_dev
                while curr != "/" and not connectors:
                    drm_dir = os.path.join(curr, "drm")
                    if os.path.isdir(drm_dir):
                        for item in os.listdir(drm_dir):
                            item_path = os.path.join(drm_dir, item)
                            if item.startswith("card") and "-" not in item:
                                conns = glob.glob(os.path.join(item_path, "{}-*".format(item)))
                                connectors.extend(conns)
                            elif "-" in item:
                                connectors.append(item_path)
                    curr = os.path.dirname(curr)
                    if len(curr.split("/")) < 4:
                        break
        return connectors

    def find_backlight_device_for_connector(self, conn_name):
        if not os.path.isdir("/sys/class/backlight"):
            return None
        candidates = []
        for dev in sorted(os.listdir("/sys/class/backlight")):
            max_b = self.get_max_brightness(dev)
            if max_b <= 0:
                continue
            conns = self.find_drm_connectors_for_backlight(dev)
            for c in conns:
                c_base = os.path.basename(c)
                base_name = c_base.split("-", 1)[1] if "-" in c_base else c_base
                if base_name.upper() == conn_name.upper() or conn_name.upper() in base_name.upper():
                    btype_file = "/sys/class/backlight/{}/type".format(dev)
                    btype = open(btype_file).read().strip() if os.path.isfile(btype_file) else ""
                    prio = 3 if btype == "raw" else (2 if btype == "platform" else 1)
                    candidates.append((prio, dev))
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1]

        # Fallback if a raw or platform backlight device exists on the system
        for dev in sorted(os.listdir("/sys/class/backlight")):
            btype_file = "/sys/class/backlight/{}/type".format(dev)
            btype = open(btype_file).read().strip() if os.path.isfile(btype_file) else ""
            if btype in ("raw", "platform") and self.get_max_brightness(dev) > 0:
                return dev
        return None

    def control_brightness(self):
        self.brightness_available = False
        self.brightness_devices = {}

        xr_info = self.get_xrandr_display_info()

        if xr_info:
            for name, data in xr_info.items():
                if not (data["connected"] and data["active"]):
                    continue

                is_builtin = any(p in name.upper() for p in ["LVDS", "EDP", "DSI"])
                geom = data.get("geom", "")

                if is_builtin:
                    # Internal display: use hardware backlight if available
                    backlight_dev = self.find_backlight_device_for_connector(name)
                    if backlight_dev:
                        max_b = self.get_max_brightness(backlight_dev)
                        curr_b = self.get_current_brightness(backlight_dev)
                        self.brightness_devices[backlight_dev] = {
                            "max_brightness": max_b,
                            "current_brightness": curr_b,
                            "connector": name,
                            "display_type": _("Built-in Display"),
                            "display_name": _("Built-in Display"),
                            "geom": geom,
                            "is_builtin": True,
                            "is_xrandr": False,
                        }
                    else:
                        self.brightness_devices["xrandr:{}".format(name)] = {
                            "max_brightness": 100,
                            "current_brightness": int(round(data.get("brightness", 1.0) * 100)),
                            "connector": name,
                            "display_type": _("Built-in Display"),
                            "display_name": _("Built-in Display"),
                            "geom": geom,
                            "is_builtin": True,
                            "is_xrandr": True,
                        }
                else:
                    # External display: ALWAYS use XRandR brightness, NEVER /sys/class/backlight
                    edid_name = data.get("edid_name", "")
                    self.brightness_devices["xrandr:{}".format(name)] = {
                        "max_brightness": 100,
                        "current_brightness": int(round(data.get("brightness", 1.0) * 100)),
                        "connector": name,
                        "display_type": _("External Display"),
                        "display_name": edid_name if edid_name else _("External Display"),
                        "geom": geom,
                        "is_builtin": False,
                        "is_xrandr": True,
                    }
        else:
            if os.path.isdir("/sys/class/backlight"):
                for dev in sorted(os.listdir("/sys/class/backlight")):
                    max_b = self.get_max_brightness(dev)
                    if max_b <= 0:
                        continue
                    connectors = self.find_drm_connectors_for_backlight(dev)
                    active_connectors = []
                    for c in connectors:
                        st_file = os.path.join(c, "status")
                        en_file = os.path.join(c, "enabled")
                        st = open(st_file).read().strip() if os.path.isfile(st_file) else ""
                        en = open(en_file).read().strip() if os.path.isfile(en_file) else ""
                        if st == "connected" and en == "enabled":
                            active_connectors.append(c)
                    if connectors and not active_connectors:
                        continue

                    conn_name = ""
                    if active_connectors:
                        c_base = os.path.basename(active_connectors[0])
                        conn_name = c_base.split("-", 1)[1] if "-" in c_base else c_base

                    is_builtin = any(p in conn_name.upper() for p in ["LVDS", "EDP", "DSI"])
                    if not is_builtin:
                        continue

                    curr_b = self.get_current_brightness(dev)
                    self.brightness_devices[dev] = {
                        "max_brightness": max_b,
                        "current_brightness": curr_b,
                        "connector": conn_name,
                        "display_type": _("Built-in Display"),
                        "display_name": _("Built-in Display"),
                        "geom": "",
                        "is_builtin": True,
                        "is_xrandr": False,
                    }

        if self.brightness_devices:
            self.brightness_available = True
        print("Brightness available. Devices: {}".format(self.brightness_devices))

    def get_max_brightness(self, device):
        max_brightness_file = "/sys/class/backlight/{}/max_brightness".format(device)
        if os.path.isfile(max_brightness_file):
            try:
                with open(max_brightness_file, "r") as f:
                    return int(f.read().strip())
            except Exception as e:
                print("Error in get_max_brightness. {}".format(e))
                return 0

    def get_current_brightness(self, device):
        current_brightness_file = "/sys/class/backlight/{}/brightness".format(device)
        if os.path.isfile(current_brightness_file):
            try:
                with open(current_brightness_file, "r") as f:
                    return int(f.read().strip())
            except Exception as e:
                print("Error in get_current_brightness. {}".format(e))
                return 0

    def set_brightness(self, device, value, from_monitoring=False):
        self.brightness_error_message = ""
        self.device = device
        self.value = value

        dev_info = self.brightness_devices.get(device, {})
        is_xrandr = dev_info.get("is_xrandr", False)
        pct = max(0, min(100, int(round(value))))

        if is_xrandr:
            conn = dev_info.get("connector") or device.replace("xrandr:", "")
            float_val = float(pct) / 100.0
            try:
                subprocess.run(["xrandr", "--output", conn, "--brightness", "{:.2f}".format(float_val)], check=False)
                dev_info["current_brightness"] = pct
                print("xrandr brightness for {}: {:.2f}".format(conn, float_val))
            except Exception as e:
                print("Error setting xrandr brightness for {}: {}".format(conn, e))
        else:
            max_b = dev_info.get("max_brightness", 100)
            hardware_val = int(round((pct / 100.0) * max_b))
            if not from_monitoring:
                if os.access("/sys/class/backlight/{}/brightness".format(device), os.W_OK):
                    self.write_brightness(device, hardware_val)
                    dev_info["current_brightness"] = hardware_val
                else:
                    try:
                        self.user_groups = [g.gr_name for g in grp.getgrall() if self.user_name in g.gr_mem]
                    except Exception as e:
                        print("{}".format(e))
                        self.user_groups = []

                    if self.brightness_group not in self.user_groups:
                        print("user: {} not in {} group; but in groups: {}".format(self.user_name,
                                                                                   self.brightness_group, self.user_groups))
                        self.ui_permission_dialog.set_title(_("Error"))
                        self.ui_permission_info_label.set_markup(
                            "<b>{}</b>\n\n{}:\n\n/sys/class/backlight/{}/brightness\n\n{}".format(
                            _("Error"), _("You don't have write permissions to file"), device,
                            _("User is not in video group.")))
                        response = self.ui_permission_dialog.run()
                        self.ui_permission_dialog.hide()
                        if response == Gtk.ResponseType.OK:
                            command = ["/usr/bin/pkexec", os.path.dirname(os.path.abspath(__file__)) + "/Brightness.py",
                                       self.user_name, self.brightness_group]
                            self.start_brightness_process(command)
                        elif response == Gtk.ResponseType.CANCEL:
                            print("Gtk.ResponseType.CANCEL")
                    else:
                        print("{} in {} group; but need restart".format(self.user_name, self.brightness_group))
                        ErrorDialog(_("Error"), "{}".format(
                            _("You need to reboot your system for group permissions to take effect.")))

            else:
                self._updating_from_monitoring = True
                try:
                    ui_brightness_adjustment = self.brightness_adjustments.get(device)
                    if ui_brightness_adjustment is not None:
                        ui_brightness_value = int(round(ui_brightness_adjustment.get_value()))
                        if ui_brightness_value != pct:
                            ui_brightness_adjustment.set_value(pct)
                finally:
                    self._updating_from_monitoring = False

        if device in self.brightness_percent_labels:
            self.brightness_percent_labels[device].set_markup("<b>{}%</b>".format(pct))

    def write_brightness(self, device, value):
        curr = self.get_current_brightness(device)
        conn = self.brightness_devices.get(device, {}).get("connector", "")

        # Handle %0 total display blanking via xrandr
        if int(value) == 0:
            if conn and conn not in getattr(self, "_blanked_connectors", set()):
                try:
                    subprocess.run(["xrandr", "--output", conn, "--brightness", "0.0"], check=False)
                    self._blanked_connectors.add(conn)
                    print("Blanked screen {} via xrandr at 0%".format(conn))
                except Exception as e:
                    print("Error blanking screen with xrandr: {}".format(e))
        else:
            if conn and conn in getattr(self, "_blanked_connectors", set()):
                try:
                    subprocess.run(["xrandr", "--output", conn, "--brightness", "1.0"], check=False)
                    self._blanked_connectors.discard(conn)
                    print("Restored screen {} via xrandr".format(conn))
                except Exception as e:
                    print("Error restoring screen with xrandr: {}".format(e))

        if int(value) == curr:
            return
        brightness_file = "/sys/class/backlight/{}/brightness".format(device)
        if os.path.isfile(brightness_file):
            with open(brightness_file, "w") as fd:
                fd.write("{}".format(int(value)))
                fd.flush()
            print("brightness changed to: {} {}".format(device, value))

    def add_brightness_devices(self):
        for row in self.ui_brightness_box:
            self.ui_brightness_box.remove(row)
        self.brightness_adjustments = {}
        self.brightness_percent_labels = {}

        for device, value in self.brightness_devices.items():
            max_b = value["max_brightness"]
            curr_b = value["current_brightness"]
            pct = int(round((curr_b / max_b) * 100)) if max_b > 0 else 0
            pct = max(0, min(100, pct))

            conn = value.get("connector", "")
            dtype = value.get("display_type", _("Display"))
            display_name = value.get("display_name", "")
            geom = value.get("geom", "")

            # Header row: [Icon] [Display title] ... [Percent label]
            header_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)

            icon = Gtk.Image.new_from_icon_name("video-display-symbolic", Gtk.IconSize.BUTTON)
            header_box.pack_start(icon, False, False, 0)

            detail_parts = []
            if conn:
                detail_parts.append(conn)
            if geom:
                detail_parts.append(geom)

            main_title = display_name if display_name else dtype

            if detail_parts:
                title_markup = "<b>{}</b> <small>({})</small>".format(
                    GLib.markup_escape_text(main_title),
                    GLib.markup_escape_text(", ".join(detail_parts))
                )
            else:
                title_markup = "<b>{}</b>".format(GLib.markup_escape_text(main_title))

            title_label = Gtk.Label.new()
            title_label.set_xalign(0.0)
            title_label.set_markup(title_markup)
            header_box.pack_start(title_label, True, True, 0)

            percent_label = Gtk.Label.new()
            percent_label.set_xalign(1.0)
            percent_label.set_markup("<b>{}%</b>".format(pct))
            header_box.pack_end(percent_label, False, False, 0)
            self.brightness_percent_labels[device] = percent_label

            adjustment = Gtk.Adjustment.new(value=pct,
                                            lower=0,
                                            upper=100,
                                            step_increment=1,
                                            page_increment=5,
                                            page_size=0)
            adjustment.name = device
            adjustment.connect("value-changed", self.on_brightness_changed)
            self.brightness_adjustments[device] = adjustment

            scale = Gtk.Scale.new(Gtk.Orientation.HORIZONTAL, adjustment)
            scale.set_draw_value(False)
            scale.set_inverted(False)
            scale.set_show_fill_level(False)
            scale.set_restrict_to_fill_level(True)
            scale.set_round_digits(0)
            scale.set_hexpand(True)
            scale.set_margin_start(6)
            scale.set_margin_end(6)

            slider_box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 10)
            dim_icon = Gtk.Image.new_from_icon_name("display-brightness-symbolic", Gtk.IconSize.MENU)
            dim_icon.set_opacity(0.5)
            dim_icon.set_margin_end(6)
            bright_icon = Gtk.Image.new_from_icon_name("display-brightness-symbolic", Gtk.IconSize.MENU)
            bright_icon.set_opacity(1.0)
            bright_icon.set_margin_start(6)

            slider_box.pack_start(dim_icon, False, False, 0)
            slider_box.pack_start(scale, True, True, 0)
            slider_box.pack_start(bright_icon, False, False, 0)

            box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 4)
            box.get_style_context().add_class("display-card")
            box.set_margin_top(2)
            box.set_margin_bottom(2)
            box.pack_start(header_box, False, False, 0)
            box.pack_start(slider_box, False, False, 0)

            self.ui_brightness_box.pack_start(box, True, True, 0)

        GLib.idle_add(self.ui_brightness_box.show_all)

    def on_brightness_changed(self, adjustment):
        device = adjustment.name
        pct = int(round(adjustment.get_value()))
        pct = max(0, min(100, pct))
        if device in self.brightness_percent_labels:
            self.brightness_percent_labels[device].set_markup("<b>{}%</b>".format(pct))

        if getattr(self, "_updating_from_monitoring", False):
            return

        print("on_brightness_changed: {} {}%".format(device, pct))
        self.set_brightness(device, pct, from_monitoring=False)

    def on_ui_powersaver_button_clicked(self, button):
        self.set_profile("power-saver")

    def on_ui_balanced_button_clicked(self, button):
        self.set_profile("balanced")

    def on_ui_performance_button_clicked(self, button):
        self.set_profile("performance")

    def on_ui_permission_close_button_clicked(self, button):
        self.ui_permission_dialog.hide()
        self.ui_permission_dialog.response(Gtk.ResponseType.CANCEL)

    def on_ui_permission_grant_button_clicked(self, button):
        self.ui_permission_dialog.response(Gtk.ResponseType.OK)

    def on_menu_show_app(self, *args):
        window_state = self.main_window.is_visible()
        if window_state:
            self.main_window.set_visible(False)
            self.item_sh_app.set_label(_("Show App"))
        else:
            self.main_window.set_visible(True)
            self.item_sh_app.set_label(_("Hide App"))
            self.main_window.present()

    def on_menu_quit_app(self, *args):
        if self.about_dialog.is_visible():
            self.about_dialog.hide()
        self.main_window.get_application().quit()

    def on_ui_about_button_clicked(self, button):
        self.about_dialog.run()
        self.about_dialog.hide()

    def on_ui_main_window_delete_event(self, widget, event):
        self.main_window.hide()
        self.item_sh_app.set_label(_("Show App"))
        return True

    def on_ui_main_window_destroy(self, widget, event):
        if self.about_dialog.is_visible():
            self.about_dialog.hide()
        self.main_window.get_application().quit()

    def start_brightness_process(self, params):
        pid, stdin, stdout, stderr = GLib.spawn_async(params, flags=GLib.SpawnFlags.DO_NOT_REAP_CHILD,
                                                      standard_output=True, standard_error=True)
        GLib.io_add_watch(GLib.IOChannel(stdout), GLib.IO_IN | GLib.IO_HUP, self.on_brightness_process_stdout)
        GLib.io_add_watch(GLib.IOChannel(stderr), GLib.IO_IN | GLib.IO_HUP, self.on_brightness_process_stderr)
        GLib.child_watch_add(GLib.PRIORITY_DEFAULT, pid, self.on_brightness_process_exit)

        return pid

    def on_brightness_process_stdout(self, source, condition):
        if condition == GLib.IO_HUP:
            return False
        line = source.readline()
        print("on_brightness_process_stdout - line: {}".format(line))
        return True

    def on_brightness_process_stderr(self, source, condition):
        if condition == GLib.IO_HUP:
            return False
        line = source.readline()
        print("on_brightness_process_stderr - line: {}".format(line))
        self.brightness_error_message = line
        return True

    def on_brightness_process_exit(self, pid, status):
        print("on_brightness_process_exit - status: {}".format(status))
        if status == 32256:  # operation cancelled | Request dismissed
            print("operation cancelled | Request dismissed")
        else:
            if self.brightness_error_message != "":
                ErrorDialog(_("Error"), "{}".format(self.brightness_error_message))
            else:
                if self.device != "" and self.value != "":
                    self.set_brightness(self.device, self.value)
