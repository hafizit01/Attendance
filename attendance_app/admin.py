from django.contrib import admin
from .models import Company, Department, Employee, Attendance, LeaveRequest, Holiday, UserProfile

# employees/admin.py
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from .models import Employee
from .forms import EmployeeForm
from .services import create_employee_with_limit


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    search_fields = ['name'] 

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'company']
    search_fields = ['user__username', 'company__name']

@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ['name', 'company', 'weekly_off_day']
    list_filter = ['company']
    search_fields = ['name']  # ← এটা যোগ করা জরুরি



@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    form = EmployeeForm
    list_display = ['name', 'company', 'device_user_id', 'department', 'is_active']
    list_filter = ['company', 'department', 'is_active']
    search_fields = ['name', 'device_user_id', 'user__username', 'user__email']
    list_select_related = ['company', 'department', 'user']
    autocomplete_fields = ['department', 'user']  # ← company বাদ
    ordering = ['company__name', 'name']

    def _owner_company(self, request):
        # যদি Company.owner ফিল্ড থাকে
        try:
            return Company.objects.get(owner=request.user)
        except Company.DoesNotExist:
            return None

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        comp = self._owner_company(request)
        return qs.filter(company=comp) if comp else qs.none()

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if not request.user.is_superuser:
            comp = self._owner_company(request)
            if 'department' in form.base_fields and comp:
                form.base_fields['department'].queryset = Department.objects.filter(company=comp)
        return form

    def save_model(self, request, obj, form, change):
        if change:
            return super().save_model(request, obj, form, change)

        # company form.save() এ সেট হয়; তবু সেফটি:
        if not obj.company and obj.department and getattr(obj.department, "company", None):
            obj.company = obj.department.company

        company = obj.company or (obj.department.company if obj.department else None)
        if company is None:
            self.message_user(request, "Company is required (via Department).", level=messages.ERROR)
            raise ValidationError("Company is required.")

        try:
            with transaction.atomic():
                emp = create_employee_with_limit(
                    company=company,
                    name=obj.name,
                    device_user_id=obj.device_user_id,
                    department=obj.department,
                    user=getattr(obj, "user", None),
                )
                obj.pk = emp.pk
        except ValidationError as e:
            self.message_user(request, "; ".join(e.messages), level=messages.ERROR)
            raise
        except IntegrityError:
            self.message_user(request, "This device_user_id already exists for the selected company.", level=messages.ERROR)
            raise



@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ['employee', 'leave_type', 'start_date', 'end_date', 'status', 'approved_by']
    list_filter = ['status', 'leave_type']
    search_fields = ['employee__name']



@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('employee', 'company', 'timestamp', 'status')
    list_filter = ('company', 'status')
    search_fields = ('employee__name', 'employee__device_user_id')
    ordering = ('-timestamp',)



admin.site.register(Holiday)

