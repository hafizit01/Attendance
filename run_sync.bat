:: Auto sync attendance every hour
cd /d D:\YourProjectPath
call venv\Scripts\activate
python manage.py shell -c "from attendance_app.utils.zk_import import import_attendance; import_attendance()"
