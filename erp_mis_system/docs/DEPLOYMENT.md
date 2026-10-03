# Deployment Guide

## 1. Prepare the host and release

- Use a supported Windows Server or Linux host and Python 3.13.
- Install MySQL 8.0.16+ on a private network or managed database service.
- Transfer a reviewed release; do not deploy a developer checkout with `.env`, test databases, or unreviewed changes.
- Install pinned dependencies in a virtual environment: `python -m pip install -r requirements.txt`.
- Copy `.env.example` to `.env`, restrict file permissions, and replace every placeholder.
- Generate `SECRET_KEY` with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
- Set a long, unique `ADMIN_PASSWORD` (minimum 12 characters and at least three character classes).
- Set `SESSION_COOKIE_SECURE=true`, `APP_ENV=production`, `DATABASE_URL`, and the admin seed values.
- For Nginx reverse proxy, configure `TRUST_PROXY=true` and `PROXY_FIX_HOPS=1`; the included Nginx config overwrites forwarded client/protocol/host headers. Do not enable ProxyFix if the app is directly internet-accessible.

## 2. Create a restricted application database identity

Run database/schema setup and migrations with a DBA or temporary migration identity. The normal web-app account should not have schema alteration or database creation privileges. Example MySQL 8 commands:

```sql
CREATE DATABASE erp_mis_system
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'erp_mis_user'@'10.20.30.15'
  IDENTIFIED BY 'REPLACE_WITH_A_UNIQUE_LONG_RANDOM_PASSWORD';
GRANT SELECT, INSERT, UPDATE, DELETE
  ON erp_mis_system.* TO 'erp_mis_user'@'10.20.30.15';
```

Replace `10.20.30.15` with the app server's fixed private address; avoid `'%'` if possible. Store the password only in the protected app `.env` or secret manager, URL-encoding special characters in `DATABASE_URL`. For a local single-host install, bind MySQL to loopback and use host `127.0.0.1`.

Create a separate backup identity on the database/backup host. It needs read-only dump privileges rather than application write privileges:

```sql
CREATE USER 'erp_backup'@'10.20.30.25'
  IDENTIFIED BY 'REPLACE_WITH_A_DIFFERENT_BACKUP_SECRET';
GRANT SELECT, SHOW VIEW, TRIGGER, EVENT
  ON erp_mis_system.* TO 'erp_backup'@'10.20.30.25';
```

Replace `10.20.30.25` with the fixed backup host address. Keep the backup client option file root-readable only.

For initial schema creation, use a temporary setup identity with the DDL privileges needed by `python seed.py` (`CREATE`, and the grants needed for configured indexes/foreign keys). Create tables and the admin, then revoke that setup identity or switch the application to the restricted runtime identity. Apply numbered SQL migrations using the DBA identity before release; do not grant schema modification to the ongoing app user.

## 3. Initialize schema and the first administrator

For a new database, configure the setup `DATABASE_URL` temporarily and run:

```text
python seed.py
python seed_demo.py   # optional; uses the previous complete month
```

For an existing database, take a verified backup and apply the applicable numbered migrations in order (002, 003, 004, 005) exactly once. New databases created from current models already include those columns/indexes; do not replay old migrations against them. After the seed/migration steps, set the restricted runtime account in production `DATABASE_URL`.

The first admin is the account configured in `.env` as `ADMIN_USERNAME` and `ADMIN_PASSWORD`; `seed.py` creates it only if absent and does not print the password or overwrite an existing account. There is no shared default password. Sign in and immediately change the initial admin password if it was delivered to the operator in a less secure channel.

## 4. Windows with Waitress

Open PowerShell in the release directory. Configure `.env` first, install requirements, and ensure Windows Firewall only permits the intended inbound HTTPS traffic to the front-end proxy or managed load balancer. For a direct private-network Waitress service:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
waitress-serve --listen=127.0.0.1:8080 wsgi:app
```

Waitress is a WSGI server, not a TLS endpoint. Put IIS/ARR, a managed HTTPS load balancer, or another maintained TLS reverse proxy in front of it. Bind Waitress to loopback whenever the proxy runs on the same machine. Run it as a dedicated low-privilege Windows service account using an approved service manager; keep `.env`, logs, and backup directories inaccessible to ordinary users.

## 5. Linux with Gunicorn and Nginx

Example install/start command from `/srv/erp_mis_system`:

```bash
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
TRUST_PROXY=true PROXY_FIX_HOPS=1 gunicorn \
  --workers 3 --bind 127.0.0.1:8000 \
  --access-logfile - --error-logfile - wsgi:app
```

Use a systemd service (or approved supervisor) with `User=erp-mis`, `Group=erp-mis`, `WorkingDirectory=/srv/erp_mis_system`, the venv Gunicorn path, and environment loaded from a root/service-readable protected file. The service should restart on failure, have no interactive shell privileges, and bind only to loopback. Do not run Flask's development server in production.

Copy [deploy/nginx/erp_mis_system.conf](../deploy/nginx/erp_mis_system.conf) to `/etc/nginx/sites-available/erp_mis_system`, replace `erp.example.com` and certificate paths, enable the site, validate with `sudo nginx -t`, then reload Nginx. Provision/renew the certificate using the organization's certificate process (for example, Certbot). The sample redirects HTTP to HTTPS, forwards only overwritten trusted headers, serves static files, and limits request bodies to the app's 2 MiB upload limit.

## 6. MySQL daily backup and restore

Install `scripts/backup_mysql.sh` on the DB host or a host with network access to MySQL. Create a MySQL client option file readable only by the backup identity:

```ini
# /etc/erp-mis/mysql-backup.cnf
[client]
user=erp_backup
password=REPLACE_WITH_BACKUP_ACCOUNT_SECRET
host=127.0.0.1
port=3306
```

Protect it, make the script executable, and test the backup:

```bash
sudo chown root:root /etc/erp-mis/mysql-backup.cnf
sudo chmod 600 /etc/erp-mis/mysql-backup.cnf
sudo chmod 750 /srv/erp_mis_system/scripts/backup_mysql.sh
sudo install -d -m 0700 -o root -g root /var/backups/erp_mis_system
sudo MYSQL_DATABASE=erp_mis_system MYSQL_CNF=/etc/erp-mis/mysql-backup.cnf \
  BACKUP_DIR=/var/backups/erp_mis_system RETENTION_DAYS=14 \
  /srv/erp_mis_system/scripts/backup_mysql.sh
```

The script creates a compressed, owner-readable-only, consistent InnoDB dump; checks gzip integrity; and removes matching dumps older than 14 days. Schedule daily at 02:15 UTC using root cron or a systemd timer:

```cron
15 2 * * * MYSQL_DATABASE=erp_mis_system MYSQL_CNF=/etc/erp-mis/mysql-backup.cnf BACKUP_DIR=/var/backups/erp_mis_system RETENTION_DAYS=14 /srv/erp_mis_system/scripts/backup_mysql.sh >> /var/log/erp-mis-backup.log 2>&1
```

Protect backups separately from the app host, encrypt them at rest/in transit using the company's backup platform, monitor job success and available space, and regularly test restoring to an isolated non-production database. Backup retention must match company/legal policy; adjust the example 14 days as approved. The example covers the MySQL database, not `.env`, uploads, logs, or TLS private keys; protect and back up those separately according to policy.

For Windows Waitress deployments, use [scripts/backup_mysql.ps1](../scripts/backup_mysql.ps1). Create `C:\ProgramData\erp-mis\mysql-backup.cnf` with the same `[client]` values as above, then restrict its ACL to Administrators, SYSTEM, and the dedicated scheduled-task identity. Test it from an elevated PowerShell prompt:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\path\to\erp_mis_system\scripts\backup_mysql.ps1" `
  -Database erp_mis_system `
  -BackupDirectory "D:\ERPBackups" `
  -RetentionDays 14 `
  -DefaultsFile "C:\ProgramData\erp-mis\mysql-backup.cnf"
```

Use Windows Task Scheduler to run that same command daily (for example, 02:15), whether the dedicated backup identity is logged on or not. Store backups on a protected separate volume/location and monitor task success. The script creates and validates a compressed dump before deleting matching archives beyond the retention period.

To restore a specific dump, first stop application writes or arrange maintenance, verify the backup, create an empty target database with the expected charset, then restore:

```bash
gzip -t /var/backups/erp_mis_system/erp_mis_system_YYYYMMDDTHHMMSSZ.sql.gz
mysql --defaults-extra-file=/etc/erp-mis/mysql-backup.cnf \
  -e "CREATE DATABASE erp_mis_restore CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
gzip -dc /var/backups/erp_mis_system/erp_mis_system_YYYYMMDDTHHMMSSZ.sql.gz \
  | mysql --defaults-extra-file=/etc/erp-mis/mysql-backup.cnf erp_mis_restore
```

Validate counts and application behavior on the isolated database before any production cutover. For an in-place disaster restore, use the approved incident/change process, stop all writers, restore only after confirming the target database, validate it, update connection settings if necessary, and then restart services. Never pipe an unverified dump directly into the live database.

On Windows, first decompress the selected `.sql.gz` file to a protected temporary `.sql` file with a trusted gzip utility, create an empty restore database using the protected MySQL defaults file, then import the SQL file with `mysql.exe --defaults-extra-file="C:\ProgramData\erp-mis\mysql-backup.cnf" erp_mis_restore` using the SQL file as standard input from `cmd.exe`. Confirm the restore database name before import, validate counts and application behavior, and securely remove the temporary plaintext SQL dump after validation.

## 7. Release and deployment checklist

- [ ] Production database and restricted runtime account use unique credentials; no wildcard host grants.
- [ ] `.env` is secret-managed, permission-restricted, excluded from source control, and contains no example values.
- [ ] `ADMIN_PASSWORD` is strong and changed after initial delivery; a second admin recovery path is documented.
- [ ] `APP_ENV=production`, production config, `DEBUG=False`, and secure cookie flags are active.
- [ ] HTTPS certificate is valid and auto-renewal is monitored; HTTP redirects to HTTPS.
- [ ] Firewall exposes only required ports (usually 443; 80 only for redirect/certificate flow); MySQL and WSGI ports are private.
- [ ] Nginx proxy headers are overwritten and `TRUST_PROXY` hop count matches the actual trusted proxy topology.
- [ ] Numbered migrations are backed up, reviewed, applied once, and recorded.
- [ ] Daily DB backup job succeeds, retention is configured, backup storage is protected, and a restore test is current.
- [ ] Application service runs as a non-root/non-admin identity; writable paths are limited to logs/temp as needed.
- [ ] Logs, disk, database availability, backup result, TLS expiry, and service health have alerting/ownership.
- [ ] Test suite passes against the release; smoke-test login, approval, scoped role access, attendance, reports, exports, and audit.
- [ ] Company owner accepts the user manual, support contacts, data retention, privacy, and incident response responsibilities.
