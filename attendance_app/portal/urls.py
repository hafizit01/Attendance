from django.urls import path

from . import views

app_name = 'portal'

urlpatterns = [
    path('', views.home, name='home'),
    path('leave/', views.leave, name='leave'),
    path('leave/<int:pk>/cancel/', views.leave_cancel, name='leave_cancel'),
    path('leave/<int:pk>/letter/', views.leave_letter, name='leave_letter'),
    path('payslips/', views.payslips, name='payslips'),
    path('payslips/<str:month>/pdf/', views.payslip_pdf, name='payslip_pdf'),
    path('punch/', views.punch_page, name='punch'),
    path('punch/submit/', views.punch_submit, name='punch_submit'),
]
