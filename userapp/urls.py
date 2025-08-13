from django.urls import path
from .views import *

urlpatterns = [
    path('login/', login_view, name='login'),
    # accounts/urls.py
    path('logout/', logout_view, name='logout')

]
