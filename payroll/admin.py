from django.contrib import admin
from .models import EmployeeSalary, SalarySummary
from .forms import EmployeeSalaryForm
@admin.register(EmployeeSalary)
class EmployeeSalaryAdmin(admin.ModelAdmin):
    list_display = ('employee', 'base_salary', 'company')
    search_fields = ('employee__name',)
    list_filter = ('company',)
    form = EmployeeSalaryForm



@admin.register(SalarySummary)
class SalarySummaryAdmin(admin.ModelAdmin):
    list_display = (
        'employee', 'month', 'base_salary', 'final_salary',
        'present_days', 'absent_days', 'leave_days', 'weekly_off_days'
    )
    list_filter = ('month', 'employee__department')
    search_fields = ('employee__name', 'month')
    date_hierarchy = 'generated_on'
    list_select_related = ('employee',)
