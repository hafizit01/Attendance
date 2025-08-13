# payroll/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # path('generate/', views.generate_salary_for_month, name='generate_salary'),
    path('summary/', views.salary_summary_list, name='salary_summary_list'),
    path('add-summary/', views.set_base_salaries, name='add_salary'),
    path('salary-summary/pdf/', views.export_salary_summary_pdf, name='salary_summary_pdf'),

]
