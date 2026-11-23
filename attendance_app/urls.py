from django.urls import path

from attendance_app.views import attendance_pdf_report, attendance_list_pdf
from .views import *
from . import analytics_views, branch_views, bulk_views, document_views, device_views, excel_views, hr_views, leave_views, shift_views
from .portal import admin_views as portal_admin
app_name = "attendance_app"

urlpatterns = [
    path('', dashboard, name='dashboard'),

    path('monthly_report/', monthly_work_time_report, name='monthly_work_time_report'),
    path('employees/<int:employee_id>/attendance/', employee_attendance_detail, name='employee_attendance_detail'),
    path('employees/<int:employee_id>/attendance/pdf/', attendance_pdf_report, name='attendance_pdf_report'),
    path('monthly-report/pdf/', monthly_work_time_pdf, name='monthly_work_time_pdf'),
    path('sync-attendance/', sync_attendance_view, name='sync_attendance'),
    path('attendance/<int:employee_id>/pdf/', employee_attendance_pdf, name='attendance_pdf'),
    

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
    path("attendance/<int:pk>/edit/", attendance_edit, name="attendance_edit"),
    path('attendance/delete/<int:pk>/', attendance_delete, name='attendance_delete'),
    path('attendance/pdf/', attendance_list_pdf, name='attendance_list_pdf'),
    path('attendance/bulk/', bulk_views.bulk_attendance, name='bulk_attendance'),
    path('attendance/bulk/preview/', bulk_views.bulk_attendance_preview, name='bulk_attendance_preview'),
    path('attendance/bulk/apply/', bulk_views.bulk_attendance_apply, name='bulk_attendance_apply'),
    path('attendance/pending/', bulk_views.missing_queue, name='missing_queue'),
    path('attendance/pending/save/', bulk_views.missing_queue_save, name='missing_queue_save'),

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

    # ----------------------Branches----------------------------
    path('branches/', branch_views.branch_list, name='branch_list'),
    path('branches/add/', branch_views.branch_form, name='branch_add'),
    path('branches/<int:pk>/edit/', branch_views.branch_form, name='branch_edit'),
    path('branches/<int:pk>/delete/', branch_views.branch_delete, name='branch_delete'),
    path('branches/<int:pk>/managers/add/', branch_views.manager_add, name='manager_add'),
    path('branches/managers/<int:user_id>/remove/', branch_views.manager_remove, name='manager_remove'),
    path('branch/switch/', branch_views.branch_switch, name='branch_switch'),
    path('employees/<int:pk>/transfer/', branch_views.employee_transfer, name='employee_transfer'),
    path('employees/<int:employee_id>/documents/', document_views.employee_documents, name='employee_documents'),
    path('documents/<int:pk>/file/', document_views.document_file, name='document_file'),
    path('documents/<int:pk>/delete/', document_views.document_delete, name='document_delete'),

    # ----------------------Shifts----------------------------
    path('shifts/', shift_views.shift_list, name='shift_list'),
    path('shifts/add/', shift_views.shift_form, name='shift_add'),
    path('shifts/<int:pk>/edit/', shift_views.shift_form, name='shift_edit'),
    path('shifts/<int:pk>/delete/', shift_views.shift_delete, name='shift_delete'),
    path('shifts/assign/', shift_views.shift_assign, name='shift_assign'),
    path('shifts/roster/', shift_views.shift_roster, name='shift_roster'),
    path('shifts/assignments/<int:pk>/delete/', shift_views.assignment_delete, name='shift_assignment_delete'),
    path('employees/<int:employee_id>/shifts/', shift_views.employee_shift_history, name='employee_shift_history'),

    # ----------------------Devices (Face / Finger / Card machines)----------------------------
    path('devices/', device_views.device_list, name='device_list'),
    path('devices/add/', device_views.device_form, name='device_add'),
    path('devices/status/', device_views.device_status_json, name='device_status'),
    path('devices/<int:pk>/', device_views.device_detail, name='device_detail'),
    path('devices/<int:pk>/edit/', device_views.device_form, name='device_edit'),
    path('devices/<int:pk>/delete/', device_views.device_delete, name='device_delete'),
    path('devices/<int:pk>/action/', device_views.device_action, name='device_action'),
    path('devices/<int:pk>/import/', device_views.device_import, name='device_import'),
    path('punches/live/', device_views.punch_feed, name='punch_feed'),

    # ----------------------Leave balance & Excel exports----------------------------
    path('leave-balance/', leave_views.leave_balance_view, name='leave_balance'),
    path('leave-balance/save/', leave_views.leave_allocation_save, name='leave_allocation_save'),
    path('leave-balance/override/', leave_views.leave_allocation_override, name='leave_allocation_override'),
    path('leave-balance/override/<int:pk>/delete/', leave_views.leave_allocation_delete, name='leave_allocation_delete'),
    path('leave-balance/excel/', leave_views.leave_balance_excel, name='leave_balance_excel'),
    path('employees/<int:employee_id>/leave-balance.json', leave_views.employee_leave_balance_json, name='employee_leave_balance_json'),

    path('attendance/excel/', excel_views.attendance_list_excel, name='attendance_list_excel'),
    path('monthly-report/excel/', excel_views.monthly_report_excel, name='monthly_report_excel'),
    path('employees/<int:employee_id>/attendance/excel/', excel_views.employee_attendance_excel, name='employee_attendance_excel'),
    path('leave-summary/excel/', excel_views.leave_summary_excel, name='leave_summary_excel'),
    path('leaves/excel/', excel_views.leave_list_excel, name='leave_list_excel'),

    # ----------------------Employee portal (admin side)----------------------------
    path('employees/<int:pk>/portal-access/', portal_admin.portal_access, name='employee_portal_access'),
    path('employees/<int:pk>/portal-access/revoke/', portal_admin.portal_access_revoke, name='employee_portal_revoke'),
    path('remote-punches/', portal_admin.remote_punch_list, name='remote_punch_list'),
    path('remote-punches/<int:pk>/selfie/', portal_admin.remote_punch_selfie, name='remote_punch_selfie'),

    # ----------------------HR rules / alerts----------------------------
    path('hr-settings/', hr_views.hr_settings, name='hr_settings'),
    path('missing-punches/', hr_views.missing_punch_list, name='missing_punch_list'),
    path('missing-punches/check/', hr_views.missing_punch_check_now, name='missing_punch_check_now'),
    path('missing-punches/<int:pk>/resolve/', hr_views.missing_punch_resolve, name='missing_punch_resolve'),
    path('notifications/', hr_views.notification_list, name='notification_list'),
    path('notifications/poll/', hr_views.notification_poll, name='notification_poll'),
    path('unknown-punches/', hr_views.unknown_punch_list, name='unknown_punch_list'),
    path('unknown-punches/resolve/', hr_views.unknown_punch_resolve, name='unknown_punch_resolve'),
    path('analytics/', analytics_views.analytics_dashboard, name='analytics'),
    path('messages/', analytics_views.message_log, name='message_log'),
    path('notifications/<int:pk>/read/', hr_views.notification_read, name='notification_read'),
    path('notifications/read-all/', hr_views.notification_read_all, name='notification_read_all'),

    path('support/', support_page, name='support_page'),
    path('support/success/', support_success, name='support_success'),
]
