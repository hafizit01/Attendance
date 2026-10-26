"""
ZKTeco push agent — লাইভ সার্ভার যে মেশিনে সরাসরি কানেক্ট করতে পারে না (হোস্টিং ফায়ারওয়াল),
সেই মেশিন থেকে এই কম্পিউটার ডাটা পড়ে লাইভ সার্ভারে HTTPS দিয়ে পাঠায়।

যেকোনো কম্পিউটারে চলে যেখান থেকে মেশিনে কানেক্ট হয় (লোকাল সার্ভার বা ব্রাঞ্চের PC)। লাগে শুধু pyzk:
    pip install pyzk

ব্যবহার:
    python push_agent.py                # push_agent.ini এর সব মেশিন
    python push_agent.py --days 60      # প্রথমবার: আগের ৬০ দিনের ডাটা পাঠাও
সেটআপ: push_agent.example.ini কপি করে push_agent.ini বানিয়ে IP/token বসান।
"""
import argparse
import configparser
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta

from zk import ZK

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "push_agent.ini")
STATE = os.path.join(HERE, "push_agent_state.json")   # প্রতি মেশিনের শেষ সফল পাঠানোর তারিখ
OVERLAP_DAYS = 1      # শেষ দিনটা আবার পাঠাই, যাতে পরে আসা Out পাঞ্চ বাদ না পড়ে
FIRST_RUN_DAYS = 7
CHUNK_PUNCHES = 2000  # এক রিকোয়েস্টে মোটামুটি এতগুলো পাঞ্চ — তবে একটা দিন কখনো দুই ভাগ হয় না


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)


def read_device(ip, port):
    conn = ZK(ip, port=port, timeout=15, force_udp=False, ommit_ping=True).connect()
    try:
        conn.disable_device()
        return conn.get_users() or [], conn.get_attendance() or []
    finally:
        try:
            conn.enable_device()
            conn.disconnect()
        except Exception:
            pass


def day_chunks(punches):
    """পাঞ্চগুলো পুরো দিন ধরে ভাগ করে (সার্ভার দিনের প্রথম/শেষ পাঞ্চ দেখে In/Out ঠিক করে)"""
    by_day = defaultdict(list)
    for p in punches:
        by_day[p["time"][:10]].append(p)
    chunk = []
    for day in sorted(by_day):
        if chunk and len(chunk) + len(by_day[day]) > CHUNK_PUNCHES:
            yield chunk
            chunk = []
        chunk.extend(by_day[day])
    if chunk:
        yield chunk


def post(url, token, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "X-Device-Token": token})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def push_device(name, cfg, url, since):
    users, atts = read_device(cfg["ip"], cfg.getint("port", 4370))
    user_rows = [{"uid": u.user_id, "name": u.name} for u in users]
    punches = [{"uid": a.user_id, "time": a.timestamp.strftime("%Y-%m-%d %H:%M:%S")}
               for a in atts if a.timestamp and a.timestamp.date() >= since]
    log(f"{name}: read {len(users)} users, {len(punches)} punches since {since}")

    totals = defaultdict(int)
    chunks = list(day_chunks(punches)) or [[]]
    for i, chunk in enumerate(chunks):
        # কর্মচারীর লিস্ট শুধু প্রথম রিকোয়েস্টে, যাতে পাঞ্চের আগেই নতুনরা যোগ হয়
        res = post(url, cfg["token"], {"users": user_rows if i == 0 else [], "punches": chunk})
        if res.get("status") != "success":
            raise RuntimeError(res.get("message") or res)
        for k in ("employees_added", "employees_not_added", "created", "updated"):
            totals[k] += res.get(k, 0)
    msg = f"{name}: {totals['created']} new, {totals['updated']} updated"
    if totals["employees_added"]:
        msg += f", {totals['employees_added']} employee(s) added"
    if totals["employees_not_added"]:
        msg += f", {totals['employees_not_added']} not added (employee limit)"
    log(msg)


def main():
    ap = argparse.ArgumentParser(description="Push ZKTeco attendance to the live server")
    ap.add_argument("--days", type=int, help="এত দিন আগের ডাটা থেকে পাঠাও (শেষ পাঠানোর তারিখ উপেক্ষা করে)")
    args = ap.parse_args()

    config = configparser.ConfigParser()
    if not config.read(CONFIG, encoding="utf-8"):
        sys.exit(f"Config not found: {CONFIG} (copy push_agent.example.ini)")
    url = config.get("server", "url", fallback="").strip()
    devices = [s for s in config.sections() if s != "server"]
    if not url or not devices:
        sys.exit("push_agent.ini needs a [server] url and at least one device section")

    state, failed, today = load_state(), 0, date.today()
    for name in devices:
        if args.days is not None:
            since = today - timedelta(days=args.days)
        elif name in state:
            since = date.fromisoformat(state[name]) - timedelta(days=OVERLAP_DAYS)
        else:
            since = today - timedelta(days=FIRST_RUN_DAYS)
        try:
            push_device(name, config[name], url, since)
            state[name] = today.isoformat()
            save_state(state)
        except urllib.error.HTTPError as e:
            failed += 1
            log(f"{name}: server error {e.code} {e.read().decode('utf-8', 'ignore')[:200]}")
        except Exception as e:
            failed += 1
            log(f"{name}: failed — {e}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
