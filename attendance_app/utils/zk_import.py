from datetime import datetime, timedelta
from django.utils.timezone import make_aware, is_naive
from zk import ZK
import logging
from attendance_app.models import Employee, Attendance

logger = logging.getLogger(__name__)

def import_attendance(devices):
    """
    Import logic:
    1. First Punch of the day = 'In'
    2. Last Punch of the day = 'Out' (Updates existing Out record)
    3. Ignore punches within 5 minutes of previous punch (Debounce)
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

            # --- Sync Users ---
            users = conn.get_users()
            for u in users:
                emp, created = Employee.objects.get_or_create(
                    device_user_id=u.user_id,
                    company=company,
                    defaults={
                        'name': u.name or f"User {u.user_id}",
                        'department': department
                    }
                )
                if created:
                    logger.info(f"➕ New employee added: {emp.name}")

            # --- Sync Attendance ---
            attendances = conn.get_attendance()
            if not attendances:
                raise Exception(f"No attendance data received or device offline.")

            # টাইম অনুযায়ী সর্ট করা খুব জরুরি "First In" লজিকের জন্য
            attendances.sort(key=lambda x: x.timestamp)

            start_date = make_aware(datetime(2025, 12, 1))
            created_count = 0
            updated_count = 0
            skipped_count = 0
            
            # Debounce Time (একই মানুষ ৫ মিনিটের মধ্যে ২ বার দিলে ইগনোর করবে)
            DEBOUNCE_MINUTES = 5 

            for att in attendances:
                user_id = att.user_id
                timestamp = att.timestamp.replace(microsecond=0)

                # Timezone adjustment
                if is_naive(timestamp):
                    timestamp = make_aware(timestamp)

                if timestamp < start_date:
                    skipped_count += 1
                    continue

                try:
                    emp = Employee.objects.get(device_user_id=user_id, company=company)
                    date_only = timestamp.date()

                    # 1. আজকের দিনের রেকর্ড চেক করি
                    day_records = Attendance.objects.filter(employee=emp, timestamp__date=date_only).order_by('timestamp')
                    
                    if not day_records.exists():
                        # A. যদি আজ কোনো রেকর্ড না থাকে -> এটাই প্রথম পাঞ্চ (In)
                        Attendance.objects.create(
                            employee=emp, timestamp=timestamp, status='In', company=company
                        )
                        created_count += 1
                    
                    else:
                        # B. যদি রেকর্ড থাকে, লজিক চেক করি
                        last_record = day_records.last()
                        first_record = day_records.first()

                        # --- Debounce Check ---
                        # যদি লাস্ট রেকর্ডের ৫ মিনিটের মধ্যে হয়, তবে ইগনোর (ভুল করে ২ বার চাপ দিয়েছে)
                        time_diff = (timestamp - last_record.timestamp).total_seconds() / 60
                        if time_diff < DEBOUNCE_MINUTES:
                            continue

                        # --- Logic: First In, Last Out ---
                        
                        # কেস ১: যদি নতুন টাইমটা 'In' টাইমের চেয়েও আগে হয় (খুব রেয়ার, ডিভাইস সিঙ্ক ইস্যু)
                        if timestamp < first_record.timestamp:
                            first_record.timestamp = timestamp
                            first_record.status = 'In'
                            first_record.save()
                            updated_count += 1
                        
                        # কেস ২: এটা কি 'Out' হবে?
                        else:
                            # যদি মাঝখানে অনেকগুলো পাঞ্চ থাকে, আমরা চাই শুধু একটাই 'Out' রেকর্ড থাকুক (Latest time)
                            # তাই আমরা চেক করব 'Out' রেকর্ড আছে কিনা
                            out_record = day_records.filter(status='Out').first()

                            if out_record:
                                # যদি অলরেডি Out থাকে, সেটার টাইম আপডেট করে লেটেস্ট টাইম বসাবো
                                out_record.timestamp = timestamp
                                out_record.save()
                                updated_count += 1
                            else:
                                # যদি Out না থাকে, নতুন Out ক্রিয়েট করব
                                Attendance.objects.create(
                                    employee=emp, timestamp=timestamp, status='Out', company=company
                                )
                                created_count += 1

                except Employee.DoesNotExist:
                    continue
                except Exception as inner_e:
                    logger.error(f"Error processing record for user {user_id}: {inner_e}")
                    continue

            results.append({
                'department': department.name,
                'status': 'success',
                'message': f"✔️ Synced {created_count} new, Updated {updated_count} records (Last Out)."
            })

        except Exception as e:
            logger.error(f"❌ Failed to sync {department.name}: {e}")
            results.append({
                'department': department.name,
                'status': 'error',
                'message': str(e)
            })

        finally:
            if conn:
                try:
                    conn.enable_device()
                    conn.disconnect()
                except:
                    pass

    return results