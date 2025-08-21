from django.db import models
from attendance_app.models import Employee
from payroll.models import EmployeeSalary
# Create your models here.
class EmployeeProfile(models.Model):
    employee = models.OneToOneField(Employee, on_delete=models.CASCADE, related_name='profile')
    
    # Personal Info
    designation = models.CharField(max_length=100, blank=True, null=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, choices=[("Male","Male"),("Female","Female"),("Other","Other")], blank=True, null=True)
    marital_status = models.CharField(max_length=20, choices=[("Single","Single"),("Married","Married")], blank=True, null=True)
    national_id = models.CharField(max_length=50, blank=True, null=True)

    # Contact
    mobile_number = models.CharField(max_length=20, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    emergency_contact_name = models.CharField(max_length=100, blank=True, null=True)
    emergency_contact_number = models.CharField(max_length=20, blank=True, null=True)

    # Job Info
    join_date = models.DateField(null=True, blank=True)
    leave_date = models.DateField(null=True, blank=True)
    employee_code = models.CharField(max_length=20, unique=True, null=True, blank=True)
    job_type = models.CharField(max_length=50, choices=[("Full Time","Full Time"),("Part Time","Part Time"),("Contract","Contract"),("Intern","Intern")], blank=True, null=True)
    shift = models.CharField(max_length=50, blank=True, null=True)

    # Finance
    bank_account = models.CharField(max_length=50, blank=True, null=True)
    bank_name = models.CharField(max_length=100, blank=True, null=True)
    branch_name = models.CharField(max_length=100, blank=True, null=True)
    tax_id = models.CharField(max_length=50, blank=True, null=True)

    # Files
    image = models.ImageField(upload_to='employee_images/', blank=True, null=True)
    resume = models.FileField(upload_to="employee_docs/", blank=True, null=True)
    offer_letter = models.FileField(upload_to="employee_docs/", blank=True, null=True)
    joining_letter = models.FileField(upload_to="employee_docs/", blank=True, null=True)

    def __str__(self):
        return f"Profile of {self.employee.name}"

    @property
    def salary(self):
        """Always fetch salary from EmployeeSalary model"""
        try:
            return self.employee.employeesalary.base_salary
        except:
            return 0.00

    @property
    def status(self):
        return "Active" if self.leave_date is None else "Inactive"
