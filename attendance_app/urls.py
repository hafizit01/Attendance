from django.urls import path

from attendance_app.views import attendance_pdf_report
from .views import *
app_name = "attendance_app"

urlpatterns = [
    path('', dashboard, name='dashboard'),

    path('monthly_report/', monthly_work_time_report, name='monthly_work_time_report'),
    path('employees/<int:employee_id>/attendance/', employee_attendance_detail, name='employee_attendance_detail'),
    path('employees/<int:employee_id>/attendance/pdf/', attendance_pdf_report, name='attendance_pdf_report'),
    path('monthly-report/pdf/', monthly_work_time_pdf, name='monthly_work_time_pdf'),
    path('sync-attendance/', sync_attendance_view, name='sync_attendance'),
    path('attendance/<int:employee_id>/pdf/', employee_attendance_pdf, name='attendance_pdf'),
    path('api/zkteco/push/', zkteco_push_view, name='zkteco_push'),
    

# ---------emplyee add-----------
    path('employees/', employee_list, name='employee_list'),
    path('employees/add/', employee_add, name='employee_add'),
    path('employees/edit/<int:pk>/',employee_edit, name='employee_edit'),
    path('employees/delete/<int:pk>/', employee_delete, name='employee_delete'),
    

    # ------------------deperment-------------

    path('departments/', department_list, name='department_list'),
    path('departments/add/', department_form_view, name='department_add'),
    path('departments/edit/<int:pk>/', department_form_view, name='department_edit'),

    path('departments/delete/<int:pk>/', department_delete, name='department_delete'),

    # ------------------Attendance--------------

    path('attendance/', attendance_list, name='attendance_list'),
    path('attendance/add/', attendance_add, name='attendance_add'),
    path('attendance/delete/<int:pk>/', attendance_delete, name='attendance_delete'),

    # ------------------Leave Request--------------

    path('leaves/', leave_list, name='leave_list'),
    path('leaves/add/', leave_create, name='leave_create'),
    path('leaves/<int:pk>/edit/', leave_update, name='leave_update'),
    path('leaves/<int:pk>/delete/', leave_delete, name='leave_delete'),

    path('leave-summary/', leave_summary, name='leave_summary'),
    path('leave-summary-pdf/', leave_summary_pdf, name='leave_summary_pdf'),
    
    # ----------------------Public Holi Day----------------------------
    
    path('holidays/', holiday_list, name='holiday_list'),
    path('holidays/add/', holiday_create, name='holiday_add'),
    path('holidays/<int:pk>/edit/', holiday_edit, name='holiday_edit'),
    path('holidays/<int:pk>/delete/', holiday_delete, name='holiday_delete'),

    
]
