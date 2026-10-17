# -*- coding: utf-8 -*-
"""ویجت صفحه‌ی مرکزی دوربین‌ها (2.2.9) — single source of truth.

نوار ابزار شبکه‌ی دوربین‌ها + سطر رسم محدوده + گرید دوربین‌ها (CameraGridWidget)
که هم در پنجره‌ی اصلی و هم در پنجره‌های شناور «نما»ی اتاق مانیتورینگ استفاده
می‌شود تا ظاهر، چیدمان و رفتار همه‌ی دکمه‌ها همه‌جا یکسان باشد.

host (معمولاً MainWindow) باید این‌ها را بدهد:
- face_engine، camera_store
- on_face_event، on_region_alert، on_fire_event، on_plate_event،
  on_person_event، on_camera_dropped_on_grid، _save_audio_creds
- toggle_sidebar، _refresh_person_detector_status، _on_video_loss
  (منطق دکمه‌های تولبار داخل خود ویجت است)"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QScrollArea, QMessageBox, QInputDialog,
)


class CameraPageWidget(QWidget):
    """صفحه‌ی مرکزی دوربین‌ها: تولبار + سطر رسم + گرید."""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._floor_detect_thread = None

        # از main.py ایمپورت می‌شود تا چرخه‌ی ایمپورت پیش نیاید
        from main import (CameraGridWidget, _NoMinWidthRow,
                          _get_floor_detect_thread, RegionManagerDialog)

        page_lay = QVBoxLayout(self)
        page_lay.setContentsMargins(0, 0, 0, 0)

        grid_toolbar = QHBoxLayout()

        # دکمه‌ی نمایش/مخفی‌کردن پنل کناری سمت چپ
        self.sidebar_toggle_btn = QPushButton("☰")
        self.sidebar_toggle_btn.setFixedWidth(32)
        self.sidebar_toggle_btn.setToolTip("نمایش / مخفی کردن پنل کناری")
        self.sidebar_toggle_btn.clicked.connect(host.toggle_sidebar)
        grid_toolbar.addWidget(self.sidebar_toggle_btn)

        grid_toolbar_label = QLabel("تعداد نمایش هم‌زمان دوربین‌ها:")
        grid_toolbar_label.setStyleSheet("font-size: 11px;")
        self.grid_size_combo = QComboBox()
        for n in (1, 4, 9, 16, 32, 64):
            self.grid_size_combo.addItem(str(n), n)
        self.grid_size_combo.setCurrentIndex(1)  # پیش‌فرض: 4
        self.grid_size_combo.currentIndexChanged.connect(
            self._on_grid_size_changed)
        grid_toolbar.addWidget(grid_toolbar_label)
        grid_toolbar.addWidget(self.grid_size_combo)

        # شمارش افراد (تک‌دکمه برای همه‌ی دوربین‌ها)
        self.people_toggle_btn = QPushButton("👥 شمارش افراد")
        self.people_toggle_btn.setCheckable(True)
        self.people_toggle_btn.setToolTip(
            "روشن/خاموش کردن شمارش افراد Real Time برای تمام دوربین‌های باز")
        self.people_toggle_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
            "QPushButton:checked{background:#e67e22; color:#fff;}"
        )
        self.people_toggle_btn.toggled.connect(self._on_people_toggle_all)
        grid_toolbar.addWidget(self.people_toggle_btn)

        # رسم محدوده هشدار
        self.draw_line_btn = QPushButton("🖊 رسم محدوده هشدار")
        self.draw_line_btn.setCheckable(True)
        self.draw_line_btn.setToolTip(
            "۱) یک دوربین را از شبکه انتخاب کنید (کلیک روی خانه‌اش)\n"
            "۲) این دکمه را بزنید\n"
            "۳) روی تصویر همان دوربین، به‌ترتیب روی نقاط زمین/اتاق کلیک کنید "
            "(برنامه نقاط را به هم وصل می‌کند)\n"
            "۴) برای بستن محدوده: روی نقطه‌ی اول (دایره‌ی بزرگ‌تر) کلیک کنید، "
            "یا دابل‌کلیک کنید (حداقل ۳ نقطه لازم است)\n"
            "کلیک راست: لغو رسمِ در حال انجام"
        )
        self.draw_line_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
            "QPushButton:checked{background:#9b59b6; color:#fff;}"
        )
        self.draw_line_btn.toggled.connect(self._on_draw_line_toggled)
        grid_toolbar.addWidget(self.draw_line_btn)

        # مدیریت محدوده‌ها
        self.manage_regions_btn = QPushButton("📋 مدیریت محدوده‌ها")
        self.manage_regions_btn.setEnabled(False)
        self.manage_regions_btn.setToolTip(
            "مشاهده و حذف محدوده‌های هشدار تعریف‌شده برای دوربین انتخاب‌شده.")
        self.manage_regions_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
            "QPushButton:enabled{background:#2980b9; color:#fff;}"
        )
        self.manage_regions_btn.clicked.connect(self._on_manage_regions_clicked)
        grid_toolbar.addWidget(self.manage_regions_btn)

        grid_toolbar.addStretch()
        page_lay.addLayout(grid_toolbar)

        # سطر دوم: دکمه‌های حالت رسم (مخفی پیش‌فرض)
        self.region_draw_row = _NoMinWidthRow()
        region_draw_layout = QHBoxLayout()
        region_draw_layout.setContentsMargins(0, 0, 0, 0)
        self.region_draw_row.setLayout(region_draw_layout)
        self.region_draw_row.setVisible(False)

        self.auto_region_btn = QPushButton("🌐 تشخیص خودکار محدوده (کل تصویر)")
        self.auto_region_btn.setToolTip(
            "۱) یک دوربین را از شبکه انتخاب کنید (کلیک روی خانه‌اش)\n"
            "۲) این دکمه را بزنید - کل تصویر دوربین به‌عنوان محدوده در نظر گرفته می‌شود\n"
            "۳) در صورت نیاز، گوشه‌های آن را با ماوس بکشید تا شکل/اندازه‌اش را دقیق‌تر کنید\n"
            "۴) «✅ تایید و نام‌گذاری» را بزنید"
        )
        self.auto_region_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
        )
        self.auto_region_btn.clicked.connect(self._on_auto_region_clicked)
        region_draw_layout.addWidget(self.auto_region_btn)

        self.ai_floor_btn = QPushButton("🧭 تشخیص هوشمند زمین (AI)")
        self.ai_floor_btn.setToolTip(
            "با مدل هوش مصنوعی (Segmentation)، فقط سطح زمین/کفِ واقعیِ تصویر "
            "تشخیص داده و به‌عنوان محدوده پیشنهاد می‌شود."
        )
        self.ai_floor_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
        )
        self.ai_floor_btn.clicked.connect(self._on_ai_floor_region_clicked)
        region_draw_layout.addWidget(self.ai_floor_btn)

        self.confirm_line_btn = QPushButton("✅ تایید و نام‌گذاری")
        self.confirm_line_btn.setEnabled(False)
        self.confirm_line_btn.setToolTip(
            "محدوده‌ی رسم‌شده را با یک نام (مثلاً نام اتاق) نهایی می‌کند.")
        self.confirm_line_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
            "QPushButton:enabled{background:#27ae60; color:#fff;}"
        )
        self.confirm_line_btn.clicked.connect(self._on_confirm_line_clicked)
        region_draw_layout.addWidget(self.confirm_line_btn)

        self.redraw_line_btn = QPushButton("❌ لغو رسم")
        self.redraw_line_btn.setEnabled(False)
        self.redraw_line_btn.setToolTip("نقاط در حال رسم/در انتظار نام‌گذاری را لغو می‌کند.")
        self.redraw_line_btn.setStyleSheet(
            "QPushButton{background:#333; color:#ccc; border-radius:4px; padding:3px 8px; font-size:11px;}"
        )
        self.redraw_line_btn.clicked.connect(self._on_redraw_line_clicked)
        region_draw_layout.addWidget(self.redraw_line_btn)

        page_lay.addWidget(self.region_draw_row)

        # گرید دوربین‌ها
        self.camera_grid = CameraGridWidget(
            host.face_engine, host.on_face_event,
            on_external_camera_drop=host.on_camera_dropped_on_grid,
            on_region_alert=host.on_region_alert,
            on_fire_event=host.on_fire_event,
            on_plate_event=host.on_plate_event,
            on_person_event=host.on_person_event,
            on_audio_url_found=lambda cam_id, url: host.camera_store.update_camera(
                cam_id, audio_url=url),
            on_audio_creds_save=host._save_audio_creds,
        )
        self.camera_grid.selection_changed.connect(
            lambda _idx: self._refresh_line_buttons())
        self.camera_grid.tripwire_changed.connect(self._refresh_line_buttons)
        self.camera_grid.detector_status_changed.connect(
            host._refresh_person_detector_status)
        self.camera_grid.video_loss.connect(host._on_video_loss)

        grid_scroll = QScrollArea()
        grid_scroll.setWidgetResizable(True)
        grid_scroll.setWidget(self.camera_grid)
        page_lay.addWidget(grid_scroll, 1)

    def _on_grid_size_changed(self, _index):
        count = self.grid_size_combo.currentData()
        self.camera_grid.set_grid_size(count)

    def _on_people_toggle_all(self, checked):
        """رفع درخواست: با این یک دکمه (بالای کل شبکه‌ی دوربین‌ها)، شمارش
        افراد Real Time برای همه‌ی دوربین‌های باز هم‌زمان روشن/خاموش می‌شود؛
        وضعیت روی دوربین‌هایی که بعداً باز شوند هم اعمال می‌ماند (رجوع کنید
        به CameraGridWidget.set_people_counting_all)."""
        if checked:
            try:
                from admin_quota import require_feature
                if not require_feature("people_counting", self):
                    self.people_toggle_btn.blockSignals(True)
                    self.people_toggle_btn.setChecked(False)
                    self.people_toggle_btn.blockSignals(False)
                    return
            except Exception:
                pass
        self.camera_grid.set_people_counting_all(checked)

    def _selected_slot(self):
        idx = self.camera_grid.selected_index
        if idx is None or not (0 <= idx < len(self.camera_grid.slots)):
            return None
        return self.camera_grid.slots[idx]

    def _on_draw_line_toggled(self, checked):
        # گیت لایسنس: رسم محدوده‌ی هشدار فقط وقتی مجاز است که قابلیت
        # «هشدار ورود به محدوده» در لایسنس فعال باشد.
        if checked:
            try:
                from admin_quota import require_feature
                if not require_feature("zone_alerts", self):
                    self.draw_line_btn.blockSignals(True)
                    self.draw_line_btn.setChecked(False)
                    self.draw_line_btn.blockSignals(False)
                    return
            except Exception:
                pass
        slot = self._selected_slot()
        if slot is None or slot.cam is None:
            if checked:
                self.draw_line_btn.blockSignals(True)
                self.draw_line_btn.setChecked(False)
                self.draw_line_btn.blockSignals(False)
                QMessageBox.information(
                    self, "رسم محدوده هشدار",
                    "ابتدا یک دوربین را از شبکه‌ی نمایش انتخاب کنید (روی خانه‌اش کلیک کنید)، سپس دوباره این دکمه را بزنید."
                )
            return
        slot.set_draw_mode(checked)
        self._refresh_line_buttons()

    def _on_auto_region_clicked(self):
        slot = self._selected_slot()
        if slot is None or slot.cam is None:
            QMessageBox.information(
                self, "تشخیص خودکار محدوده",
                "ابتدا یک دوربین را از شبکه‌ی نمایش انتخاب کنید (روی خانه‌اش کلیک کنید)."
            )
            return
        if slot.is_draw_mode():
            self.draw_line_btn.setChecked(False)
        if not slot.start_auto_full_frame_region():
            QMessageBox.information(
                self, "تشخیص خودکار محدوده",
                "ابتدا محدوده‌ی در انتظار/در حال ویرایشِ فعلی را با «✅ تایید» یا «❌ لغو» تمام کنید."
            )
            return
        self._refresh_line_buttons()

    def _on_ai_floor_region_clicked(self):
        """رفع درخواست «حالت جدید که خودش سطح زمین رو تشخیص بده»: برخلاف
        _on_auto_region_clicked (که فوری و همزمان کل کادر را می‌گذارد)،
        اینجا باید روی آخرین فریمِ زنده‌ی دوربین یک مدل واقعی اجرا شود -
        این کار در یک ترد جدا (FloorDetectThread) انجام می‌شود تا در طول
        چند ثانیه‌ی پردازش (به‌خصوص بار اول، وقتی مدل هنوز دانلود/بارگذاری
        نشده)، بقیه‌ی برنامه (از جمله پخش زنده‌ی سایر دوربین‌های باز) فریز
        نشود."""
        if self._floor_detect_thread is not None:
            return  # یک تشخیص از قبل در حال اجراست؛ از کلیک تکراری صرف‌نظر می‌شود
        slot = self._selected_slot()
        if slot is None or slot.cam is None:
            QMessageBox.information(
                self, "تشخیص هوشمند زمین",
                "ابتدا یک دوربین را از شبکه‌ی نمایش انتخاب کنید (روی خانه‌اش کلیک کنید)."
            )
            return
        if slot.has_pending_region() or slot.is_editing_region():
            QMessageBox.information(
                self, "تشخیص هوشمند زمین",
                "ابتدا محدوده‌ی در انتظار/در حال ویرایشِ فعلی را با «✅ تایید» یا «❌ لغو» تمام کنید."
            )
            return
        frame = slot.latest_raw_frame
        if frame is None:
            QMessageBox.information(
                self, "تشخیص هوشمند زمین",
                "هنوز هیچ فریمی از این دوربین دریافت نشده - چند لحظه صبر کنید تا تصویر زنده بیاید و دوباره امتحان کنید."
            )
            return
        if slot.is_draw_mode():
            self.draw_line_btn.setChecked(False)
        self.ai_floor_btn.setEnabled(False)
        self.ai_floor_btn.setText("⏳ در حال تحلیل تصویر...")
        thread = _get_floor_detect_thread()(frame.copy(), parent=self)
        thread.finished_signal.connect(
            lambda points, message, _slot=slot: self._on_ai_floor_detect_finished(_slot, points, message)
        )
        thread.finished.connect(self._on_ai_floor_detect_thread_done)
        self._floor_detect_thread = thread
        thread.start()

    def _on_ai_floor_detect_thread_done(self):
        self._floor_detect_thread = None
        self.ai_floor_btn.setText("🧭 تشخیص هوشمند زمین (AI)")
        self._refresh_line_buttons()

    def _on_ai_floor_detect_finished(self, slot, points, message):
        """رفع درخواست: نتیجه‌ی FloorDetectThread را روی تصویر همان دوربین
        (slot) به‌عنوان یک محدوده‌ی «در انتظار» و کاملاً قابل‌ویرایش قرار
        می‌دهد - دقیقاً مثل خروجی رسم دستی یا تشخیص خودکار کل تصویر. اگر
        مدل چیزی پیدا نکرد یا اصلاً در دسترس نبود، به‌جای گذاشتن یک محدوده‌ی
        نادرست/حدسی، فقط دلیل را روشن به کاربر می‌گوید و پیشنهاد می‌دهد از
        «تشخیص خودکار محدوده (کل تصویر)» یا رسم دستی استفاده کند."""
        if points is None:
            QMessageBox.warning(self, "تشخیص هوشمند زمین ناموفق بود", message)
            return
        # ممکن است در طول چند ثانیه‌ی پردازش، کاربر دوربین دیگری انتخاب
        # کرده یا محدوده‌ی دیگری روی همین دوربین شروع کرده باشد؛ در این
        # حالت نتیجه‌ی دیرکرد‌شده را بی‌سروصدا نادیده می‌گیریم تا چیزی
        # غیرمنتظره روی تصویر ظاهر نشود.
        if slot.has_pending_region() or slot.is_editing_region():
            return
        if not slot.start_ai_floor_region(points):
            return
        if self._selected_slot() is slot:
            self._refresh_line_buttons()

    def _on_confirm_line_clicked(self):
        slot = self._selected_slot()
        if slot is None or not slot.has_pending_region():
            return
        # رفع درخواست «قابلیت ادیت‌کردن»: اگر در حال ویرایشِ شکل/اندازه‌ی یک
        # محدوده‌ی از قبل تایید‌شده هستیم (رجوع کنید به
        # CameraSlotWidget.start_edit_region)، همین دکمه به‌جای پرسیدن یک
        # نام تازه، فقط نقاط جدید را روی همان محدوده ذخیره می‌کند - نامش
        # دست‌نخورده می‌ماند.
        if slot.is_editing_region():
            region = slot.save_region_edit()
            if region is not None and slot.cam is not None:
                self._host.camera_store.update_camera(slot.cam["id"], regions=list(slot.regions))
            self._refresh_line_buttons()
            return
        name, ok = QInputDialog.getText(
            self, "نام‌گذاری محدوده",
            "نام این محدوده را وارد کنید (مثلاً اسم اتاق) - اختیاری:"
        )
        if not ok:
            return
        region = slot.confirm_region(name)
        if region is not None and slot.cam is not None:
            self._host.camera_store.update_camera(slot.cam["id"], regions=list(slot.regions))
        # رفع درخواست: قبلاً اگر تشخیص شخص (YOLOv8) بارگذاری نشده بود، کاربر
        # محدوده را تایید می‌کرد و فکر می‌کرد همه‌چیز فعال شده، در حالی که
        # هشدار هرگز صادر نمی‌شد و هیچ توضیحی هم نمی‌دید. حالا هشدار با فالبک
        # «تشخیص حرکت» کار می‌کند؛ فقط به کاربر اطلاع می‌دهیم که حالت جایگزین
        # فعال است (نه تشخیص دقیق شخص).
        if region is not None and slot._detector_available is False:
            QMessageBox.information(
                self, "محدوده ذخیره شد (حالت جایگزین)",
                "این محدوده ذخیره شد و هشدار ورود به آن فعال است، اما مدل "
                "تشخیص دقیق شخص (YOLOv8) در این اجرا بارگذاری نشد؛ فعلاً با "
                "«تشخیص حرکت» کار می‌کند (حساس‌تر به حرکت‌های غیرانسانی).\n\n"
                "کتابخانه‌ی لازم (ultralytics + وزن مدل yolov8n.pt) داخل "
                "نسخه‌ی رسمی برنامه بسته‌بندی شده و نیازی به نصب هیچ‌چیز "
                "روی ویندوز نیست. اگر این پیام را در نسخه‌ی رسمی "
                "(ساخته‌شده با GitHub Actions) می‌بینید، آخرین نسخه‌ی "
                "ساخته‌شده را دانلود و جایگزین کنید."
            )
        self.draw_line_btn.blockSignals(True)
        self.draw_line_btn.setChecked(False)
        self.draw_line_btn.blockSignals(False)
        self._refresh_line_buttons()

    def _on_redraw_line_clicked(self):
        slot = self._selected_slot()
        if slot is None:
            return
        # رفع درخواست «قابلیت ادیت‌کردن»: در حالت ویرایشِ یک محدوده‌ی
        # قبلاً تایید‌شده، این دکمه فقط ویرایش را لغو می‌کند (خودِ محدوده با
        # شکل/اندازه‌ی قبلی دست‌نخورده می‌ماند) - نه اینکه محدوده حذف شود.
        # (2.0.38-beta) اگر چندضلعی هنوز بسته نشده و فقط نقطه‌هایی «در حال
        # رسم»‌اند، همان نقاطِ نیمه‌کاره لغو می‌شوند.
        if slot.is_editing_region():
            slot.cancel_region_edit()
        elif slot.has_pending_region():
            slot.cancel_pending_region()
        else:
            slot.cancel_draw_points()
        self.draw_line_btn.blockSignals(True)
        self.draw_line_btn.setChecked(False)
        self.draw_line_btn.blockSignals(False)
        self._refresh_line_buttons()

    def _on_manage_regions_clicked(self):
        slot = self._selected_slot()
        if slot is None:
            return

        def _on_changed():
            if slot.cam is not None:
                self._host.camera_store.update_camera(slot.cam["id"], regions=list(slot.regions))
            self._refresh_line_buttons()

        dialog = RegionManagerDialog(slot, _on_changed, self)
        dialog.exec()

    def _refresh_line_buttons(self):
        """دکمه‌های «تایید و نام‌گذاری»/«لغو رسم»/«مدیریت محدوده‌ها» و
        وضعیت تیک‌خورده‌ی «رسم محدوده هشدار» را بر اساس خانه‌ی فعلاً
        انتخاب‌شده به‌روز می‌کند - چون هر خانه محدوده‌های مستقل خودش را
        دارد."""
        slot = self._selected_slot()
        has_pending = slot.has_pending_region() if slot is not None else False
        has_confirmed = bool(slot.regions) if slot is not None else False
        is_editing = slot.is_editing_region() if slot is not None else False
        # (فعال/غیرفعال دکمه‌های تایید/لغو در انتهای همین تابع تنظیم می‌شود)
        self.manage_regions_btn.setEnabled(bool(has_confirmed))
        # (2.0.12-beta به دستور کاربر): دکمه‌های مربوط به رسم فقط وقتی دیده
        # می‌شوند که لازم باشند (رجوع کنید به انتهای همین تابع)؛ بقیه‌ی
        # وقت‌ها مخفی‌اند تا نوار ابزار خلوت بماند. ردیف رسم سطر دوم نوار
        # ابزار است و _NoMinWidthRow جلوی اثرش روی اندازه‌ی پنجره/کادرها را
        # می‌گیرد (باگ 2.0.14-beta).
        # رفع درخواست «قابلیت ادیت‌کردن»: وقتی یک محدوده‌ی «در انتظار» (چه
        # تازه رسم‌شده، چه محدوده‌ی خودکارِ کل تصویر، چه در حال ویرایش شکلِ
        # یک محدوده‌ی قبلی) روی تصویر هست، رسم/تشخیص خودکارِ تازه غیرفعال
        # می‌شود تا دو جریان هم‌زمان با هم قاطی نشوند؛ متن دکمه‌های
        # تایید/لغو هم بسته به این‌که «ویرایش یک محدوده‌ی موجود» است یا
        # «محدوده‌ی تازه»، عوض می‌شود.
        self.draw_line_btn.setEnabled(not has_pending)
        self.auto_region_btn.setEnabled(not has_pending)
        # رفع درخواست: دکمه‌ی «تشخیص هوشمند زمین» هم مثل «تشخیص خودکار
        # (کل تصویر)» وقتی محدوده‌ای در انتظار/در حال ویرایش است غیرفعال
        # می‌شود؛ علاوه بر آن، اگر همین الان یک تشخیص در حال اجرا باشد
        # (self._floor_detect_thread) هم غیرفعال می‌ماند تا این تابع (که از
        # چند رویداد دیگر - مثل تغییر انتخاب دوربین - هم صدا زده می‌شود)
        # وسط پردازش دوباره فعالش نکند.
        self.ai_floor_btn.setEnabled(not has_pending and self._floor_detect_thread is None)
        if is_editing:
            self.confirm_line_btn.setText("💾 ذخیره ویرایش شکل")
            self.redraw_line_btn.setText("↩ لغو ویرایش")
        else:
            self.confirm_line_btn.setText("✅ تایید و نام‌گذاری")
            self.redraw_line_btn.setText("❌ لغو رسم")
        self.draw_line_btn.blockSignals(True)
        self.draw_line_btn.setChecked(bool(slot.is_draw_mode()) if slot is not None else False)
        self.draw_line_btn.blockSignals(False)
        # (2.0.14-beta به دستور کاربر): «تایید» و «لغو» فقط وقتی دیده می‌شوند
        # که محدوده‌ای «رسم شده» باشد (در انتظار تایید/نام‌گذاری) یا در حال
        # ویرایش شکل یک محدوده‌ی قبلی باشیم؛ در حالتِ خالصِ رسم (هنوز چیزی
        # رسم نشده) فقط دکمه‌های «نوع رسم» (خودکار/هوشمند) دیده می‌شوند.
        # خود ردیف هم فقط وقتی نمایان است که حداقل یکی از این دو گروه نمایان
        # باشد. ردیف، سطر دوم نوار ابزار است (_NoMinWidthRow) و ظاهر/مخفی
        # شدنش اندازه‌ی پنجره و کادرها را عوض نمی‌کند.
        in_draw_mode = bool(slot.is_draw_mode()) if slot is not None else False
        # (2.0.38-beta) «لغو رسم» علاوه بر محدوده‌ی در انتظار/در حال ویرایش،
        # وقتی هم که نقطه‌هایی «در حال رسم»‌اند (هنوز چندضلعی بسته نشده)
        # دیده و فعال می‌شود — تا کاربر هیچ‌وقت با رسمِ نیمه‌کاره‌ی
        # بی‌دکمه مواجه نشود. «تایید و نام‌گذاری» همچنان فقط برای محدوده‌ی
        # بسته‌شده (در انتظار) یا در حال ویرایش است.
        has_drawing = bool(slot.has_draw_points()) if slot is not None else False
        show_types = bool(in_draw_mode and not has_pending and not is_editing)
        show_confirm = bool(has_pending or is_editing)
        show_cancel = bool(has_pending or is_editing or (in_draw_mode and has_drawing))
        self.auto_region_btn.setVisible(show_types)
        self.ai_floor_btn.setVisible(show_types)
        self.confirm_line_btn.setVisible(show_confirm)
        self.confirm_line_btn.setEnabled(bool(has_pending or is_editing))
        self.redraw_line_btn.setVisible(show_cancel)
        self.redraw_line_btn.setEnabled(bool(has_pending or is_editing or has_drawing))
        self.region_draw_row.setVisible(bool(show_types or show_confirm or show_cancel))
