from app import create_app
from app.backup_scheduler import run_scheduled_backup_once

app = create_app()
with app.app_context():
    result = run_scheduled_backup_once()
    print(result)
