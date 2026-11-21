# attendance_app/shift_forms.py — শিফট ও শিফট অ্যাসাইন
from django import forms

from .models import Branch, Employee, Shift


class ShiftForm(forms.ModelForm):
    class Meta:
        model = Shift
        fields = ['name', 'code', 'branch', 'start_time', 'end_time', 'grace_minutes', 'break_minutes',
                  'weekly_off_day', 'color', 'is_active']
        labels = {'is_active': 'Active shift'}
        widgets = {
            'start_time': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'),
            'end_time': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'),
            'color': forms.RadioSelect,
        }

    def __init__(self, *args, company=None, branch=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company
        qs = Branch.objects.filter(company=company) if company else Branch.objects.none()
        if branch is not None:          # ব্রাঞ্চ ম্যানেজার: শুধু নিজের ব্রাঞ্চ
            qs = qs.filter(pk=branch.pk)
            self.fields['branch'].initial = branch
            self.fields['branch'].required = True
        self.fields['branch'].queryset = qs
        self.fields['branch'].empty_label = "All branches"
        self.fields['weekly_off_day'].choices = [('', "Department's weekly off")] + list(
            self.fields['weekly_off_day'].choices)[1:]

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        qs = Shift.objects.filter(company=self.company, name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("এই নামে একটা শিফট আগে থেকেই আছে।")
        return name

    def clean(self):
        cleaned = super().clean()
        s, e = cleaned.get('start_time'), cleaned.get('end_time')
        if s and e and s == e:
            self.add_error('end_time', "শুরু ও শেষের সময় এক হতে পারে না।")
        return cleaned


class ShiftAssignForm(forms.Form):
    shift = forms.ModelChoiceField(queryset=Shift.objects.none(), empty_label="Select shift")
    start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), label="From date")
    end_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False, label="To date",
                               help_text="খালি রাখলে পরবর্তী পরিবর্তন পর্যন্ত এই শিফট চলবে।")
    employees = forms.ModelMultipleChoiceField(queryset=Employee.objects.none(),
                                               widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, shifts=None, employees=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['shift'].queryset = shifts if shifts is not None else Shift.objects.none()
        self.fields['employees'].queryset = employees if employees is not None else Employee.objects.none()

    def clean(self):
        cleaned = super().clean()
        s, e = cleaned.get('start_date'), cleaned.get('end_date')
        if s and e and e < s:
            self.add_error('end_date', "শেষ তারিখ শুরুর আগে হতে পারে না।")
        return cleaned
