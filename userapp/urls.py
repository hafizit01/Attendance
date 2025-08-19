from django.urls import path
from .views import *
from subscription_app.views import subscription_expired 


urlpatterns = [
    path('login/', login_view, name='login'),
    # accounts/urls.py
    path('logout/', logout_view, name='logout'),

    path("my-plans/", subscription_expired, name="my_plans"),

]
