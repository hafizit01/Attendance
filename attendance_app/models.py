import secrets
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.contrib.auth.models import User

# সপ্তাহের দিনের জন্য অপশন টুপল
WEEKDAYS = [
    ('Saturday', 'Saturday'),
    ('Sunday', 'Sunday'),
    ('Monday', 'Monday'),
    ('Tuesday', 'Tuesday'),
    ('Wednesday', 'Wednesday'),
    ('Thursday', 'Thursday'),
    ('Friday', 'Friday'),
]

class Company(models.Model):
    name = models.CharField(max_length=200)
    address = models.TextField(blank=True, null=True)
    phone = models.CharField(max_length=30, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    owner = models.ForeignKey(  # ← নতুন
        User,
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="companies",
    )
    # নগদ বেতন 50/100 টাকায় মেলানো (payroll/rounding.py); বন্ধ থাকলে কোনো পরিবর্তন নেই
    salary_rounding_enabled = models.BooleanField(
        default=False,
        verbose_name="Round cash salary (50/100)",
        help_text="চালু থাকলে নগদ বেতনের শেষ দুই অঙ্ক 1–50 হলে 50 এ, 51–99 হলে পরের 100 এ মেলানো হবে।",
    )

    # ওভারটাইম সেটিং (payroll/overtime.py) — ডিফল্ট মান আগের হিসাব হুবহু রাখে
    OT_CALCULATION_CHOICES = [
        ('monthly', 'মাসের মোট ধরে (একদিনের বেশি আরেকদিনের কমের সাথে কাটাকাটি)'),
        ('daily', 'প্রতিদিন আলাদা (শিফটের বেশি সময়ই ওভারটাইম)'),
    ]
    ot_calculation = models.CharField(
        max_length=10, choices=OT_CALCULATION_CHOICES, default='monthly',
        verbose_name="ওভারটাইম হিসাবের পদ্ধতি",
    )
    ot_min_minutes = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(600)],
        verbose_name="ন্যূনতম ওভারটাইম (মিনিট)",
        help_text="কর্মদিবসে শিফট শেষের পর এর কম সময় থাকলে সেটা গোনা হবে না। 0 = সব গোনা হবে।",
    )
    ot_multiplier = models.DecimalField(
        max_digits=4, decimal_places=2, default=Decimal('1.50'),
        validators=[MinValueValidator(Decimal('1')), MaxValueValidator(Decimal('5'))],
        verbose_name="ওভারটাইমের গুণক",
        help_text="যেমন 1.5 = ঘণ্টার রেটের দেড় গুণ।",
    )
    holiday_ot_multiplier = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('1')), MaxValueValidator(Decimal('5'))],
        verbose_name="ছুটির দিনের কাজের গুণক",
        help_text="সাপ্তাহিক/সরকারি ছুটিতে কাজ করলে সবসময় এই গুণকে। খালি রাখলে আলাদা রেট নেই (সাধারণ কাজের মতো)।",
    )

    def __str__(self):
        return self.name

    @property
    def subscription(self):
        """কোম্পানির সক্রিয় সাবস্ক্রিপশন (না থাকলে None)"""
        from subscription_app.utils import get_company_subscription  # circular import এড়াতে
        return get_company_subscription(self)


class Branch(models.Model):
    """কোম্পানির শাখা (যেমন Paltan, Baitul Mukarram, Bashundhara)। Company → Branch → Department → Employee"""
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='branches')
    name = models.CharField(max_length=100)
    address = models.TextField(blank=True, null=True)
    phone = models.CharField(max_length=30, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['company', 'name'], name='uniq_branch_name_per_company'),
        ]

    def __str__(self):
        return self.name


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    company = models.ForeignKey(Company, on_delete=models.SET_NULL, null=True, blank=True, related_name='users')
    # সেট থাকলে এই ইউজার ব্রাঞ্চ ম্যানেজার: শুধু নিজের ব্রাঞ্চের ডাটা দেখবে। খালি = মালিক/পুরো কোম্পানি
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name='managers')

    def __str__(self):
        return f"{self.user.username} Profile"

    @property
    def company_name(self):
        return self.company.name if self.company else ''



class Department(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='departments',
        null=True,
        blank=True
    )
    # ডিপার্টমেন্ট কোন ব্রাঞ্চের; ডিভাইস, অফিস টাইম ও সাপ্তাহিক ছুটি তাই ব্রাঞ্চ অনুযায়ী আলাদা।
    # ব্রাঞ্চে ডিপার্টমেন্ট থাকলে ব্রাঞ্চ মোছা যায় না (PROTECT)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, null=True, blank=True, related_name='departments')
    name = models.CharField(max_length=100)
    weekly_off_day = models.CharField(
        max_length=10,
        choices=WEEKDAYS,
        default='Friday'
    )

    device_ip = models.GenericIPAddressField(blank=True, null=True)
    device_port = models.IntegerField(blank=True, null=True)

    in_time = models.TimeField(default='10:30', help_text="Office start time")
    out_time = models.TimeField(default='20:30', help_text="Office end time")

    # ZKTeco push API: ডিভাইস এই টোকেন পাঠালে বোঝা যায় কোন ডিপার্টমেন্ট/কোম্পানির ডিভাইস
    push_token = models.CharField(max_length=64, unique=True, null=True, blank=True, editable=False)
    # শেষ সফল সিঙ্কের সময়; পরের সিঙ্ক এখান থেকে শুরু হয়
    last_synced_at = models.DateTimeField(null=True, blank=True, editable=False)
    # ডিপার্টমেন্টের ম্যানেজার — প্রতিদিনের অনুপস্থিত/দেরি তালিকা SMS/WhatsApp এ এই নম্বরে যায়
    manager_name = models.CharField(max_length=100, blank=True)
    manager_phone = models.CharField(max_length=20, blank=True,
                                     help_text="যেমন 01712345678 — প্রতিদিনের Absent/Late তালিকা এই নম্বরে যাবে।")
    # ঐচ্ছিক: এই ডিপার্টমেন্টের যাদের আলাদা শিফট দেওয়া নেই তাদের শিফট। খালি = উপরের in/out time
    default_shift = models.ForeignKey('Shift', on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='default_for_departments',
                                      help_text="খালি রাখলে উপরের Office start/end time দিয়ে হিসাব হবে।")

    def save(self, *args, **kwargs):
        if not self.push_token:
            self.push_token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)


    def __str__(self):
        return f"{self.name} ({self.company.name if self.company else 'No Company'})"


class Employee(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='employees',
        null=True,
        blank=True
    )
    name = models.CharField(max_length=100)
    device_user_id = models.IntegerField()
    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='employees'
    )
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    # প্রতিটা ব্রাঞ্চের মেশিনে ID আলাদাভাবে 1,2,3... শুরু হয়, তাই device_user_id ব্রাঞ্চের ভেতরে ইউনিক
    # (কোম্পানিজুড়ে নয়)। ব্রাঞ্চ ডিপার্টমেন্ট থেকে আসে বলে DB constraint নেই — চেক id_scope() দিয়ে।

    @staticmethod
    def id_scope(department):
        """department এর মেশিনের সাথে একই ID সিরিজ শেয়ার করা employee (একই কোম্পানি + একই ব্রাঞ্চ)"""
        qs = Employee.objects.filter(company_id=department.company_id)
        if department.branch_id:
            return qs.filter(department__branch_id=department.branch_id)
        return qs.filter(department__branch__isnull=True)

    def __str__(self):
        return f"{self.name} ({self.company.name if self.company else 'No Company'})"

from django.db import models
from django.core.exceptions import ValidationError


def _private_storage():
    """গোপন ফাইলের স্টোরেজ (settings.PRIVATE_MEDIA_ROOT) — পাবলিক /media/ URL নেই"""
    from django.conf import settings
    from django.core.files.storage import FileSystemStorage
    return FileSystemStorage(location=settings.PRIVATE_MEDIA_ROOT, base_url=None)


class Attendance(models.Model):
    STATUS_CHOICES = [
        ('In', 'In'),
        ('Out', 'Out'),
    ]

    company = models.ForeignKey(
        'Company',
        on_delete=models.CASCADE,
        related_name='attendances',
        null=True,
        blank=True,
    )
    employee = models.ForeignKey(
        'Employee',
        on_delete=models.CASCADE,
        related_name='attendances',
    )
    timestamp = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)

    # মোবাইল থেকে GPS + সেলফি দিয়ে পাঞ্চ (কর্মচারী পোর্টাল)। মেশিন/ম্যানুয়াল হাজিরায় এগুলো খালি থাকে।
    is_remote_punch = models.BooleanField(default=False, db_index=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    location_accuracy = models.FloatField(null=True, blank=True, help_text="মিটার")
    selfie = models.ImageField(upload_to='remote_punch/%Y/%m/', storage=_private_storage, null=True, blank=True)
    remote_note = models.CharField(max_length=255, blank=True)

    # হাতে দেওয়া হাজিরা (Bulk / Official Duty / Missing-punch queue)। মেশিনের পাঞ্চে এগুলো খালি/False।
    # হিসাব আগের মতোই পাঞ্চের সময় থেকে হয় — এগুলো শুধু কে, কেন দিয়েছে তার রেকর্ড।
    ENTRY_TYPES = [
        ('present', 'Present'),
        ('official_duty', 'Official Duty / Outstation'),
        ('half_day', 'Half Day'),
        ('wfh', 'Work From Home'),
    ]
    is_manual = models.BooleanField(default=False, db_index=True)
    entry_type = models.CharField(max_length=20, choices=ENTRY_TYPES, blank=True)
    remarks = models.CharField(max_length=255, blank=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    updated_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.employee.name} - {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')} - {self.status}"

    @property
    def map_url(self):
        if self.latitude is None or self.longitude is None:
            return ''
        return f"https://www.google.com/maps?q={self.latitude},{self.longitude}"

    def clean(self):
        # company auto-assign (validation phase); employee invalid হলে employee_id None থাকে
        if self.employee_id and not self.company:
            self.company = getattr(self.employee, 'company', None)
        if not self.company:
            raise ValidationError("Attendance.company নির্ধারণ করা যায়নি (employee.company সেট আছে কিনা দেখুন)।")

    def save(self, *args, **kwargs):
        # save এর সময়ও company ensure করি (signals ছাড়াই কাজ হবে)
        if self.employee and not self.company:
            self.company = getattr(self.employee, 'company', None)
        super().save(*args, **kwargs)

    class Meta:
        # ডুপ্লিকেট এন্ট্রি রোখা
        constraints = [
            models.UniqueConstraint(
                fields=['employee', 'timestamp', 'status'],
                name='uniq_employee_timestamp_status'
            ),
        ]
        # সাধারণ রিপোর্টে কাজে লাগবে
        indexes = [
            models.Index(fields=['employee', 'timestamp']),
            models.Index(fields=['company', 'timestamp']),
            models.Index(fields=['status', 'timestamp']),
        ]
        # তোমার রিপোর্টিং সুবিধা মতো বদলাতে পারো
        ordering = ['-timestamp']
        verbose_name = "Attendance"
        verbose_name_plural = "Attendances"



class EmployeeDocument(models.Model):
    """
    কর্মচারীর ডকুমেন্ট (NID, CV, সার্টিফিকেট, নিয়োগপত্র ...) — যত খুশি।
    ফাইল গোপন স্টোরেজে (PRIVATE_MEDIA_ROOT) — পাবলিক /media/ লিংক নেই; শুধু ঐ কোম্পানির লগইন করা ইউজার
    (ম্যানেজার হলে নিজের ব্রাঞ্চের) document_views দিয়ে খুলতে পারে।
    """
    DOC_TYPES = [
        ('nid', 'National ID (NID)'),
        ('photo', 'Photo'),
        ('cv', 'CV / Resume'),
        ('certificate', 'Educational certificate'),
        ('appointment', 'Appointment / Offer letter'),
        ('contract', 'Contract / Agreement'),
        ('experience', 'Experience / Release letter'),
        ('bank', 'Bank document'),
        ('medical', 'Medical'),
        ('police', 'Police verification'),
        ('other', 'Other'),
    ]

    company = models.ForeignKey('Company', on_delete=models.CASCADE, related_name='employee_documents')
    employee = models.ForeignKey('Employee', on_delete=models.CASCADE, related_name='documents')
    doc_type = models.CharField(max_length=20, choices=DOC_TYPES, default='other')
    title = models.CharField(max_length=120, blank=True)
    file = models.FileField(upload_to='employee_docs/%Y/%m/', storage=_private_storage)
    original_name = models.CharField(max_length=255, blank=True)
    size = models.PositiveIntegerField(default=0)
    expiry_date = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['doc_type', '-uploaded_at']
        indexes = [models.Index(fields=['employee', 'doc_type'])]

    def __str__(self):
        return f"{self.employee.name} · {self.get_doc_type_display()}"

    @property
    def display_name(self):
        return self.title or self.original_name or self.get_doc_type_display()

    @property
    def extension(self):
        name = self.original_name or self.file.name or ''
        return name.rsplit('.', 1)[-1].lower() if '.' in name else ''

    @property
    def kind(self):
        ext = self.extension
        if ext == 'pdf':
            return 'pdf'
        if ext in ('jpg', 'jpeg', 'png', 'webp', 'gif'):
            return 'image'
        if ext in ('xls', 'xlsx', 'csv'):
            return 'sheet'
        return 'doc'

    @property
    def days_to_expiry(self):
        if not self.expiry_date:
            return None
        from django.utils import timezone as _tz
        return (self.expiry_date - _tz.localdate()).days


class LeaveRequest(models.Model):
    LEAVE_TYPES = [
        ('Casual', 'Casual Leave'),
        ('Sick', 'Sick Leave'),
        ('Jamat', 'Jamat Leave'),
        ('Earned', 'Earned Leave'),
        ('Unpaid', 'Unpaid Leave'),
        ('Other', 'Other'),
    ]

    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Approved', 'Approved'),
        ('Rejected', 'Rejected'),
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='leave_requests',
        null=True,
        blank=True
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='leave_requests'
    )
    # কোড (Casual, Sick ... বা কোম্পানির বানানো ধরনের নাম) — কোন কোড কী তা LeaveType বলে
    leave_type = models.CharField(max_length=20)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='Pending')
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    applied_on = models.DateTimeField(auto_now_add=True)
    # কখন Approve/Reject হলো (অনুমোদনপত্রে দেখায়)
    decided_at = models.DateTimeField(null=True, blank=True, editable=False)

    @property
    def reference(self):
        """অনুমোদনপত্রের নম্বর, যেমন LV-2026-0042"""
        return f"LV-{self.start_date:%Y}-{self.pk:04d}" if self.pk else ''

    def __str__(self):
        return f"{self.employee.name} - {self.leave_type} ({self.start_date} to {self.end_date})"

    def get_leave_type_display(self):
        return leave_type_label(self.leave_type)

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1


TOTAL_LEAVE = 'TOTAL'   # LeaveAllocation এ এই কোড = সব (গোনা হয় এমন) ছুটি মিলিয়ে বছরের মোট সীমা


def leave_type_label(code):
    if code == TOTAL_LEAVE:
        return 'All leave (yearly total)'
    return dict(LeaveRequest.LEAVE_TYPES).get(code, code)


class LeaveType(models.Model):
    """
    কোম্পানির ছুটির ধরন। বিল্ট-ইন ৬টা (Casual, Sick, Jamat, Earned, Unpaid, Other) নিজে থেকেই তৈরি হয়;
    কোম্পানি নিজের ধরন বানাতে পারে (যেমন Hajj Leave) — তার নামই কোড।
      • is_paid: Approved ছুটির দিনে বেতন কাটা যাবে না (পুরো শিফট ক্রেডিট)। বন্ধ = অনুপস্থিতির মতো বেতন কাটা
      • counts_in_limit: বছরের মোট সীমায় (যেমন সব মিলিয়ে ১২ দিন) গোনা হবে কিনা
    """
    DEFAULTS = [
        # code, paid, counts in yearly total
        ('Casual', True, True),
        ('Sick', True, True),
        ('Jamat', True, False),     # কোম্পানির অতিরিক্ত ছুটি — বছরের সীমায় গোনা হয় না
        ('Earned', True, False),
        ('Unpaid', False, False),
        ('Other', True, True),
    ]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='leave_types')
    code = models.CharField(max_length=20)
    is_paid = models.BooleanField(default=True, verbose_name="Paid (বেতন কাটবে না)")
    counts_in_limit = models.BooleanField(default=True, verbose_name="বছরের মোট সীমায় গোনা হবে")
    is_builtin = models.BooleanField(default=False, editable=False)
    is_active = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']
        constraints = [models.UniqueConstraint(fields=['company', 'code'], name='uniq_leave_type_code')]

    def __str__(self):
        return self.label

    @property
    def label(self):
        return leave_type_label(self.code)

    @classmethod
    def for_company(cls, company, active_only=False):
        """কোম্পানির সব ধরন (প্রথমবার বিল্ট-ইনগুলো তৈরি হয়)"""
        qs = cls.objects.filter(company=company)
        if not qs.filter(is_builtin=True).exists():
            cls.objects.bulk_create([
                cls(company=company, code=c, is_paid=p, counts_in_limit=n, is_builtin=True, order=i)
                for i, (c, p, n) in enumerate(cls.DEFAULTS)
            ], ignore_conflicts=True)
        if active_only:
            qs = qs.filter(is_active=True)
        return list(qs.order_by('order', 'id'))


class LeaveAllocation(models.Model):
    """
    বছরে কোন ধরনের ছুটি কত দিন (যেমন Casual 10, Sick 14)।
    employee খালি = কোম্পানির সবার জন্য; employee দিলে শুধু তার জন্য (কোম্পানির নিয়মের বদলে)।
    কোনো ধরনের allocation না থাকলে সেই ছুটির সীমা নেই (আগের মতো)।
    ব্যবহার হওয়া দিন = ওই বছরের Approved ছুটি — তাই approve করলেই ব্যালান্স থেকে কমে, reject/মুছলে ফিরে আসে।
    """
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='leave_allocations')
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, null=True, blank=True,
                                 related_name='leave_allocations',
                                 help_text="খালি = সব কর্মচারী। দিলে শুধু এই কর্মচারীর জন্য আলাদা সীমা।")
    # ছুটির কোড, অথবা TOTAL_LEAVE = সব মিলিয়ে বছরের মোট সীমা
    leave_type = models.CharField(max_length=20)
    days_per_year = models.PositiveSmallIntegerField(validators=[MaxValueValidator(366)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['leave_type']
        constraints = [
            models.UniqueConstraint(fields=['company', 'leave_type'], condition=models.Q(employee__isnull=True),
                                    name='uniq_company_leave_allocation'),
            models.UniqueConstraint(fields=['employee', 'leave_type'], condition=models.Q(employee__isnull=False),
                                    name='uniq_employee_leave_allocation'),
        ]

    def __str__(self):
        who = self.employee.name if self.employee_id else 'All'
        return f"{who} · {self.leave_type}: {self.days_per_year}/year"

    def get_leave_type_display(self):
        return leave_type_label(self.leave_type)


class Holiday(models.Model):
    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name='holidays',
        null=True,
        blank=True
    )
    # খালি = সব ব্রাঞ্চের ছুটি; সেট থাকলে শুধু ওই ব্রাঞ্চের
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True, related_name='holidays')
    title = models.CharField(max_length=100)
    start_date = models.DateField()
    end_date = models.DateField()
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.title} ({self.start_date} to {self.end_date})"

    class Meta:
        ordering = ['start_date']


class Shift(models.Model):
    """
    কাজের শিফট (Morning / Evening / Night ...)। কর্মচারীকে তারিখসহ শিফট দেওয়া হয় (ShiftAssignment)।
    কারো শিফট না থাকলে আগের মতোই ডিপার্টমেন্টের in_time/out_time দিয়ে হিসাব হয়।
    """
    COLOR_CHOICES = [
        ('indigo', 'Indigo'), ('sky', 'Sky'), ('emerald', 'Emerald'), ('amber', 'Amber'),
        ('rose', 'Rose'), ('violet', 'Violet'), ('teal', 'Teal'), ('slate', 'Slate'),
    ]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='shifts')
    # খালি = সব ব্রাঞ্চে ব্যবহারযোগ্য
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, null=True, blank=True, related_name='shifts',
                               help_text="খালি রাখলে সব ব্রাঞ্চে ব্যবহার করা যাবে।")
    name = models.CharField(max_length=60, help_text="যেমন: Morning, Evening, Night")
    code = models.CharField(max_length=6, blank=True, help_text="রোস্টারে ছোট করে দেখাতে (যেমন M, E, N)")
    start_time = models.TimeField()
    end_time = models.TimeField(help_text="শুরুর চেয়ে আগের সময় দিলে রাতের শিফট (পরের দিন শেষ) ধরা হবে।")
    grace_minutes = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(240)], verbose_name="Late grace (minutes)",
        help_text="শিফট শুরুর পর এত মিনিটের মধ্যে এলে Late ধরা হবে না। 0 = আগের নিয়ম।")
    break_minutes = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(480)], verbose_name="Unpaid break (minutes)",
        help_text="বিরতির সময় — শিফটের দৈর্ঘ্য ও কাজের সময় থেকে বাদ যাবে। 0 = বাদ যাবে না।")
    weekly_off_day = models.CharField(max_length=10, choices=WEEKDAYS, blank=True,
                                      help_text="খালি রাখলে ডিপার্টমেন্টের সাপ্তাহিক ছুটি।")
    color = models.CharField(max_length=10, choices=COLOR_CHOICES, default='indigo')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start_time', 'name']
        constraints = [
            models.UniqueConstraint(fields=['company', 'name'], name='uniq_shift_name_per_company'),
        ]

    def __str__(self):
        return f"{self.name} ({self.start_time:%I:%M %p}–{self.end_time:%I:%M %p})"

    @property
    def is_night(self):
        return self.end_time <= self.start_time

    @property
    def duration(self):
        from datetime import datetime as _dt, timedelta as _td
        start = _dt.combine(_dt.min.date(), self.start_time)
        end = _dt.combine(_dt.min.date(), self.end_time)
        if end <= start:
            end += _td(days=1)
        return end - start - _td(minutes=self.break_minutes or 0)

    @property
    def short(self):
        return (self.code or self.name[:1]).upper()


class ShiftAssignment(models.Model):
    """কর্মচারী কোন তারিখ থেকে কোন তারিখ পর্যন্ত কোন শিফটে (end_date খালি = এখন পর্যন্ত চলছে)"""
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='shift_assignments')
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name='assignments')
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start_date', 'id']
        indexes = [models.Index(fields=['employee', 'start_date'])]

    def __str__(self):
        return f"{self.employee} → {self.shift.name} ({self.start_date} – {self.end_date or '…'})"


class Device(models.Model):
    """
    হাজিরা মেশিন (ফেস / ফিঙ্গার / কার্ড / পাম)। একটা ডিপার্টমেন্টে একাধিক মেশিন থাকতে পারে।
    মেশিনের ইউজার ID (PIN) department এর ব্রাঞ্চের Employee.device_user_id এর সাথে মেলে।
    """
    PROTOCOL_ZK_PULL = 'zk_pull'
    PROTOCOL_ZK_ADMS = 'zk_adms'
    PROTOCOL_HIK_ISAPI = 'hik_isapi'
    PROTOCOL_HIK_PUSH = 'hik_push'
    PROTOCOL_API = 'api'
    PROTOCOL_FILE = 'file'
    PROTOCOL_CHOICES = [
        (PROTOCOL_ZK_PULL, 'ZKTeco / eSSL / compatible — LAN (IP:4370)'),
        (PROTOCOL_ZK_ADMS, 'ZKTeco / eSSL — Cloud push (ADMS, real-time)'),
        (PROTOCOL_HIK_ISAPI, 'Hikvision face/finger terminal — LAN (ISAPI)'),
        (PROTOCOL_HIK_PUSH, 'Hikvision face/finger terminal — HTTP push (real-time)'),
        (PROTOCOL_API, 'Other machine / software — JSON API'),
        (PROTOCOL_FILE, 'Offline machine — USB file import only'),
    ]
    # সার্ভার নিজে মেশিনে কানেক্ট করে যেগুলোতে
    PULL_PROTOCOLS = (PROTOCOL_ZK_PULL, PROTOCOL_HIK_ISAPI)
    # মেশিন নিজে সার্ভারে ডাটা পাঠায় যেগুলোতে
    PUSH_PROTOCOLS = (PROTOCOL_ZK_ADMS, PROTOCOL_HIK_PUSH, PROTOCOL_API)
    ONLINE_WINDOW_MINUTES = 10

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='devices')
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, related_name='devices',
        help_text="নতুন কর্মচারী এই ডিপার্টমেন্টে যোগ হবে; ID মেলানো হয় এর ব্রাঞ্চের ভেতরে।",
    )
    name = models.CharField(max_length=100, help_text="যেমন: Main gate face reader")
    protocol = models.CharField(max_length=20, choices=PROTOCOL_CHOICES, default=PROTOCOL_ZK_PULL)
    is_active = models.BooleanField(default=True)

    # LAN কানেকশন (zk_pull / hik_isapi)
    ip = models.GenericIPAddressField(blank=True, null=True, verbose_name="IP address")
    port = models.PositiveIntegerField(blank=True, null=True, help_text="ZKTeco: 4370 · Hikvision: 80")
    comm_key = models.PositiveIntegerField(default=0, verbose_name="Comm key (ZKTeco)",
                                           help_text="মেশিনে Comm Key/Password সেট থাকলে দিন, নইলে 0")
    username = models.CharField(max_length=64, blank=True, help_text="Hikvision লগইন ইউজার (সাধারণত admin)")
    password = models.CharField(max_length=128, blank=True, help_text="Hikvision লগইন পাসওয়ার্ড")

    # Push (zk_adms: সিরিয়াল নম্বর দিয়ে চেনা; hik_push / api: token দিয়ে)
    serial_number = models.CharField(max_length=64, unique=True, null=True, blank=True,
                                     help_text="মেশিনের SN (Menu → System Info → Device Info)। ADMS এর জন্য জরুরি।")
    push_token = models.CharField(max_length=64, unique=True, editable=False)
    adms_att_stamp = models.CharField(max_length=32, blank=True, editable=False)
    adms_op_stamp = models.CharField(max_length=32, blank=True, editable=False)

    # অবস্থা / তথ্য
    last_seen_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_synced_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_error = models.CharField(max_length=500, blank=True, editable=False)
    last_ip_seen = models.GenericIPAddressField(null=True, blank=True, editable=False)
    firmware = models.CharField(max_length=100, blank=True, editable=False)
    model_name = models.CharField(max_length=100, blank=True, editable=False)
    user_count = models.PositiveIntegerField(null=True, blank=True, editable=False)
    finger_count = models.PositiveIntegerField(null=True, blank=True, editable=False)
    face_count = models.PositiveIntegerField(null=True, blank=True, editable=False)
    record_count = models.PositiveIntegerField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['department__branch__name', 'name']

    def save(self, *args, **kwargs):
        if not self.push_token:
            self.push_token = secrets.token_urlsafe(32)
        if self.serial_number is not None:
            self.serial_number = self.serial_number.strip() or None
        if self.department_id and not self.company_id:
            self.company_id = self.department.company_id
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    @property
    def branch(self):
        return self.department.branch if self.department_id else None

    @property
    def is_pull(self):
        return self.protocol in self.PULL_PROTOCOLS

    @property
    def is_push(self):
        return self.protocol in self.PUSH_PROTOCOLS

    @property
    def brand(self):
        return 'hikvision' if self.protocol.startswith('hik') else ('zkteco' if self.protocol.startswith('zk') else 'other')

    @property
    def is_online(self):
        from django.utils import timezone
        from datetime import timedelta
        return bool(self.last_seen_at and
                    timezone.now() - self.last_seen_at <= timedelta(minutes=self.ONLINE_WINDOW_MINUTES))

    @property
    def default_port(self):
        return 80 if self.protocol == self.PROTOCOL_HIK_ISAPI else 4370


class DeviceCommand(models.Model):
    """ADMS মেশিনের জন্য কমান্ডের সারি — মেশিন পরের বার /iclock/getrequest এ এলে নিয়ে যায়।"""
    STATUS_CHOICES = [('pending', 'Pending'), ('sent', 'Sent'), ('done', 'Done'), ('failed', 'Failed')]

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name='commands')
    command = models.TextField()
    label = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', db_index=True)
    return_code = models.CharField(max_length=20, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.device} · {self.label or self.command[:40]} ({self.status})"


class PunchLog(models.Model):
    """
    মেশিনের প্রতিটা কাঁচা পাঞ্চ (কোন মেশিন, কোন পদ্ধতি — ফেস/ফিঙ্গার/কার্ড)।
    Attendance (দিনের In/Out) এখান থেকে হিসাব হয়, তাই রিয়েল-টাইমে একটা একটা পাঞ্চ এলেও Out ঠিক থাকে।
    """
    METHOD_CHOICES = [
        ('face', 'Face'), ('finger', 'Fingerprint'), ('card', 'Card'), ('palm', 'Palm'),
        ('password', 'Password'), ('manual', 'Manual'), ('other', 'Other'),
    ]
    SOURCE_CHOICES = [
        ('pull', 'LAN sync'), ('adms', 'ZKTeco cloud push'), ('hik', 'Hikvision'),
        ('agent', 'Push agent'), ('api', 'API'), ('file', 'File import'),
    ]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='punch_logs')
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    device = models.ForeignKey(Device, on_delete=models.SET_NULL, null=True, blank=True, related_name='punches')
    employee = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name='punches')
    device_user_id = models.IntegerField()
    timestamp = models.DateTimeField(db_index=True)
    method = models.CharField(max_length=10, choices=METHOD_CHOICES, default='other')
    verify_code = models.SmallIntegerField(null=True, blank=True)
    punch_state = models.SmallIntegerField(null=True, blank=True)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES)
    # "company:branch" — একই ব্রাঞ্চে একই মানুষের একই সেকেন্ডের পাঞ্চ একবারই থাকে (NULL ছাড়া ইউনিক)
    scope_key = models.CharField(max_length=40, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        constraints = [
            models.UniqueConstraint(fields=['scope_key', 'device_user_id', 'timestamp'], name='uniq_punch_per_scope'),
        ]
        indexes = [
            models.Index(fields=['company', 'timestamp']),
            models.Index(fields=['scope_key', 'device_user_id', 'timestamp']),
        ]

    @staticmethod
    def make_scope_key(company_id, branch_id):
        return f"{company_id}:{branch_id or 0}"

    def __str__(self):
        return f"{self.device_user_id} @ {self.timestamp} ({self.method})"


class HRSettings(models.Model):
    """
    কোম্পানির HR নিয়ম (প্রতি কোম্পানিতে একটাই — for_company() দিয়ে নিন)।
    সব টগল ডিফল্টে বন্ধ: চালু না করা পর্যন্ত বেতন ও হাজিরার হিসাব আগের মতোই।
    """
    BASIS_WORKING = 'working'
    BASIS_CALENDAR = 'calendar'
    BASIS_CHOICES = [
        (BASIS_WORKING, 'মূল বেতন ÷ মাসের কর্মদিবস'),
        (BASIS_CALENDAR, 'মূল বেতন ÷ মাসের মোট দিন (৩০/৩১)'),
    ]

    company = models.OneToOneField(Company, on_delete=models.CASCADE, related_name='hr_settings')

    enable_late_deduction = models.BooleanField(
        default=False, verbose_name="Late deduction চালু",
        help_text="নির্দিষ্ট সংখ্যক দিন দেরি হলে বেতন থেকে ১ দিনের বেতন কাটা হবে।")
    lates_equal_one_day_deduction = models.PositiveSmallIntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(31)],
        verbose_name="কত দিন দেরি = ১ দিনের বেতন কাটা",
        help_text="যেমন 3 দিলে: ৩–৫ দিন দেরি = ১ দিন, ৬–৮ দিন = ২ দিন কাটা।")
    late_deduction_basis = models.CharField(
        max_length=10, choices=BASIS_CHOICES, default=BASIS_WORKING,
        verbose_name="১ দিনের বেতন হিসাব")

    enable_missing_punch_alert = models.BooleanField(
        default=False, verbose_name="Missing punch alert চালু",
        help_text="প্রতিদিন রাত ১১:৫৯ এ — যাদের In আছে কিন্তু Out নেই, তাদের তালিকা ও নোটিফিকেশন।")

    enable_gps_attendance = models.BooleanField(
        default=False, verbose_name="GPS attendance চালু",
        help_text="কর্মচারীর পোর্টালে মোবাইল থেকে GPS লোকেশন + সেলফি দিয়ে হাজিরা।")

    # ── SMS / WhatsApp ──
    CHANNEL_CHOICES = [('sms', 'SMS'), ('whatsapp', 'WhatsApp')]
    alert_channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES, default='sms', verbose_name="পাঠানোর মাধ্যম")
    enable_daily_absence_alert = models.BooleanField(
        default=False, verbose_name="প্রতিদিনের Absent/Late তালিকা ম্যানেজারকে",
        help_text="প্রতিটা ডিপার্টমেন্টের ম্যানেজারের নম্বরে (Departments → Edit) দিনে একবার।")
    daily_alert_time = models.TimeField(default='11:00', verbose_name="কখন পাঠাবে",
                                        help_text="অফিস শুরুর পরের একটা সময় দিন (যেমন 11:00)।")
    enable_salary_sms = models.BooleanField(
        default=False, verbose_name="বেতন দেওয়ার SMS কর্মচারীকে",
        help_text="বেতনের পেজের 'Send salary SMS' বাটনে — পে-স্লিপের নিরাপদ লিংকসহ (কর্মচারীর প্রোফাইলের মোবাইল নম্বরে)।")

    # ── অচেনা ID ──
    auto_create_unknown_ids = models.BooleanField(
        default=False, verbose_name="অচেনা ID থেকে নিজে কর্মচারী বানাও",
        help_text="বন্ধ থাকলে (প্রস্তাবিত) মেশিনের অচেনা ID এর পাঞ্চ 'Unknown punches' এ জমা হয় ও অ্যাডমিন সতর্কবার্তা পান।")

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        verbose_name = "HR settings"
        verbose_name_plural = "HR settings"

    def __str__(self):
        return f"HR settings · {self.company}"

    @classmethod
    def for_company(cls, company):
        """কোম্পানির সেটিং; না থাকলে ডিফল্ট (সব বন্ধ) দিয়ে বানায়"""
        if company is None:
            return cls()
        obj, _ = cls.objects.get_or_create(company=company)
        return obj


class MissingPunch(models.Model):
    """যেদিন কর্মচারীর In আছে কিন্তু Out নেই (রাতের চেক বানায়; পরে Out এলে নিজে থেকেই resolved)"""
    STATUS_OPEN = 'open'
    STATUS_RESOLVED = 'resolved'
    STATUS_CHOICES = [(STATUS_OPEN, 'Missing Out-Punch'), (STATUS_RESOLVED, 'Resolved')]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='missing_punches')
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='missing_punches')
    date = models.DateField(db_index=True)
    in_time = models.DateTimeField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', 'employee__name']
        constraints = [models.UniqueConstraint(fields=['employee', 'date'], name='uniq_missing_punch_per_day')]

    def __str__(self):
        return f"{self.employee} · {self.date} ({self.get_status_display()})"


class Notification(models.Model):
    """সফটওয়্যারের ভেতরের নোটিফিকেশন (হেডারের ঘণ্টা)"""
    KIND_CHOICES = [('missing_punch', 'Missing punch'), ('device', 'Device'), ('payroll', 'Payroll'), ('info', 'Info'),
                    ('leave', 'Leave request')]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='notifications')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default='info')
    title = models.CharField(max_length=150)
    message = models.TextField(blank=True)
    link = models.CharField(max_length=300, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', 'is_read', '-created_at'])]

    def __str__(self):
        return f"{self.user} · {self.title}"


class UnknownPunch(models.Model):
    """মেশিন থেকে আসা পাঞ্চ যার ID কোনো কর্মচারীর সাথে মেলে না (বা Hikvision এর অচেনা মুখ)"""
    REASON_CHOICES = [('unmapped', 'Unknown ID'), ('stranger', 'Unknown face / stranger')]
    STATUS_CHOICES = [('open', 'Open'), ('resolved', 'Employee added'), ('ignored', 'Ignored')]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='unknown_punches')
    device = models.ForeignKey('Device', on_delete=models.SET_NULL, null=True, blank=True, related_name='unknown_punches')
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    raw_id = models.CharField(max_length=64, blank=True, help_text="মেশিনের ইউজার ID (অচেনা মুখে খালি)")
    timestamp = models.DateTimeField(db_index=True)
    method = models.CharField(max_length=10, default='other')
    reason = models.CharField(max_length=10, choices=REASON_CHOICES, default='unmapped')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='open', db_index=True)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        constraints = [models.UniqueConstraint(fields=['company', 'raw_id', 'timestamp', 'reason'],
                                               name='uniq_unknown_punch')]

    def __str__(self):
        return f"{self.get_reason_display()} {self.raw_id} @ {self.timestamp}"


class MessageLog(models.Model):
    """প্রতিটা SMS/WhatsApp — কাকে, কী, পাঠানো গেল কিনা। dedupe_key দিয়ে একই বার্তা দুবার যায় না।"""
    STATUS_CHOICES = [('sent', 'Sent'), ('logged', 'Logged only (no gateway)'), ('failed', 'Failed'), ('skipped', 'Skipped')]
    KIND_CHOICES = [('daily_alert', 'Daily absent/late'), ('salary', 'Salary disbursed'), ('test', 'Test')]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='messages')
    channel = models.CharField(max_length=10, default='sms')
    to = models.CharField(max_length=20, blank=True)
    text = models.TextField()
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, db_index=True)
    provider = models.CharField(max_length=20, blank=True)
    response = models.CharField(max_length=500, blank=True)
    dedupe_key = models.CharField(max_length=120, null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.channel} → {self.to} ({self.status})"


class EmployeeTransfer(models.Model):
    """
    এক ব্রাঞ্চ/ডিপার্টমেন্ট থেকে আরেকটায় বদলির ইতিহাস।
    রিপোর্টে কোনো দিনে কর্মচারী কোন ব্রাঞ্চে ছিল তা এখান থেকে বের হয় (attendance_helpers.branch_on)।
    """
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='transfers')
    from_branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    to_branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    from_department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    to_department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    effective_date = models.DateField(help_text="এই তারিখ থেকে নতুন ব্রাঞ্চে কাজ শুরু")
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['effective_date', 'id']

    def __str__(self):
        return f"{self.employee} → {self.to_branch} ({self.effective_date})"

