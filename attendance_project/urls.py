from django.contrib import admin
from django.urls import path, include
from django.conf.urls import handler404
from django.urls import re_path
from django.views.static import serve 

from django.conf import settings
from django.conf.urls.static import static
from django.contrib.staticfiles.urls import static, staticfiles_urlpatterns
from attendance_app import views

from decorator_include import decorator_include
from subscription_app.decorators import subscription_required

urlpatterns = [
    # EXEMPT / PUBLIC
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    re_path(r'^static/(?P<path>.*)$', serve, {'document_root': settings.STATIC_ROOT}),

    path('admin/', admin.site.urls),
    path('subscription/', include(('subscription_app.urls', 'subscription_app'), namespace='subscription_app')),
    path('', include(('payment_app.urls', 'payment_app'), namespace='payment_app')),  # ✅ গার্ড ছাড়া, নিজস্ব প্রিফিক্স

    # PROTECTED
    path("", decorator_include(subscription_required, ("attendance_app.urls", "attendance_app"))),
    path('', decorator_include(subscription_required, ('userapp.urls', 'userapp'))),
    path('', decorator_include(subscription_required, ('payroll.urls', 'payroll'))),
    path('ckeditor/', include('ckeditor_uploader.urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT) + static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

if settings.DEBUG:
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
        re_path(r'^static/(?P<path>.*)$', serve, {'document_root': settings.STATIC_ROOT}),
    ]

handler404 = 'attendance_app.views.custom_404_view'

