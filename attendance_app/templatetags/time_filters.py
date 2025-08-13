from django import template
from datetime import timedelta

register = template.Library()

@register.filter
def format_timedelta(value):
    try:
        if isinstance(value, timedelta):
            total_seconds = int(value.total_seconds())
        else:
            total_seconds = int(float(value) * 3600)  # যদি float ঘন্টা আসে

        hours, remainder = divmod(abs(total_seconds), 3600)
        minutes, seconds = divmod(remainder, 60)

        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    except:
        return "00:00:00"


@register.filter
def format_diff_timedelta(value):
    try:
        if isinstance(value, timedelta):
            total_seconds = int(value.total_seconds())
        else:
            total_seconds = int(float(value) * 3600)

        if total_seconds == 0:
            return "00:00:00"

        sign = '+' if total_seconds < 0 else '-'
        total_seconds = abs(total_seconds)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"
    except:
        return "00:00:00"
