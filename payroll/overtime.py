# payroll/overtime.py
# ওভারটাইম ও অর্জিত বেতনের হিসাব — কোম্পানির সেটিং অনুযায়ী।
#
# ডিফল্ট সেটিং (monthly, min 0, ×1.5, ছুটির আলাদা রেট নেই) আগের হিসাব হুবহু রাখে:
#   ঘণ্টার রেট = মূল বেতন ÷ (কর্মদিবস × শিফট ঘণ্টা)
#   মাসের মোট কাজ ≤ প্রত্যাশিত হলে  → মোট × রেট
#   বেশি হলে                       → প্রত্যাশিত × রেট + বাড়তি × রেট × গুণক
#
# সেটিং:
#   ot_calculation = 'monthly' → মাসের মোট ধরে (একদিনের বেশি আরেকদিনের কমের সাথে কাটাকাটি)
#                    'daily'   → প্রতিদিন আলাদা (শিফটের বেশি = ওভারটাইম, কম দিনের সাথে কাটাকাটি নেই)
#   ot_min_minutes            → কর্মদিবসে শিফটের পরের বাড়তি সময় এর কম হলে সেটা গোনা হয় না
#   ot_multiplier             → ওভারটাইমের গুণক
#   holiday_ot_multiplier     → সাপ্তাহিক/সরকারি ছুটির দিনের কাজ সবসময় এই গুণকে (খালি = আলাদা রেট নেই)
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

ZERO = timedelta()


@dataclass
class DayWork:
    work: timedelta = ZERO      # সেদিনের কাজ (Out − max(In, শিফট শুরু))
    is_off: bool = False        # সাপ্তাহিক বা সরকারি ছুটি
    is_leave: bool = False      # approved leave (পুরো শিফট ক্রেডিট)


@dataclass
class OvertimeSettings:
    ot_calculation: str = 'monthly'
    ot_min_minutes: int = 0
    ot_multiplier: Decimal = Decimal('1.5')
    holiday_ot_multiplier: Decimal = None

    @classmethod
    def for_company(cls, company):
        return cls(
            ot_calculation=company.ot_calculation,
            ot_min_minutes=company.ot_min_minutes,
            ot_multiplier=Decimal(company.ot_multiplier),
            holiday_ot_multiplier=(Decimal(company.holiday_ot_multiplier)
                                   if company.holiday_ot_multiplier is not None else None),
        )


def _hours(td):
    return td.total_seconds() / 3600


def calculate_pay(base_salary, regular, working_days, days, settings=None):
    """
    base_salary: Decimal, regular: শিফটের দৈর্ঘ্য (timedelta), working_days: কর্মদিবসের সংখ্যা,
    days: [DayWork, ...]
    রিটার্ন: dict(earned, hourly_rate, expected, total_work, over_time, overtime_hours, overtime_pay)
    """
    settings = settings or OvertimeSettings()
    min_extra = timedelta(minutes=settings.ot_min_minutes or 0)

    normal = ZERO          # শিফটের ভেতরের কাজ + leave ক্রেডিট
    work_extra = ZERO      # কর্মদিবসে শিফটের পরের বাড়তি (ন্যূনতম সীমা পার হলে)
    holiday_work = ZERO    # ছুটির দিনের কাজ
    for d in days:
        if d.is_leave:
            normal += regular
        elif d.is_off:
            holiday_work += d.work
        else:
            extra = max(d.work - regular, ZERO)
            normal += d.work - extra
            if extra and extra >= min_extra:
                work_extra += extra

    expected_h = working_days * _hours(regular)
    hourly_rate = (base_salary / Decimal(expected_h)) if expected_h > 0 else Decimal(0)

    holiday_pay = Decimal(0)
    holiday_in_total = holiday_work
    if settings.holiday_ot_multiplier is not None:
        holiday_pay = Decimal(_hours(holiday_work)) * hourly_rate * settings.holiday_ot_multiplier
        holiday_in_total = ZERO

    mult = settings.ot_multiplier
    if settings.ot_calculation == 'daily':
        ot_h = _hours(work_extra + holiday_in_total)
        earned = Decimal(_hours(normal)) * hourly_rate + Decimal(ot_h) * hourly_rate * mult
        ot_pay = Decimal(ot_h) * hourly_rate * mult
    else:  # monthly
        total_h = _hours(normal + work_extra + holiday_in_total)
        if total_h <= expected_h:
            ot_h = 0.0
            earned = Decimal(total_h) * hourly_rate
            ot_pay = Decimal(0)
        else:
            ot_h = total_h - expected_h
            earned = (Decimal(expected_h) * hourly_rate) + (Decimal(ot_h) * hourly_rate * mult)
            ot_pay = Decimal(ot_h) * hourly_rate * mult

    return {
        'earned': earned + holiday_pay,
        'hourly_rate': hourly_rate,
        'expected_hours': expected_h,
        'total_work': normal + work_extra + holiday_work,
        'over_time': work_extra + holiday_work,        # রিপোর্টের "Over" কলাম
        'overtime_hours': ot_h + _hours(holiday_work) * (settings.holiday_ot_multiplier is not None),
        'overtime_pay': ot_pay + holiday_pay,
    }
