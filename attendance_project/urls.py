from django.contrib import admin
from django.urls import path, include
from django.conf.urls import handler404
from django.urls import re_path
from django.views.static import serve 

from django.conf import settings
from django.conf.urls.static import static
from django.contrib.staticfiles.urls import static, staticfiles_urlpatterns
from attendance_app import views, device_api
from attendance_app.portal import views as portal_views

from decorator_include import decorator_include
from subscription_app.decorators import subscription_required

urlpatterns = [
    # EXEMPT / PUBLIC
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    re_path(r'^static/(?P<path>.*)$', serve, {'document_root': settings.STATIC_ROOT}),

    path('admin/', admin.site.urls),
    path('subscription/', include(('subscription_app.urls', 'subscription_app'), namespace='subscription_app')),
    path('', include(('payment_app.urls', 'payment_app'), namespace='payment_app')),  # ✅ গার্ড ছাড়া, নিজস্ব প্রিফিক্স

    # Device push: no user login; verified by department push_token (see views.zkteco_push_view)
    path('api/zkteco/push/', views.zkteco_push_view, name='zkteco_push'),
    # ZKTeco ADMS / cloud push (মেশিন নিজে আসে, SN দিয়ে চেনা) — /iclock/cdata, /iclock/cdata.aspx, /iclock/getrequest ...
    re_path(r'^iclock/(?P<action>[A-Za-z]+)(?:\.aspx)?/?$', device_api.iclock, name='iclock'),
    # Hikvision HTTP push ও যেকোনো মেশিনের JSON API (প্রতি মেশিনের নিজস্ব token)
    path('api/devices/<str:token>/hikvision/', device_api.hikvision_push, name='device_hikvision_push'),
    path('api/devices/<str:token>/push/', device_api.api_push, name='device_api_push'),

    # কর্মচারীর সেলফ-সার্ভিস পোর্টাল (নিজের লগইন + কোম্পানির সাবস্ক্রিপশন portal_view এ চেক হয়)
    path('me/', include(('attendance_app.portal.urls', 'portal'), namespace='portal')),
    # SMS এর পে-স্লিপ লিংক (signed token, মেয়াদসহ — লগইন লাগে না)
    path('p/<str:token>/', portal_views.public_payslip, name='public_payslip'),

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

