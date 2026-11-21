"""
মেশিন ইন্টিগ্রেশন (ADMS / Hikvision / API / USB ফাইল) ও শিফটের হিসাবের টেস্ট।
চালাতে:  python manage.py test attendance_app
"""
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from payroll.overtime import DayWork, calculate_pay
from subscription_app.models import SubscriptionPlan, UserSubscription

from attendance_app.devices import adms, fileimport
from attendance_app.hr import missing_punch
from attendance_app.models import (Attendance, Branch, Company, Department, Device, DeviceCommand, Employee,
                                   HRSettings, LeaveRequest, MissingPunch, Notification, PunchLog, Shift, ShiftAssignment,
                                   UserProfile)
from attendance_app.shift_views import assign_shift
from attendance_app.utils import attendance_helpers as h


def aware(y, m, d, hh, mm, ss=0):
    return timezone.make_aware(datetime(y, m, d, hh, mm, ss))


class Base(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('owner', password='x', is_superuser=True, is_staff=True)
        self.company = Company.objects.create(name='Riya', owner=self.owner)
        UserProfile.objects.create(user=self.owner, company=self.company)
        plan = SubscriptionPlan.objects.create(name='Pro', price=1, employee_limit=100)
        UserSubscription.objects.create(user=self.owner, plan=plan, start_date=date(2020, 1, 1), end_date=date(2099, 1, 1))
        self.branch = Branch.objects.create(company=self.company, name='Paltan')
        self.dept = Department.objects.create(company=self.company, branch=self.branch, name='Shop',
                                              in_time=time(10, 0), out_time=time(18, 0), weekly_off_day='Friday')
        self.emp = Employee.objects.create(company=self.company, department=self.dept, name='Rahim', device_user_id=1)


class AdmsTests(Base):
    def setUp(self):
        super().setUp()
        self.device = Device.objects.create(company=self.company, department=self.dept, name='Gate',
                                            protocol=Device.PROTOCOL_ZK_ADMS, serial_number='CKJX123')

    def test_unknown_serial_rejected_so_device_keeps_data(self):
        r = self.client.post('/iclock/cdata?SN=NOPE&table=ATTLOG', data='1\t2026-11-03 09:00:00\t0\t15',
                             content_type='text/plain')
        self.assertEqual(r.status_code, 403)
        self.assertFalse(PunchLog.objects.exists())

    def test_handshake_returns_options(self):
        r = self.client.get('/iclock/cdata?SN=CKJX123&options=all&pushver=2.4.1')
        self.assertEqual(r.status_code, 200)
        self.assertIn('GET OPTION FROM: CKJX123', r.content.decode())
        self.device.refresh_from_db()
        self.assertTrue(self.device.is_online)

    def test_realtime_punches_build_in_and_out(self):
        # সকালে ফেস, বিকেলে ফিঙ্গার — আলাদা রিকোয়েস্টে (রিয়েল-টাইম)
        r1 = self.client.post('/iclock/cdata?SN=CKJX123&table=ATTLOG&Stamp=100',
                              data='1\t2026-11-03 09:55:00\t0\t15\t0\t0', content_type='text/plain')
        self.assertEqual(r1.content.decode(), 'OK: 1')
        r2 = self.client.post('/iclock/cdata.aspx?SN=CKJX123&table=ATTLOG&Stamp=101',
                              data='1\t2026-11-03 18:30:00\t1\t1\t0\t0\n', content_type='text/plain')
        self.assertEqual(r2.status_code, 200)

        recs = list(Attendance.objects.filter(employee=self.emp).order_by('timestamp').values_list('status', 'timestamp'))
        self.assertEqual([s for s, _ in recs], ['In', 'Out'])
        self.assertEqual(recs[1][1], aware(2026, 11, 3, 18, 30))
        self.assertEqual(sorted(PunchLog.objects.values_list('method', flat=True)), ['face', 'finger'])
        self.device.refresh_from_db()
        self.assertEqual(self.device.adms_att_stamp, '101')

    def test_unknown_pin_logged_and_alerted_by_default(self):
        from attendance_app.models import UnknownPunch
        now = timezone.localtime().replace(microsecond=0, tzinfo=None)
        self.client.post('/iclock/cdata?SN=CKJX123&table=ATTLOG', data=f'7\t{now:%Y-%m-%d %H:%M:%S}\t0\t15',
                         content_type='text/plain')
        self.assertFalse(Employee.objects.filter(device_user_id=7).exists())
        u = UnknownPunch.objects.get()
        self.assertEqual((u.raw_id, u.reason, u.method, u.device_id), ('7', 'unmapped', 'face', self.device.id))
        self.assertTrue(Notification.objects.filter(user=self.owner, kind='device', message__contains='7').exists())
        # অ্যাডমিন কর্মচারী যোগ করলে আগের পাঞ্চ হাজিরায় বসে
        self.client.force_login(self.owner)
        self.client.post(reverse('attendance_app:unknown_punch_resolve'),
                         {'raw_id': '7', 'action': 'create', 'name': 'Karim', 'department': self.dept.pk})
        emp = Employee.objects.get(device_user_id=7)
        self.assertEqual(Attendance.objects.filter(employee=emp).count(), 1)
        u.refresh_from_db()
        self.assertEqual(u.status, 'resolved')

    def test_old_unknown_punch_logged_without_notification(self):
        from attendance_app.models import UnknownPunch
        self.client.post('/iclock/cdata?SN=CKJX123&table=ATTLOG', data='9\t2025-01-03 10:00:00\t0\t1',
                         content_type='text/plain')
        self.assertEqual(UnknownPunch.objects.count(), 1)
        self.assertFalse(Notification.objects.exists())

    def test_unknown_pin_creates_employee_then_name_from_operlog(self):
        hr = HRSettings.for_company(self.company)
        hr.auto_create_unknown_ids = True
        hr.save()
        self.client.post('/iclock/cdata?SN=CKJX123&table=ATTLOG', data='7\t2026-11-03 10:00:00\t0\t1',
                         content_type='text/plain')
        emp = Employee.objects.get(device_user_id=7)
        self.assertEqual(emp.name, 'User 7')
        self.assertEqual(Attendance.objects.filter(employee=emp).count(), 1)
        r = self.client.post('/iclock/cdata?SN=CKJX123&table=OPERLOG',
                             data='USER PIN=7\tName=Karim\tPri=0\tPasswd=\tCard=\tGrp=1\nFP PIN=7\tFID=1\tSize=10\tTMP=abc',
                             content_type='text/plain')
        self.assertEqual(r.content.decode(), 'OK: 2')
        emp.refresh_from_db()
        self.assertEqual(emp.name, 'Karim')

    def test_command_queue_round_trip(self):
        cmd = DeviceCommand.objects.create(device=self.device, command='REBOOT', label='Restart')
        r = self.client.get('/iclock/getrequest?SN=CKJX123&INFO=Ver 6.60,12,8,3000,192.168.1.201,10,7,12,9,111')
        self.assertEqual(r.content.decode().strip(), f'C:{cmd.pk}:REBOOT')
        self.device.refresh_from_db()
        self.assertEqual((self.device.user_count, self.device.finger_count, self.device.face_count), (12, 8, 9))
        self.assertEqual(self.client.get('/iclock/getrequest?SN=CKJX123').content.decode(), 'OK')
        self.client.post('/iclock/devicecmd?SN=CKJX123', data=f'ID={cmd.pk}&Return=0&CMD=REBOOT',
                         content_type='text/plain')
        cmd.refresh_from_db()
        self.assertEqual(cmd.status, 'done')

    def test_push_users_action_queues_commands(self):
        self.client.force_login(self.owner)
        r = self.client.post(reverse('attendance_app:device_action', args=[self.device.pk]),
                             {'action': 'push_users'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.json()['status'], 'success')
        self.assertIn('PIN=1\tName=Rahim', DeviceCommand.objects.get(device=self.device).command)

    def test_push_users_skips_people_already_on_machine(self):
        """মেশিনে আগে থেকে থাকা ইউজারকে আবার পাঠালে admin/পাসওয়ার্ড/কার্ড মুছে যেত — তাই পাঠানো হয় না"""
        self.client.force_login(self.owner)
        Attendance.objects.create(employee=self.emp, company=self.company, timestamp=aware(2026, 11, 2, 10, 0), status='In')
        karim = Employee.objects.create(company=self.company, department=self.dept, name='Karim', device_user_id=2)
        PunchLog.objects.create(company=self.company, device=self.device, employee=karim, device_user_id=2,
                                timestamp=aware(2026, 11, 2, 10, 0), source='adms')
        Employee.objects.create(company=self.company, department=self.dept, name='Notun', device_user_id=3)
        field = Employee.objects.create(company=self.company, department=self.dept, name='Field', device_user_id=4)
        Attendance.objects.create(employee=field, company=self.company, timestamp=aware(2026, 11, 2, 9, 0),
                                  status='In', is_manual=True)
        r = self.client.post(reverse('attendance_app:device_action', args=[self.device.pk]),
                             {'action': 'push_users'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.json()['status'], 'success')
        sent = sorted(c.command.split('\t')[0] for c in DeviceCommand.objects.filter(device=self.device))
        self.assertEqual(sent, ['DATA UPDATE USERINFO PIN=3', 'DATA UPDATE USERINFO PIN=4'])
        # সবাই মেশিনে থাকলে কিছুই যায় না
        DeviceCommand.objects.all().delete()
        for e in Employee.objects.filter(device_user_id__in=[3, 4]):
            PunchLog.objects.create(company=self.company, device=self.device, employee=e, device_user_id=e.device_user_id,
                                    timestamp=aware(2026, 11, 3, 10, e.device_user_id), source='adms')
        r = self.client.post(reverse('attendance_app:device_action', args=[self.device.pk]),
                             {'action': 'push_users'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertFalse(DeviceCommand.objects.exists())
        self.assertIn('কিছু পাঠানো হয়নি', r.json()['message'])


class HikvisionAndApiTests(Base):
    def test_hikvision_http_push(self):
        dev = Device.objects.create(company=self.company, department=self.dept, name='Hik',
                                    protocol=Device.PROTOCOL_HIK_PUSH)
        event = {"dateTime": "2026-11-03T09:40:00+06:00", "eventType": "AccessControllerEvent",
                 "AccessControllerEvent": {"majorEventType": 5, "subEventType": 75, "employeeNoString": "1",
                                           "currentVerifyMode": "face"}}
        r = self.client.post(f'/api/devices/{dev.push_token}/hikvision/', {'event_log': json.dumps(event)})
        self.assertEqual(r.json()['accepted'], 1)
        p = PunchLog.objects.get()
        self.assertEqual((p.method, p.employee_id), ('face', self.emp.id))
        self.assertTrue(Attendance.objects.filter(employee=self.emp, status='In').exists())

    def test_hikvision_heartbeat_and_bad_token(self):
        self.assertEqual(self.client.post('/api/devices/wrong/hikvision/', {}).status_code, 403)

    def test_json_api(self):
        dev = Device.objects.create(company=self.company, department=self.dept, name='Anviz', protocol=Device.PROTOCOL_API)
        body = {"punches": [{"uid": 1, "time": "2026-11-03 09:00:00", "method": "palm"},
                            {"uid": 1, "time": "2026-11-03 19:00:00", "method": "card"},
                            {"uid": "x", "time": "bad"}]}
        r = self.client.post(f'/api/devices/{dev.push_token}/push/', json.dumps(body), content_type='application/json')
        data = r.json()
        self.assertEqual((data['created'], data['skipped']), (2, 1))

    def test_api_token_of_other_protocol_rejected(self):
        dev = Device.objects.create(company=self.company, department=self.dept, name='Z', protocol=Device.PROTOCOL_ZK_PULL, ip='10.0.0.1')
        r = self.client.post(f'/api/devices/{dev.push_token}/push/', '[]', content_type='application/json')
        self.assertEqual(r.status_code, 403)


class FileImportTests(Base):
    def test_parse_zk_attlog_and_csv(self):
        dat = b"     1\t2026-11-03 09:01:02\t0\t1\t0\t0\n     2\t2026-11-03 09:05:00\t0\t15\t0\t0\n"
        punches, bad = fileimport.parse(dat)
        self.assertEqual((len(punches), bad), (2, 0))
        self.assertEqual([p.method for p in punches], ['finger', 'face'])
        csv = "ID,Date,Time,Verify\n1,03/11/2026,09:01,Face\n1,03/11/2026,18:10,Finger\n"
        punches, bad = fileimport.parse(csv)
        self.assertEqual((len(punches), bad), (2, 1))
        self.assertEqual(punches[0].timestamp, datetime(2026, 11, 3, 9, 1))
        self.assertEqual(punches[0].method, 'face')

    def test_upload_view(self):
        dev = Device.objects.create(company=self.company, department=self.dept, name='USB', protocol=Device.PROTOCOL_FILE)
        self.client.force_login(self.owner)
        f = SimpleUploadedFile('1_attlog.dat', b"1\t2026-11-03 09:00:00\t0\t1\n1\t2026-11-03 18:00:00\t1\t1\n")
        self.client.post(reverse('attendance_app:device_import', args=[dev.pk]), {'file': f})
        self.assertEqual(Attendance.objects.filter(employee=self.emp).count(), 2)

    def test_adms_parsers(self):
        users, total = adms.parse_users("USER PIN=3\tName=A\nOPLOG 4\t0\t2026\n")
        self.assertEqual((len(users), total), (1, 2))
        self.assertEqual(adms.parse_command_results("ID=5&Return=-1&CMD=DATA"), [(5, '-1')])


class ShiftCalculationTests(Base):
    def _day(self, emp, d):
        [(_e, _s, days)] = h.build_attendance([emp], d, d)
        return days[0]

    def _punch(self, *dts):
        for i, dt in enumerate(dts):
            Attendance.objects.create(employee=self.emp, company=self.company, timestamp=dt, status='In' if i == 0 else 'Out')

    def test_no_shift_uses_department_time_like_before(self):
        self._punch(aware(2026, 11, 3, 10, 10), aware(2026, 11, 3, 18, 0))
        d = self._day(self.emp, date(2026, 11, 3))
        self.assertEqual(d['late'], timedelta(minutes=10))
        self.assertEqual(d['work'], timedelta(hours=7, minutes=50))
        self.assertEqual(h.shift_for(self.emp), h.department_shift(self.dept))

    def test_assigned_shift_with_grace_and_break(self):
        s = Shift.objects.create(company=self.company, name='Morning', start_time=time(8, 0), end_time=time(16, 0),
                                 grace_minutes=15, break_minutes=30)
        ShiftAssignment.objects.create(employee=self.emp, shift=s, start_date=date(2026, 11, 1))
        self._punch(aware(2026, 11, 3, 8, 10), aware(2026, 11, 3, 16, 30))
        d = self._day(self.emp, date(2026, 11, 3))
        self.assertEqual(d['late'], timedelta())                        # grace এর মধ্যে
        self.assertEqual(d['work'], timedelta(hours=7, minutes=50))     # 8:10→16:30 − 30m break
        self.assertEqual(d['over'], timedelta(minutes=20))              # শিফট 7h30m
        # অ্যাসাইনমেন্টের আগের দিন ডিপার্টমেন্টের সময়
        self.assertEqual(h.ShiftResolver([self.emp], date(2026, 10, 31), date(2026, 10, 31)).on(self.emp, date(2026, 10, 31)).in_time, time(10, 0))

    def test_night_shift_spans_midnight(self):
        s = Shift.objects.create(company=self.company, name='Night', start_time=time(22, 0), end_time=time(6, 0))
        ShiftAssignment.objects.create(employee=self.emp, shift=s, start_date=date(2026, 11, 1))
        self._punch(aware(2026, 11, 3, 22, 5))
        Attendance.objects.create(employee=self.emp, company=self.company, timestamp=aware(2026, 11, 4, 6, 10), status='In')
        [(_e, _s, days)] = h.build_attendance([self.emp], date(2026, 11, 3), date(2026, 11, 4))
        self.assertEqual(days[0]['status'], 'Present')
        self.assertEqual(days[0]['out_time'], aware(2026, 11, 4, 6, 10))
        self.assertEqual(days[0]['work'], timedelta(hours=8, minutes=5))
        self.assertEqual(days[1]['status'], 'Absent')                  # সকালের পাঞ্চ আগের রাতের হিসাবে গেছে

    def test_assign_shift_splits_existing_ranges(self):
        a = Shift.objects.create(company=self.company, name='A', start_time=time(8), end_time=time(16))
        b = Shift.objects.create(company=self.company, name='B', start_time=time(14), end_time=time(22))
        assign_shift([self.emp], a, date(2026, 11, 1))
        assign_shift([self.emp], b, date(2026, 11, 10), date(2026, 11, 15))
        rows = list(ShiftAssignment.objects.filter(employee=self.emp).order_by('start_date')
                    .values_list('shift__name', 'start_date', 'end_date'))
        self.assertEqual(rows, [('A', date(2026, 11, 1), date(2026, 11, 9)),
                                ('B', date(2026, 11, 10), date(2026, 11, 15)),
                                ('A', date(2026, 11, 16), None)])

    def test_payroll_calc_unchanged_without_per_day_regular(self):
        days = [DayWork(work=timedelta(hours=9)), DayWork(is_leave=True), DayWork(work=timedelta(hours=2), is_off=True)]
        old = calculate_pay(Decimal('30000'), timedelta(hours=8), 26, days)
        new = calculate_pay(Decimal('30000'), timedelta(hours=8), 26,
                            [DayWork(work=d.work, is_off=d.is_off, is_leave=d.is_leave, regular=None) for d in days],
                            expected_hours=None)
        self.assertEqual(old, new)


class LateDeductionTests(Base):
    """নভেম্বর 2026: ৩০ দিন, ৪ শুক্রবার ছুটি → ২৬ কর্মদিবস। মূল বেতন ২৫,০০০।"""

    def setUp(self):
        super().setUp()
        from payroll.models import EmployeeSalary
        EmployeeSalary.objects.create(employee=self.emp, company=self.company, base_salary=Decimal('25000'))
        # ৭ দিন দেরি (১০:১৫ এ In), সব দিন ৬টা পর্যন্ত (শুক্রবার বাদে)
        late_days = 0
        for day in range(1, 31):
            d = date(2026, 11, day)
            if d.strftime('%A') == 'Friday':
                continue
            start = time(10, 15) if late_days < 7 else time(9, 55)
            late_days += start == time(10, 15)
            Attendance.objects.create(employee=self.emp, company=self.company,
                                      timestamp=timezone.make_aware(datetime.combine(d, start)), status='In')
            Attendance.objects.create(employee=self.emp, company=self.company,
                                      timestamp=timezone.make_aware(datetime.combine(d, time(18, 0))), status='Out')

    def _row(self):
        from django.test import RequestFactory
        from payroll.views import get_salary_summary_data
        req = RequestFactory().get('/')
        req.user, req.session = self.owner, {}
        data = get_salary_summary_data(req, '2026-11')
        return data, data['summaries'][0]

    def test_disabled_by_default_no_change(self):
        data, row = self._row()
        self.assertFalse(data['late_deduction_enabled'])
        self.assertEqual(row['late_days'], 7)
        self.assertEqual(row['late_deduction'], Decimal(0))

    def test_three_lates_equal_one_day(self):
        _d, before = self._row()
        hr = HRSettings.for_company(self.company)
        hr.enable_late_deduction, hr.lates_equal_one_day_deduction = True, 3
        hr.save()
        data, row = self._row()
        self.assertEqual(row['late_deduction_days'], 2)                     # 7 // 3
        expected = (Decimal('25000') / 26 * 2).quantize(Decimal('0.01'))   # নভেম্বরে ৪ শুক্রবার → ২৬ কর্মদিবস
        self.assertEqual(row['late_deduction'], expected)
        self.assertEqual(row['final_salary'], before['final_salary'] - expected)
        self.assertEqual(data['total_late_deduction'], expected)

    def test_calendar_basis(self):
        hr = HRSettings.for_company(self.company)
        hr.enable_late_deduction, hr.lates_equal_one_day_deduction = True, 7
        hr.late_deduction_basis = HRSettings.BASIS_CALENDAR
        hr.save()
        _d, row = self._row()
        self.assertEqual(row['late_deduction'], (Decimal('25000') / 30).quantize(Decimal('0.01')))

    def test_salary_page_and_pdf_show_late_cut(self):
        hr = HRSettings.for_company(self.company)
        hr.enable_late_deduction = True
        hr.save()
        self.client.force_login(self.owner)
        r = self.client.get(reverse('payroll:salary_summary_list') + '?month=2026-11')
        self.assertContains(r, 'Late cut')
        self.assertContains(r, '7 late → 2 days')


class MissingPunchTests(Base):
    def test_in_without_out_creates_record_and_notification(self):
        hr = HRSettings.for_company(self.company)
        hr.enable_missing_punch_alert = True
        hr.save()
        Attendance.objects.create(employee=self.emp, company=self.company, timestamp=aware(2026, 11, 3, 10, 0), status='In')
        from django.core.management import call_command
        import io
        out = io.StringIO()
        call_command('check_missing_punches', '--date', '2026-11-03', stdout=out)
        self.assertIn('1 new missing out-punch', out.getvalue())
        m = MissingPunch.objects.get()
        self.assertEqual((m.employee_id, m.date, m.status), (self.emp.id, date(2026, 11, 3), 'open'))
        n = Notification.objects.get(user=self.owner)
        self.assertIn('Rahim', n.message)

        # আবার চালালে ডুপ্লিকেট নয়; Out এলে resolved
        Attendance.objects.create(employee=self.emp, company=self.company, timestamp=aware(2026, 11, 3, 18, 0), status='Out')
        missing_punch.run(day=date(2026, 11, 4))
        m.refresh_from_db()
        self.assertEqual(m.status, 'resolved')
        self.assertEqual(MissingPunch.objects.count(), 1)

    def test_disabled_company_skipped(self):
        Attendance.objects.create(employee=self.emp, company=self.company, timestamp=aware(2026, 11, 3, 10, 0), status='In')
        self.assertEqual(missing_punch.run(day=date(2026, 11, 3)), {})
        self.assertFalse(MissingPunch.objects.exists())

    def test_night_shift_checked_next_morning(self):
        s = Shift.objects.create(company=self.company, name='Night', start_time=time(22), end_time=time(6))
        ShiftAssignment.objects.create(employee=self.emp, shift=s, start_date=date(2026, 11, 1))
        Attendance.objects.create(employee=self.emp, company=self.company, timestamp=aware(2026, 11, 3, 22, 0), status='In')
        # ৩ তারিখ রাতে চেক: Out আসার কথা ৪ তারিখ সকালে — এখন কিছু হবে না
        self.assertEqual(missing_punch.check_company(self.company, date(2026, 11, 3))['created'], 0)
        # ৪ তারিখ রাতে চেক: গত রাতের Out আসেনি
        self.assertEqual(missing_punch.check_company(self.company, date(2026, 11, 4))['created'], 1)
        self.assertEqual(MissingPunch.objects.get().date, date(2026, 11, 3))

    def test_celery_task_wrapper(self):
        from attendance_app import tasks
        self.assertEqual(tasks.check_missing_punches(), {})

    def test_pages(self):
        self.client.force_login(self.owner)
        for name in ('hr_settings', 'missing_punch_list', 'notification_list'):
            self.assertEqual(self.client.get(reverse(f'attendance_app:{name}')).status_code, 200, name)
        r = self.client.post(reverse('attendance_app:hr_settings'),
                             {'enable_late_deduction': 'on', 'lates_equal_one_day_deduction': 4,
                              'late_deduction_basis': 'working'})
        self.assertEqual(r.status_code, 302)
        hr = HRSettings.objects.get(company=self.company)
        self.assertTrue(hr.enable_late_deduction)
        self.assertFalse(hr.enable_missing_punch_alert)
        self.assertEqual(hr.lates_equal_one_day_deduction, 4)


class LeaveBalanceTests(Base):
    def _post(self, **data):
        self.client.force_login(self.owner)
        payload = {'company': self.company.pk, 'employee': self.emp.pk, 'leave_type': 'Casual',
                   'start_date': '2026-03-01', 'end_date': '2026-03-03', 'status': 'Approved', 'reason': 'x'}
        payload.update(data)
        return self.client.post(reverse('attendance_app:leave_create'), payload)

    def test_no_allocation_means_unlimited_like_before(self):
        r = self._post(start_date='2026-03-01', end_date='2026-03-30')
        self.assertEqual(r.status_code, 302)

    def test_balance_deducts_on_approval_and_blocks_when_exhausted(self):
        from attendance_app.hr import leave_balance
        from attendance_app.models import LeaveAllocation
        LeaveAllocation.objects.create(company=self.company, leave_type='Casual', days_per_year=5)
        self.assertEqual(self._post().status_code, 302)                                  # 3 দিন approved
        bal = {b['type']: b for b in leave_balance.balance(self.emp, 2026)}
        self.assertEqual((bal['Casual']['used'], bal['Casual']['remaining']), (3, 2))
        r = self._post(start_date='2026-04-01', end_date='2026-04-03')                   # 3 চাই, বাকি 2
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'বাকি আছে মাত্র 2 দিন')
        self.assertEqual(self._post(start_date='2026-04-01', end_date='2026-04-02').status_code, 302)
        r = self._post(start_date='2026-05-01', end_date='2026-05-01')                   # শেষ
        self.assertContains(r, 'ব্যালান্স শেষ')
        # Reject করলে ফেরত আসে
        LeaveRequest.objects.filter(start_date=date(2026, 3, 1)).update(status='Rejected')
        self.assertEqual(self._post(start_date='2026-05-01', end_date='2026-05-01').status_code, 302)

    def test_employee_override_and_cross_year(self):
        from attendance_app.hr import leave_balance
        from attendance_app.models import LeaveAllocation
        LeaveAllocation.objects.create(company=self.company, leave_type='Sick', days_per_year=14)
        LeaveAllocation.objects.create(company=self.company, employee=self.emp, leave_type='Sick', days_per_year=2)
        self.assertIsNotNone(leave_balance.validate_request(self.emp, 'Sick', date(2026, 1, 1), date(2026, 1, 3)))
        # ডিসেম্বর-জানুয়ারি: প্রতি বছরে ২ দিন করে — চলবে
        self.assertIsNone(leave_balance.validate_request(self.emp, 'Sick', date(2026, 12, 30), date(2027, 1, 2)))

    def test_editing_existing_leave_not_blocked(self):
        from attendance_app.models import LeaveAllocation
        lv = LeaveRequest.objects.create(company=self.company, employee=self.emp, leave_type='Casual',
                                         start_date=date(2026, 3, 1), end_date=date(2026, 3, 10), status='Approved')
        LeaveAllocation.objects.create(company=self.company, leave_type='Casual', days_per_year=5)  # পরে সীমা কমানো
        self.client.force_login(self.owner)
        r = self.client.post(reverse('attendance_app:leave_update', args=[lv.pk]), {
            'company': self.company.pk, 'employee': self.emp.pk, 'leave_type': 'Casual', 'start_date': '2026-03-01',
            'end_date': '2026-03-10', 'status': 'Approved', 'reason': 'updated'})
        self.assertEqual(r.status_code, 302)

    def test_pages_and_json(self):
        self.client.force_login(self.owner)
        self.client.post(reverse('attendance_app:leave_allocation_save'), {'alloc_Casual': '10', 'alloc_Sick': '14', 'alloc_Unpaid': ''})
        self.assertEqual(self.client.get(reverse('attendance_app:leave_balance')).status_code, 200)
        data = self.client.get(reverse('attendance_app:employee_leave_balance_json', args=[self.emp.pk])).json()
        self.assertEqual({b['type']: b['allocated'] for b in data['balance']}, {'Casual': 10, 'Sick': 14})


class LoanTests(Base):
    def setUp(self):
        super().setUp()
        from payroll.models import EmployeeLoan, EmployeeSalary
        EmployeeSalary.objects.create(employee=self.emp, company=self.company, base_salary=Decimal('20000'))
        self.loan = EmployeeLoan.objects.create(company=self.company, employee=self.emp, amount=Decimal('5000'),
                                                emi_count=2, monthly_deduction=Decimal('3000'), start_month=date(2026, 9, 15))

    def _row(self, month):
        from django.test import RequestFactory
        from payroll.views import get_salary_summary_data
        req = RequestFactory().get('/')
        req.user, req.session = self.owner, {}
        data = get_salary_summary_data(req, month)
        return data, data['summaries'][0]

    def test_schedule_and_recovery(self):
        self.assertEqual(self.loan.start_month, date(2026, 9, 1))
        self.assertEqual([(m.month, c) for m, c, _r in self.loan.schedule()], [(9, Decimal('3000')), (10, Decimal('2000'))])
        self.assertEqual(self.loan.deduction_for(2026, 8), 0)
        self.assertEqual(self.loan.deduction_for(2026, 11), 0)            # পুরো টাকা উঠে গেছে
        self.assertEqual(self.loan.end_month, date(2026, 10, 1))

    def test_salary_too_low_shows_shortfall(self):
        _d, sep = self._row('2026-09')              # হাজিরা নেই → বেতন ০
        self.assertEqual(sep['loan_deduction'], 0)
        self.assertEqual(sep['loan_shortfall'], Decimal('3000'))

    def test_payroll_deducts_emi_and_shows_on_payslip(self):
        for d in list(h.date_range(date(2026, 9, 1), date(2026, 10, 31))):
            if d.strftime('%A') != 'Friday':
                Attendance.objects.create(employee=self.emp, company=self.company,
                                          timestamp=timezone.make_aware(datetime.combine(d, time(9, 55))), status='In')
                Attendance.objects.create(employee=self.emp, company=self.company,
                                          timestamp=timezone.make_aware(datetime.combine(d, time(18, 0))), status='Out')
        _d, before = self._row('2026-08')
        self.assertEqual(before['loan_deduction'], 0)
        data, sep = self._row('2026-09')
        self.assertEqual(sep['final_salary'], Decimal('17000'))   # ২০০০০ − ৩০০০
        self.assertEqual(sep['loan_deduction'], Decimal('3000'))
        self.assertTrue(data['loan_deduction_any'])
        _d, oct_ = self._row('2026-10')
        self.assertEqual(oct_['loan_deduction'], Decimal('2000'))
        self.assertEqual(oct_['loan_items'][0]['remaining'], 0)
        self.client.force_login(self.owner)
        r = self.client.get(reverse('payroll:salary_summary_list') + '?month=2026-09')
        self.assertContains(r, 'Loan deduction')

    def test_cash_repayment_and_close(self):
        from payroll.models import LoanRepayment
        LoanRepayment.objects.create(loan=self.loan, date=date(2026, 9, 20), amount=Decimal('4000'))
        self.loan = type(self.loan).objects.get(pk=self.loan.pk)
        self.assertEqual(self.loan.deduction_for(2026, 9), Decimal('1000'))   # ঐ মাসে হাতে 4000 — বাকি 1000
        self.assertEqual(self.loan.deduction_for(2026, 10), 0)
        self.loan.status = 'closed'
        self.loan.closed_from = date(2026, 9, 1)
        self.loan.save()
        self.assertEqual(self.loan.deduction_for(2026, 9), 0)

    def test_loan_pages(self):
        self.client.force_login(self.owner)
        for name, args in [('loan_list', []), ('loan_add', []), ('loan_detail', [self.loan.pk]), ('loan_edit', [self.loan.pk])]:
            self.assertEqual(self.client.get(reverse(f'payroll:{name}', args=args)).status_code, 200, name)
        r = self.client.post(reverse('payroll:loan_add'), {'employee': self.emp.pk, 'loan_type': 'advance', 'amount': '1200',
                                                           'emi_count': 3, 'monthly_deduction': '', 'start_month': '2026-12',
                                                           'status': 'active', 'note': ''})
        self.assertEqual(r.status_code, 302)
        from payroll.models import EmployeeLoan
        self.assertEqual(EmployeeLoan.objects.get(loan_type='advance').monthly_deduction, Decimal('400.00'))


class ExcelExportTests(Base):
    def setUp(self):
        super().setUp()
        from payroll.models import EmployeeLoan, EmployeeSalary
        EmployeeSalary.objects.create(employee=self.emp, company=self.company, base_salary=Decimal('20000'))
        EmployeeLoan.objects.create(company=self.company, employee=self.emp, amount=Decimal('1000'), emi_count=1,
                                    monthly_deduction=Decimal('1000'), start_month=date(2026, 11, 1))
        Attendance.objects.create(employee=self.emp, company=self.company, timestamp=aware(2026, 11, 3, 10, 5), status='In')
        Attendance.objects.create(employee=self.emp, company=self.company, timestamp=aware(2026, 11, 3, 18, 0), status='Out')
        LeaveRequest.objects.create(company=self.company, employee=self.emp, leave_type='Sick',
                                    start_date=date(2026, 11, 4), end_date=date(2026, 11, 5), status='Approved')
        self.client.force_login(self.owner)

    def _book(self, url):
        import io
        from openpyxl import load_workbook
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200, url)
        self.assertEqual(r['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertIn('.xlsx', r['Content-Disposition'])
        return load_workbook(io.BytesIO(r.content))

    def _headers(self, ws):
        for row in ws.iter_rows(min_row=1, max_row=12):
            vals = [c.value for c in row]
            if row[0].font.bold and row[0].fill.fgColor.rgb.endswith('4F46E5'):
                return vals
        return []

    def test_all_reports_export(self):
        q = '?start_date=2026-11-01&end_date=2026-11-07'
        wb = self._book(reverse('attendance_app:attendance_list_excel') + q)
        self.assertEqual(wb.sheetnames, ['Daily', 'Register'])
        self.assertIn('Worked', self._headers(wb['Daily']))
        self.assertIn('Late', self._headers(wb['Register']))
        self._book(reverse('attendance_app:monthly_report_excel') + q)
        self._book(reverse('attendance_app:employee_attendance_excel', args=[self.emp.pk]) + q)
        wb = self._book(reverse('attendance_app:leave_summary_excel'))
        self.assertEqual(wb.sheetnames, ['Summary', 'Requests'])
        self._book(reverse('attendance_app:leave_list_excel'))
        self._book(reverse('attendance_app:leave_balance_excel'))
        self._book(reverse('payroll:loan_excel'))
        wb = self._book(reverse('payroll:salary_summary_excel') + '?month=2026-11')
        ws = wb['Salary sheet']
        headers = self._headers(ws)
        self.assertIn('Loan deduction', headers)
        self.assertIn('Final salary', headers)
        # নামের সারিতে লোনের কাটা ১০০০
        for row in ws.iter_rows(values_only=True):
            if row and row[0] == 'Rahim':
                self.assertEqual(row[headers.index('Loan deduction')], 1000)
                break
        else:
            self.fail('employee row missing')


def _jpeg_data_url():
    import base64
    import io as _io
    from PIL import Image
    buf = _io.BytesIO()
    Image.new('RGB', (40, 50), (200, 150, 120)).save(buf, format='JPEG')
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()


class PortalTests(Base):
    def setUp(self):
        super().setUp()
        import tempfile
        from django.test import override_settings
        self._tmp = tempfile.TemporaryDirectory()
        self._override = override_settings(PRIVATE_MEDIA_ROOT=self._tmp.name)
        self._override.enable()
        self.emp_user = User.objects.create_user('rahim', password='Str0ng-pass!')
        self.emp.user = self.emp_user
        self.emp.save()

    def tearDown(self):
        self._override.disable()
        self._tmp.cleanup()
        super().tearDown()

    def _login_emp(self):
        self.client.force_login(self.emp_user)

    def test_login_redirects_employee_to_portal(self):
        r = self.client.post('/login/', {'username': 'rahim', 'password': 'Str0ng-pass!'})
        self.assertRedirects(r, reverse('portal:home'), fetch_redirect_response=False)

    def test_employee_cannot_open_admin_pages(self):
        self._login_emp()
        for url in [reverse('attendance_app:dashboard'), reverse('attendance_app:employee_list'),
                    reverse('attendance_app:attendance_list'), reverse('payroll:salary_summary_list'),
                    reverse('attendance_app:attendance_list_excel'), reverse('payroll:salary_summary_excel'),
                    reverse('attendance_app:leave_list'), reverse('userapp:employee_profile_list')]:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertEqual(r['Location'], reverse('portal:home'), url)
        r = self.client.get(reverse('attendance_app:device_list'), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 403)

    def test_admin_still_works_and_portal_sends_admin_back(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('attendance_app:dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('portal:home')).status_code, 302)

    def test_portal_pages(self):
        Attendance.objects.create(employee=self.emp, company=self.company, timestamp=aware(2026, 10, 5, 10, 20), status='In')
        Attendance.objects.create(employee=self.emp, company=self.company, timestamp=aware(2026, 10, 5, 18, 0), status='Out')
        self._login_emp()
        r = self.client.get(reverse('portal:home') + '?month=2026-10')
        self.assertContains(r, 'October 2026')
        self.assertNotContains(r, 'Remote Punch')                 # GPS বন্ধ
        self.assertEqual(self.client.get(reverse('portal:leave')).status_code, 200)
        self.assertEqual(self.client.get(reverse('portal:payslips')).status_code, 200)
        self.assertEqual(self.client.get(reverse('portal:punch')).status_code, 302)  # বন্ধ থাকলে ফেরত

    def test_leave_application_respects_balance(self):
        from attendance_app.models import LeaveAllocation
        LeaveAllocation.objects.create(company=self.company, leave_type='Casual', days_per_year=2)
        self._login_emp()
        today = timezone.localdate()
        r = self.client.post(reverse('portal:leave'), {'leave_type': 'Casual', 'start_date': today, 'end_date': today + timedelta(days=4), 'reason': 'x'})
        self.assertContains(r, 'বাকি আছে মাত্র 2 দিন')
        r = self.client.post(reverse('portal:leave'), {'leave_type': 'Casual', 'start_date': today, 'end_date': today + timedelta(days=1), 'reason': 'x'})
        self.assertEqual(r.status_code, 302)
        lv = LeaveRequest.objects.get(employee=self.emp)
        self.assertEqual(lv.status, 'Pending')
        self.assertTrue(Notification.objects.filter(user=self.owner, title__contains='Rahim').exists())
        self.client.post(reverse('portal:leave_cancel', args=[lv.pk]))
        self.assertFalse(LeaveRequest.objects.exists())

    def test_payslip_matches_admin_salary_sheet(self):
        from payroll.models import EmployeeLoan, EmployeeSalary
        from attendance_app.portal import services
        EmployeeSalary.objects.create(employee=self.emp, company=self.company, base_salary=Decimal('26000'))
        EmployeeLoan.objects.create(company=self.company, employee=self.emp, amount=Decimal('500'), emi_count=1,
                                    monthly_deduction=Decimal('500'), start_month=date(2026, 10, 1))
        for d in h.date_range(date(2026, 10, 1), date(2026, 10, 31)):
            if d.strftime('%A') != 'Friday':
                Attendance.objects.create(employee=self.emp, company=self.company,
                                          timestamp=timezone.make_aware(datetime.combine(d, time(9, 50))), status='In')
                Attendance.objects.create(employee=self.emp, company=self.company,
                                          timestamp=timezone.make_aware(datetime.combine(d, time(18, 0))), status='Out')
        from attendance_app.utils import pdf as pdf_utils
        if pdf_utils.HTML is not None:
            self._login_emp()
            r = self.client.get(reverse('portal:payslip_pdf', args=['2026-10']))
            self.assertEqual((r.status_code, r['Content-Type']), (200, 'application/pdf'))
            self.assertTrue(r.content.startswith(b'%PDF'))
            self.assertEqual(self.client.get(reverse('portal:payslip_pdf', args=['2031-01'])).status_code, 404)
        slip = services.payslip(self.emp, '2026-10')
        from django.test import RequestFactory
        from payroll.views import get_salary_summary_data
        req = RequestFactory().get('/')
        req.user, req.session = self.owner, {}
        admin_row = get_salary_summary_data(req, '2026-10')['summaries'][0]
        self.assertEqual(slip['final_salary'], admin_row['final_salary'])
        self.assertEqual(slip['loan_deduction'], Decimal('500'))
        self.assertIn(date(2026, 10, 1), services.payslip_months(self.emp, limit=120))

    def test_remote_punch_gps_selfie(self):
        hr = HRSettings.for_company(self.company)
        hr.enable_gps_attendance = True
        hr.save()
        self._login_emp()
        self.assertContains(self.client.get(reverse('portal:home')), 'Remote Punch')
        self.assertEqual(self.client.get(reverse('portal:punch')).status_code, 200)
        url = reverse('portal:punch_submit')
        bad = self.client.post(url, {'latitude': '23.7', 'longitude': '90.4', 'selfie': 'data:text/plain;base64,aGVsbG8='})
        self.assertEqual(bad.status_code, 400)
        bad = self.client.post(url, {'latitude': '123', 'longitude': '90.4', 'selfie': _jpeg_data_url()})
        self.assertEqual(bad.status_code, 400)
        r = self.client.post(url, {'latitude': '23.810331', 'longitude': '90.412521', 'accuracy': '12.5',
                                   'selfie': _jpeg_data_url(), 'note': 'Client office'})
        self.assertEqual(r.json()['punch'], 'In')
        att = Attendance.objects.get(employee=self.emp)
        self.assertTrue(att.is_remote_punch)
        self.assertEqual(str(att.latitude), '23.810331')
        self.assertTrue(att.selfie.name.startswith('remote_punch/'))
        self.assertTrue(att.selfie.storage.exists(att.selfie.name))
        self.assertEqual(self.client.post(url, {'latitude': '23.8', 'longitude': '90.4', 'selfie': _jpeg_data_url()}).status_code, 400)  # ২ মিনিটের মধ্যে
        Attendance.objects.filter(pk=att.pk).update(timestamp=timezone.now() - timedelta(hours=8))
        r = self.client.post(url, {'latitude': '23.8', 'longitude': '90.4', 'selfie': _jpeg_data_url()})
        if timezone.localtime(timezone.now() - timedelta(hours=8)).date() == timezone.localdate():
            self.assertEqual(r.json()['punch'], 'Out')

        # সেলফি: শুধু কোম্পানির অ্যাডমিন; কর্মচারী/অন্য কোম্পানি না
        selfie_url = reverse('attendance_app:remote_punch_selfie', args=[att.pk])
        self.assertEqual(self.client.get(selfie_url).status_code, 302)
        self.client.force_login(self.owner)
        r = self.client.get(selfie_url)
        self.assertEqual((r.status_code, r['Content-Type']), (200, 'image/jpeg'))
        self.assertContains(self.client.get(reverse('attendance_app:remote_punch_list')), 'View on map')
        other = User.objects.create_user('other', password='x')
        other_co = Company.objects.create(name='Other', owner=other)
        UserProfile.objects.create(user=other, company=other_co)
        UserSubscription.objects.create(user=other, plan=SubscriptionPlan.objects.first(), start_date=date(2020, 1, 1), end_date=date(2099, 1, 1))
        self.client.force_login(other)
        self.assertEqual(self.client.get(selfie_url).status_code, 404)

    def test_gps_disabled_rejects_submit(self):
        self._login_emp()
        r = self.client.post(reverse('portal:punch_submit'), {'latitude': '23.8', 'longitude': '90.4', 'selfie': _jpeg_data_url()})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Attendance.objects.exists())

    def test_expired_company_blocks_portal(self):
        UserSubscription.objects.update(end_date=date(2021, 1, 1))
        self._login_emp()
        self.assertContains(self.client.get(reverse('portal:home')), 'সাবস্ক্রিপশন', status_code=403)

    def test_admin_creates_and_disables_portal_login(self):
        emp2 = Employee.objects.create(company=self.company, department=self.dept, name='Karim', device_user_id=2)
        self.client.force_login(self.owner)
        url = reverse('attendance_app:employee_portal_access', args=[emp2.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url, {'username': 'karim01', 'password': 'Another-Str0ng1'})
        emp2.refresh_from_db()
        self.assertEqual(emp2.user.username, 'karim01')
        self.assertTrue(emp2.user.check_password('Another-Str0ng1'))
        self.client.post(reverse('attendance_app:employee_portal_revoke', args=[emp2.pk]))
        emp2.user.refresh_from_db()
        self.assertFalse(emp2.user.is_active)
        # অ্যাডমিন লগইন যুক্ত থাকলে বদলানো যায় না
        self.emp.user = self.owner
        self.emp.save()
        r = self.client.get(reverse('attendance_app:employee_portal_access', args=[self.emp.pk]))
        self.assertEqual(r.status_code, 302)


class MessagingTests(Base):
    def setUp(self):
        super().setUp()
        from userapp.models import EmployeeProfile
        EmployeeProfile.objects.create(employee=self.emp, mobile_number='01712-345678')
        self.dept.manager_phone = '+8801811111111'
        self.dept.save()

    def test_normalize_and_dedupe(self):
        from attendance_app.messaging import backends
        from attendance_app.messaging.service import send_message
        from attendance_app.models import MessageLog
        self.assertEqual(backends.normalize_bd('01712-345678'), '8801712345678')
        self.assertEqual(backends.normalize_bd('+880 1712 345678'), '8801712345678')
        self.assertEqual(backends.normalize_bd('12345'), '')
        a = send_message(self.company, '01712345678', 'hi', kind='test', dedupe_key='k1')
        b = send_message(self.company, '01712345678', 'hi', kind='test', dedupe_key='k1')
        self.assertEqual((a.status, a.pk), ('logged', b.pk))            # gateway নেই → শুধু লগ, একবারই
        self.assertEqual(send_message(self.company, 'abc', 'hi', kind='test').status, 'skipped')
        self.assertEqual(MessageLog.objects.count(), 2)

    def test_http_gateway(self):
        import os
        from unittest import mock
        from attendance_app.messaging.service import send_message
        env = {'MESSAGING_SMS_BACKEND': 'http', 'MESSAGING_HTTP_URL': 'https://sms.example/api?k=1&to={to}&msg={text}'}
        resp = mock.Mock(ok=True, status_code=200, text='{"response_code":202}')
        with mock.patch.dict(os.environ, env), mock.patch('requests.request', return_value=resp) as req:
            log = send_message(self.company, '01712345678', 'আজ অনুপস্থিত', kind='test')
        self.assertEqual(log.status, 'sent')
        url = req.call_args[0][1]
        self.assertIn('to=8801712345678', url)
        self.assertNotIn(' ', url)
        resp.ok = False
        with mock.patch.dict(os.environ, env), mock.patch('requests.request', return_value=resp):
            self.assertEqual(send_message(self.company, '01712345678', 'x', kind='test').status, 'failed')

    def test_daily_absent_late_alert(self):
        from attendance_app.messaging.alerts import daily_absence_alerts
        from attendance_app.models import MessageLog
        karim = Employee.objects.create(company=self.company, department=self.dept, name='Karim', device_user_id=2)
        day = date(2026, 11, 3)    # মঙ্গলবার
        Attendance.objects.create(employee=karim, company=self.company, timestamp=aware(2026, 11, 3, 10, 25), status='In')
        hr = HRSettings.for_company(self.company)
        hr.enable_daily_absence_alert, hr.daily_alert_time = True, time(11, 0)
        hr.save()
        self.assertEqual(daily_absence_alerts(now=aware(2026, 11, 3, 10, 30)), {})          # সময় হয়নি
        res = daily_absence_alerts(now=aware(2026, 11, 3, 11, 5))
        self.assertEqual(res, {'Riya': ['logged']})
        msg = MessageLog.objects.get(kind='daily_alert')
        self.assertIn('Absent (1): Rahim', msg.text)
        self.assertIn('Late (1): Karim 25m', msg.text)
        self.assertEqual(msg.to, '8801811111111')
        daily_absence_alerts(now=aware(2026, 11, 3, 13, 0))                                    # দিনে একবার
        self.assertEqual(MessageLog.objects.filter(kind='daily_alert').count(), 1)
        self.assertIsNotNone(day)

    def test_salary_sms_with_secure_payslip_link(self):
        from payroll.models import EmployeeSalary
        from attendance_app.models import MessageLog
        EmployeeSalary.objects.create(employee=self.emp, company=self.company, base_salary=Decimal('20000'))
        Attendance.objects.create(employee=self.emp, company=self.company, timestamp=aware(2026, 10, 5, 9, 55), status='In')
        Attendance.objects.create(employee=self.emp, company=self.company, timestamp=aware(2026, 10, 5, 18, 0), status='Out')
        hr = HRSettings.for_company(self.company)
        hr.enable_salary_sms = True
        hr.save()
        self.client.force_login(self.owner)
        r = self.client.post(reverse('payroll:send_salary_sms'), {'month': '2026-10'})
        self.assertEqual(r.status_code, 302)
        msg = MessageLog.objects.get(kind='salary')
        self.assertIn('salary for October 2026 has been disbursed', msg.text)
        link = msg.text.split('Payslip: ')[1].strip()
        path = '/' + link.split('://', 1)[1].split('/', 1)[1]
        self.client.logout()
        from attendance_app.utils import pdf as pdf_utils
        if pdf_utils.HTML is not None:
            r = self.client.get(path)
            self.assertEqual((r.status_code, r['Content-Type']), (200, 'application/pdf'))
        self.assertEqual(self.client.get(path[:-3] + 'xyz/').status_code, 404)              # বদলানো টোকেন
        # একই মাসে আবার চাপলে দ্বিতীয়বার যায় না
        self.client.force_login(self.owner)
        self.client.post(reverse('payroll:send_salary_sms'), {'month': '2026-10'})
        self.assertEqual(MessageLog.objects.filter(kind='salary').count(), 1)

    def test_salary_sms_requires_setting(self):
        self.client.force_login(self.owner)
        self.client.post(reverse('payroll:send_salary_sms'), {'month': '2026-10'})
        from attendance_app.models import MessageLog
        self.assertFalse(MessageLog.objects.exists())


class UnknownAndAnalyticsTests(Base):
    def test_hikvision_stranger_face(self):
        from attendance_app.models import UnknownPunch
        dev = Device.objects.create(company=self.company, department=self.dept, name='Hik', protocol=Device.PROTOCOL_HIK_PUSH)
        now = timezone.localtime().replace(microsecond=0)
        event = {"dateTime": now.isoformat(), "AccessControllerEvent": {"majorEventType": 5, "subEventType": 76,
                                                                        "currentVerifyMode": "face"}}
        r = self.client.post(f'/api/devices/{dev.push_token}/hikvision/', {'event_log': json.dumps(event)})
        self.assertTrue(r.json()['stranger'])
        self.assertEqual(UnknownPunch.objects.get().reason, 'stranger')
        self.assertFalse(Attendance.objects.exists())
        self.assertTrue(Notification.objects.filter(title__contains='অচেনা মুখ').exists())

    def test_notification_poll(self):
        self.client.force_login(self.owner)
        n = Notification.objects.create(company=self.company, user=self.owner, title='T', message='M')
        d = self.client.get(reverse('attendance_app:notification_poll') + '?since=0').json()
        self.assertEqual((d['unread'], d['items'][0]['title'], d['last_id']), (1, 'T', n.id))
        self.assertEqual(self.client.get(reverse('attendance_app:notification_poll') + f'?since={n.id}').json()['items'], [])

    def test_analytics_insights(self):
        from attendance_app.analytics_views import attendance_frame, compute_insights
        sales = Department.objects.create(company=self.company, branch=self.branch, name='Sales',
                                          in_time=time(10, 0), out_time=time(18, 0), weekly_off_day='Friday')
        karim = Employee.objects.create(company=self.company, department=sales, name='Karim', device_user_id=2)
        # নভেম্বর ২–৭ (শুক্রবার ৬ বাদ): Rahim প্রতিদিন ২০ মিনিট দেরি; Karim শুধু ২ ও ৩ তারিখে এসেছে
        for d in (2, 3, 4, 5, 7):
            Attendance.objects.create(employee=self.emp, company=self.company, timestamp=aware(2026, 11, d, 10, 20), status='In')
            Attendance.objects.create(employee=self.emp, company=self.company, timestamp=aware(2026, 11, d, 18, 0), status='Out')
        for d in (2, 3):
            Attendance.objects.create(employee=karim, company=self.company, timestamp=aware(2026, 11, d, 9, 55), status='In')
        data = compute_insights(attendance_frame([self.emp, karim], date(2026, 11, 2), date(2026, 11, 7)))
        self.assertEqual(data['top_late'][0], {'name': 'Rahim', 'dept': 'Shop', 'late_days': 5, 'late_min': 100, 'avg_min': 20})
        rates = {d['dept']: d['rate'] for d in data['dept_rates']}
        self.assertEqual(rates, {'Sales': 60.0, 'Shop': 0.0})
        self.assertEqual(data['kpi']['absence_rate'], 30.0)                 # 3 of 10
        self.assertEqual(data['worst_days'][0]['absent'], 1)
        self.assertTrue(any('Sales' in n for n in data['notes']))
        # ৩+ জনের ডিপার্টমেন্টে কেউ না এলে → "ডাটা নেই" দিন, হিসাব থেকে বাদ
        for i in range(3, 6):
            Employee.objects.create(company=self.company, department=sales, name=f'S{i}', device_user_id=i)
        data2 = compute_insights(attendance_frame(Employee.objects.filter(department=sales), date(2026, 11, 4), date(2026, 11, 4)))
        self.assertEqual(len(data2['data_gaps']), 1)
        self.assertEqual(data2['kpi']['absent_days'], 0)
        self.assertTrue(any('সিঙ্ক হয়নি' in n for n in data2['notes']))
        self.client.force_login(self.owner)
        r = self.client.get(reverse('attendance_app:analytics') + '?month=2026-11')
        self.assertContains(r, 'Top 5 frequently late employees')
        self.assertContains(r, 'chDept')
        self.assertEqual(self.client.get(reverse('attendance_app:message_log')).status_code, 200)
        self.assertEqual(self.client.get(reverse('attendance_app:unknown_punch_list')).status_code, 200)


class SalaryStructureTests(Base):
    """Basic + allowance − fixed deductions; payslip এর Earnings − Deductions = Net"""

    def setUp(self):
        super().setUp()
        from payroll.models import EmployeeSalary
        self.sal = EmployeeSalary.objects.create(employee=self.emp, company=self.company, base_salary=Decimal('20000'),
                                                 bank_transfer_amount=Decimal('10000'))
        # নভেম্বর: ২৬ কর্মদিবস; ৩ দিন অনুপস্থিত (১, ২, ৩ তারিখ) → ২৩ দিন উপস্থিত
        for d in h.date_range(date(2026, 11, 4), date(2026, 11, 30)):
            if d.strftime('%A') != 'Friday':
                Attendance.objects.create(employee=self.emp, company=self.company,
                                          timestamp=timezone.make_aware(datetime.combine(d, time(9, 55))), status='In')
                Attendance.objects.create(employee=self.emp, company=self.company,
                                          timestamp=timezone.make_aware(datetime.combine(d, time(18, 0))), status='Out')
        self.client.force_login(self.owner)

    def _row(self):
        from django.test import RequestFactory
        from payroll.views import get_salary_summary_data
        req = RequestFactory().get('/')
        req.user, req.session = self.owner, {}
        return get_salary_summary_data(req, '2026-11')['summaries'][0]

    def _save(self, **extra):
        from payroll.models import SalaryComponent
        SalaryComponent.ensure_defaults(self.company)
        comps = {c.name: c.id for c in SalaryComponent.objects.filter(company=self.company)}
        data = {'employee': self.emp.pk, 'basic': '20000', 'bank': '10000', 'bonus_percent': '0', 'bonus_fixed': '0',
                'bonus_month': '12', f"component_{comps['House Rent']}": '6000', f"component_{comps['Medical']}": '0',
                f"component_{comps['Transport']}": '0', f"component_{comps['Tax / PF']}": '1000'}
        data.update(extra)
        return self.client.post(reverse('payroll:salary_base_save'), data, HTTP_X_REQUESTED_WITH='XMLHttpRequest'), comps

    def test_no_components_same_as_before(self):
        from payroll.payslip import payslip_lines
        row = self._row()
        self.assertEqual(row['base_salary'], Decimal('20000'))
        self.assertEqual(row['fixed_deductions'], 0)
        lines = payslip_lines(row)
        self.assertEqual((lines['total_earnings'] - lines['total_deductions']).quantize(Decimal('0.01')), row['final_salary'].quantize(Decimal('0.01')))

    def test_allowances_and_deductions_flow_into_payroll(self):
        from payroll.models import SalaryChange
        from payroll.payslip import payslip_lines
        before = self._row()['final_salary']                     # 20000 × 23/26
        r, comps = self._save()
        self.assertEqual(r.json()['status'], 'success')
        self.assertEqual(SalaryChange.objects.get().gross, Decimal('26000'))
        row = self._row()
        self.assertEqual(row['base_salary'], Decimal('26000'))    # gross
        self.assertEqual(row['fixed_deductions'], Decimal('1000'))
        # House rent attendance অনুযায়ী আনুপাতিক → (26000 × 23/26) − 1000
        self.assertEqual(row['final_salary'].quantize(Decimal('0.01')), (Decimal(26000) * 23 / 26 - 1000).quantize(Decimal('0.01')))
        self.assertGreater(row['final_salary'], before)
        lines = payslip_lines(row)
        names = [n for n, _a in lines['earnings']]
        self.assertEqual(names[:2], ['Basic salary', 'House Rent'])
        self.assertTrue(any(n.startswith('Absence') for n, _a in lines['deductions']))
        self.assertEqual((lines['total_earnings'] - lines['total_deductions']).quantize(Decimal('0.01')),
                         row['final_salary'].quantize(Decimal('0.01')))

    def test_fixed_allowance_not_prorated(self):
        from payroll.models import SalaryComponent
        self._save()
        SalaryComponent.objects.filter(name='House Rent').update(prorate=False)
        row = self._row()
        self.assertEqual(row['final_salary'].quantize(Decimal('0.01')), (Decimal(20000) * 23 / 26 + 6000 - 1000).quantize(Decimal('0.01')))

    def test_save_validation(self):
        r, _c = self._save(bank='30000')
        self.assertEqual(r.status_code, 400)
        r, _c = self._save(basic='0')
        self.assertEqual(r.status_code, 400)

    def test_pages_render(self):
        self._save()
        for name, args in [('add_salary', []), ('salary_bulk_edit', []), ('components', []), ('payslip_list', []),
                           ('bonus_ot', []), ('salary_summary_list', []), ('loan_list', []), ('payroll_settings', [])]:
            r = self.client.get(reverse(f'payroll:{name}', args=args))
            self.assertEqual(r.status_code, 200, name)
            self.assertContains(r, 'Salary Base', msg_prefix=name)            # সাব-নেভিগেশন
        r = self.client.get(reverse('payroll:add_salary') + '?status=configured&type=mixed')
        self.assertContains(r, 'Rahim')
        self.assertContains(r, 'Live net salary preview')
        r = self.client.get(reverse('payroll:payslip_view', args=[self.emp.pk, '2026-11']))
        self.assertContains(r, 'Total deductions')
        self.assertContains(r, '@media print')
        self.assertEqual(self.client.get(reverse('payroll:salary_history', args=[self.emp.pk])).json()['history'][0]['gross'], '26000.00')
        from attendance_app.utils import pdf as pdf_utils
        if pdf_utils.HTML is not None:
            r = self.client.get(reverse('payroll:payslip_view', args=[self.emp.pk, '2026-11']) + '?format=pdf')
            self.assertEqual(r['Content-Type'], 'application/pdf')

    def test_component_crud(self):
        self.client.post(reverse('payroll:components'), {'name': 'Mobile bill', 'kind': 'earning', 'calc_type': 'fixed',
                                                         'default_value': '500', 'prorate': '', 'order': 5, 'is_active': 'on'})
        from payroll.models import SalaryComponent
        c = SalaryComponent.objects.get(name='Mobile bill')
        self.assertFalse(c.prorate)
        self._save()
        hr = SalaryComponent.objects.get(name='House Rent')
        self.client.post(reverse('payroll:component_delete', args=[hr.pk]))
        hr.refresh_from_db()
        self.assertFalse(hr.is_active)                            # ব্যবহার হচ্ছে → শুধু বন্ধ
        self.assertEqual(self._row()['base_salary'], Decimal('20000'))


class BulkAttendanceTests(Base):
    """Bulk / Official Duty এন্ট্রি আর Missing-punch quick-fix কুইউ"""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def _post(self, name, body):
        return self.client.post(reverse(f'attendance_app:{name}'), json.dumps(body), content_type='application/json')

    def _bulk(self, name='bulk_attendance_apply', **kw):
        body = {'employee_ids': [self.emp.pk], 'from_date': '2026-11-09', 'to_date': '2026-11-15',
                'status': 'official_duty', 'in_time': '', 'out_time': '', 'skip_off_days': True,
                'overwrite': False, 'remarks': 'Field Visit'}
        body.update(kw)
        r = self._post(name, body)
        return r, r.json()

    def _days(self, start=date(2026, 11, 9), end=date(2026, 11, 15)):
        return {d['date']: d for d in h.build_attendance([self.emp], start, end)[0][2]}

    def test_parse_time(self):
        from attendance_app.hr.bulk_entry import parse_time
        self.assertEqual(parse_time('9'), time(9, 0))
        self.assertEqual(parse_time('930'), time(9, 30))
        self.assertEqual(parse_time('18:30'), time(18, 30))
        self.assertEqual(parse_time('6:30pm'), time(18, 30))
        self.assertEqual(parse_time('12am'), time(0, 0))
        self.assertIsNone(parse_time(''))
        for bad in ('25:00', '9:75', 'abc', '13pm'):
            with self.assertRaises(ValueError):
                parse_time(bad)

    def test_preview_counts_and_skips_weekly_off(self):
        r, d = self._bulk('bulk_attendance_preview')
        self.assertEqual(r.status_code, 200)
        self.assertEqual((d['records'], d['employees'], d['days_each'], d['skipped_weekly_off']), (6, 1, 6, 1))
        self.assertFalse(Attendance.objects.exists())   # preview কিছু লেখে না

    def test_apply_writes_shift_times_and_is_idempotent(self):
        r, d = self._bulk()
        self.assertEqual(d['saved'], 6)
        qs = Attendance.objects.filter(employee=self.emp)
        self.assertEqual(qs.count(), 12)
        a = qs.filter(status='In').first()
        self.assertTrue(a.is_manual)
        self.assertEqual((a.entry_type, a.remarks, a.updated_by), ('official_duty', 'Field Visit', self.owner))
        days = self._days()
        mon = days[date(2026, 11, 9)]
        self.assertEqual((mon['status'], mon['work'], mon['late']), ('Present', timedelta(hours=8), timedelta()))
        self.assertEqual(days[date(2026, 11, 13)]['status'], 'Weekly Off')
        # আবার দিলে: আগের দিনগুলো বাদ (overwrite বন্ধ)
        _r, d2 = self._bulk()
        self.assertEqual((d2['saved'], d2['skipped_existing']), (0, 6))
        # overwrite চালু: একই সময়ের রেকর্ড আবার — IntegrityError নয়, সংখ্যা একই
        _r, d3 = self._bulk(overwrite=True, remarks='Client Meeting')
        self.assertEqual(d3['saved'], 6)
        self.assertEqual(qs.count(), 12)
        self.assertEqual(set(qs.values_list('remarks', flat=True)), {'Client Meeting'})

    def test_machine_punch_kept_unless_overwrite(self):
        Attendance.objects.create(employee=self.emp, company=self.company, timestamp=aware(2026, 11, 10, 10, 20), status='In')
        _r, d = self._bulk(in_time='09:00', out_time='17:00')
        self.assertEqual((d['saved'], d['skipped_existing']), (5, 1))
        self.assertEqual(self._days()[date(2026, 11, 10)]['punch_count'], 1)
        _r, d = self._bulk(in_time='09:00', out_time='17:00', overwrite=True)
        tue = self._days()[date(2026, 11, 10)]
        self.assertEqual((tue['punch_count'], h.to_local(tue['in_time']).time()), (2, time(9, 0)))

    def test_half_day_and_leave(self):
        LeaveRequest.objects.create(company=self.company, employee=self.emp, leave_type='Casual',
                                    start_date=date(2026, 11, 11), end_date=date(2026, 11, 11), status='Approved')
        _r, d = self._bulk(status='half_day')
        self.assertEqual((d['saved'], d['skipped_leave']), (5, 1))
        days = self._days()
        self.assertEqual(days[date(2026, 11, 9)]['work'], timedelta(hours=4))
        self.assertEqual(days[date(2026, 11, 11)]['status'], 'Leave')

    def test_night_shift_out_next_day(self):
        s = Shift.objects.create(company=self.company, name='Night', start_time=time(22), end_time=time(6))
        ShiftAssignment.objects.create(employee=self.emp, shift=s, start_date=date(2026, 11, 1))
        self._bulk(from_date='2026-11-09', to_date='2026-11-09')
        mon = self._days(date(2026, 11, 9), date(2026, 11, 9))[date(2026, 11, 9)]
        self.assertEqual(mon['work'], timedelta(hours=8))
        self.assertEqual(h.to_local(mon['out_time']).date(), date(2026, 11, 10))

    def test_other_company_rejected(self):
        other_c = Company.objects.create(name='Other', owner=User.objects.create_user('o2'))
        other = Employee.objects.create(company=other_c, name='X', device_user_id=9)
        r, d = self._bulk(employee_ids=[other.pk])
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Attendance.objects.exists())
        r = self._post('missing_queue_save', {'rows': [{'employee_id': other.pk, 'date': '2026-11-09', 'in': '9', 'out': '17'}]})
        self.assertEqual(r.json()['saved'], [])
        self.assertFalse(Attendance.objects.exists())

    def test_queue_lists_and_fixes(self):
        machine_in = Attendance.objects.create(employee=self.emp, company=self.company,
                                               timestamp=aware(2026, 11, 10, 10, 5), status='In')
        MissingPunch.objects.create(company=self.company, employee=self.emp, date=date(2026, 11, 10),
                                    in_time=machine_in.timestamp)
        r = self.client.get(reverse('attendance_app:missing_queue'), {'from': '2026-11-09', 'to': '2026-11-11'})
        self.assertEqual(r.status_code, 200)
        rows = {x['date']: x for x in r.context['rows']}
        self.assertEqual(rows['2026-11-09']['issue'], 'no_punch')
        self.assertEqual((rows['2026-11-10']['issue'], rows['2026-11-10']['current_in']), ('missing_out', '10:05'))
        self.assertEqual(rows['2026-11-09']['shift']['in'], '10:00')

        r = self._post('missing_queue_save', {'rows': [
            {'employee_id': self.emp.pk, 'date': '2026-11-09', 'in': '10:00', 'out': '18:00', 'status': 'present', 'remark': 'Forgot'},
            {'employee_id': self.emp.pk, 'date': '2026-11-10', 'in': '10:05', 'out': '6:30p', 'status': 'present'},
            {'employee_id': self.emp.pk, 'date': '2026-11-11', 'in': '', 'out': '18:00', 'status': 'present'},
        ]})
        d = r.json()
        self.assertEqual(sorted(d['saved']), [f'{self.emp.pk}:2026-11-09', f'{self.emp.pk}:2026-11-10'])
        self.assertIn(f'{self.emp.pk}:2026-11-11', d['errors'])
        self.assertTrue(Attendance.objects.filter(pk=machine_in.pk, is_manual=False).exists())   # মেশিনের In থাকে
        days = self._days(date(2026, 11, 9), date(2026, 11, 11))
        self.assertEqual(days[date(2026, 11, 9)]['status'], 'Present')
        self.assertEqual(h.to_local(days[date(2026, 11, 10)]['out_time']).time(), time(18, 30))
        self.assertEqual(MissingPunch.objects.get().status, MissingPunch.STATUS_RESOLVED)
        # আবার একই সারি: এখন ঠিক হয়ে গেছে — ওভাররাইট নয়
        d = self._post('missing_queue_save', {'rows': [
            {'employee_id': self.emp.pk, 'date': '2026-11-09', 'in': '08:00', 'out': '12:00', 'status': 'present'}]}).json()
        self.assertEqual(d['already_fixed'], [f'{self.emp.pk}:2026-11-09'])
        self.assertEqual(h.to_local(self._days(date(2026, 11, 9), date(2026, 11, 9))[date(2026, 11, 9)]['in_time']).time(), time(10, 0))

    def test_pages_render(self):
        for name in ('bulk_attendance', 'missing_queue'):
            r = self.client.get(reverse(f'attendance_app:{name}'))
            self.assertEqual(r.status_code, 200, name)
        r = self.client.get(reverse('attendance_app:missing_queue'), {'month': '2026-10', 'department': self.dept.pk, 'issue': 'no_punch'})
        self.assertEqual(r.context['f']['from'], '2026-10-01')
        self.assertEqual(r.context['f']['to'], '2026-10-31')


class ScaleTests(Base):
    """অনেক কর্মচারী: ড্যাশবোর্ড Load more, পেজিনেশন — মোট সবসময় সবার"""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)
        from payroll.models import EmployeeSalary
        self.emps = [self.emp] + [Employee.objects.create(company=self.company, department=self.dept, name=f'E{i:03d}',
                                                          device_user_id=i + 10) for i in range(124)]
        for e in self.emps:
            EmployeeSalary.objects.create(employee=e, company=self.company, base_salary=Decimal('10000'),
                                          bank_transfer_amount=Decimal('0'))
        Employee.objects.create(company=self.company, department=self.dept, name='Left job', device_user_id=999,
                                is_active=False)

    def test_dashboard_first_rows_and_load_more(self):
        r = self.client.get(reverse('attendance_app:dashboard'))
        self.assertEqual(r.context['total_employees'], 125)          # inactive বাদ
        self.assertEqual(len(r.context['employee_rows']), 20)
        seen = 20
        while seen < 125:
            r = self.client.get(reverse('attendance_app:dashboard'), {'log_offset': seen},
                                HTTP_X_REQUESTED_WITH='XMLHttpRequest')
            n = int(r['X-Rows'])
            self.assertGreater(n, 0)
            seen += n
        self.assertEqual(seen, 125)

    def test_employee_list_paginated(self):
        r = self.client.get(reverse('attendance_app:employee_list'))
        self.assertEqual((r.context['total_employees'], len(r.context['employees'])), (126, 50))
        r = self.client.get(reverse('attendance_app:employee_list'), {'q': 'E12'})
        self.assertEqual(r.context['total_employees'], 4)            # E120..E123 — সার্চ পুরো তালিকায়

    def test_salary_sheet_totals_cover_all_pages(self):
        r = self.client.get(reverse('payroll:salary_summary_list'), {'month': '2026-10'})
        self.assertEqual(r.context['summary_count'], 125)
        self.assertEqual(len(r.context['page_rows']), 100)
        total = sum(s['final_salary'] for s in r.context['summaries'])
        self.assertEqual(r.context['total_final_salary'], total)     # মোট = সব ১২৫ জনের
        r2 = self.client.get(reverse('payroll:salary_summary_list'), {'month': '2026-10', 'page': 2})
        self.assertEqual((len(r2.context['page_rows']), r2.context['page_offset']), (25, 100))
        self.assertEqual(r2.context['total_final_salary'], total)
        r = self.client.get(reverse('payroll:payslip_list'), {'month': '2026-10'})
        self.assertEqual((r.context['row_count'], len(r.context['rows'])), (125, 100))
        self.assertEqual(r.context['totals']['net'], total)


class PagesSmokeTests(Base):
    def test_new_pages_render(self):
        self.client.force_login(self.owner)
        dev = Device.objects.create(company=self.company, department=self.dept, name='Gate',
                                    protocol=Device.PROTOCOL_ZK_ADMS, serial_number='SN1')
        s = Shift.objects.create(company=self.company, name='Night', start_time=time(22), end_time=time(6), color='violet')
        ShiftAssignment.objects.create(employee=self.emp, shift=s, start_date=date(2026, 1, 1))
        for name, args in [('device_list', []), ('device_add', []), ('device_detail', [dev.pk]), ('device_edit', [dev.pk]),
                           ('punch_feed', []), ('shift_list', []), ('shift_add', []), ('shift_edit', [s.pk]),
                           ('shift_assign', []), ('shift_roster', []), ('employee_shift_history', [self.emp.pk]),
                           ('sync_attendance', []), ('dashboard', []), ('department_list', []),
                           ('department_edit', [self.dept.pk]), ('branch_list', [])]:
            r = self.client.get(reverse(f'attendance_app:{name}', args=args))
            self.assertEqual(r.status_code, 200, name)

    def test_shift_assign_post(self):
        self.client.force_login(self.owner)
        s = Shift.objects.create(company=self.company, name='M', start_time=time(8), end_time=time(16))
        r = self.client.post(reverse('attendance_app:shift_assign'),
                             {'shift': s.pk, 'start_date': '2026-11-01', 'employees': [self.emp.pk]})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(ShiftAssignment.objects.filter(employee=self.emp, shift=s).exists())

    def test_device_form_requires_serial_for_adms(self):
        self.client.force_login(self.owner)
        r = self.client.post(reverse('attendance_app:device_add'),
                             {'name': 'X', 'department': self.dept.pk, 'protocol': 'zk_adms', 'comm_key': 0, 'is_active': 'on'})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Device.objects.exists())
