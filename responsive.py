# -*- coding: utf-8 -*-
"""responsive.py — محاسبات ریسپانسیو اندازه‌ی پنجره‌ها (2.2.19).

عمداً بدون وابستگی به Qt است تا بدون نمایشگر هم تست شود: ورودی‌ها فقط
اعداد (ابعاد صفحه) و خروجی‌ها فقط اعدادند. فراخواننده‌ها (main.py و
monitor_room.py) اعداد را به setGeometry/setSizes می‌دهند.

اصل‌ها:
  * هیچ پنجره‌ای در شروع بزرگ‌تر از صفحه‌ای که روی آن باز می‌شود نیست.
  * شبکه‌ی دوربین همیشه اولویت دارد: پنل‌های کناری سقف و کف دارند و
    باقی‌مانده‌ی عرض به شبکه می‌رسد.
  * حداقل اندازه‌ی کاشی‌های دوربین (۱۴۰×۱۱۰) دست نمی‌خورد — کوچک‌تر از
    آن، دکمه‌های کنترل هر خانه بریده می‌شوند؛ در عوض پنل‌ها جمع می‌شوند.
"""

# سقف‌ها/کف‌های طراحی (پیکسل).
MAIN_MAX_W = 1500
MAIN_MAX_H = 860
LEFT_W = 200
LEFT_MIN_W = 160
RIGHT_MIN_W = 180
RIGHT_MAX_W = 340
MONITOR_MIN_W, MONITOR_MIN_H = 640, 520
MONITOR_MAX_W, MONITOR_MAX_H = 1600, 900


def initial_window_size(avail_w: int, avail_h: int):
    """اندازه‌ی اولیه‌ی پنجره‌ی اصلی از روی فضای در دسترس صفحه.

    ۹۲٪ صفحه، سقف ۱۵۰۰×۸۶۰، و هرگز بزرگ‌تر از خود صفحه نیست.
    """
    try:
        aw, ah = int(avail_w), int(avail_h)
    except Exception:
        return MAIN_MAX_W, MAIN_MAX_H
    if aw <= 0 or ah <= 0:
        return MAIN_MAX_W, MAIN_MAX_H
    w = min(MAIN_MAX_W, int(aw * 0.92))
    h = min(MAIN_MAX_H, int(ah * 0.92))
    w, h = min(w, aw), min(h, ah)
    # روی صفحه‌های خیلی کوچک، کف را هم از خود صفحه بزرگ‌تر نکن.
    if w < 320:
        w = min(320, aw)
    if h < 240:
        h = min(240, ah)
    return w, h


def splitter_sizes(total_w: int):
    """تقسیم عرض پنجره بین (پنل چپ، شبکه، پنل راست).

    پنل چپ حداکثر ۲۰۰ (روی پنجره‌های باریک تا ۱۶۰ کوچک می‌شود)؛ پنل
    راست بین ۱۸۰ تا ۳۴۰؛ باقی‌مانده برای شبکه‌ی دوربین‌هاست.
    جمع سه عدد دقیقاً برابر total_w است.
    """
    try:
        tw = int(total_w)
    except Exception:
        tw = MAIN_MAX_W
    if tw <= 0:
        tw = MAIN_MAX_W
    left = LEFT_W if tw >= 900 else LEFT_MIN_W
    right = min(RIGHT_MAX_W, max(RIGHT_MIN_W, tw * 2 // 9))
    # روی پنجره‌های خیلی باریک، پنل راست را هم جمع‌تر کن تا شبکه نفس بکشد.
    if tw < 700:
        right = min(right, 160)
    # روی پنجره‌های فوق‌باریک، سقف پنل‌ها را به خود عرض محدود کن تا
    # جمع سه عدد همیشه دقیقاً برابر total_w بماند.
    if left + right > tw:
        right = max(0, tw - left)
        if left > tw:
            left, right = tw, 0
    grid = tw - left - right
    return left, grid, right


def right_panel_heights(total_h: int):
    """تقسیم ارتفاع ستون راست بین (تشخیص چهره ۶۰٪، حریق ۴۰٪)."""
    try:
        th = int(total_h)
    except Exception:
        th = MAIN_MAX_H
    if th <= 0:
        th = MAIN_MAX_H
    return th * 6 // 10, th * 4 // 10


def monitor_room_size(avail_w: int, avail_h: int):
    """اندازه‌ی اولیه‌ی پنجره‌ی اتاق مانیتورینگ (نما).

    ۸۵٪ صفحه، سقف ۱۶۰۰×۹۰۰، کف ۶۴۰×۵۲۰ (اگر صفحه کوچک‌تر بود، همان صفحه).
    """
    try:
        aw, ah = int(avail_w), int(avail_h)
    except Exception:
        return 1280, 700
    if aw <= 0 or ah <= 0:
        return 1280, 700
    w = min(MONITOR_MAX_W, int(aw * 0.85))
    h = min(MONITOR_MAX_H, int(ah * 0.85))
    w = max(min(MONITOR_MIN_W, aw), min(w, aw))
    h = max(min(MONITOR_MIN_H, ah), min(h, ah))
    return w, h
