from django import forms
from django.core.exceptions import ValidationError
from .models import *

# employees/forms.py
from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from .models import Employee
from subscription_app.utils_limit import get_employee_limit_for

class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = ['name', 'device_user_id', 'department']  # ← company বাদ
        widgets = {
            'name': forms.TextInput(attrs={ 'class': 'w-full border border-gray-300 rounded px-3 py-2' }),
            'device_user_id': forms.NumberInput(attrs={ 'class': 'w-full border border-gray-300 rounded px-3 py-2' }),
            'department': forms.Select(attrs={ 'class': 'w-full border border-gray-300 rounded px-3 py-2' }),
        }

    def _has_is_active(self):
        return any(f.name == "is_active" for f in Employee._meta.get_fields())

    def clean(self):
        cleaned = super().clean()
        department = cleaned.get('department')
        if not department or not getattr(department, "company", None):
            raise ValidationError(_("Please select a department that belongs to a company."))

        company = department.company

        # limit check
        limit = get_employee_limit_for(company)
        qs = Employee.objects.filter(company=company)
        if self._has_is_active():
            qs = qs.filter(is_active=True)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)

        current = qs.count()
        if limit is not None and current >= limit:
            raise ValidationError(
            _("❌ Cannot add employee — limit reached %(current)d/%(limit)d.")
            % {"current": current, "limit": limit}
        )



        # save() এ ব্যবহার করার জন্য stash করে রাখলাম
        self._resolved_company = company
        return cleaned

    def clean_device_user_id(self):
        device_user_id = self.cleaned_data.get('device_user_id')
        department = self.cleaned_data.get('department')
        company = getattr(department, "company", None)
        if company:
            qs = Employee.objects.filter(company=company, device_user_id=device_user_id)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(_(f"Device User ID {device_user_id} already exists in {company.name}."))
        return device_user_id

    def save(self, commit=True):
        obj = super().save(commit=False)
        # company auto-set from department
        if hasattr(self, "_resolved_company"):
            obj.company = self._resolved_company
        else:
            # সেফটি নেট: department থেকে সেট করো
            if obj.department and getattr(obj.department, "company", None):
                obj.company = obj.department.company
        if commit:
            obj.save()
        return obj




from django import forms
from .models import Department

class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = "__all__"   # 👉 সব ফিল্ড দেখাবে
        widgets = {
            "company": forms.Select(attrs={
                "class": "w-full border rounded px-3 py-2",
            }),
            "name": forms.TextInput(attrs={
                "class": "w-full border rounded px-3 py-2",
                "placeholder": "Department name",
            }),
            "weekly_off_day": forms.Select(attrs={
                "class": "w-full border rounded px-3 py-2",
            }),
            "device_ip": forms.TextInput(attrs={
                "class": "w-full border rounded px-3 py-2",
                "placeholder": "Device IP (optional)",
            }),
            "device_port": forms.NumberInput(attrs={
                "class": "w-full border rounded px-3 py-2",
                "placeholder": "Device Port (optional)",
            }),
            "in_time": forms.TimeInput(attrs={
                "type": "time",
                "class": "w-full border rounded px-3 py-2",
            }, format="%H:%M"),
            "out_time": forms.TimeInput(attrs={
                "type": "time",
                "class": "w-full border rounded px-3 py-2",
            }, format="%H:%M"),
        }



class AttendanceForm(forms.ModelForm):
    class Meta:
        model = Attendance
        fields = ['employee', 'timestamp', 'status']
        widgets = {
            "employee": forms.Select(attrs={"class": "w-full rounded border px-3 py-2 dark:bg-gray-900 dark:text-white"}),
            "timestamp": forms.DateTimeInput(attrs={"type": "datetime-local", "class": "w-full rounded border px-3 py-2 dark:bg-gray-900 dark:text-white"}),
            "status": forms.Select(attrs={"class": "w-full rounded border px-3 py-2 dark:bg-gray-900 dark:text-white"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        employee = cleaned_data.get('employee')
        timestamp = cleaned_data.get('timestamp')
        status = cleaned_data.get('status')

        if employee and timestamp and status:
            date = timestamp.date()

            # আজকের দিনের সকল attendance status বের করি
            attendance_qs = Attendance.objects.filter(
                employee=employee,
                timestamp__date=date
            )

            # যদি edit mode হয়, তাহলে নিজের টা বাদ দিয়ে হিসাব করব
            if self.instance.pk:
                attendance_qs = attendance_qs.exclude(pk=self.instance.pk)

            statuses = attendance_qs.values_list('status', flat=True)

            # Check for duplicate In or Out
            if 'In' in statuses and status == 'In':
                raise ValidationError("আজকের জন্য In টাইম ইতিমধ্যেই যুক্ত হয়েছে।")

            if 'Out' in statuses and status == 'Out':
                raise ValidationError("আজকের জন্য Out টাইম ইতিমধ্যেই যুক্ত হয়েছে।")

            # Limit to max 2 entries
            if attendance_qs.count() >= 2:
                raise ValidationError("এই কর্মচারীর আজকের জন্য In এবং Out টাইম ইতিমধ্যেই আছে।")

        return cleaned_data



from django import forms
from .models import LeaveRequest, Employee

class LeaveRequestForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = '__all__'
        widgets = {
            "leave_type": forms.Select(attrs={"class": "w-full px-3 py-2 border rounded-md dark:bg-gray-900 dark:text-white"}),
            "start_date": forms.DateInput(attrs={"type": "date", "class": "w-full px-3 py-2 border rounded-md dark:bg-gray-900 dark:text-white"}),
            "end_date": forms.DateInput(attrs={"type": "date", "class": "w-full px-3 py-2 border rounded-md dark:bg-gray-900 dark:text-white"}),
            "reason": forms.Textarea(attrs={"rows": 3, "class": "w-full px-3 py-2 border rounded-md dark:bg-gray-900 dark:text-white"}),
            "company": forms.Select(attrs={"class": "w-full px-3 py-2 border rounded-md dark:bg-gray-900 dark:text-white"}),
            "employee": forms.Select(attrs={"class": "w-full px-3 py-2 border rounded-md dark:bg-gray-900 dark:text-white"}),
            "status": forms.Select(attrs={"class": "w-full px-3 py-2 border rounded-md dark:bg-gray-900 dark:text-white"}),
            "approved_by": forms.Select(attrs={"class": "w-full px-3 py-2 border rounded-md dark:bg-gray-900 dark:text-white"}),
            "applied_on": forms.Select(attrs={"class": "w-full px-3 py-2 border rounded-md dark:bg-gray-900 dark:text-white"}),

        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)  # view থেকে user পাস করা হবে
        super().__init__(*args, **kwargs)
        if user:
            company = getattr(user.profile, 'company', None)
            if company:
                # শুধু user-এর company এর employees দেখাবে
                self.fields['employee'].queryset = Employee.objects.filter(company=company)
            else:
                self.fields['employee'].queryset = Employee.objects.none()




class HolidayForm(forms.ModelForm):
    class Meta:
        model = Holiday
        fields = ['title', 'start_date', 'end_date', 'description']
        widgets = {
            'title': forms.TextInput(attrs={
                'class': 'w-full border border-gray-300 rounded-md px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500',
                'placeholder': 'Holiday title'
            }),
            'start_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'w-full border border-gray-300 rounded-md px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500'
            }),
            'end_date': forms.DateInput(attrs={
                'type': 'date',
                'class': 'w-full border border-gray-300 rounded-md px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500'
            }),
            'description': forms.Textarea(attrs={
                'rows': 4,
                'class': 'w-full border border-gray-300 rounded-md px-4 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500',
                'placeholder': 'Optional description'
            }),
        }


