from django.urls import path
from . import views

app_name = 'subscription_app'

urlpatterns = [
    path('plans/', views.view_plans, name='view_plans'),
    path('dashboard/', views.dashboard_subscription_check, name='dashboard'),
]
