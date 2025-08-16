# payroll/models.py
from django.db import models
from attendance_app.models import Employee
from datetime import date
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

