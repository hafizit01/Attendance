"""
আগে মেশিনের IP/Port ডিপার্টমেন্টে রাখা হত — এখন আলাদা Device। পুরনো সেটিং হুবহু Device এ কপি হয়
(শেষ সিঙ্কের সময়সহ, যাতে পরের সিঙ্ক আগের জায়গা থেকেই চলে)। ডিপার্টমেন্টের পুরনো ঘর মুছে ফেলা হয় না।
"""
import secrets

from django.db import migrations


def copy_devices(apps, schema_editor):
    Department = apps.get_model('attendance_app', 'Department')
    Device = apps.get_model('attendance_app', 'Device')
    seen = set()
    qs = (Department.objects.exclude(device_ip__isnull=True).exclude(company__isnull=True)
          .order_by('id'))
    for dept in qs:
        port = dept.device_port or 4370
        # একই ব্রাঞ্চে একই মেশিন একাধিক ডিপার্টমেন্টে দেওয়া থাকলে একটাই Device (একই ID সিরিজ)
        key = (dept.company_id, dept.branch_id, dept.device_ip, port)
        if key in seen:
            continue
        seen.add(key)
        if Device.objects.filter(company_id=dept.company_id, ip=dept.device_ip, port=port,
                                 department__branch_id=dept.branch_id).exists():
            continue
        Device.objects.create(
            company_id=dept.company_id, department_id=dept.id,
            name=f"{dept.name} machine"[:100], protocol='zk_pull',
            ip=dept.device_ip, port=port, comm_key=0, is_active=True,
            push_token=secrets.token_urlsafe(32),
            last_synced_at=dept.last_synced_at,
        )


class Migration(migrations.Migration):
    dependencies = [
        ('attendance_app', '0007_shifts_devices_punchlog'),
    ]

    operations = [
        migrations.RunPython(copy_devices, migrations.RunPython.noop),
    ]
