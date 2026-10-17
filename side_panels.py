# -*- coding: utf-8 -*-
"""ویجت‌های پنل‌های کناری صفحه‌ی اصلی (2.2.10) — single source of truth.

LeftPanelWidget: پنل چپ (اطلاعات ورود، اسکن شبکه، دوربین‌ها و NVRها، تست شبکه)
RightPanelWidget: پنل راست (پنل رویدادها، کنترل PTZ، هشدارهای حریق)

هر دو هم در پنجره‌ی اصلی و هم در پنجره‌های شناور «نما»ی اتاق مانیتورینگ
استفاده می‌شوند تا ظاهر و رفتار همه‌جا یکسان باشد.

host (معمولاً MainWindow) سرویس‌ها و کال‌بک‌ها را می‌دهد؛ منطق دکمه‌ها
داخل خود ویجت‌هاست.
"""

import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QAction
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QListWidget, QListWidgetItem, QTreeWidgetItem, QGroupBox, QSplitter,
    QSizePolicy, QMessageBox, QInputDialog, QMenu,
)

from scanner import NetworkScanThread, parse_ip_range
from device_detect import DeviceDetectThread
from add_camera_dialog import AddCameraDialog
from add_nvr_dialog import AddNVRDialog
from report_store import report_store


class LeftPanelWidget(QWidget):
    """پنل چپ: اسکن شبکه + لیست دوربین‌ها و NVRها."""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        # تردهای پس‌زمینه (قبلاً attrهای MainWindow بودند)
        self.network_scan_thread = None
        self.detect_thread = None
        self._detect_queue = None
        self._net_probe_thread = None

        from main import CameraTreeWidget

        left_panel = QVBoxLayout(self)
        left_panel.setContentsMargins(0, 0, 0, 0)

        # اطلاعات ورود برای اتصال به دوربین‌ها
        scan_cred_group = QGroupBox("اطلاعات ورود برای اتصال به دوربین‌ها")
        scan_cred_layout = QVBoxLayout()
        self.scan_user_input = QLineEdit("admin")
        self.scan_user_input.setPlaceholderText("نام کاربری")
        self.scan_pass_input = QLineEdit()
        self.scan_pass_input.setPlaceholderText("رمز عبور")
        self.scan_pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        scan_cred_layout.addWidget(self.scan_user_input)
        scan_cred_layout.addWidget(self.scan_pass_input)
        scan_cred_group.setLayout(scan_cred_layout)
        left_panel.addWidget(scan_cred_group)

        # اسکن شبکه
        scan_group = QGroupBox("اسکن شبکه (Network Scan)")
        scan_layout = QVBoxLayout()
        self.subnet_input = QLineEdit("192.168.1")
        self.subnet_input.setPlaceholderText(
            "رنج IP؛ مثلاً: 192.168.1 یا 192.168.1.20-192.168.1.80 یا 192.168.2.0/24")
        self.subnet_input.setToolTip(
            "رنج IP برای اسکن شبکه.\n"
            "قالب‌ها: «192.168.1» (کل ساب‌نت)، «192.168.1.20-192.168.1.80»،\n"
            "«192.168.1.20-80»، «192.168.2.0/24»؛ چند رنج با ویرگول جدا شود.")
        self.scan_btn = QPushButton("اسکن شبکه")
        self.scan_btn.clicked.connect(self.run_network_scan)
        self.scan_result_list = QListWidget()
        self.scan_result_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.scan_result_list.itemDoubleClicked.connect(self.on_scan_result_selected)
        self.add_selected_scan_btn = QPushButton("+ افزودن دستگاه‌های انتخاب‌شده")
        self.add_selected_scan_btn.clicked.connect(self.on_add_selected_scan_results)
        self.detect_status_label = QLabel("")
        self.detect_status_label.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        scan_layout.addWidget(self.subnet_input)
        scan_layout.addWidget(self.scan_btn)
        scan_layout.addWidget(self.scan_result_list)
        scan_layout.addWidget(self.add_selected_scan_btn)
        scan_layout.addWidget(self.detect_status_label)
        scan_group.setLayout(scan_layout)
        left_panel.addWidget(scan_group)

        # دوربین‌ها و NVRهای من
        cam_group = QGroupBox("دوربین‌ها و NVRهای من")
        cam_layout = QVBoxLayout()
        add_btn_row = QHBoxLayout()
        self.add_camera_btn = QPushButton("افزودن دوربین")
        self.add_camera_btn.clicked.connect(lambda: self.open_add_camera_dialog())
        self.add_nvr_btn = QPushButton("افزودن NVR")
        self.add_nvr_btn.clicked.connect(lambda: self.open_add_nvr_dialog())
        add_btn_row.addWidget(self.add_camera_btn, 1)
        add_btn_row.addWidget(self.add_nvr_btn, 1)
        self.net_probe_btn = QPushButton("📶 تست پایداری شبکه")
        self.net_probe_btn.setToolTip(
            "اسکن پهنای باند و تاخیر شبکه‌ی همه‌ی دوربین‌ها (چند ثانیه طول می‌کشد)")
        self.net_probe_btn.clicked.connect(self._on_network_probe_clicked)
        self.camera_list = CameraTreeWidget()
        self.camera_list.setHeaderHidden(True)
        self.camera_list.itemDoubleClicked.connect(self.on_camera_item_activated)
        self.camera_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.camera_list.customContextMenuRequested.connect(self.show_camera_context_menu)
        connect_hint = QLabel("برای پخش زنده روی یک دوربین/کانال دابل‌کلیک کنید. کلیک راست: ویرایش/حذف/بازخوانی کانال‌ها")
        connect_hint.setStyleSheet("color: #888; font-size: 10px;")
        connect_hint.setWordWrap(True)
        cam_layout.addLayout(add_btn_row)
        cam_layout.addWidget(self.camera_list)
        cam_layout.addWidget(connect_hint)
        cam_group.setLayout(cam_layout)
        left_panel.addWidget(cam_group)
        left_panel.addWidget(self.net_probe_btn)
        left_panel.addStretch()



    def _on_network_probe_clicked(self):
        """شروع اسکن پایداری شبکه‌ی همه‌ی دوربین‌ها در ترد پس‌زمینه (رابط
        قفل نمی‌شود)."""
        if getattr(self, "_net_probe_thread", None) is not None:
            return  # اسکن در حال اجراست
        cams = list(self._host.camera_store.cameras)
        if not cams:
            QMessageBox.information(self, "تست پایداری شبکه",
                                    "هنوز دوربینی ثبت نشده است.")
            return
        # نکته: اگر رمز دوربینی در حافظه نباشد (ذخیره‌ی امن خاموش و هنوز
        # وارد نشده)، نمونه‌گیری استریمش «نامشخص» می‌شود ولی تاخیر TCP
        # همچنان اندازه‌گیری می‌شود.
        from network_probe import NetworkProbeThread
        self._net_probe_thread = NetworkProbeThread(cams, self)
        self._net_probe_thread.probe_done.connect(self._on_probe_done)
        self._net_probe_thread.finished.connect(self._on_probe_finished)
        self.net_probe_btn.setEnabled(False)
        self.net_probe_btn.setText("📶 در حال اسکن...")
        self._net_probe_thread.start()

    def _make_camera_tree_item(self, cam):
        """(2.0.61-beta) ساخت آیتم درختی یک دوربین (مستقل یا کانال NVR) —
        بدون افزودن به والد؛ برای استفاده‌ی مشترک در حالت گروه‌دار/بدون‌گروه."""
        # رفع درخواست: در صورت شناسایی IP واقعی دوربین شبکه‌ای پشت این
        # کانال (متفاوت از IP خود NVR)، جلوی نام کانال هم نمایش داده می‌شود.
        cam_label = cam["name"]
        if cam.get("camera_ip"):
            cam_label += f"  ({cam['camera_ip']})"
        badge, style = self._net_badge(cam)
        cam_item = QTreeWidgetItem([cam_label + badge])
        if style:
            cam_item.setForeground(0, QColor(style[0]))
            cam_item.setToolTip(0, style[1])
        cam_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "camera", "id": cam["id"]})
        return cam_item

    def _make_nvr_tree_item(self, nvr):
        """(2.0.61-beta) ساخت آیتم درختی یک NVR همراه با کانال‌هایش."""
        channel_count = len(self._host.camera_store.cameras_for_nvr(nvr["id"]))
        nvr_item = QTreeWidgetItem([f"🖥 {nvr['name']}  ({nvr['ip']}) — {channel_count} کانال"])
        nvr_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "nvr", "id": nvr["id"]})
        for cam in self._host.camera_store.cameras_for_nvr(nvr["id"]):
            nvr_item.addChild(self._make_camera_tree_item(cam))
        nvr_item.setExpanded(True)
        return nvr_item

    def reload_camera_list(self):
        self.camera_list.clear()
        # پنل «هشدارهای حریق و دود» فقط وقتی دیده می‌شود که حداقل یک دوربین
        # تشخیص حریق فعال داشته باشد (با افزودن/حذف/ویرایش دوربین تازه می‌شود).
        try:
            self._host._refresh_fire_panel_visibility()
        except Exception:
            pass
        # broadcast به بقیه‌ی پنجره‌ها
        try:
            self._host._reload_all_camera_lists(exclude=self)
        except Exception:
            pass

        def _group_of(x):
            return (x.get("group") or "").strip()

        def _add_item(item, parent):
            if parent is None:
                self.camera_list.addTopLevelItem(item)
            else:
                parent.addChild(item)

        # (2.0.61-beta) گروه‌بندی: هر گروه یک پوشه‌ی 📁 در سطح بالا می‌گیرد؛
        # کانال‌های NVR مثل قبل زیر NVR خودشان می‌مانند. آیتم‌های بدون گروه
        # دقیقاً مثل قبل در سطح بالا نمایش داده می‌شوند.
        groups = self._host.camera_store.get_groups()
        for g in groups:
            folder = QTreeWidgetItem([f"📁 {g}"])
            folder.setData(0, Qt.ItemDataRole.UserRole, {"type": "group", "name": g})
            self.camera_list.addTopLevelItem(folder)
            for nvr in self._host.camera_store.nvrs:
                if _group_of(nvr) == g:
                    _add_item(self._make_nvr_tree_item(nvr), folder)
            for cam in self._host.camera_store.standalone_cameras():
                if _group_of(cam) == g:
                    _add_item(self._make_camera_tree_item(cam), folder)
            folder.setExpanded(True)

        # NVRهای بدون گروه — مثل قبل در سطح بالا.
        for nvr in self._host.camera_store.nvrs:
            if not _group_of(nvr):
                _add_item(self._make_nvr_tree_item(nvr), None)

        # دوربین‌های مستقل (بدون NVR) و بدون گروه — مثل قبل در سطح بالا.
        for cam in self._host.camera_store.standalone_cameras():
            if not _group_of(cam):
                _add_item(self._make_camera_tree_item(cam), None)

    def _scan_credentials(self):
        """رفع درخواست: نام‌کاربری/رمز کادر بالای پنل اسکن شبکه را برمی‌گرداند
        (این مقادیر هرگز روی دیسک ذخیره نمی‌شوند - فقط در حافظه‌ی همین کادر)."""
        user = self.scan_user_input.text().strip() or "admin"
        pwd = self.scan_pass_input.text()
        return user, pwd

    def open_add_camera_dialog(self, prefill_ip=None, detected_path=None, detected_full_url=None,
                                prefill_user=None, prefill_pass=None):
        # گیت سهمیه‌ی «تعداد کل دوربین‌ها»
        try:
            from admin_quota import check_quota, quota_denied_message
            allowed, used, quota = check_quota("cameras", self._host.camera_store)
            if not allowed:
                QMessageBox.warning(
                    self, "قابلیت غیرفعال" if quota == -1 else "سهمیه تکمیل است",
                    quota_denied_message("cameras", used, quota))
                return
        except Exception:
            pass
        dialog = AddCameraDialog(self)
        if prefill_ip:
            dialog.ip_input.setText(prefill_ip)
        if prefill_user is not None:
            dialog.user_input.setText(prefill_user)
        if prefill_pass is not None:
            dialog.pass_input.setText(prefill_pass)
        if detected_path is not None or detected_full_url is not None:
            dialog.set_detected_stream(path=detected_path, full_url=detected_full_url)
        if dialog.exec():
            data = dialog.get_camera_data()
            cam = self._host.camera_store.add_camera(
                data["name"], data["ip"], data["port"], data["user"], data["pass"], data["path"],
                full_url=data.get("full_url"),
                floor_id=data.get("floor_id", ""),
                group=data.get("group", ""),
                bitrate_kbps=data.get("bitrate_kbps", 0),
            )
            self.reload_camera_list()
            # (2.0.68-beta) شناسایی خودکار PTZ/لنز موتورایزد در پس‌زمینه
            self._host._autodetect_ptz_async(cam["id"])
            # رفع درخواست: دوربین تازه‌اضافه‌شده اتوماتیک به پنجره‌ی نمایش اضافه شود.
            self._host.open_live_view(cam)

    def open_add_nvr_dialog(self, prefill_ip=None, detected_brand=None, detected_onvif_port=None,
                             prefill_user=None, prefill_pass=None):
        dialog = AddNVRDialog(self)
        if prefill_ip:
            dialog.ip_input.setText(prefill_ip)
        if prefill_user is not None:
            dialog.user_input.setText(prefill_user)
        if prefill_pass is not None:
            dialog.pass_input.setText(prefill_pass)
        if detected_brand:
            dialog.set_detected_brand(brand=detected_brand, onvif_port=detected_onvif_port)
        if dialog.exec():
            data = dialog.get_nvr_data()
            # گیت سهمیه‌ی «تعداد کل دوربین‌ها» برای کانال‌های انتخاب‌شده‌ی NVR
            try:
                from admin_quota import check_quota, quota_denied_message
                n_channels = len(dialog.get_selected_channels())
                allowed, used, quota = check_quota("cameras", self._host.camera_store,
                                                   count=n_channels)
                if not allowed:
                    QMessageBox.warning(
                        self,
                        "قابلیت غیرفعال" if quota == -1 else "سهمیه تکمیل است",
                        quota_denied_message("cameras", used, quota)
                        + f"\nکانال‌های انتخاب‌شده: {n_channels}")
                    return
            except Exception:
                pass
            nvr = self._host.camera_store.add_nvr(
                name=data["name"], ip=data["ip"], rtsp_port=data["rtsp_port"],
                onvif_port=data["onvif_port"], user=data["user"],
                pwd=data["pass"], brand=data["brand"],
                camera_brand=data["camera_brand"],
                group=data.get("group", ""),
            )
            added_cams = [
                self._add_channel_from_entry(nvr, entry, default_name)
                for entry, default_name in dialog.get_selected_channels()
            ]
            self.reload_camera_list()
            # رفع درخواست: هر کانال تازه‌اضافه‌شده اتوماتیک به پنجره‌ی نمایش اضافه شود.
            not_shown = sum(1 for cam in added_cams if not self._host._auto_display_camera(cam))
            msg = f"NVR «{nvr['name']}» با {len(added_cams)} کانال اضافه شد."
            if not_shown:
                msg += (
                    f"\n{not_shown} کانال به دلیل پر بودن شبکه‌ی نمایش به‌صورت خودکار باز "
                    "نشدند؛ برای باز کردن آن‌ها، تعداد نمایش هم‌زمان را افزایش دهید یا "
                    "روی آن‌ها در لیست دابل‌کلیک کنید."
                )
            QMessageBox.information(self, "NVR اضافه شد", msg)

    def rescan_nvr(self, nvr_id):
        nvr = self._host.camera_store.get_nvr(nvr_id)
        if not nvr:
            return
        dialog = AddNVRDialog(self)
        dialog.setWindowTitle(f"بازخوانی کانال‌های «{nvr['name']}»")
        dialog.name_input.setText(nvr["name"])
        dialog.ip_input.setText(nvr["ip"])
        dialog.rtsp_port_input.setText(str(nvr.get("rtsp_port", "554")))
        dialog.onvif_port_input.setText(str(nvr.get("onvif_port", "") or ""))
        dialog.user_input.setText(nvr.get("user", ""))
        dialog.pass_input.setText(nvr.get("pass", ""))
        idx = dialog.brand_combo.findData(nvr.get("brand", "auto"))
        if idx >= 0:
            dialog.brand_combo.setCurrentIndex(idx)
        cam_brand_idx = dialog.camera_brand_combo.findData(nvr.get("camera_brand", "auto"))
        if cam_brand_idx >= 0:
            dialog.camera_brand_combo.setCurrentIndex(cam_brand_idx)

        if dialog.exec():
            data = dialog.get_nvr_data()
            self._host.camera_store.update_nvr(nvr_id, **data)
            existing_channels = {c.get("channel") for c in self._host.camera_store.cameras_for_nvr(nvr_id)}
            added_cams = []
            for entry, default_name in dialog.get_selected_channels():
                if entry["channel"] in existing_channels:
                    continue  # این کانال قبلاً اضافه شده است
                added_cams.append(self._add_channel_from_entry(nvr, entry, default_name))
            self.reload_camera_list()
            # رفع درخواست: کانال‌های تازه‌کشف‌شده هم اتوماتیک به پنجره‌ی نمایش اضافه شوند.
            for cam in added_cams:
                self._host._auto_display_camera(cam)
            QMessageBox.information(self, "بازخوانی کامل شد", f"{len(added_cams)} کانال جدید اضافه شد.")

    def show_camera_context_menu(self, pos):
        item = self.camera_list.itemAt(pos)
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        menu = QMenu(self)
        if data["type"] == "camera":
            edit_action = QAction("ویرایش", self)
            edit_action.triggered.connect(lambda: self._host.edit_camera(data["id"]))
            # (2.0.61-beta) گروه‌بندی
            group_action = QAction("📁 انتقال به گروه…", self)
            group_action.triggered.connect(lambda: self._host.move_camera_to_group(data["id"]))
            delete_action = QAction("حذف", self)
            delete_action.triggered.connect(lambda: self._host.delete_camera(data["id"]))
            # (2.0.68-beta) کنترل PTZ/لنز موتورایزد
            ptz_action = QAction("🎮 کنترل PTZ", self)
            ptz_action.triggered.connect(lambda: self._host.open_ptz_control(data["id"]))
            # (2.0.82-beta) شنیدن صدای دوربین (ترک صوتی RTSP)
            listen_action = QAction("🎧 شنیدن صدای دوربین", self)
            listen_action.triggered.connect(lambda: self._host.open_listen_dialog(data["id"]))
            menu.addAction(edit_action)
            menu.addAction(ptz_action)
            menu.addAction(listen_action)
            menu.addAction(group_action)
            menu.addAction(delete_action)
        elif data["type"] == "group":
            # (2.0.61-beta) گروه‌بندی: مدیریت پوشه‌ی گروه
            rename_action = QAction("✏ تغییر نام گروه", self)
            rename_action.triggered.connect(lambda: self._host.rename_camera_group(data["name"]))
            delete_group_action = QAction("🗑 حذف گروه", self)
            delete_group_action.triggered.connect(lambda: self._host.delete_camera_group(data["name"]))
            menu.addAction(rename_action)
            menu.addAction(delete_group_action)
        else:  # nvr
            rescan_action = QAction("بازخوانی کانال‌ها", self)
            rescan_action.triggered.connect(lambda: self.rescan_nvr(data["id"]))
            # رفع درخواست: برای NVRهایی که سرویس RTSP‌شان مشکل فریمور دارد
            # (پخش زنده و بازخوانی کانال از طریق RTSP/ONVIF جواب نمی‌دهد)،
            # یک راه جایگزین: باز کردن پنل وب واقعی خود NVR داخل برنامه (با
            # موتور Chromium) که هم پخش زنده در آن سالم کار می‌کند و هم
            # می‌توان لیست کانال‌های واقعی را از همان‌جا گرفت.
            webview_action = QAction("باز کردن پنل وب NVR (برای دستگاه‌های با RTSP خراب)", self)
            webview_action.triggered.connect(lambda: self._host.open_nvr_webview(data["id"]))
            # رفع درخواست: بررسی وضعیت هارددیسک NVR و جست‌وجوی بازه‌های
            # زمانی‌ای که واقعاً روی هارد خودِ NVR ضبط شده - از طریق همان
            # API وب رسمی سازنده (رجوع کنید به nvr_storage_api.py).
            storage_action = QAction("🗄 هارد و ضبط‌های NVR", self)
            storage_action.triggered.connect(lambda: self._host.open_nvr_storage(data["id"]))
            # (2.0.52-beta) قالب دلخواه آدرس پخش بازبینی: برای NVRهایی که
            # قالب استاندارد Hikvision/Dahua را پشتیبانی نمی‌کنند.
            playback_tpl_action = QAction("⚙ قالب آدرس پخش بازبینی", self)
            playback_tpl_action.triggered.connect(
                lambda: self._host.edit_nvr_playback_template(data["id"]))
            delete_action = QAction("حذف NVR و همه کانال‌ها", self)
            delete_action.triggered.connect(lambda: self._host.delete_nvr(data["id"]))
            # (2.0.61-beta) گروه‌بندی
            nvr_group_action = QAction("📁 انتقال به گروه…", self)
            nvr_group_action.triggered.connect(lambda: self._host.move_nvr_to_group(data["id"]))
            menu.addAction(rescan_action)
            menu.addAction(webview_action)
            menu.addAction(storage_action)
            menu.addAction(playback_tpl_action)
            menu.addAction(nvr_group_action)
            menu.addAction(delete_action)
        menu.exec(self.camera_list.mapToGlobal(pos))

    def on_camera_item_activated(self, item, column=0):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or data["type"] != "camera":
            return
        cam = self._host.camera_store.get_camera(data["id"])
        if cam:
            self._host.open_live_view(cam)

    def on_scan_result_selected(self, item):
        text = item.text()
        if " " not in text:
            return
        ip = text.split(" ")[0]
        # رفع باگ: ردیف‌های موقت («در حال اسکن...» / «هیچ دستگاهی یافت نشد.»)
        # IP واقعی ندارند؛ فقط IPهایی که واقعاً در اسکن پیدا شده‌اند پردازش
        # می‌شوند.
        if ip not in self._scan_ports_by_ip:
            return

        if self.detect_thread is not None and self.detect_thread.isRunning():
            return  # یک تشخیص در حال اجراست؛ منتظر پایان آن بمانیم.

        # دابل‌کلیک یعنی فقط همین یک دستگاه (صف خالی است).
        self._detect_queue = []
        self._start_device_detect(ip)

    def on_add_selected_scan_results(self):
        """رفع درخواست: چند نتیجه‌ی اسکن هم‌زمان انتخاب و پشت‌سرهم اضافه شوند.
        چون هر تشخیص (و در ادامه‌ی آن، دیالوگ افزودن دوربین/NVR) نیاز به
        تعامل کاربر دارد، دستگاه‌ها یکی‌یکی (نه هم‌زمان) پردازش می‌شوند: بعد
        از بسته‌شدن دیالوگ مربوط به هر دستگاه، خودکار سراغ دستگاه بعدی در صف
        می‌رود."""
        if self.detect_thread is not None and self.detect_thread.isRunning():
            return

        ips = []
        # رفع درخواست: انتخاب چندتایی دیگر با Ctrl/Shift نیست؛ آیتم‌هایی که
        # چک‌باکس‌شان تیک خورده (Qt.CheckState.Checked) به‌عنوان انتخاب‌شده
        # در نظر گرفته می‌شوند.
        for i in range(self.scan_result_list.count()):
            item = self.scan_result_list.item(i)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            text = item.text()
            if " " not in text:
                continue
            ip = text.split(" ")[0]
            if ip not in self._scan_ports_by_ip:
                continue
            if ip not in ips:
                ips.append(ip)

        if not ips:
            QMessageBox.information(
                self, "موردی انتخاب نشده",
                "ابتدا چک‌باکس کنار یک یا چند دستگاه را از لیست نتایج اسکن تیک بزنید."
            )
            return

        self._detect_queue = ips[1:]
        self._start_device_detect(ips[0])

    def run_network_scan(self):
        # رفع باگ: قبلاً scan_subnet مستقیماً روی ترد UI اجرا می‌شد و کل برنامه
        # را برای طول مدت اسکن (چند ثانیه تا چند ده ثانیه) کاملاً فریز می‌کرد؛
        # حالا در یک QThread جداگانه (NetworkScanThread) اجرا می‌شود.
        if self.network_scan_thread is not None and self.network_scan_thread.isRunning():
            return

        subnet = self.subnet_input.text().strip()
        # (2.0.31-beta) اعتبارسنجی رنج IP قبل از شروع اسکن؛ قالب‌های مجاز:
        # «192.168.1»، «192.168.1.20-192.168.1.80»، «192.168.1.20-80»،
        # «192.168.2.0/24» و ترکیب چند رنج با ویرگول.
        try:
            ip_count = len(parse_ip_range(subnet))
        except ValueError as ex:
            QMessageBox.warning(self, "رنج IP نامعتبر", str(ex))
            return
        self.scan_result_list.clear()
        self.scan_result_list.addItem(f"در حال اسکن {ip_count} آدرس...")
        # رفع باگ: حین اسکن، لیست نتایج غیرفعال می‌شود تا دابل‌کلیک روی ردیف
        # موقت «در حال اسکن...» (که IP واقعی نیست) تشخیص دستگاه را با IP
        # نامعتبر شروع نکند.
        self.scan_result_list.setEnabled(False)
        self.scan_btn.setEnabled(False)

        self.network_scan_thread = NetworkScanThread(subnet, self)
        self.network_scan_thread.finished_signal.connect(self._on_network_scan_finished)
        self.network_scan_thread.start()

    def _on_network_scan_finished(self, devices):
        self.scan_btn.setEnabled(True)
        self.scan_result_list.setEnabled(True)
        self.scan_result_list.clear()
        self._scan_ports_by_ip = {}
        if not devices:
            self.scan_result_list.addItem("هیچ دستگاهی یافت نشد.")
            return

        for dev in devices:
            self._scan_ports_by_ip[dev["ip"]] = dev["ports"]
            ports_str = ",".join(map(str, dev["ports"]))
            item = QListWidgetItem(f"{dev['ip']} (پورت‌ها: {ports_str})")
            # رفع درخواست: به‌جای انتخاب با Ctrl/Shift، کنار هر دستگاه یک
            # چک‌باکس قرار می‌گیرد تا کاربر با تیک زدن، دستگاه‌های موردنظر
            # برای اتصال هم‌زمان را مشخص کند.
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.scan_result_list.addItem(item)



    def _net_badge(self, cam):
        q = (cam or {}).get("net_quality", "")
        kbps = (cam or {}).get("net_kbps")
        rtt = (cam or {}).get("net_rtt_ms")
        if not q:
            return "", None
        icon = {"خوب": "📶", "متوسط": "📶", "ضعیف": "⚠️", "قطع": "⛔",
                "نامشخص": "❓"}.get(q, "📶")
        color = {"خوب": "#2ecc71", "متوسط": "#f1c40f", "ضعیف": "#e67e22",
                 "قطع": "#e74c3c", "نامشخص": "#95a5a6"}.get(q)
        tip = "آخرین اسکن شبکه: %s" % q
        if isinstance(kbps, (int, float)):
            tip += f" (~{kbps:.0f} کیلوبیت/ثانیه)"
        if isinstance(rtt, (int, float)):
            tip += f" (تاخیر {rtt:.0f}ms)"
        return f"  {icon}{q}", (color, tip)

    def _on_probe_done(self, cam_id, res):
        cam = self._host.camera_store.get_camera(cam_id)
        if cam is None or not isinstance(res, dict):
            return
        cam["net_rtt_ms"] = res.get("rtt_ms")
        kbps = res.get("kbps")
        cam["net_kbps"] = round(kbps, 1) if isinstance(kbps, (int, float)) else None
        cam["net_quality"] = res.get("quality") or "نامشخص"
        cam["net_checked_at"] = res.get("checked_at")

    def _on_probe_finished(self):
        try:
            self._host.camera_store.save()
        except Exception:
            pass
        self._net_probe_thread = None
        self.net_probe_btn.setEnabled(True)
        self.net_probe_btn.setText("📶 تست پایداری شبکه")
        self.reload_camera_list()
        try:
            quals = [c.get("net_quality", "?") for c in self._host.camera_store.cameras]
            summary = ", ".join(f"{q}: {quals.count(q)}" for q in
                                ("خوب", "متوسط", "ضعیف", "قطع", "نامشخص")
                                if quals.count(q))
            QMessageBox.information(self, "تست پایداری شبکه",
                                    "اسکن تمام شد.\n" + (summary or "نتیجه‌ای ثبت نشد."))
        except Exception:
            pass

    def _add_channel_from_entry(self, nvr, entry, default_name):
        camera_ip = entry.get("camera_ip") or ""
        if entry["is_full_url"]:
            # ONVIF: خودِ URL کشف‌شده معمولاً از قبل مستقیماً به IP دوربین
            # اشاره می‌کند (رجوع کنید به nvr_scanner._extract_camera_ip_from_uri).
            return self._host.camera_store.add_channel_camera(
                nvr, entry["channel"], default_name, path="", full_url=entry["path_or_url"],
                camera_ip=camera_ip,
            )
        if camera_ip and entry.get("direct"):
            # رفع درخواست: این کانال با یک مسیر واقعاً تست‌شده روی خودِ
            # دوربین تایید شده (نه پروکسی NVR که خطا می‌داد)؛ دقیقاً مثل
            # افزودن یک دوربین تکی، مستقیماً به همان IP/پورت ۵۵۴ دوربین وصل
            # می‌شویم - در حالی که کانال همچنان زیر همین NVR در لیست می‌ماند.
            return self._host.camera_store.add_channel_camera(
                nvr, entry["channel"], default_name, path=entry["path_or_url"],
                camera_ip=camera_ip, connect_ip=camera_ip, connect_port="554",
            )
        return self._host.camera_store.add_channel_camera(
            nvr, entry["channel"], default_name, path=entry["path_or_url"], camera_ip=camera_ip,
        )

    def _start_device_detect(self, ip):
        # رفع درخواست: دیگر از کاربر «دوربین تکی یا NVR؟» پرسیده نمی‌شود؛
        # DeviceDetectThread با یک اتصال آزمایشی (ONVIF یا تست کانال‌ها)
        # خودش نوع دستگاه را تشخیص می‌دهد - رجوع کنید به device_detect.py.
        self._detect_ip = ip
        self.scan_result_list.setEnabled(False)
        self.add_selected_scan_btn.setEnabled(False)
        self.detect_status_label.setText(f"در حال تشخیص نوع دستگاه {ip}...")

        # رفع درخواست: تشخیص نوع دستگاه (و در ادامه، اتصال به دوربین/NVR) با
        # نام‌کاربری/رمز کادر بالای پنل اسکن شبکه انجام می‌شود، نه مقدار ثابت
        # admin/بدون‌رمز.
        scan_user, scan_pass = self._scan_credentials()
        self.detect_thread = DeviceDetectThread(
            ip=ip,
            open_ports=self._scan_ports_by_ip.get(ip, []),
            rtsp_port="554",
            user=scan_user,
            pwd=scan_pass,
            parent=self,
        )
        self.detect_thread.progress_signal.connect(self.detect_status_label.setText)
        self.detect_thread.detected_signal.connect(self._on_device_detected)
        self.detect_thread.failed_signal.connect(self._on_device_detect_failed)
        self.detect_thread.start()

    def _reset_detect_ui(self):
        self.scan_result_list.setEnabled(True)
        self.detect_status_label.setText("")

    def _advance_detect_queue(self):
        """بعد از بسته‌شدن دیالوگ دستگاه فعلی، اگر مورد دیگری در صفِ انتخاب
        چندتایی باقی مانده، تشخیص آن را شروع می‌کند."""
        if self._detect_queue:
            next_ip = self._detect_queue.pop(0)
            self._start_device_detect(next_ip)
        else:
            self.add_selected_scan_btn.setEnabled(True)

    def _on_device_detected(self, kind, payload):
        ip = self._detect_ip
        self._reset_detect_ui()
        scan_user, scan_pass = self._scan_credentials()
        if kind == "nvr":
            self.open_add_nvr_dialog(
                prefill_ip=ip,
                detected_brand=payload.get("brand"),
                detected_onvif_port=payload.get("onvif_port"),
                prefill_user=scan_user,
                prefill_pass=scan_pass,
            )
        else:
            self.open_add_camera_dialog(
                prefill_ip=ip,
                detected_path=payload.get("path"),
                detected_full_url=payload.get("full_url"),
                prefill_user=scan_user,
                prefill_pass=scan_pass,
            )
        self._advance_detect_queue()

    def _on_device_detect_failed(self, msg):
        ip = self._detect_ip
        self._reset_detect_ui()

        # تشخیص خودکار با نام کاربری/رمز پیش‌فرض (admin/بدون رمز) ممکن است روی
        # دستگاه‌هایی با اطلاعات ورود سفارشی شکست بخورد؛ در این حالت کاربر
        # می‌تواند به‌صورت دستی و با وارد کردن رمز درست، نوع دستگاه را انتخاب کند.
        box = QMessageBox(self)
        box.setWindowTitle("تشخیص خودکار ناموفق بود")
        box.setText(f"{msg}\n\nنوع دستگاه {ip} را به‌صورت دستی مشخص کنید:")
        camera_btn = box.addButton("دوربین تکی", QMessageBox.ButtonRole.AcceptRole)
        nvr_btn = box.addButton("NVR (چند کاناله)", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("انصراف", QMessageBox.ButtonRole.RejectRole)
        box.exec()

        scan_user, scan_pass = self._scan_credentials()
        if box.clickedButton() == camera_btn:
            self.open_add_camera_dialog(prefill_ip=ip, prefill_user=scan_user, prefill_pass=scan_pass)
        elif box.clickedButton() == nvr_btn:
            self.open_add_nvr_dialog(prefill_ip=ip, prefill_user=scan_user, prefill_pass=scan_pass)
        self._advance_detect_queue()
class RightPanelWidget(QWidget):
    """پنل راست: رویدادها + PTZ + حریق."""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host

        from PyQt6.QtCore import QSize
        from ptz_panel import PTZPanel

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        # پنل رویدادها
        events_panel_group = QGroupBox("🚨 پنل رویدادها")
        events_panel_layout = QVBoxLayout()
        gallery_btn = QPushButton("🖼 دیدن تصاویر")
        gallery_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        gallery_btn.clicked.connect(self.open_face_gallery)
        events_panel_layout.addWidget(gallery_btn)
        self.events_panel_list = QListWidget()
        self.events_panel_list.setIconSize(QSize(64, 64))
        self.events_panel_list.setWordWrap(True)
        self.events_panel_list.itemDoubleClicked.connect(
            self._on_event_item_double_clicked)
        events_panel_layout.addWidget(self.events_panel_list)
        events_panel_group.setLayout(events_panel_layout)
        events_panel_group.setMinimumHeight(120)

        # پنل PTZ (مخفی پیش‌فرض)
        self.ptz_panel_group = QGroupBox("🎮 کنترل PTZ")
        _ptz_lay = QVBoxLayout()
        self.ptz_panel = PTZPanel()
        _ptz_lay.addWidget(self.ptz_panel)
        self.ptz_panel_group.setLayout(_ptz_lay)
        self.ptz_panel_group.setMinimumHeight(150)
        self.ptz_panel_group.setVisible(False)

        # پنل حریق
        fire_panel_group = QGroupBox("🔥 هشدارهای حریق و دود")
        self.fire_panel_group = fire_panel_group
        fire_panel_layout = QVBoxLayout()
        self.fire_panel_list = QListWidget()
        self.fire_panel_list.setWordWrap(True)
        fire_panel_layout.addWidget(self.fire_panel_list)
        fire_panel_group.setLayout(fire_panel_layout)
        fire_panel_group.setMinimumHeight(120)

        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter.addWidget(events_panel_group)
        self.right_splitter.addWidget(self.ptz_panel_group)
        self.right_splitter.addWidget(fire_panel_group)
        self.right_splitter.setStretchFactor(0, 3)
        self.right_splitter.setStretchFactor(1, 2)
        self.right_splitter.setStretchFactor(2, 2)
        lay.addWidget(self.right_splitter)

    def _refresh_fire_panel_visibility(self):
        """رفع درخواست: پنل «هشدارهای حریق و دود» در صفحه‌ی اصلی فقط وقتی
        دیده می‌شود که حداقل یک دوربین «تشخیص حریق» فعال داشته باشد؛ با
        فعال/غیرفعال شدن تیک حریق هر دوربین یا افزودن/حذف دوربین، پنل خودکار
        ظاهر/مخفی می‌شود."""
        try:
            store = self._host.camera_store
            cams = list(getattr(store, "cameras", None) or [])
            any_fire = any(bool(c.get("fire_detection")) for c in cams)
        except Exception:
            any_fire = False
        panel = getattr(self, "fire_panel_group", None)
        if panel is None:
            return
        want_visible = bool(any_fire)
        # نکته: isVisible() زنجیره‌ی والدها را هم چک می‌کند و قبل از
        # show() شدن پنجره همیشه False است؛ برای مقایسه‌ی «وضعیتِ خواسته‌شده»
        # باید پرچم صریح خودِ ویجت (isHidden) را خواند، وگرنه در startup
        # هیچ‌وقت مخفی نمی‌شود.
        if panel.isHidden() == (not want_visible):
            return
        # (2.0.12-beta: مثل action #207 - فقط مخفی/نمایان ساده، بدون
        # ذخیره/بازیابی اندازه‌ی splitter.)
        panel.setVisible(want_visible)

    def _on_event_item_double_clicked(self, item):
        """(1.0.0) دبل‌کلیک روی آیتم پنل رویدادها: نمایش تصویر با جزئیات."""
        try:
            # داده‌ی رویداد را از آیتم می‌خوانیم (اگر ذخیره شده باشد)
            ev = item.data(Qt.ItemDataRole.UserRole)
            if not ev:
                return
            img_path = (ev.get("image_path") or "") if isinstance(ev, dict) else ""
            if not img_path or not os.path.isfile(img_path):
                return
            # پنجره‌ی نمایش تصویر با جزئیات
            from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel
            from PyQt6.QtGui import QPixmap
            from PyQt6.QtCore import Qt as _Qt
            dlg = QDialog(self)
            dlg.setWindowTitle("تصویر رویداد")
            dlg.setLayoutDirection(_Qt.LayoutDirection.RightToLeft)
            dlg.resize(600, 550)
            lay = QVBoxLayout(dlg)
            # تصویر
            pix = QPixmap(img_path)
            img_lbl = QLabel()
            img_lbl.setAlignment(_Qt.AlignmentFlag.AlignCenter)
            if not pix.isNull():
                img_lbl.setPixmap(pix.scaled(
                    560, 400, _Qt.AspectRatioMode.KeepAspectRatio,
                    _Qt.TransformationMode.SmoothTransformation))
            lay.addWidget(img_lbl)
            # جزئیات
            if isinstance(ev, dict):
                from gallery_dialog import _fa_event_type
                lines = []
                lines.append(f"<b>نوع رویداد:</b> {_fa_event_type(ev.get('event_type'))}")
                if ev.get("camera_name"):
                    lines.append(f"<b>دوربین:</b> {ev.get('camera_name')}")
                if ev.get("ts"):
                    lines.append(f"<b>زمان:</b> {ev.get('ts')}")
                if ev.get("person_name"):
                    lines.append(f"<b>شخص:</b> {ev.get('person_name')}")
                if ev.get("detail"):
                    lines.append(f"<b>توضیح:</b> {ev.get('detail')}")
                det = QLabel("<br>".join(lines))
                det.setWordWrap(True)
                lay.addWidget(det)
            dlg.exec()
        except Exception:
            pass

    def open_face_gallery(self):
        """(1.0.0) دکمه‌ی «🖼 دیدن تصاویر» حالا پنجره‌ی گالری را باز می‌کند:
        مرتب‌سازی بر اساس رویداد/تاریخ/ساعت، نمایش جزئیات، و ذخیره با
        کیفیت بالا."""
        try:
            from gallery_dialog import GalleryDialog
            dlg = GalleryDialog(report_store, parent=self)
            dlg.exec()
        except Exception as e:
            try:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "خطا",
                                    f"باز کردن گالری ناموفق بود:\n{e}")
            except Exception:
                pass
