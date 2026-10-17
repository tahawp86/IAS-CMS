# -*- coding: utf-8 -*-
"""اتاق مانیتورینگ (multi-monitor) — نسخه‌ی 2.2.4.

قابلیت پخش تصاویر دوربین‌ها روی چند مانیتور مجزا (مناسب اتاق مانیتورینگ):
- شناسایی مانیتورهای متصل به سیستم
- باز کردن پنجره‌ی نمایش روی مانیتور انتخاب‌شده + حالت تمام‌صفحه
- تقسیم دوربین‌ها بین مانیتورها (هر نما: مانیتور + چیدمان گرید + لیست دوربین‌ها)
- ذخیره‌ی چیدمان در monitor_layout.json و بازیابی خودکار در استارتاپ

نکته‌ی معماری: تایل‌های این پنجره‌ها عمداً سبک‌اند (فقط نمایش ویدیو، بدون
موتور تشخیص چهره/حریق/پلاک) تا روی چند مانیتور با ده‌ها دوربین هم روان
بمانند. برای تحلیل‌ها همان صفحه‌ی اصلی برنامه استفاده می‌شود.
"""

import json
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QCheckBox, QListWidget, QListWidgetItem,
    QMessageBox, QScrollArea, QGroupBox, QSpinBox,
)

from app_paths import get_data_dir

LAYOUT_FILENAME = "monitor_layout.json"
GRID_CHOICES = [1, 4, 9, 16]


def get_layout_path():
    return os.path.join(get_data_dir(), LAYOUT_FILENAME)


def get_monitors():
    """لیست مانیتورهای متصل: [{index, name, x, y, width, height, is_primary}]."""
    out = []
    try:
        screens = QGuiApplication.screens()
    except Exception:
        screens = []
    for i, s in enumerate(screens):
        try:
            g = s.geometry()
            out.append({
                "index": i,
                "name": s.name() or f"مانیتور {i + 1}",
                "x": g.x(), "y": g.y(),
                "width": g.width(), "height": g.height(),
                "is_primary": (s == QGuiApplication.primaryScreen()),
            })
        except Exception:
            continue
    return out



# ---------------------------------------------------------------- window --

class MonitorWindow(QMainWindow):
    """پنجره‌ی نمایش دوربین‌ها برای یک نما (view).

    (2.2.9) بازطراحی کامل: به‌جای تایل‌های سبک و نوار ابزار جدا، از
    CameraPageWidget (همان صفحه‌ی مرکزی پنجره‌ی اصلی) استفاده می‌کند —
    single source of truth؛ ظاهر، چیدمان و همه‌ی دکمه‌ها دقیقاً مثل
    صفحه‌ی اصلی و همه درست کار می‌کنند.
    """

    def __init__(self, title="اتاق مانیتورینگ", parent=None, main_window=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._main = main_window or parent
        self._view_mode = "monitor"
        self._mon_index = 0

        from camera_page import CameraPageWidget
        self.camera_page = CameraPageWidget(self._main, self)
        # دکمه‌ی سایدبار در پنجره‌ی نما معنا ندارد — مخفی‌اش می‌کنیم
        try:
            self.camera_page.sidebar_toggle_btn.setVisible(False)
        except Exception:
            pass
        self.setCentralWidget(self.camera_page)
        self.resize(960, 600)

    @property
    def camera_grid(self):
        return self.camera_page.camera_grid

    def set_grid_size(self, n):
        """تغییر چیدمان گرید (همگام با کمبوباکس صفحه)."""
        try:
            idx = self.camera_page.grid_size_combo.findData(n)
            if idx >= 0:
                self.camera_page.grid_size_combo.setCurrentIndex(idx)
            else:
                self.camera_grid.set_grid_size(n)
        except Exception:
            try:
                self.camera_grid.set_grid_size(n)
            except Exception:
                pass

    def set_cameras(self, cam_url_list):
        """cam_url_list: لیست (cam_dict, rtsp_url) — در اسلات‌های اول می‌نشینند."""
        grid = self.camera_grid
        try:
            grid.stop_all()
        except Exception:
            pass
        for i, (cam, url) in enumerate(cam_url_list):
            try:
                grid.assign_camera_to_slot(cam, url, i)
            except Exception:
                pass

    def move_to_monitor(self, mon, fullscreen=True):
        """انتقال پنجره به مانیتور مشخص (mon: dict از get_monitors)."""
        try:
            screens = QGuiApplication.screens()
            if 0 <= mon["index"] < len(screens):
                scr = screens[mon["index"]]
                self.showNormal()
                try:
                    self.windowHandle().setScreen(scr)
                except Exception:
                    pass
                if fullscreen:
                    self.showFullScreen()
                else:
                    g = scr.geometry()
                    self.setGeometry(g.x() + 40, g.y() + 40,
                                     min(1200, g.width() - 80),
                                     min(800, g.height() - 80))
                    self.show()
                return True
        except Exception:
            pass
        if fullscreen:
            self.showFullScreen()
        else:
            self.show()
        return False

    def open_floating(self, geom=None):
        """باز کردن به‌صورت پنجره‌ی عادیِ قابل‌حرکت/تغییراندازه."""
        if geom and len(geom) == 4:
            try:
                self.setGeometry(int(geom[0]), int(geom[1]),
                                 int(geom[2]), int(geom[3]))
            except Exception:
                pass
        self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def get_geom(self):
        g = self.geometry()
        return [g.x(), g.y(), g.width(), g.height()]

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def mouseDoubleClickEvent(self, ev):
        self.toggle_fullscreen()
        super().mouseDoubleClickEvent(ev)

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
            return
        super().keyPressEvent(ev)

    def closeEvent(self, ev):
        try:
            self.camera_grid.stop_all()
        except Exception:
            pass
        super().closeEvent(ev)


# ----------------------------------------------------------------- dialog --

class MonitorRoomDialog(QDialog):
    """دیالوگ مدیریت اتاق مانیتورینگ: تعریف نماها (مانیتور+دوربین‌ها)."""

    def __init__(self, cameras, build_rtsp_url, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🖥️ اتاق مانیتورینگ")
        self.setMinimumSize(640, 520)
        self._cameras = list(cameras or [])
        self._build_rtsp_url = build_rtsp_url
        self._monitors = get_monitors()
        self._views = []  # هر نما: dict(monitor, grid, fullscreen, cam_ids)
        lay = QVBoxLayout(self)

        info = QLabel(
            "هر «نما» یک پنجره‌ی نمایش است که روی مانیتور انتخاب‌شده باز می‌شود.\n"
            f"مانیتورهای شناسایی‌شده: {len(self._monitors)}")
        info.setWordWrap(True)
        lay.addWidget(info)

        self.views_list = QListWidget()
        lay.addWidget(self.views_list, 1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("➕ افزودن نما")
        add_btn.clicked.connect(self._add_view)
        edit_btn = QPushButton("✏️ ویرایش نما")
        edit_btn.clicked.connect(self._edit_view)
        del_btn = QPushButton("🗑 حذف نما")
        del_btn.clicked.connect(self._del_view)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(edit_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        ok_row = QHBoxLayout()
        ok_row.addStretch()
        save_btn = QPushButton("💾 ذخیره و اعمال")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("انصراف")
        cancel_btn.clicked.connect(self.reject)
        ok_row.addWidget(save_btn)
        ok_row.addWidget(cancel_btn)
        lay.addLayout(ok_row)
        self._refresh_list()

    # -- لیست نماها --

    def _view_label(self, v):
        n = len(v.get("cam_ids", []))
        if v.get("mode") == "floating":
            fs = "تمام‌صفحه" if v.get("fullscreen") else "شناور"
            return (f"🪟 پنجره‌ی شناور — گرید {v.get('grid', 4)}تایی — "
                    f"{n} دوربین — {fs}")
        mon_name = f"مانیتور {v['monitor'] + 1}"
        for m in self._monitors:
            if m["index"] == v["monitor"]:
                mon_name = m["name"]
                break
        fs = "تمام‌صفحه" if v.get("fullscreen") else "پنجره‌ای"
        return (f"{mon_name} — گرید {v.get('grid', 4)}تایی — "
                f"{n} دوربین — {fs}")

    def _refresh_list(self):
        self.views_list.clear()
        for v in self._views:
            QListWidgetItem(self._view_label(v), self.views_list)

    def set_views(self, views):
        self._views = [dict(v) for v in (views or [])]
        self._refresh_list()

    def get_views(self):
        return [dict(v) for v in self._views]

    # -- افزودن/ویرایش/حذف --

    def _add_view(self):
        v = {"monitor": 0, "grid": 4, "fullscreen": True, "cam_ids": []}
        if self._edit_view_dialog(v):
            self._views.append(v)
            self._refresh_list()

    def _edit_view(self):
        row = self.views_list.currentRow()
        if row < 0 or row >= len(self._views):
            QMessageBox.information(self, "اتاق مانیتورینگ",
                                   "اول یک نما را انتخاب کنید.")
            return
        v = dict(self._views[row])
        v["cam_ids"] = list(v.get("cam_ids", []))
        if self._edit_view_dialog(v):
            self._views[row] = v
            self._refresh_list()

    def _del_view(self):
        row = self.views_list.currentRow()
        if row < 0 or row >= len(self._views):
            return
        del self._views[row]
        self._refresh_list()

    def _edit_view_dialog(self, v):
        d = QDialog(self)
        d.setWindowTitle("تنظیم نما")
        d.setMinimumWidth(480)
        lay = QVBoxLayout(d)

        # حالت نما: مانیتور مشخص یا پنجره‌ی شناور
        mode_row = QHBoxLayout()
        float_check = QCheckBox("🪟 پنجره‌ی شناور (خودم با موس جابه‌جاش می‌کنم)")
        float_check.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        float_check.setChecked(v.get("mode", "monitor") == "floating")
        float_check.setToolTip(
            "اگر فعال باشد، پنجره به‌صورت عادی (قابل حرکت و تغییراندازه)\n"
            "باز می‌شود تا خودتان آن را به مانیتور دلخواه درگ کنید؛\n"
            "دابل‌کلیک = تمام‌صفحه، Esc = خروج از تمام‌صفحه.")
        mode_row.addWidget(float_check)
        mode_row.addStretch()
        lay.addLayout(mode_row)

        # مانیتور
        mrow = QHBoxLayout()
        mrow.addWidget(QLabel("مانیتور:"))
        mon_combo = QComboBox()
        for m in self._monitors:
            tag = " (اصلی)" if m["is_primary"] else ""
            mon_combo.addItem(
                f"{m['name']}{tag} — {m['width']}×{m['height']}",
                m["index"])
        idx = mon_combo.findData(v.get("monitor", 0))
        mon_combo.setCurrentIndex(max(0, idx))
        mrow.addWidget(mon_combo, 1)
        lay.addLayout(mrow)
        # در حالت شناور، انتخاب مانیتور بی‌معناست
        mon_combo.setEnabled(not float_check.isChecked())
        float_check.toggled.connect(
            lambda on: mon_combo.setEnabled(not on))

        # گرید + تمام‌صفحه
        grow = QHBoxLayout()
        grow.addWidget(QLabel("چیدمان:"))
        grid_combo = QComboBox()
        for g in GRID_CHOICES:
            grid_combo.addItem(f"{g}تایی ({int(g ** 0.5)}×{int(g ** 0.5)})", g)
        gi = grid_combo.findData(v.get("grid", 4))
        grid_combo.setCurrentIndex(max(0, gi))
        grow.addWidget(grid_combo)
        fs_check = QCheckBox("تمام‌صفحه")
        fs_check.setChecked(bool(v.get("fullscreen", True)))
        # در Qt، راست‌به‌چپ: متن فارسی چک‌باکس
        fs_check.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        grow.addWidget(fs_check)
        grow.addStretch()
        lay.addLayout(grow)

        # لیست دوربین‌ها
        lay.addWidget(QLabel("دوربین‌های این نما (به ترتیب نمایش):"))
        cam_list = QListWidget()
        cam_list.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection)
        id_to_row = {}
        for i, cam in enumerate(self._cameras):
            cid = str(cam.get("id", ""))
            item = QListWidgetItem(cam.get("name", cid) or cid)
            item.setData(Qt.ItemDataRole.UserRole, cid)
            if cid in v.get("cam_ids", []):
                item.setSelected(True)
            cam_list.addItem(item)
            id_to_row[cid] = i
        # مرتب‌سازی انتخاب‌شده‌ها طبق ترتیب ذخیره‌شده
        lay.addWidget(cam_list, 1)

        hint = QLabel("💡 با Ctrl+کلیک چند دوربین انتخاب کنید. ترتیب نمایش = "
                      "ترتیب لیست بالا.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9b978c; font-size: 11px;")
        lay.addWidget(hint)

        brow = QHBoxLayout()
        brow.addStretch()
        okb = QPushButton("تأیید")
        okb.setDefault(True)
        okb.clicked.connect(d.accept)
        cb = QPushButton("انصراف")
        cb.clicked.connect(d.reject)
        brow.addWidget(okb)
        brow.addWidget(cb)
        lay.addLayout(brow)

        if d.exec() != QDialog.DialogCode.Accepted:
            return False
        v["mode"] = "floating" if float_check.isChecked() else "monitor"
        v["monitor"] = mon_combo.currentData()
        v["grid"] = grid_combo.currentData()
        v["fullscreen"] = fs_check.isChecked()
        # ترتیب انتخاب = ترتیب لیست (نه ترتیب کلیک) — ساده و قابل پیش‌بینی
        v["cam_ids"] = [str(cam_list.item(i).data(Qt.ItemDataRole.UserRole))
                        for i in range(cam_list.count())
                        if cam_list.item(i).isSelected()]
        return True


# ---------------------------------------------------------------- manager --

class MonitorRoomManager:
    """مدیریت پنجره‌های اتاق مانیتورینگ + ذخیره/بازیابی چیدمان."""

    def __init__(self, main_window):
        self.main = main_window
        self.windows = []

    # -- دیالوگ --

    def open_manager(self):
        try:
            cams = self.main.camera_store.cameras
        except Exception:
            cams = []
        if not cams:
            QMessageBox.information(
                self.main, "اتاق مانیتورینگ",
                "هنوز دوربینی اضافه نشده است.")
            return
        from camera_store import CameraStore
        d = MonitorRoomDialog(
            cams, CameraStore.build_rtsp_url, self.main)
        d.set_views(self._current_views())
        if d.exec() == QDialog.DialogCode.Accepted:
            views = d.get_views()
            self.apply_views(views)
            self.save_layout(views)

    # -- اعمال چیدمان --

    def _current_views(self):
        views = []
        for w in self.windows:
            try:
                v = {
                    "mode": getattr(w, "_view_mode", "monitor"),
                    "monitor": getattr(w, "_mon_index", 0),
                    "grid": w.camera_page.grid_size_combo.currentData(),
                    "fullscreen": w.isFullScreen(),
                    "cam_ids": [str((s.cam or {}).get("id", ""))
                                for s in w.camera_grid.slots if s.cam],
                }
                # موقعیت/اندازه‌ی پنجره‌های شناور هم ذخیره شود
                if v["mode"] == "floating" and not w.isFullScreen():
                    v["geom"] = w.get_geom()
                views.append(v)
            except Exception:
                continue
        return views

    def close_all(self):
        for w in self.windows:
            try:
                w.close()
            except Exception:
                pass
        self.windows = []

    def apply_views(self, views):
        self.close_all()
        if not views:
            return
        try:
            cams = {str(c.get("id")): c
                    for c in self.main.camera_store.cameras}
        except Exception:
            cams = {}
        from camera_store import CameraStore
        monitors = {m["index"]: m for m in get_monitors()}
        for i, v in enumerate(views):
            try:
                win = MonitorWindow(f"🖥️ اتاق مانیتورینگ — نمای {i + 1}",
                                    self.main)
                mode = v.get("mode", "monitor")
                win._view_mode = mode
                win._mon_index = int(v.get("monitor", 0))
                grid = int(v.get("grid", 4))
                if grid not in GRID_CHOICES:
                    grid = 4
                win.set_grid_size(grid)
                pairs = []
                for cid in v.get("cam_ids", [])[:grid]:
                    cam = cams.get(str(cid))
                    if not cam:
                        continue
                    try:
                        url = CameraStore.build_rtsp_url(cam)
                    except Exception:
                        url = ""
                    pairs.append((cam, url))
                win.set_cameras(pairs)
                if mode == "floating":
                    # پنجره‌ی شناور: کاربر خودش با موس جابه‌جا می‌کند
                    geom = v.get("geom")
                    if not geom:
                        # آبشاری: هر پنجره‌ی بعدی کمی جابه‌جا
                        geom = [80 + i * 40, 80 + i * 40, 960, 600]
                    win.open_floating(geom)
                    if v.get("fullscreen"):
                        win.showFullScreen()
                else:
                    mon = monitors.get(win._mon_index)
                    if mon is not None:
                        win.move_to_monitor(mon, bool(v.get("fullscreen", True)))
                    else:
                        (win.showFullScreen() if v.get("fullscreen")
                         else win.show())
                self.windows.append(win)
            except Exception:
                continue

    # -- ذخیره/بازیابی --

    def save_layout(self, views=None):
        if views is None:
            views = self._current_views()
        try:
            with open(get_layout_path(), "w", encoding="utf-8") as f:
                json.dump({"views": views}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def load_layout(self):
        try:
            with open(get_layout_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            views = data.get("views", [])
            return [v for v in views if isinstance(v, dict)]
        except Exception:
            return []

    def restore_on_startup(self):
        views = self.load_layout()
        if views:
            self.apply_views(views)

    def shutdown(self):
        self.save_layout()
        self.close_all()
