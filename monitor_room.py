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
import time

import cv2
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPoint
from PyQt6.QtGui import QGuiApplication, QImage, QPainter, QColor, QFont, QPen, QPolygon
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QComboBox, QCheckBox, QListWidget, QListWidgetItem,
    QMessageBox, QScrollArea, QGroupBox, QSpinBox, QToolBar, QDockWidget,
)

from app_paths import get_data_dir
from rtsp_utils import open_capture, STREAM_FFMPEG_OPTS

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


# ------------------------------------------------------------ stream worker --

class _MonitorStreamWorker(QThread):
    """ترد سبک دریافت فریم از یک دوربین (فقط نمایش، بدون تحلیل)."""
    frame_ready = pyqtSignal(object)  # QImage
    status_changed = pyqtSignal(str)  # connecting|live|error

    def __init__(self, rtsp_url, parent=None):
        super().__init__(parent)
        self._url = rtsp_url
        self._running = True

    def run(self):
        self.status_changed.emit("connecting")
        while self._running:
            cap = None
            try:
                cap = open_capture(self._url, STREAM_FFMPEG_OPTS)
                if cap is None or not cap.isOpened():
                    raise RuntimeError("open failed")
                # چند تلاش برای اولین فریم (دی‌کدر باید به I-frame برسد)
                ok_first = False
                for _ in range(6):
                    ok, _f = cap.read()
                    if ok:
                        ok_first = True
                        break
                    time.sleep(0.4)
                    if not self._running:
                        break
                if not ok_first:
                    raise RuntimeError("no frames")
                self.status_changed.emit("live")
                while self._running:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        break
                    try:
                        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        h, w, _ = rgb.shape
                        img = QImage(rgb.data, w, h, 3 * w,
                                     QImage.Format.Format_RGB888).copy()
                        self.frame_ready.emit(img)
                    except Exception:
                        pass
                    # ~۱۵ فریم‌برثانیه برای سبک‌ماندن روی چند مانیتور
                    time.sleep(0.066)
            except Exception:
                if self._running:
                    self.status_changed.emit("error")
            finally:
                try:
                    if cap is not None:
                        cap.release()
                except Exception:
                    pass
            if self._running:
                time.sleep(3.0)  # تلاش مجدد

    def stop(self):
        # (بازبینی IVDagent - 2.2.8) توقف امن: مثل _stop_thread؛ اگر ترد در
        # cap.read() گیر کرده باشد (rw_timeout تا ~۵ ثانیه)، wait ساده
        # تایم‌اوت می‌شود و deleteLater روی QThread در حال اجرا = کرش qFatal.
        self._running = False
        try:
            if self.isRunning() and not self.wait(7000):
                self.terminate()
                self.wait(3000)
        except Exception:
            pass


# ------------------------------------------------------------------ tile --

class MonitorTileWidget(QWidget):
    """یک خانه‌ی نمایش در پنجره‌ی مانیتور: نام دوربین + تصویر زنده.

    (2.2.8) انتخاب تایل (کلیک) + زوم دیجیتال + رسم محدوده — برای نوار
    ابزار مدیریت دوربین در پنجره‌های شناور نما.
    """

    clicked = pyqtSignal(object)  # self
    zone_drawn = pyqtSignal(list)  # points نرمال‌شده

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cam = None
        self._rtsp_url = None
        self._worker = None
        self._img = None
        self._status = "empty"  # empty|connecting|live|error
        self.setMinimumSize(160, 120)
        self.setStyleSheet("background: #0d1117;")
        # انتخاب / زوم / رسم
        self._selected = False
        self._zoom = 1.0
        self._draw_mode = False
        self._draw_start = None
        self._draw_cur = None
        self._zones = []
        self.setMouseTracking(True)

    def set_camera(self, cam, rtsp_url):
        self.stop()
        self.cam = dict(cam) if cam else None
        self._rtsp_url = rtsp_url
        self._img = None
        if not self.cam or not rtsp_url:
            self._status = "empty"
            self.update()
            return
        self._status = "connecting"
        self._worker = _MonitorStreamWorker(rtsp_url, self)
        self._worker.frame_ready.connect(self._on_frame)
        self._worker.status_changed.connect(self._on_status)
        self._worker.start()
        self.update()

    def clear(self):
        self.set_camera(None, None)

    def stop(self):
        w, self._worker = self._worker, None
        if w is not None:
            try:
                w.status_changed.disconnect()
            except Exception:
                pass
            try:
                w.frame_ready.disconnect()
            except Exception:
                pass
            w.stop()

    def _on_frame(self, img):
        self._img = img
        self.update()

    def _on_status(self, st):
        self._status = st
        self.update()

    # -- انتخاب / زوم / رسم (2.2.8) --

    def set_selected(self, on):
        self._selected = bool(on)
        self.update()

    def set_zoom(self, z):
        self._zoom = max(1.0, min(8.0, float(z)))
        self.update()

    def zoom_step(self, direction):
        self.set_zoom(self._zoom * (1.25 if direction > 0 else 1 / 1.25))

    def reset_zoom(self):
        self.set_zoom(1.0)

    def set_draw_mode(self, on):
        self._draw_mode = bool(on)
        self._draw_start = None
        self._draw_cur = None
        self.setCursor(Qt.CursorShape.CrossCursor if on
                       else Qt.CursorShape.ArrowCursor)
        self.update()

    def set_zones(self, zones):
        self._zones = []
        for z in (zones or []):
            pts = z.get("points") if isinstance(z, dict) else None
            if pts:
                self._zones.append(list(pts))
        self.update()

    def _widget_to_norm(self, x, y):
        if self._img is None:
            return None
        iw, ih = self._img.width(), self._img.height()
        rw, rh = self.width(), self.height()
        scale = min(rw / max(1, iw), rh / max(1, ih))
        dw, dh = iw * scale, ih * scale
        ox, oy = (rw - dw) / 2, (rh - dh) / 2
        zw, zh = dw / self._zoom, dh / self._zoom
        zx = ox + (dw - zw) / 2
        zy = oy + (dh - zh) / 2
        nx = (x - zx) / max(1, zw)
        ny = (y - zy) / max(1, zh)
        return (max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny)))

    def mousePressEvent(self, ev):
        if self._draw_mode and ev.button() == Qt.MouseButton.LeftButton:
            self._draw_start = (ev.position().x(), ev.position().y())
            self._draw_cur = self._draw_start
        else:
            self.clicked.emit(self)
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._draw_mode and self._draw_start:
            self._draw_cur = (ev.position().x(), ev.position().y())
            self.update()
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._draw_mode and self._draw_start:
            x0, y0 = self._draw_start
            x1, y1 = ev.position().x(), ev.position().y()
            self._draw_start = None
            self._draw_cur = None
            if abs(x1 - x0) > 10 and abs(y1 - y0) > 10 and self._img:
                p1 = self._widget_to_norm(min(x0, x1), min(y0, y1))
                p2 = self._widget_to_norm(max(x0, x1), max(y0, y1))
                if p1 and p2:
                    points = [p1, (p2[0], p1[1]), p2, (p1[0], p2[1])]
                    self.zone_drawn.emit(points)
            self.set_draw_mode(False)
            self.update()
        super().mouseReleaseEvent(ev)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0d1117"))
        if self._img is not None:
            # حفظ نسبت تصویر
            iw, ih = self._img.width(), self._img.height()
            rw, rh = self.width(), self.height()
            scale = min(rw / max(1, iw), rh / max(1, ih))
            dw, dh = int(iw * scale), int(ih * scale)
            x, y = (rw - dw) // 2, (rh - dh) // 2
            if self._zoom > 1.0:
                sw, sh = int(iw / self._zoom), int(ih / self._zoom)
                sx = (iw - sw) // 2
                sy = (ih - sh) // 2
                crop = self._img.copy(sx, sy, sw, sh)
                p.drawImage(x, y, crop.scaled(
                    dw, dh, Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
            else:
                p.drawImage(x, y, self._img.scaled(
                    dw, dh, Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
            # رسم زون‌ها
            if self._zones:
                p.setPen(QPen(QColor("#e74c3c"), 2))
                for pts in self._zones:
                    poly = [QPoint(int(x + nx * dw), int(y + ny * dh))
                            for (nx, ny) in pts]
                    if len(poly) >= 3:
                        p.drawPolygon(QPolygon(poly))
            # مستطیل در حال رسم
            if self._draw_mode and self._draw_start and self._draw_cur:
                p.setPen(QPen(QColor("#2a9bd8"), 2, Qt.PenStyle.DashLine))
                dx0, dy0 = self._draw_start
                dx1, dy1 = self._draw_cur
                p.drawRect(int(min(dx0, dx1)), int(min(dy0, dy1)),
                           int(abs(dx1 - dx0)), int(abs(dy1 - dy0)))
        # نوار نام دوربین
        name = (self.cam or {}).get("name", "")
        if name or self._status in ("connecting", "error"):
            p.fillRect(0, 0, self.width(), 24, QColor(0, 0, 0, 160))
            p.setPen(QColor("#e9eef1"))
            p.setFont(QFont("Segoe UI", 9))
            if self._status == "connecting":
                txt = f"{name} — در حال اتصال…"
            elif self._status == "error":
                txt = f"{name} — خطا در اتصال"
            else:
                txt = name
            p.drawText(8, 16, txt)
        # قاب قرمز هنگام خطا
        if self._status == "error":
            p.setPen(QColor("#e74c3c"))
            p.drawRect(1, 1, self.width() - 2, self.height() - 2)
        # قاب آبی برای تایل انتخاب‌شده
        if self._selected:
            p.setPen(QPen(QColor("#2a9bd8"), 3))
            p.drawRect(2, 2, self.width() - 4, self.height() - 4)
        p.end()

    def closeEvent(self, ev):
        self.stop()
        super().closeEvent(ev)


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
