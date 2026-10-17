# -*- coding: utf-8 -*-
"""کنسول مدیریت دوربین (2.2.7) — پنجره‌ی یکپارچه‌ی مدیریت یک دوربین.

از دکمه‌ی «گزارش ویدیویی» (reports_dialog) باز می‌شود و در یک پنجره این‌ها
را یکجا می‌دهد:
- تب «📡 زنده»: پخش زنده + زوم دیجیتال + رسم محدوده روی تصویر
- تب «🎬 پخش ضبط‌شده»: پخش بازبینی NVR (NVRPlaybackWidget)
- پنل کناری: مدیریت محدوده‌ها، شمارش افراد، صدا (شنیدن/صحبت)، زوم، رویدادها

از کامپوننت‌های موجود حداکثر استفاده‌ی مجدد شده:
- پخش ضبط‌شده: NVRPlaybackWidget
- صدا: ListenDialog و TalkDialog
- رویدادها: report_store.query
- محدوده‌ها: همان فرمت cam["regions"] (لیست {"id","number","name","points"})
"""

import time

import cv2
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPoint
from PyQt6.QtGui import QImage, QPainter, QColor, QPen, QPolygon
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QLabel,
    QPushButton, QGroupBox, QCheckBox, QListWidget, QListWidgetItem,
    QSlider, QMessageBox, QScrollArea, QInputDialog, QComboBox, QGridLayout,
)

from rtsp_utils import open_capture, STREAM_FFMPEG_OPTS


# ------------------------------------------------------- live stream -------

class _LiveWorker(QThread):
    frame_ready = pyqtSignal(object)  # QImage
    status_changed = pyqtSignal(str)

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
                for _ in range(6):
                    ok, _f = cap.read()
                    if ok:
                        break
                    time.sleep(0.4)
                    if not self._running:
                        break
                else:
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
                time.sleep(3.0)

    def stop(self):
        # (بازبینی IVDagent) توقف امن: مثل _stop_thread در nvr_playback_dialog؛
        # اگر ترد در cap.read() گیر کرده باشد (rw_timeout تا ~۵ ثانیه)،
        # wait ساده تایم‌اوت می‌شود و تخریب QThread در حال اجرا = کرش qFatal.
        self._running = False
        try:
            if self.isRunning() and not self.wait(7000):
                self.terminate()
                self.wait(3000)
        except Exception:
            pass


class ConsoleLiveWidget(QWidget):
    """نمایش زنده + زوم دیجیتال + رسم محدوده."""

    zone_drawn = pyqtSignal(list)  # points نرمال‌شده‌ی مستطیل رسم‌شده

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._img = None
        self._status = "empty"
        self._zoom = 1.0
        self._zoom_cx = 0.5
        self._zoom_cy = 0.5
        # حالت رسم محدوده
        self._draw_mode = False
        self._draw_start = None  # (x, y) پیکسل ویجت
        self._draw_cur = None
        self._zones = []  # لیست points برای نمایش
        self.setMinimumSize(320, 240)
        self.setStyleSheet("background: #0d1117;")
        self.setMouseTracking(True)

    # -- استریم --

    def start(self, rtsp_url):
        self.stop()
        self._img = None
        if not rtsp_url:
            self._status = "empty"
            self.update()
            return
        self._status = "connecting"
        self._worker = _LiveWorker(rtsp_url, self)
        self._worker.frame_ready.connect(self._on_frame)
        self._worker.status_changed.connect(self._on_status)
        self._worker.start()
        self.update()

    def stop(self):
        w, self._worker = self._worker, None
        if w is not None:
            try:
                w.frame_ready.disconnect()
            except Exception:
                pass
            try:
                w.status_changed.disconnect()
            except Exception:
                pass
            w.stop()

    def _on_frame(self, img):
        self._img = img
        self.update()

    def _on_status(self, st):
        self._status = st
        self.update()

    # -- زوم دیجیتال --

    def set_zoom(self, z):
        self._zoom = max(1.0, min(8.0, float(z)))
        self.update()

    def reset_zoom(self):
        self._zoom = 1.0
        self._zoom_cx = 0.5
        self._zoom_cy = 0.5
        self.update()

    def wheelEvent(self, ev):
        if self._img is None:
            return
        delta = ev.angleDelta().y()
        factor = 1.15 if delta > 0 else 1 / 1.15
        # زوم حول موقعیت نشانگر
        pos = ev.position()
        nx = pos.x() / max(1, self.width())
        ny = pos.y() / max(1, self.height())
        old = self._zoom
        new = max(1.0, min(8.0, old * factor))
        if new != old:
            # تنظیم مرکز برای زوم حول نشانگر (تقریبی)
            self._zoom_cx = max(0.0, min(1.0, self._zoom_cx + (nx - 0.5) * 0.1))
            self._zoom_cy = max(0.0, min(1.0, self._zoom_cy + (ny - 0.5) * 0.1))
            self._zoom = new
            self.update()

    # -- رسم محدوده --

    def set_draw_mode(self, on):
        self._draw_mode = on
        self._draw_start = None
        self._draw_cur = None
        self.setCursor(Qt.CursorShape.CrossCursor if on
                       else Qt.CursorShape.ArrowCursor)
        self.update()

    def set_zones(self, zones):
        """zones: لیست {"points": [(x,y), ...]} نرمال‌شده."""
        self._zones = []
        for z in (zones or []):
            pts = z.get("points") if isinstance(z, dict) else None
            if pts:
                self._zones.append(list(pts))
        self.update()

    def _widget_to_norm(self, x, y):
        """تبدیل مختصات ویجت به نرمال تصویر (با در نظر گرفتن زوم)."""
        if self._img is None:
            return None
        iw, ih = self._img.width(), self._img.height()
        rw, rh = self.width(), self.height()
        # ناحیه‌ی نمایشی فعلی (مثل paintEvent)
        scale = min(rw / max(1, iw), rh / max(1, ih))
        dw, dh = iw * scale, ih * scale
        ox, oy = (rw - dw) / 2, (rh - dh) / 2
        # اعمال زوم
        zw, zh = dw / self._zoom, dh / self._zoom
        zx = ox + (dw - zw) / 2 + (self._zoom_cx - 0.5) * (dw - zw)
        zy = oy + (dh - zh) / 2 + (self._zoom_cy - 0.5) * (dh - zh)
        nx = (x - zx) / max(1, zw)
        ny = (y - zy) / max(1, zh)
        return (max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny)))

    def mousePressEvent(self, ev):
        if self._draw_mode and ev.button() == Qt.MouseButton.LeftButton:
            self._draw_start = (ev.position().x(), ev.position().y())
            self._draw_cur = self._draw_start

    def mouseMoveEvent(self, ev):
        if self._draw_mode and self._draw_start:
            self._draw_cur = (ev.position().x(), ev.position().y())
            self.update()

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

    # -- رسم --

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0d1117"))
        if self._img is not None:
            iw, ih = self._img.width(), self._img.height()
            rw, rh = self.width(), self.height()
            scale = min(rw / max(1, iw), rh / max(1, ih))
            dw, dh = int(iw * scale), int(ih * scale)
            ox, oy = (rw - dw) // 2, (rh - dh) // 2
            if self._zoom > 1.0:
                # کراپ مرکزی با زوم
                zw, zh = dw / self._zoom, dh / self._zoom
                sx = int((iw - iw / self._zoom) * self._zoom_cx)
                sy = int((ih - ih / self._zoom) * self._zoom_cy)
                sw, sh = int(iw / self._zoom), int(ih / self._zoom)
                crop = self._img.copy(sx, sy, sw, sh)
                p.drawImage(ox, oy, crop.scaled(
                    dw, dh, Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
            else:
                p.drawImage(ox, oy, self._img.scaled(
                    dw, dh, Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
            # رسم زون‌های ذخیره‌شده
            pen = QPen(QColor("#e74c3c"), 2)
            p.setPen(pen)
            for pts in self._zones:
                poly = [QPoint(int(ox + nx * dw), int(oy + ny * dh))
                        for (nx, ny) in pts]
                if len(poly) >= 3:
                    p.drawPolygon(QPolygon(poly))
            # مستطیل در حال رسم
            if self._draw_mode and self._draw_start and self._draw_cur:
                p.setPen(QPen(QColor("#2a9bd8"), 2, Qt.PenStyle.DashLine))
                x0, y0 = self._draw_start
                x1, y1 = self._draw_cur
                p.drawRect(int(min(x0, x1)), int(min(y0, y1)),
                           int(abs(x1 - x0)), int(abs(y1 - y0)))
        else:
            p.setPen(QColor("#9b978c"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "در حال اتصال..." if self._status == "connecting"
                       else "خطا در اتصال" if self._status == "error" else "")
        p.end()

    def closeEvent(self, ev):
        self.stop()
        super().closeEvent(ev)


# ------------------------------------------------------------- dialog -------

class CameraConsoleDialog(QDialog):
    """کنسول مدیریت دوربین: زنده + پخش ضبط‌شده + کنترل‌ها."""

    def __init__(self, cam: dict, camera_store=None, report_store=None,
                 nvr=None, channel=None, start_dt=None, end_dt=None,
                 parent=None):
        super().__init__(parent)
        self.cam = dict(cam) if cam else {}
        self.camera_store = camera_store
        self.report_store = report_store
        self._nvr = nvr
        self._channel = channel
        self._start_dt = start_dt
        self._end_dt = end_dt

        cam_name = self.cam.get("name", "")
        self.setWindowTitle(f"🎛️ کنسول مدیریت دوربین — {cam_name}")
        self.resize(1100, 700)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

        main_lay = QHBoxLayout(self)

        # -- سمت چپ: ویدیو (تب‌ها) --
        self.tabs = QTabWidget()

        # تب زنده: با انتخاب چیدمان (۱/۴ تایی)
        live_tab = QWidget()
        live_tab_lay = QVBoxLayout(live_tab)
        live_tab_lay.setContentsMargins(0, 0, 0, 0)
        grid_row = QHBoxLayout()
        grid_row.addWidget(QLabel("چیدمان نمایش:"))
        self.grid_combo = QComboBox()
        self.grid_combo.addItem("تکی (۱)", 1)
        self.grid_combo.addItem("۴تایی (۲×۲)", 4)
        self.grid_combo.currentIndexChanged.connect(self._on_grid_changed)
        grid_row.addWidget(self.grid_combo)
        grid_row.addStretch()
        live_tab_lay.addLayout(grid_row)
        self.live_grid = QGridLayout()
        self.live_grid.setSpacing(4)
        live_tab_lay.addLayout(self.live_grid, 1)
        self.tabs.addTab(live_tab, "📡 زنده")

        self.live_tiles = []
        self._build_live_grid(1)
        # live_widget همان تایل اصلی (اول) است — برای سازگاری با متدهای پایین
        self.live_widget = self.live_tiles[0] if self.live_tiles else None

        self.playback_widget = None
        if nvr is not None and channel:
            from nvr_playback_dialog import NVRPlaybackWidget
            self.playback_widget = NVRPlaybackWidget(
                nvr, channel, start_dt, end_dt, cam_name, self)
            self.tabs.addTab(self.playback_widget, "🎬 پخش ضبط‌شده")
        main_lay.addWidget(self.tabs, 3)

        # -- سمت راست: پنل کنترل --
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(280)
        scroll.setMaximumWidth(340)
        panel = QWidget()
        panel_lay = QVBoxLayout(panel)
        panel_lay.setSpacing(10)

        panel_lay.addWidget(self._build_zones_group())
        panel_lay.addWidget(self._build_people_group())
        panel_lay.addWidget(self._build_audio_group())
        panel_lay.addWidget(self._build_zoom_group())
        panel_lay.addWidget(self._build_events_group())
        panel_lay.addStretch()
        scroll.setWidget(panel)
        main_lay.addWidget(scroll, 1)

    def _build_live_grid(self, n):
        """ساخت گرید نمای زنده (۱ یا ۴ تایل)."""
        for t in self.live_tiles:
            self.live_grid.removeWidget(t)
            t.stop()
            t.setParent(None)
            t.deleteLater()
        self.live_tiles = []
        rows = cols = 1 if n == 1 else 2
        for r in range(rows):
            for c in range(cols):
                tile = ConsoleLiveWidget()
                if r == 0 and c == 0:
                    tile.zone_drawn.connect(self._on_zone_drawn)
                self.live_grid.addWidget(tile, r, c)
                self.live_tiles.append(tile)
        self.live_widget = self.live_tiles[0] if self.live_tiles else None
        # تایل اول: دوربین اصلی؛ بقیه: خالی (کاربر از کمبو انتخاب می‌کند)
        if self.live_tiles:
            self._start_live()
            # (بازبینی IVDagent) زون‌های رسم‌شده را روی تایل تازه هم نشان بده
            self.live_widget.set_zones(self.cam.get("regions") or [])
            if n > 1 and self.camera_store:
                self._add_tile_pickers()

    def _add_tile_pickers(self):
        """کمبوباکس انتخاب دوربین برای تایل‌های اضافی."""
        try:
            cams = self.camera_store.cameras
        except Exception:
            cams = []
        for i in range(1, len(self.live_tiles)):
            tile = self.live_tiles[i]
            # یک کمبو روی تایل خالی می‌گذاریم
            combo = QComboBox(tile)
            combo.addItem("— انتخاب دوربین —", "")
            for c in cams:
                combo.addItem(c.get("name", ""), c.get("id"))
            combo.currentIndexChanged.connect(
                lambda _idx, _t=tile, _c=combo: self._on_tile_camera(_t, _c))
            # قرار دادن کمبو وسط تایل
            combo.setGeometry(10, 10, 200, 30)
            combo.show()

    def _on_tile_camera(self, tile, combo):
        cid = combo.currentData()
        if not cid:
            return
        try:
            cam = next((c for c in self.camera_store.cameras
                        if str(c.get("id")) == str(cid)), None)
            if cam:
                from camera_store import CameraStore
                tile.start(CameraStore.build_rtsp_url(cam))
        except Exception:
            pass

    def _on_grid_changed(self, idx):
        n = self.grid_combo.currentData()
        self._build_live_grid(n if n in (1, 4) else 1)

    # -- ساخت گروه‌های پنل --

    def _build_zones_group(self):
        g = QGroupBox("⚠ محدوده‌ها")
        lay = QVBoxLayout(g)
        self.draw_zone_btn = QPushButton("✏️ رسم محدوده جدید")
        self.draw_zone_btn.setToolTip(
            "روی تصویر زنده درگ کنید تا مستطیل محدوده رسم شود")
        self.draw_zone_btn.clicked.connect(
            lambda: self.live_widget.set_draw_mode(True))
        lay.addWidget(self.draw_zone_btn)
        self.zone_list = QListWidget()
        self.zone_list.setMaximumHeight(120)
        lay.addWidget(self.zone_list)
        row = QHBoxLayout()
        del_btn = QPushButton("🗑 حذف")
        del_btn.clicked.connect(self._delete_zone)
        row.addWidget(del_btn)
        row.addStretch()
        lay.addLayout(row)
        self._refresh_zones()
        return g

    def _build_people_group(self):
        g = QGroupBox("🔢 شمارش افراد")
        lay = QVBoxLayout(g)
        self.people_check = QCheckBox("فعال‌سازی شمارش افراد برای این دوربین")
        self.people_check.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        current = bool(self.cam.get("people_counting", False))
        self.people_check.setChecked(current)
        self.people_check.toggled.connect(self._on_people_toggled)
        lay.addWidget(self.people_check)
        hint = QLabel("شمارش در پنجره‌ی اصلی (شبکه‌ی دوربین‌ها) انجام می‌شود.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9b978c; font-size: 11px;")
        lay.addWidget(hint)
        return g

    def _build_audio_group(self):
        g = QGroupBox("🎧 صدا")
        lay = QVBoxLayout(g)
        row = QHBoxLayout()
        listen_btn = QPushButton("🎧 شنیدن صدا")
        listen_btn.clicked.connect(self._open_listen)
        talk_btn = QPushButton("🎤 صحبت")
        talk_btn.clicked.connect(self._open_talk)
        row.addWidget(listen_btn)
        row.addWidget(talk_btn)
        lay.addLayout(row)
        return g

    def _build_zoom_group(self):
        g = QGroupBox("🔍 زوم تصویر")
        lay = QVBoxLayout(g)
        row = QHBoxLayout()
        row.addWidget(QLabel("زوم:"))
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(100, 800)
        self.zoom_slider.setValue(100)
        self.zoom_slider.valueChanged.connect(
            lambda v: self.live_widget.set_zoom(v / 100.0))
        row.addWidget(self.zoom_slider, 1)
        lay.addLayout(row)
        reset_btn = QPushButton("↺ بازنشانی زوم")
        reset_btn.clicked.connect(self._reset_zoom)
        lay.addWidget(reset_btn)
        hint = QLabel("💡 با اسکرول موس هم می‌توانید زوم کنید.")
        hint.setStyleSheet("color: #9b978c; font-size: 11px;")
        lay.addWidget(hint)
        return g

    def _build_events_group(self):
        g = QGroupBox("📊 رویدادهای اخیر")
        lay = QVBoxLayout(g)
        self.event_list = QListWidget()
        self.event_list.setMaximumHeight(160)
        lay.addWidget(self.event_list)
        self._refresh_events()
        return g

    # -- منطق --

    def _rtsp_url(self):
        try:
            from camera_store import CameraStore
            return CameraStore.build_rtsp_url(self.cam)
        except Exception:
            return ""

    def _start_live(self):
        url = self._rtsp_url()
        if url:
            self.live_widget.start(url)

    def _reset_zoom(self):
        self.live_widget.reset_zoom()
        self.zoom_slider.setValue(100)

    # -- محدوده‌ها --

    def _refresh_zones(self):
        self.zone_list.clear()
        zones = self.cam.get("regions") or []
        for z in zones:
            name = z.get("name") or f"محدوده {z.get('number', '?')}"
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, z.get("id"))
            self.zone_list.addItem(item)
        self.live_widget.set_zones(zones)

    def _on_zone_drawn(self, points):
        name, ok = QInputDialog.getText(self, "محدوده جدید", "نام محدوده:")
        if not ok:
            return
        name = name.strip() or f"محدوده {len(self.cam.get('regions') or []) + 1}"
        zones = list(self.cam.get("regions") or [])
        import uuid as _uuid
        zones.append({
            "id": str(_uuid.uuid4()),
            "number": len(zones) + 1,
            "name": name,
            "points": [list(p) for p in points],
        })
        self._save_zones(zones)

    def _delete_zone(self):
        item = self.zone_list.currentItem()
        if not item:
            return
        zid = item.data(Qt.ItemDataRole.UserRole)
        zones = [z for z in (self.cam.get("regions") or [])
                 if z.get("id") != zid]
        self._save_zones(zones)

    def _save_zones(self, zones):
        self.cam["regions"] = zones
        try:
            if self.camera_store:
                self.camera_store.update_camera(self.cam.get("id"),
                                                regions=zones)
        except Exception:
            pass
        self._refresh_zones()

    # -- شمارش افراد --

    def _on_people_toggled(self, on):
        self.cam["people_counting"] = bool(on)
        try:
            if self.camera_store:
                self.camera_store.update_camera(self.cam.get("id"),
                                                people_counting=bool(on))
        except Exception:
            pass

    # -- صدا --

    def _open_listen(self):
        try:
            from listen_dialog import ListenDialog
            dlg = ListenDialog(dict(self.cam), parent=self)
            dlg.exec()
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"باز کردن شنیدن صدا ممکن نشد:\n{e}")

    def _open_talk(self):
        try:
            from talk_dialog import TalkDialog
            dlg = TalkDialog(dict(self.cam), parent=self)
            dlg.exec()
        except Exception as e:
            QMessageBox.warning(self, "خطا", f"باز کردن صحبت ممکن نشد:\n{e}")

    # -- رویدادها --

    def _refresh_events(self):
        self.event_list.clear()
        if not self.report_store:
            return
        try:
            cam_name = self.cam.get("name", "")
            rows = self.report_store.query(camera_name=cam_name, limit=50)
            for r in rows[:50]:
                ts = r[0] if len(r) > 0 else ""
                ev = r[1] if len(r) > 1 else ""
                self.event_list.addItem(f"{ts} — {ev}")
            if not rows:
                self.event_list.addItem("رویدادی ثبت نشده است.")
        except Exception:
            pass

    # -- بستن --

    def closeEvent(self, event):
        try:
            for t in self.live_tiles:
                t.stop()
        except Exception:
            pass
        try:
            if self.playback_widget:
                self.playback_widget.stop()
        except Exception:
            pass
        super().closeEvent(event)
