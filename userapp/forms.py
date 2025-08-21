from django import forms
from .models import EmployeeProfile
from payroll.models import EmployeeSalary

class EmployeeProfileForm(forms.ModelForm):
    class Meta:
        model = EmployeeProfile
        fields = [
            "employee", "designation", "date_of_birth", "gender", "marital_status", "national_id",
            "mobile_number", "email", "address",
            "emergency_contact_name", "emergency_contact_number",
            "join_date", "leave_date", "employee_code", "job_type", "shift",
            "bank_account", "bank_name", "branch_name", "tax_id",
            "image", "resume", "offer_letter", "joining_letter"
        ]
        widgets = {
            "employee": forms.Select(attrs={"class": "w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:text-white"}),

            # Personal Info
            "designation": forms.TextInput(attrs={"class": "w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:text-white"}),
            "date_of_birth": forms.DateInput(attrs={"type": "date", "class": "w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:text-white"}),
            "gender": forms.Select(attrs={"class": "w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:text-white"}),
            "marital_status": forms.Select(attrs={"class": "w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:text-white"}),
            "national_id": forms.TextInput(attrs={"class": "w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:text-white"}),

            # Contact
            "mobile_number": forms.TextInput(attrs={"class": "w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:text-white"}),
            "email": forms.EmailInput(attrs={"class": "w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:text-white"}),
            "address": forms.Textarea(attrs={"rows":3, "class": "w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:text-white"}),
            "emergency_contact_name": forms.TextInput(attrs={"class": "w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:text-white"}),
            "emergency_contact_number": forms.TextInput(attrs={"class": "w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:text-white"}),

            # Job Info
            "join_date": forms.DateInput(attrs={"type": "date", "class": "w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:text-white"}),
            "leave_date": forms.DateInput(attrs={"type": "date", "class": "w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:text-white"}),
            "employee_code": forms.TextInput(attrs={"class": "w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:text-white"}),
            "job_type": forms.Select(attrs={"class": "w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:text-white"}),
            "shift": forms.TextInput(attrs={"class": "w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:text-white"}),

            # Finance
            "bank_account": forms.TextInput(attrs={"class": "w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:text-white"}),
            "bank_name": forms.TextInput(attrs={"class": "w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:text-white"}),
            "branch_name": forms.TextInput(attrs={"class": "w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:text-white"}),
            "tax_id": forms.TextInput(attrs={"class": "w-full px-3 py-2 border rounded-lg dark:bg-gray-700 dark:text-white"}),

            # Files
            "image": forms.ClearableFileInput(attrs={"class": "w-full text-gray-700 dark:text-white"}),
            "resume": forms.ClearableFileInput(attrs={"class": "w-full text-gray-700 dark:text-white"}),
            "offer_letter": forms.ClearableFileInput(attrs={"class": "w-full text-gray-700 dark:text-white"}),
            "joining_letter": forms.ClearableFileInput(attrs={"class": "w-full text-gray-700 dark:text-white"}),
        }

