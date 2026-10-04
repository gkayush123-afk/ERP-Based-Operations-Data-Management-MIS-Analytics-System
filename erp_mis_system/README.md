# ERP Data Management & MIS Reporting System

## Project description

A role-based ERP data-management and management-information system for centralizing employee and attendance records, enforcing approval/verification workflows, surfacing data-quality issues, and producing department-scoped operational reports with auditable exports.

## Requirements

- Python 3.13
- MySQL 8.0.16+
- Windows Server with Waitress, or Linux with Gunicorn and Nginx
- HTTPS reverse proxy/TLS certificate for production

Python application/server/test dependencies are pinned in `requirements.txt`.

## Quick setup

1. Clone/copy the release to the deployment host. Do not commit or distribute `.env`.
2. Create a Python 3.13 virtual environment and install dependencies:

   ```powershell
   py -3.13 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```

   Linux: `python3.13 -m venv .venv && . .venv/bin/activate && python -m pip install -r requirements.txt`
3. Copy `.env.example` to `.env`, replace all placeholders, generate a random secret key, and securely configure MySQL credentials.
4. Create the database and restricted runtime account as described in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
5. On a new database, run `python seed.py` once to create the initial schema, General department, and administrator. On an existing database, back up first and apply only the required numbered migrations exactly once in order.
6. Optional demo setup: keep `DATABASE_URL` pointed at the same database used by the app, set private `DEMO_MANAGER_PASSWORD` and `DEMO_STAFF_PASSWORD` values, then run `python seed_demo.py`. It adds one Operations department, five employees, previous-month attendance, a pending Manager review example, and active Manager/Staff demo accounts. It is safe to rerun and preserves existing records.
7. Start production using [Waitress on Windows](docs/DEPLOYMENT.md#4-windows-with-waitress) or [Gunicorn and Nginx on Linux](docs/DEPLOYMENT.md#5-linux-with-gunicorn-and-nginx). Do not use Flask's development server in production.
8. Run tests before delivery: `python -m pytest`.

## Initial administrator login

There is **no shared default password**. `seed.py` creates the first active administrator using `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ADMIN_EMAIL`, and `ADMIN_FULL_NAME` from the private `.env`. The example username is `admin`, but the operator must replace the sample password with a strong unique value before running the seed. The seed script will not overwrite an existing administrator. Sign in using the exact configured username and password; then change the password through the account menu.

## Demo role accounts

Set `DEMO_MANAGER_PASSWORD` and `DEMO_STAFF_PASSWORD` privately in the deployment environment, then run `python seed_demo.py` using the same `DATABASE_URL` as the running app. The seeder creates `demo_manager` (Manager) and `demo_staff` (Data-entry Staff) in the Operations department, with passwords stored as hashes. Usernames, email addresses, and passwords can be customized with the corresponding `DEMO_MANAGER_*` and `DEMO_STAFF_*` variables. Passwords must be at least 12 characters and include at least three character groups. The script never prints passwords.

Demo Manager can review the sample pending attendance record and see Operations reports. Demo Staff can work with Operations data but cannot verify attendance. Publicly registered users remain pending Data-entry accounts until an Admin approves them; the demo seeder does not bypass that approval workflow. Do not publish demo passwords in source control or public documentation; share them only with intended demo users. Configure demo accounts only on a disposable demo deployment.

## Environment variables

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Random, private Flask signing/CSRF secret; required |
| `DATABASE_URL` | SQLAlchemy MySQL/PyMySQL URL; required |
| `ADMIN_USERNAME` | Initial administrator username used by `seed.py` |
| `ADMIN_PASSWORD` | Initial administrator password; at least 12 characters and 3 character classes |
| `ADMIN_EMAIL` | Initial administrator email |
| `ADMIN_FULL_NAME` | Initial administrator display name |
| `DEMO_MANAGER_PASSWORD` | Optional private password for the active demo Manager account |
| `DEMO_STAFF_PASSWORD` | Optional private password for the active demo Data-entry account |
| `APP_ENV` | `production` by default; `development` opts into development config |
| `SESSION_COOKIE_SECURE` | `true` for HTTPS; production config always enforces secure cookies |
| `ALLOW_SQLITE_FALLBACK` | Set `false` in production so database configuration failures are not hidden by a local SQLite database |
| `TRUST_PROXY` | Enable `ProxyFix` only behind a trusted reverse proxy |
| `PROXY_FIX_HOPS` | Number of trusted reverse-proxy hops; match actual topology |
| `MYSQL_DATABASE` | Optional database name for `scripts/backup_mysql.sh` |
| `MYSQL_CNF` | Optional path to protected MySQL backup client credentials |
| `BACKUP_DIR` | Optional destination for SQL backup archives |
| `RETENTION_DAYS` | Optional backup retention age; default is 14 days |

Use URL-encoded credentials in `DATABASE_URL` if the password contains reserved URL characters. Never place credentials in source, shell history, issue trackers, or log output. See [.env.example](.env.example).

## Folder structure

```text
app/
  auth/              login, registration, password flows
  departments/       department management
  employees/         employee management and Excel import
  attendance/        attendance entry, verification, import
  data_quality/      validation report and service
  reports/           MIS report queries, charts, Excel/PDF export
  users/             admin user management and audit log
  main/              landing page and dashboard
  templates/          shared and module-specific Jinja templates
  static/             CSS and JavaScript
  utils/              RBAC, audit, validation helpers
migrations/           ordered MySQL migrations for existing databases
scripts/              operations scripts, including MySQL backup
deploy/nginx/         example reverse-proxy configuration
docs/                 deployment guide and role-specific user manual
tests/                pytest coverage
seed.py                initial schema and administrator
seed_demo.py           optional demo accounts and demonstration records
wsgi.py                production WSGI application entry point
run.py                 local/Waitress-compatible app entry point
```

## Troubleshooting

- **Missing environment setting:** verify `.env` exists in the project root and includes `SECRET_KEY` and `DATABASE_URL`.
- **Cannot connect to MySQL:** check service status, database host/firewall, database name, URL-encoded password, and grants for the app server host.
- **Access denied after deployment:** confirm required migrations ran, the runtime user has the required DML grants, and database charset/schema are correct.
- **Login says pending/deactivated:** an administrator must approve the registration or reactivate the account.
- **Secure cookie does not persist on local HTTP:** production config requires HTTPS. For local development, set `APP_ENV=development`; do not use development config in production.
- **Reports/dashboard are empty:** validate the date range, role department scope, attendance records, and employee active/deleted status.
- **Excel/PDF fails:** reinstall pinned dependencies with `python -m pip install -r requirements.txt`; check disk space and writable temporary/log paths.
- **MySQL restore:** restore to an isolated database and validate before cutover; follow the backup/restore procedure in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#6-mysql-daily-backup-and-restore).
- **Application error:** inspect protected `instance/logs/erp_mis.log`; include timestamp and route in support requests, never secrets.

## Operational documentation

- [Deployment, backup/restore, and release checklist](docs/DEPLOYMENT.md)
- [User Manual: Admin, Manager, and Data-entry](docs/USER_MANUAL.md)
- Database backup and retention scripts: [Linux](scripts/backup_mysql.sh) / [Windows](scripts/backup_mysql.ps1)
- Sample HTTPS Nginx proxy: [deploy/nginx/erp_mis_system.conf](deploy/nginx/erp_mis_system.conf)

## Resume-ready project highlights

- Built a Flask ERP/MIS platform using SQLAlchemy and MySQL with a modular application-factory and blueprint architecture.
- Implemented secure authentication, account approval, password-lockout controls, session timeout, CSRF protection, and role-based access for Admin, Manager, and Data-entry users.
- Delivered department-scoped employee and attendance management, Excel bulk imports, pending/verified/rejected attendance workflows, and auditable administrative operations.
- Created reusable data-quality checks for missing fields, duplicates, attendance gaps, and invalid values, with actionable repair links.
- Developed SQL-aggregated daily, weekly, monthly, and department-wise reports with Chart.js visualizations and formatted Excel/PDF exports.
- Prepared operational deployment assets for Windows Waitress and Linux Gunicorn/Nginx, including pinned dependencies, MySQL backup/retention automation, migration guidance, and a role-based user manual.
