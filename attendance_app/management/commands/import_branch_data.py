import json
import re
from collections import defaultdict
from datetime import datetime

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from attendance_app.models import Attendance, Department, Employee, Holiday, LeaveRequest
from attendance_app.services import create_employee_with_limit
from payroll.models import EmployeeSalary

AUTO_NAME = re.compile(r"^User \d+$")   # মেশিনে নাম না থাকলে সিঙ্ক এই নাম দেয়


class Command(BaseCommand):
    help = (
        "export_branch_data এর JSON ফাইল এই সার্ভারের একটা ডিপার্টমেন্টে ঢোকায়। "
        "কর্মচারী মেলানো হয় device ID দিয়ে (ওই ব্রাঞ্চের ভেতরে)। যে কর্মচারীর যে দিনের হাজিরা "
        "এখানে আগে থেকেই আছে সেই দিন বাদ যায় — এখানকার (লাইভের) ডাটাই ঠিক ধরা হয়। বারবার চালালেও ডুপ্লিকেট হয় না।"
    )

    def add_arguments(self, parser):
        parser.add_argument("file", help="export_branch_data দিয়ে বানানো JSON ফাইল")
        parser.add_argument("--department", type=int, required=True, help="এই সার্ভারের ডিপার্টমেন্ট id (যেখানে ঢুকবে)")
        parser.add_argument("--dry-run", action="store_true", help="কিছু না বদলে শুধু কী হবে দেখাও")
        parser.add_argument("--office-hours", action="store_true",
                            help="ফাইলের অফিস টাইম ও সাপ্তাহিক ছুটি এই ডিপার্টমেন্টে বসাও")
        parser.add_argument("--overwrite-salary", action="store_true",
                            help="এখানে আগে থেকে বেতন সেট থাকলেও ফাইলেরটা বসাও (না দিলে আগেরটা থাকে)")

    def handle(self, *args, file, department, dry_run=False, office_hours=False, overwrite_salary=False, **options):
        try:
            with open(file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise CommandError(f"Cannot read {file}: {e}")
        if data.get("format") not in ("smartattendance-branch-export/1", "smartattendance-branch-export/2"):
            raise CommandError("This file was not made by export_branch_data.")

        dept = Department.objects.select_related("company", "branch").filter(pk=department).first()
        if not dept or not dept.company_id:
            choices = ", ".join(f"{d.id}={d.name}" for d in Department.objects.order_by("id"))
            raise CommandError(f"Department {department} not found. Available: {choices}")
        src = data.get("source", {})
        where = f"{dept.branch.name} · {dept.name}" if dept.branch_id else dept.name
        self.stdout.write(f"From: {src.get('company')} / {src.get('department')} (exported {src.get('exported_at', '?')[:16]})")
        self.stdout.write(f"Into: {dept.company.name} / {where}")

        with transaction.atomic():
            stats = self._import(data, dept)
            self._import_extras(data, dept, stats, office_hours, overwrite_salary)
            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(
            f"Employees: {stats['matched']} matched, {stats['created']} added, {stats['renamed']} renamed"
            + (f", {stats['not_added']} NOT added (employee limit)" if stats['not_added'] else ""))
        self.stdout.write(
            f"Attendance: {stats['att_new']} records added for {stats['days_new']} employee-days; "
            f"{stats['days_skipped']} employee-days skipped (already on this server)")
        if data.get("leaves") is not None:
            self.stdout.write(f"Leaves: {stats['leave_new']} added, {stats['leave_dup']} already here")
            self.stdout.write(f"Holidays: {stats['hol_new']} added (for this branch), {stats['hol_dup']} already here")
            self.stdout.write(f"Salaries: {stats['sal_new']} added, {stats['sal_updated']} overwritten, {stats['sal_kept']} kept as is")
        oh = data.get("office_hours")
        if oh:
            state = "APPLIED" if office_hours else "not applied (add --office-hours)"
            self.stdout.write(f"Office hours in file: {oh['in_time']}–{oh['out_time']}, weekly off {oh['weekly_off_day']} → {state}")
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — nothing was saved."))
        else:
            self.stdout.write(self.style.SUCCESS("Import done."))

    def _import(self, data, dept):
        stats = defaultdict(int)
        by_uid = {e.device_user_id: e for e in Employee.id_scope(dept)}

        # 1) কর্মচারী: device ID দিয়ে মেলাই; না থাকলে যোগ (limit মেনে)
        for row in data.get("employees", []):
            uid, name = int(row["uid"]), (row.get("name") or "").strip()
            emp = by_uid.get(uid)
            if emp:
                stats["matched"] += 1
                # এখানে মেশিনের ডিফল্ট নাম ("User 12") আর লোকালে আসল নাম থাকলে আসলটা নিই
                if name and AUTO_NAME.match(emp.name) and not AUTO_NAME.match(name):
                    emp.name = name
                    emp.save(update_fields=["name"])
                    stats["renamed"] += 1
                continue
            try:
                by_uid[uid] = create_employee_with_limit(
                    company=dept.company, name=name or f"User {uid}", device_user_id=uid, department=dept)
                stats["created"] += 1
            except ValidationError:
                stats["not_added"] += 1

        # 2) হাজিরা: কর্মচারী-দিন ধরে; এখানে ওই দিনের রেকর্ড থাকলে পুরো দিন বাদ
        incoming = defaultdict(list)   # (employee, local_date) -> [(ts, status)]
        for row in data.get("attendance", []):
            emp = by_uid.get(int(row["uid"]))
            if not emp:
                continue
            ts = datetime.fromisoformat(row["timestamp"])
            if timezone.is_naive(ts):
                ts = timezone.make_aware(ts)
            incoming[(emp, timezone.localtime(ts).date())].append((ts, row["status"]))
        if not incoming:
            return stats

        emps = {emp.id for emp, _ in incoming}
        days = [d for _, d in incoming]
        existing = {(emp_id, timezone.localtime(ts).date())
                    for emp_id, ts in Attendance.objects.filter(
                        employee_id__in=emps, timestamp__date__range=(min(days), max(days))
                    ).values_list("employee_id", "timestamp")}

        to_create = []
        for (emp, day), punches in incoming.items():
            if (emp.id, day) in existing:
                stats["days_skipped"] += 1
                continue
            stats["days_new"] += 1
            to_create += [Attendance(employee=emp, company=dept.company, timestamp=ts, status=st) for ts, st in punches]
        Attendance.objects.bulk_create(to_create, batch_size=1000, ignore_conflicts=True)
        stats["att_new"] = len(to_create)
        return stats

    def _import_extras(self, data, dept, stats, office_hours, overwrite_salary):
        """ছুটি, সরকারি ছুটি, বেতন, অফিস টাইম (ফরম্যাট 2) — সব ডুপ্লিকেট-নিরাপদ"""
        company = dept.company
        by_uid = {e.device_user_id: e for e in Employee.id_scope(dept)}

        for row in data.get("leaves", []):
            emp = by_uid.get(int(row["uid"]))
            if not emp:
                continue
            _, created = LeaveRequest.objects.get_or_create(
                employee=emp, leave_type=row["leave_type"], start_date=row["start_date"], end_date=row["end_date"],
                defaults={"company": company, "reason": row.get("reason") or "", "status": row.get("status") or "Pending"})
            stats["leave_new" if created else "leave_dup"] += 1

        # পুরনো সার্ভারের ছুটিগুলো ওই শাখার জন্যই ছিল — তাই এই ব্রাঞ্চের ছুটি হিসেবে বসে
        for row in data.get("holidays", []):
            exists = Holiday.objects.filter(company=company, start_date=row["start_date"], end_date=row["end_date"]).filter(
                branch__isnull=True).exists() or Holiday.objects.filter(
                company=company, branch_id=dept.branch_id, start_date=row["start_date"], end_date=row["end_date"]).exists()
            if exists:
                stats["hol_dup"] += 1
                continue
            Holiday.objects.create(company=company, branch_id=dept.branch_id, title=row["title"],
                                   start_date=row["start_date"], end_date=row["end_date"], description=row.get("description") or "")
            stats["hol_new"] += 1

        fields = ("base_salary", "bank_transfer_amount", "yearly_bonus_percent", "yearly_bonus_fixed", "bonus_payout_month")
        for row in data.get("salaries", []):
            emp = by_uid.get(int(row["uid"]))
            if not emp:
                continue
            values = {f: row[f] for f in fields if f in row}
            sal = EmployeeSalary.objects.filter(employee=emp).first()
            if sal is None:
                EmployeeSalary.objects.create(employee=emp, company=company, **values)
                stats["sal_new"] += 1
            elif overwrite_salary:
                for f, v in values.items():
                    setattr(sal, f, v)
                sal.save()
                stats["sal_updated"] += 1
            else:
                stats["sal_kept"] += 1

        oh = data.get("office_hours")
        if office_hours and oh:
            dept.weekly_off_day = oh["weekly_off_day"]
            dept.in_time = oh["in_time"]
            dept.out_time = oh["out_time"]
            dept.save(update_fields=["weekly_off_day", "in_time", "out_time"])
