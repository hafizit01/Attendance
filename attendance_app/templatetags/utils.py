from django import template

register = template.Library()

@register.filter
def hhmmss(value):
    if not value:
        return "00:00:00"
    total_seconds = int(value.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"



