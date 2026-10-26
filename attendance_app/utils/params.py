# attendance_app/utils/params.py
# URL query param পড়ার সেফ হেল্পার: ভুল ইনপুট হলে 500 না দিয়ে 400 Bad Request
# (Django BadRequest exception নিজেই 400 রেসপন্স বানায়)
import re
from datetime import date, datetime

from django.core.exceptions import BadRequest

# টেমপ্লেট থেকে অনেক সময় None/খালি মান স্ট্রিং হয়ে আসে — এগুলোকে "দেওয়া হয়নি" ধরি
_EMPTY = {"", "none", "null", "undefined"}


def _raw(params, name):
    value = (params.get(name) or "").strip()
    return None if value.lower() in _EMPTY else value


def int_param(params, name):
    value = _raw(params, name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        raise BadRequest(f"Invalid '{name}': must be a number.")


def date_param(params, name, default=None) -> date:
    value = _raw(params, name)
    if value is None:
        return default
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise BadRequest(f"Invalid '{name}': use YYYY-MM-DD.")


def month_param(params, name, default=None):
    value = _raw(params, name)
    if value is None:
        return default
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise BadRequest(f"Invalid '{name}': use YYYY-MM.")
    return value
