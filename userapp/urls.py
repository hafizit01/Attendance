from django.urls import path
from .views import *
from subscription_app.views import subscription_expired 
app_name = "userapp"


urlpatterns = [
    path('login/', login_view, name='login'),
    # accounts/urls.py
    path('logout/', logout_view, name='logout'),
    path("post-login/", post_login_router, name="post_login_router"),
    path("my-plans/", subscription_expired, name="my_plans"),


    path('profiles/',employee_profile_list, name="employee_profile_list"),
    path('profiles/<int:pk>/',employee_profile_detail, name="employee_profile_detail"),
    path('profiles/add/',employee_profile_create, name="employee_profile_create"),
    path('profiles/<int:pk>/edit/',employee_profile_update, name="employee_profile_update"),
    path('profiles/<int:pk>/delete/',employee_profile_delete, name="employee_profile_delete"),

    path('profiles/<int:pk>/pdf/',employee_profile_pdf, name="employee_profile_pdf"),

    path("profiles/pdf/", employee_profile_list_pdf, name="employee_profile_list_pdf"),


]
