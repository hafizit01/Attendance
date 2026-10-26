from collections import defaultdict
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from zk import ZK

from attendance_app.models import Attendance, Department
from attendance_app.utils.zk_import import _sync_attendance, _sync_users


class Command(BaseCommand):
    help = (
        "সব মেশিন থেকে ডাটা নতুন করে এনে হাজিরা আবার বানায় (--since থেকে)। "
        "আগে দুই ব্রাঞ্চে একই device ID থাকায় যে পাঞ্চ ভুল কর্মচারীর নামে গেছে, সেগুলো ঠিক হয় "
        "এবং বাদ পড়া কর্মচারীরা যোগ হয়। ম্যানুয়ালি এডিট করা সময় (যা কোনো মেশিনে নেই) বদলায় না।"
    )

    def add_arguments(self, parser):
        parser.add_argument("--since", required=True, help="এই তারিখ থেকে (YYYY-MM-DD)")
        parser.add_argument("--company", type=int, help="শুধু এই কোম্পানি id")
        parser.add_argument("--dry-run", action="store_true", help="কিছু না বদলে শুধু হিসাব দেখাও")

    def handle(self, *args, since, company=None, dry_run=False, **options):
        try:
            since = datetime.strptime(since, "%Y-%m-%d").date()
        except ValueError:
            raise CommandError("--since must be YYYY-MM-DD")

        qs = (Department.objects.select_related("company", "branch")
              .exclude(device_ip__isnull=True).exclude(device_port__isnull=True)
              .exclude(company__isnull=True))
        if company:
            qs = qs.filter(company_id=company)

        # একই মেশিন একাধিক ডিপার্টমেন্টে সেট থাকলে একবারই পড়ি
        by_device = {}
        for d in qs.order_by("id"):
            by_device.setdefault((d.company_id, d.device_ip, d.device_port), d)
        if not by_device:
            raise CommandError("No department has a device IP/Port set.")

        # 1) সব মেশিন থেকে পড়া — একটাও ব্যর্থ হলে কিছুই বদলাই না (নাহলে ওই মেশিনের রেকর্ড মুছে আর ফিরবে না)
        fetched = []
        for (_, ip, port), dept in by_device.items():
            label = f"{dept.branch.name if dept.branch_id else '-'} · {dept.name} ({ip}:{port})"
            zk, conn = ZK(ip, port=port, timeout=10, force_udp=False, ommit_ping=True), None
            try:
                conn = zk.connect()
                conn.disable_device()
                users, punches = conn.get_users(), conn.get_attendance() or []
            except Exception as e:
                raise CommandError(f"{label}: could not read device ({e}). Nothing was changed.")
            finally:
                if conn:
                    try:
                        conn.enable_device()
                        conn.disconnect()
                    except Exception:
                        pass
            self.stdout.write(f"Read {label}: {len(users)} users, {len(punches)} punches")
            fetched.append((dept, users, punches))

        # 2) মেশিনের সব পাঞ্চের সময় (কোম্পানি অনুযায়ী) — এই সময়ের DB রেকর্ডগুলো মেশিন থেকে আসা
        machine_times = defaultdict(set)
        for dept, _, punches in fetched:
            for a in punches:
                if a.timestamp is None:
                    continue
                t = a.timestamp.replace(microsecond=0)
                if timezone.is_naive(t):
                    t = timezone.make_aware(t)
                if timezone.localtime(t).date() >= since:
                    machine_times[dept.company_id].add(t)

        start = timezone.make_aware(datetime.combine(since, datetime.min.time()))
        stale = [r.pk for r in Attendance.objects.filter(company_id__in=machine_times.keys(), timestamp__gte=start)
                 .only("pk", "company_id", "timestamp")
                 if r.timestamp in machine_times[r.company_id]]
        self.stdout.write(f"Machine-sourced records to rebuild since {since}: {len(stale)}")
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — nothing changed."))
            return

        # 3) মুছে আবার বানাই — ব্রাঞ্চ ধরে কর্মচারী মিলিয়ে
        with transaction.atomic():
            for i in range(0, len(stale), 500):
                Attendance.objects.filter(pk__in=stale[i:i + 500]).delete()
            for dept, users, punches in fetched:
                added, skipped = _sync_users(users, dept, dept.company)
                created, updated = _sync_attendance(punches, dept, since)
                line = f"{dept.branch.name if dept.branch_id else '-'} · {dept.name}: {created} records"
                if added:
                    line += f", {added} employee(s) added"
                if skipped:
                    line += f", {skipped} not added (employee limit)"
                self.stdout.write(self.style.SUCCESS(line))
