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

    def __str__(self):
        return f"{self.employee.name} - {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')} - {self.status}"

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
    leave_type = models.CharField(max_length=20, choices=LEAVE_TYPES)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='Pending')
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    applied_on = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.employee.name} - {self.leave_type} ({self.start_date} to {self.end_date})"


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

