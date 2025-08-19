from django.urls import path
from . import views


app_name = 'subscription_app'

urlpatterns = [
    path('plans/', views.view_plans, name='view_plans'),
    path("my-plans/", views.subscription_expired, name="my_plans"),
    path("expired/",  views.subscription_expired, name="expired_notice"),
    path("after-login/", views.post_login_router, name="post_login_router"),
    path("subscriptions/", views.subscription_list, name="subscription_list"),
    
]
