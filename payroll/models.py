# payroll/models.py
from django.db import models
from attendance_app.models import Employee
from datetime import date
from decimal import Decimal

class EmployeeSalary(models.Model):
    employee = models.OneToOneField('attendance_app.Employee', on_delete=models.CASCADE)
    base_salary = models.DecimalField(max_digits=10, decimal_places=2)
    bank_transfer_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    company = models.ForeignKey(
    'attendance_app.Company', 
    on_delete=models.CASCADE, 
    blank=True, 
    null=True
)


    def __str__(self):
        return f"{self.employee.name} - {self.base_salary}"

    @property
    def cash_amount(self):
        return max(self.base_salary - self.bank_transfer_amount, Decimal(0))


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

