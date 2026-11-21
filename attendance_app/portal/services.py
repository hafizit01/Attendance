"""পোর্টালের হিসাব — বেতনের পেজ/হাজিরার রিপোর্টের একই কোড ব্যবহার করে, যাতে কর্মচারী আর অ্যাডমিন একই সংখ্যা দেখে।"""
import base64
import binascii
import io
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.utils import timezone

from attendance_app.models import Attendance
from attendance_app.utils.attendance_helpers import day_range

MAX_SELFIE_BYTES = 6 * 1024 * 1024     # মোবাইলের ছবি — কমপ্রেস করে রাখা হয়
SELFIE_MAX_SIDE = 720
REMOTE_PUNCH_GAP = timedelta(minutes=2)


# ---------------------------------------------------------
# পে-স্লিপ
# ---------------------------------------------------------
def _company_request(company):
    """
    payroll.views.get_salary_summary_data অ্যাডমিনের request থেকে কোম্পানি ও স্কোপ নেয়। কর্মচারীর
    নিজের পে-স্লিপের জন্য একই ফাংশন চালাতে কোম্পানি-লেভেলের (সব ব্রাঞ্চ, কোনো বিশেষ অনুমতি নেই)
    একটা অভ্যন্তরীণ request বানাই — ফলাফল সবসময় employee_id দিয়ে শুধু নিজের সারিতে সীমিত।
    """
    user = SimpleNamespace(profile=SimpleNamespace(company=company, branch=None), is_superuser=False, id=None,
                           is_authenticated=True, has_perm=lambda *_a, **_k: False)
    return SimpleNamespace(user=user, session={}, GET={})


def payslip(employee, month_str):
    """এক মাসের পে-স্লিপ (dict) — বেতন সেট না থাকলে None"""
    from payroll.views import get_salary_summary_data
    data = get_salary_summary_data(_company_request(employee.company), month_str, employee_id=employee.id)
    rows = [r for r in data['summaries'] if r['employee'].id == employee.id]
    if not rows:
        return None
    return {**rows[0], 'rounding_enabled': data['rounding_enabled'],
            'late_deduction_enabled': data['late_deduction_enabled'], 'lates_per_day': data['lates_per_day']}


def payslip_months(employee, limit=12):
    """শেষ হওয়া মাসগুলো (চলতি মাস বাদ) যেগুলোতে কর্মচারীর হাজিরা আছে — নতুন থেকে পুরনো"""
    if not hasattr(employee, 'employeesalary'):
        return []
    today = timezone.localdate()
    first = Attendance.objects.filter(employee=employee).order_by('timestamp').values_list('timestamp', flat=True).first()
    if not first:
        return []
    start = timezone.localtime(first).date().replace(day=1)
    months, m = [], today.replace(day=1) - timedelta(days=1)
    m = m.replace(day=1)
    while m >= start and len(months) < limit:
        months.append(m)
        m = (m - timedelta(days=1)).replace(day=1)
    return months


# ---------------------------------------------------------
# GPS + সেলফি পাঞ্চ
# ---------------------------------------------------------
def _decimal(value, lo, hi, name):
    try:
        d = Decimal(str(value)).quantize(Decimal('0.000001'))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(f"{name} ঠিক নেই।")
    if not lo <= d <= hi:
        raise ValidationError(f"{name} ঠিক নেই।")
    return d


def _selfie_file(data_url=None, upload=None):
    """base64 data URL বা আপলোড করা ফাইল → ছোট করা JPEG (ContentFile)। ছবি না হলে ValidationError"""
    from PIL import Image, ImageOps, UnidentifiedImageError
    if upload is not None:
        if upload.size > MAX_SELFIE_BYTES:
            raise ValidationError("ছবি অনেক বড়।")
        raw = upload.read()
    elif data_url:
        head, _, b64 = data_url.partition(',')
        if not head.startswith('data:image/') or not b64:
            raise ValidationError("সেলফি পাওয়া যায়নি।")
        if len(b64) > MAX_SELFIE_BYTES * 4 // 3 + 4:
            raise ValidationError("ছবি অনেক বড়।")
        try:
            raw = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise ValidationError("সেলফি পড়া যায়নি।")
    else:
        raise ValidationError("সেলফি তুলুন।")
    try:
        img = Image.open(io.BytesIO(raw))
        img.verify()                     # নষ্ট/ছবি-নয় ফাইল আটকায়
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img).convert('RGB')
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise ValidationError("সেলফির ফাইলটা ছবি নয়।")
    img.thumbnail((SELFIE_MAX_SIDE, SELFIE_MAX_SIDE))
    out = io.BytesIO()
    img.save(out, format='JPEG', quality=80, optimize=True)
    return ContentFile(out.getvalue(), name=f"{uuid.uuid4().hex}.jpg")


def remote_punch(employee, *, latitude, longitude, accuracy=None, selfie_data=None, selfie_upload=None, note=''):
    """
    GPS + সেলফি পাঞ্চ বসায়। দিনের প্রথম পাঞ্চ In, পরেরগুলো Out (হাজিরার হিসাব দিনের প্রথম ও শেষ পাঞ্চ নেয়)।
    রিটার্ন: Attendance
    """
    lat = _decimal(latitude, Decimal(-90), Decimal(90), "Latitude")
    lng = _decimal(longitude, Decimal(-180), Decimal(180), "Longitude")
    acc = None
    if accuracy not in (None, ''):
        try:
            acc = max(float(accuracy), 0.0)
        except (TypeError, ValueError):
            acc = None
    now = timezone.now().replace(microsecond=0)
    recent = Attendance.objects.filter(employee=employee, timestamp__gte=now - REMOTE_PUNCH_GAP,
                                       timestamp__lte=now + REMOTE_PUNCH_GAP).exists()
    if recent:
        raise ValidationError("এইমাত্র একটা পাঞ্চ হয়েছে — ২ মিনিট পরে আবার চেষ্টা করুন।")
    selfie = _selfie_file(selfie_data, selfie_upload)

    today = timezone.localdate()
    has_in = Attendance.objects.filter(employee=employee, status='In', **day_range(today)).exists()
    return Attendance.objects.create(
        employee=employee, company=employee.company, timestamp=now, status='Out' if has_in else 'In',
        is_remote_punch=True, latitude=lat, longitude=lng, location_accuracy=acc, selfie=selfie,
        remote_note=(note or '')[:255],
    )
