
from zk import ZK, const
from attendance_app.models import Attendance, Employee
from django.utils.timezone import make_aware
import logging


from datetime import datetime

logger = logging.getLogger(__name__)

# def import_attendance(ip='192.168.68.111', port=4370, department=None):
#     zk = ZK(ip, port=port, timeout=5)
#     conn = None
#     try:
#         conn = zk.connect()
#         conn.disable_device()

#         users = conn.get_users()
#         for u in users:
#             # ডিপার্টমেন্ট প্যারামিটার থাকলে সেটাও employee তে অ্যাসাইন করো
#             emp, created = Employee.objects.get_or_create(
#                 device_user_id=u.user_id,
#                 defaults={
#                     'name': u.name,
#                     'department': department
#                 }
#             )
#             if created:
#                 logger.info(f"New employee added: {emp.name} (ID: {emp.device_user_id}), Department: {department}")

#         attendances = conn.get_attendance()

#         for att in attendances:
#             user_id = att.user_id
#             timestamp = att.timestamp.replace(microsecond=0)

#             if timestamp.tzinfo is None or timestamp.tzinfo.utcoffset(timestamp) is None:
#                 timestamp = make_aware(timestamp)

#             status = 'In' if timestamp.hour < 12 else 'Out'

#             try:
#                 emp = Employee.objects.get(device_user_id=user_id)
#                 obj, created = Attendance.objects.get_or_create(
#                     employee=emp,
#                     timestamp=timestamp,
#                     status=status
#                 )
#                 if created:
#                     logger.info(f"✔️ Attendance created: {emp.name} - {timestamp} - {status}")
#                 else:
#                     logger.debug(f"ℹ️ Already exists: {emp.name} - {timestamp} - {status}")
#             except Employee.DoesNotExist:
#                 logger.warning(f"⚠️ Employee with device_user_id={user_id} not found.")
#                 continue

#     except Exception as e:
#         logger.error(f"❌ Error syncing attendance: {e}")
#         raise e

#     finally:
#         if conn:
#             try:
#                 conn.enable_device()
#                 conn.disconnect()
#             except Exception as e:
#                 logger.error(f"⚠️ Cleanup error: {e}")

from datetime import datetime
from django.utils.timezone import make_aware
from zk import ZK
import logging
from attendance_app.models import Employee, Attendance

logger = logging.getLogger(__name__)

def import_attendance(devices):
    """
    devices: list of dicts with keys: ip, port, department
    Returns: list of results per device (success/failure and message)
    """
    results = []

    for device in devices:
        ip = device.get('ip')
        port = device.get('port')
        department = device.get('department')
        company = getattr(department, 'company', None)

        zk = ZK(ip, port=port, timeout=10, force_udp=False, ommit_ping=True)
        conn = None

        try:
            logger.info(f"🔌 Connecting to device {ip}:{port} for department {department.name}")
            conn = zk.connect()
            conn.disable_device()

            users = conn.get_users()
            for u in users:
                emp, created = Employee.objects.get_or_create(
                    device_user_id=u.user_id,
                    company=company,  # company যুক্ত করলুম
                    defaults={
                        'name': u.name or f"User {u.user_id}",
                        'department': department
                    }
                )
                if created:
                    logger.info(f"➕ New employee added: {emp.name} (ID: {emp.device_user_id}), Dept: {department.name}")

            attendances = conn.get_attendance()
            if not attendances:
                raise Exception(f"No attendance data received from device {ip}:{port} or device is offline.")

            start_date = make_aware(datetime(2025, 1, 1))
            created_count = 0
            skipped_count = 0

            for att in attendances:
                user_id = att.user_id
                timestamp = att.timestamp.replace(microsecond=0)

                if timestamp.tzinfo is None or timestamp.tzinfo.utcoffset(timestamp) is None:
                    timestamp = make_aware(timestamp)

                if timestamp < start_date:
                    skipped_count += 1
                    continue

                status = 'In' if timestamp.hour < 13 else 'Out'

                try:
                    emp = Employee.objects.get(device_user_id=user_id, company=company)

                    # Avoid duplicate attendance
                    if not Attendance.objects.filter(employee=emp, timestamp=timestamp, status=status).exists():
                        Attendance.objects.create(
                            employee=emp,
                            timestamp=timestamp,
                            status=status
                        )
                        created_count += 1

                except Employee.DoesNotExist:
                    logger.warning(f"⚠️ Employee with device_user_id={user_id} not found in company {company}.")
                    continue

            results.append({
                'department': department.name,
                'status': 'success',
                'message': f"✔️ Synced {created_count} new records. Skipped {skipped_count} old entries."
            })

        except Exception as e:
            logger.error(f"❌ Failed to sync from {department.name} ({ip}:{port}) - {e}")
            results.append({
                'department': department.name,
                'status': 'error',
                'message': f"❌ Failed to sync: {e}"
            })

        finally:
            if conn:
                try:
                    conn.enable_device()
                    conn.disconnect()
                except Exception as e:
                    logger.error(f"⚠️ Cleanup error on device {ip}:{port} - {e}")

    return results
