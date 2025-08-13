# payroll/forms.py

from django import forms
from .models import EmployeeSalary, SalarySummary
from attendance_app.models import Employee
from django.forms import modelformset_factory

class EmployeeSalaryForm(forms.ModelForm):
    class Meta:
        model = EmployeeSalary
        fields = ['employee', 'base_salary', 'bank_transfer_amount']

    def save(self, commit=True):
        instance = super().save(commit=False)
        # employee এর company অনুযায়ী company set করা
        if instance.employee and not instance.company:
            instance.company = instance.employee.company
        if commit:
            instance.save()
        return instance



# Optional: Salary formset to handle multiple employees at once
EmployeeSalaryFormSet = modelformset_factory(
    EmployeeSalary,
    form=EmployeeSalaryForm,
    extra=0,
)


class GenerateSalaryForm(forms.Form):
    month = forms.DateField(
        label="Select Month",
        widget=forms.widgets.DateInput(attrs={'type': 'month'}),
        input_formats=['%Y-%m']
    )


class SalarySummaryFilterForm(forms.Form):
    month = forms.DateField(
        label="Month",
        required=False,
        widget=forms.widgets.DateInput(attrs={'type': 'month'}),
        input_formats=['%Y-%m']
    )
    employee = forms.ModelChoiceField(
        queryset=Employee.objects.all(),
        required=False,
        label="Employee"
    )
