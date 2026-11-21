# payroll/urls.py
from django.urls import path
from . import excel_views, loan_views, message_views, structure_views, views
app_name = "payroll"

urlpatterns = [
    # path('generate/', views.generate_salary_for_month, name='generate_salary'),
    path('summary/', views.salary_summary_list, name='salary_summary_list'),
    # Salary Base (নতুন ডিজাইন + ড্রয়ার) — আগের পুরো-টেবিল এডিট "Bulk edit" হিসেবে থাকল
    path('add-summary/', structure_views.salary_base, name='add_salary'),
    path('salary-base/bulk/', views.set_base_salaries, name='salary_bulk_edit'),
    path('salary-base/save/', structure_views.salary_base_save, name='salary_base_save'),
    path('salary-base/<int:employee_id>/history/', structure_views.salary_history, name='salary_history'),
    path('components/', structure_views.components_view, name='components'),
    path('components/<int:pk>/edit/', structure_views.components_view, name='component_edit'),
    path('components/<int:pk>/delete/', structure_views.component_delete, name='component_delete'),
    path('payslips/', structure_views.payslip_list, name='payslip_list'),
    path('payslips/<int:employee_id>/<str:month>/', structure_views.payslip_view, name='payslip_view'),
    path('bonus-overtime/', structure_views.bonus_ot, name='bonus_ot'),
    path('salary-summary/pdf/', views.export_salary_summary_pdf, name='salary_summary_pdf'),
    path('payroll-settings/', views.payroll_settings, name='payroll_settings'),
    path('salary-summary/excel/', excel_views.salary_summary_excel, name='salary_summary_excel'),
    path('salary-summary/send-sms/', message_views.send_salary_sms, name='send_salary_sms'),

    # লোন / অগ্রিম
    path('loans/', loan_views.loan_list, name='loan_list'),
    path('loans/add/', loan_views.loan_form, name='loan_add'),
    path('loans/excel/', loan_views.loan_excel, name='loan_excel'),
    path('loans/<int:pk>/', loan_views.loan_detail, name='loan_detail'),
    path('loans/<int:pk>/edit/', loan_views.loan_form, name='loan_edit'),
    path('loans/<int:pk>/repay/', loan_views.loan_repay, name='loan_repay'),
    path('loans/<int:pk>/delete/', loan_views.loan_delete, name='loan_delete'),

]
