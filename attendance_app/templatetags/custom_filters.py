from django import template
from datetime import timedelta

register = template.Library()

@register.filter
def format_timedelta(value):
    """
    timedelta বা ঘন্টার float ইনপুটকে HH:MM:SS ফরম্যাটে রূপান্তর করে।
    """
    try:
        if isinstance(value, timedelta):
            total_seconds = int(value.total_seconds())
        else:
            total_seconds = int(float(value) * 3600)  # ঘন্টা float হলে

        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    except:
        return "00:00:00"

@register.filter
def format_diff_timedelta(value):
    """
    timedelta বা ঘন্টার float হলে পার্থক্য হিসেবে + বা - সহ HH:MM:SS ফরম্যাটে রূপান্তর করে।
    """
    try:
        sign = ''
        if isinstance(value, timedelta):
            total_seconds = int(value.total_seconds())
        else:
            total_seconds = int(float(value) * 3600)

        if total_seconds < 0:
            sign = '-'
            total_seconds = abs(total_seconds)
        elif total_seconds > 0:
            sign = '+'

        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"
    except:
        return "00:00:00"

@register.filter
def qs_without_page(query_dict):
    """
    request.GET থেকে 'page' বাদ দিয়ে বাকি ফিল্টারগুলোর query string (শেষে & সহ, খালি হলে "")।
    পেজিনেশন লিংকে ফিল্টার ধরে রাখার জন্য: href="?{{ request.GET|qs_without_page }}page=2"
    """
    params = query_dict.copy()
    params.pop('page', None)
    encoded = params.urlencode()
    return f"{encoded}&" if encoded else ""


@register.filter
def abs_timedelta(value):
    """
    timedelta বা float টাইমের absolute মান (ঘাটতি/বাড়তি সময়) রিটার্ন করে।
    """
    try:
        if isinstance(value, timedelta):
            return abs(value)
        else:
            return abs(float(value))
    except:
        return value
