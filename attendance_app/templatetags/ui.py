"""তালিকা/কার্ডের ছোট UI সাহায্যকারী — {% load ui %}"""
import zlib
from datetime import timedelta

from django import template

register = template.Library()

# নরম রঙের জোড়া (পটভূমি, লেখা) — একই নামের সবসময় একই রঙ
_AVATAR = [
    ('#eef2ff', '#4338ca'), ('#ecfeff', '#0e7490'), ('#f0fdf4', '#15803d'), ('#fff7ed', '#c2410c'),
    ('#fdf2f8', '#be185d'), ('#f5f3ff', '#6d28d9'), ('#fefce8', '#a16207'), ('#f0f9ff', '#0369a1'),
    ('#fef2f2', '#b91c1c'), ('#f7fee7', '#4d7c0f'),
]


@register.filter
def avatar_style(name):
    bg, fg = _AVATAR[zlib.crc32(str(name or '').encode('utf-8')) % len(_AVATAR)]
    return f"background:{bg};color:{fg}"


@register.filter
def initials(name):
    parts = [p for p in str(name or '').replace('(', ' ').split() if p[:1].isalpha()]
    if not parts:
        return '?'
    return (parts[0][0] + (parts[1][0] if len(parts) > 1 else '')).upper()


@register.filter
def hm(td):
    """timedelta → '8h 05m' (০ হলে '—')"""
    if not isinstance(td, timedelta) or td.total_seconds() <= 0:
        return '—'
    m = int(td.total_seconds() // 60)
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


@register.filter
def short_late(td):
    """দেরি → '+12m' / '+1h 05m'"""
    if not isinstance(td, timedelta) or td.total_seconds() <= 0:
        return ''
    m = int(td.total_seconds() // 60)
    if m == 0:
        return "+<1m"
    h, m = divmod(m, 60)
    return f"+{h}h {m:02d}m" if h else f"+{m}m"


@register.filter
def pct_of(part, whole):
    """timedelta/সংখ্যার শতাংশ (০–১০০)"""
    try:
        p = part.total_seconds() if isinstance(part, timedelta) else float(part)
        w = whole.total_seconds() if isinstance(whole, timedelta) else float(whole)
    except (TypeError, ValueError, AttributeError):
        return 0
    return max(0, min(100, round(100 * p / w))) if w else 0
