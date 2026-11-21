# payroll/models.py
from django.db import models
from attendance_app.models import Employee
from datetime import date, timedelta
from decimal import Decimal
from decimal import Decimal
from django.db import models

class EmployeeSalary(models.Model):
    employee = models.OneToOneField(
        'attendance_app.Employee',
        on_delete=models.CASCADE
    )
    base_salary = models.DecimalField(max_digits=10, decimal_places=2)
    bank_transfer_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    company = models.ForeignKey(
        'attendance_app.Company',
        on_delete=models.CASCADE,
        blank=True,
        null=True
    )

    # 🎁 Bonus ফিল্ডগুলো
    yearly_bonus_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Base salary এর কত % বোনাস হবে (যদি 0 থাকে তবে fixed bonus ব্যবহার হবে)"
    )
    yearly_bonus_fixed = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="এককালীন fixed bonus (percent 0 হলে)"
    )
    bonus_payout_month = models.PositiveSmallIntegerField(
        default=12,
        help_text="কোন মাসে বোনাস দেওয়া হবে (1-12)"
    )

    def __str__(self):
        return f"{self.employee.name} - {self.base_salary}"

    @property
    def cash_amount(self):
        """ক্যাশ অংশ (base - bank transfer)"""
        return max(self.base_salary - self.bank_transfer_amount, Decimal(0))

    # 🎁 Bonus হিসাব
    def yearly_bonus_amount(self) -> Decimal:
        """Fixed বা percentage দিয়ে বোনাস এমাউন্ট বের করো"""
        base = self.base_salary or Decimal(0)
        if self.yearly_bonus_percent and self.yearly_bonus_percent > 0:
            return (base * self.yearly_bonus_percent) / Decimal('100.00')
        return self.yearly_bonus_fixed or Decimal(0)

    def bonus_for_month(self, year: int, month: int) -> Decimal:
        """
        নির্দিষ্ট payout মাসেই বোনাস যোগ করবে, নাহলে 0.00
        """
        if int(month) == int(self.bonus_payout_month):
            return self.yearly_bonus_amount()
        return Decimal('0.00')



class SalaryComponent(models.Model):
    """
    বেতনের অংশ (ক্যাটাগরি) — কোম্পানি নিজে ঠিক করে: Earning (House Rent, Medical, Transport…) বা
    Deduction (Tax, PF, Canteen…)। প্রতি কর্মচারীর পরিমাণ EmployeeSalaryComponent এ।
    কোনো অংশ না থাকলে Gross = Basic — বেতনের হিসাব আগের মতো।
    """
    KIND_EARNING = 'earning'
    KIND_DEDUCTION = 'deduction'
    KIND_CHOICES = [(KIND_EARNING, 'Earning / allowance'), (KIND_DEDUCTION, 'Deduction')]
    CALC_CHOICES = [('fixed', 'Fixed amount (৳)'), ('percent', '% of basic salary')]

    company = models.ForeignKey('attendance_app.Company', on_delete=models.CASCADE, related_name='salary_components')
    name = models.CharField(max_length=60)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=KIND_EARNING)
    calc_type = models.CharField(max_length=10, choices=CALC_CHOICES, default='fixed',
                                 help_text="নতুন কর্মচারীর বেতন সেট করার সময় ডিফল্ট কত বসবে")
    default_value = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'),
                                        help_text="Fixed হলে টাকা, % হলে মূল বেতনের শতাংশ")
    prorate = models.BooleanField(default=True, verbose_name="Attendance অনুযায়ী কমবে-বাড়বে",
                                  help_text="Earning: অনুপস্থিত দিনে আনুপাতিক কাটা (মূল বেতনের মতো)। Deduction এ প্রযোজ্য নয়।")
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['kind', 'order', 'id']
        constraints = [models.UniqueConstraint(fields=['company', 'name'], name='uniq_salary_component_name')]

    def __str__(self):
        return self.name

    def default_for(self, basic):
        if self.calc_type == 'percent':
            return (Decimal(basic or 0) * self.default_value / Decimal(100)).quantize(Decimal('0.01'))
        return Decimal(self.default_value)

    DEFAULTS = [
        ('House Rent', 'earning', 'percent', Decimal('0')),
        ('Medical', 'earning', 'fixed', Decimal('0')),
        ('Transport', 'earning', 'fixed', Decimal('0')),
        ('Tax / PF', 'deduction', 'fixed', Decimal('0')),
    ]

    @classmethod
    def ensure_defaults(cls, company):
        """প্রথমবার: সাধারণ ৪টা অংশ (সব ০ — কারো বেতন বদলায় না)"""
        if not cls.objects.filter(company=company).exists():
            cls.objects.bulk_create([cls(company=company, name=n, kind=k, calc_type=c, default_value=v, order=i)
                                     for i, (n, k, c, v) in enumerate(cls.DEFAULTS)])


class EmployeeSalaryComponent(models.Model):
    salary = models.ForeignKey(EmployeeSalary, on_delete=models.CASCADE, related_name='components')
    component = models.ForeignKey(SalaryComponent, on_delete=models.CASCADE, related_name='employee_values')
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))

    class Meta:
        constraints = [models.UniqueConstraint(fields=['salary', 'component'], name='uniq_salary_component_value')]


class SalaryChange(models.Model):
    """বেতন কাঠামো বদলের ইতিহাস (History বাটন)"""
    salary = models.ForeignKey(EmployeeSalary, on_delete=models.CASCADE, related_name='history')
    basic = models.DecimalField(max_digits=10, decimal_places=2)
    gross = models.DecimalField(max_digits=10, decimal_places=2)
    deductions = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    bank_transfer = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))
    detail = models.JSONField(default=dict, blank=True)
    changed_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-changed_at', '-id']


def salary_breakdown(salary):
    """
    EmployeeSalary → dict(basic, earnings[(name, amount, prorate)], deductions[(name, amount)],
    gross, prorated_gross, fixed_deductions, net) — শুধু চালু (is_active) অংশ, ০ বাদ।
    gross = basic + সব earning;  prorated_gross = basic + যেসব earning attendance অনুযায়ী কমে-বাড়ে
    """
    # `or 0` দিলে 0.00 → 0 হয়ে যেত; মান হুবহু রাখি (আগের হিসাবের সাথে অক্ষরে অক্ষরে মেলে)
    basic = salary.base_salary if salary.base_salary is not None else Decimal(0)
    earnings, deductions = [], []
    rows = salary.components.all() if salary.pk else []
    for c in sorted(rows, key=lambda r: (r.component.order, r.component_id)):
        if not c.component.is_active or not c.amount:
            continue
        if c.component.kind == SalaryComponent.KIND_EARNING:
            earnings.append((c.component.name, Decimal(c.amount), c.component.prorate))
        else:
            deductions.append((c.component.name, Decimal(c.amount)))
    # কোনো অংশ না থাকলে Basic যেমন আছে তেমনই (Decimal যোগে ঘর/এক্সপোনেন্ট বদলায় না)
    gross = basic + sum((a for _n, a, _p in earnings), Decimal(0)) if earnings else basic
    prorated = basic + sum((a for _n, a, p in earnings if p), Decimal(0)) if any(p for _n, _a, p in earnings) else basic
    fixed_ded = sum((a for _n, a in deductions), Decimal(0))
    return {'basic': basic, 'earnings': earnings, 'deductions': deductions, 'gross': gross,
            'prorated_gross': prorated, 'fixed_earnings': gross - prorated,
            'fixed_deductions': fixed_ded, 'net': gross - fixed_ded}


class EmployeeLoan(models.Model):
    """
    লোন / অগ্রিম বেতন। start_month থেকে প্রতি মাসে বেতন থেকে কিস্তি (EMI) কাটা হয়, পুরো টাকা না ওঠা পর্যন্ত।
    কোন মাসে কত কাটবে তা হিসাব থেকে বের হয় (schedule) — বেতনের পেজ যতবার খোলা হোক একই থাকে।
    হাতে টাকা ফেরত দিলে (LoanRepayment) বাকি কমে, শেষের কিস্তি আগে শেষ হয়।
    """
    TYPE_CHOICES = [('loan', 'Loan'), ('advance', 'Salary advance')]
    STATUS_CHOICES = [('active', 'Active'), ('closed', 'Closed')]

    company = models.ForeignKey('attendance_app.Company', on_delete=models.CASCADE, related_name='loans')
    employee = models.ForeignKey('attendance_app.Employee', on_delete=models.CASCADE, related_name='loans')
    loan_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default='loan')
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Total amount (৳)")
    emi_count = models.PositiveSmallIntegerField(default=1, verbose_name="Number of EMIs (months)")
    monthly_deduction = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Monthly deduction (৳)",
                                            help_text="খালি রাখলে মোট ÷ কিস্তির সংখ্যা।")
    start_month = models.DateField(help_text="যে মাসের বেতন থেকে কাটা শুরু (মাসের ১ তারিখ হিসেবে রাখা হয়)")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active',
                              help_text="Closed = এই মাস থেকে আর কাটা হবে না (আগের মাসের বেতন যেমন ছিল থাকে)")
    closed_from = models.DateField(null=True, blank=True, editable=False,
                                   help_text="এই মাস থেকে কাটা বন্ধ (Closed করার মাস)")
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_month', '-id']

    def __str__(self):
        return f"{self.employee.name} · {self.get_loan_type_display()} ৳{self.amount}"

    def save(self, *args, **kwargs):
        from django.utils import timezone
        self.start_month = self.start_month.replace(day=1)
        if not self.monthly_deduction and self.emi_count:
            self.monthly_deduction = (Decimal(self.amount) / self.emi_count).quantize(Decimal('0.01'))
        if self.status == 'closed' and not self.closed_from:
            self.closed_from = timezone.localdate().replace(day=1)
        elif self.status == 'active':
            self.closed_from = None
        super().save(*args, **kwargs)

    # ---------- হিসাব ----------
    @staticmethod
    def next_month(d):
        return (d.replace(day=28) + timedelta(days=4)).replace(day=1)

    def _manual_before(self, month_start):
        return sum((r.amount for r in self.repayments.all() if r.date < month_start), Decimal(0))

    def schedule(self, until=None):
        """
        [(মাস, কাটা, কাটার পর বাকি)] — start_month থেকে বাকি ০ হওয়া পর্যন্ত (বা until মাস পর্যন্ত)।
        প্রতি মাসে বাকি = মোট − আগের মাসগুলোতে বেতন থেকে কাটা − ওই মাসের শেষ পর্যন্ত হাতে ফেরত।
        """
        rows, from_salary, month = [], Decimal(0), self.start_month
        for _ in range(600):
            if until is not None and month > until:
                break
            if self.closed_from and month >= self.closed_from:
                break
            # ওই মাসের ভেতরে হাতে ফেরত দিলেও সেটা ধরা হয় — যাতে বেশি কাটা না পড়ে
            remaining = max(Decimal(self.amount) - from_salary - self._manual_before(self.next_month(month)), Decimal(0))
            if remaining <= 0:
                break
            cut = min(Decimal(self.monthly_deduction), remaining)
            from_salary += cut
            rows.append((month, cut, remaining - cut))
            month = self.next_month(month)
        return rows

    def deduction_for(self, year, month):
        """এই মাসের বেতন থেকে কত কাটবে"""
        target = date(year, month, 1)
        if target < self.start_month:
            return Decimal(0)
        for m, cut, _r in self.schedule(until=target):
            if m == target:
                return cut
        return Decimal(0)

    def recovered_until(self, year, month):
        """এই মাস পর্যন্ত (সহ) মোট কত উঠেছে (বেতন থেকে + হাতে)"""
        target = date(year, month, 1)
        from_salary = sum((c for _m, c, _r in self.schedule(until=target)), Decimal(0))
        return min(from_salary + self._manual_before(self.next_month(target)), Decimal(self.amount))

    @property
    def remaining_now(self):
        from django.utils import timezone
        today = timezone.localdate()
        return max(Decimal(self.amount) - self.recovered_until(today.year, today.month), Decimal(0))

    @property
    def end_month(self):
        """শেষ কিস্তির মাস (Closed হলে বা বাকি না মিটলে None)"""
        rows = self.schedule()
        return rows[-1][0] if rows and rows[-1][2] == 0 else None

    @property
    def is_recovered(self):
        return self.remaining_now <= 0


class LoanRepayment(models.Model):
    """বেতনের বাইরে হাতে ফেরত দেওয়া টাকা"""
    loan = models.ForeignKey(EmployeeLoan, on_delete=models.CASCADE, related_name='repayments')
    date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return f"{self.loan} · ৳{self.amount} on {self.date}"


class SalarySummary(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    company = models.ForeignKey('attendance_app.Company', on_delete=models.CASCADE, default='')  # নতুন field
    month = models.CharField(max_length=7)  # Format: YYYY-MM
    base_salary = models.DecimalField(max_digits=10, decimal_places=2)
    present_days = models.IntegerField()
    absent_days = models.IntegerField()
    leave_days = models.IntegerField()
    weekly_off_days = models.IntegerField()
    total_work_hours = models.DurationField()
    late_time = models.DurationField()
    early_leave_time = models.DurationField()
    over_time = models.DurationField()
    final_salary = models.DecimalField(max_digits=10, decimal_places=2)
    generated_on = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.employee.name} - {self.month}"

