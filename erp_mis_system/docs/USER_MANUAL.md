# ERP MIS User Manual

## Sign in and common navigation

1. Open the company-provided HTTPS address and enter the username/password issued to you.
2. Registration requests remain pending until an administrator approves them. Contact the administrator if you cannot sign in or your account is deactivated.
3. Use the top navigation to open Dashboard, Records, Departments, Data Quality (authorized roles), Reports (authorized roles), or account settings.
4. Sign out using **Log out**. The system also ends an inactive session after 30 minutes.

Attendance status values are Present, Absent, Leave, and Half-day. Verification values are Pending, Verified, and Rejected. Filters limit visible rows; always confirm the selected dates, department, and status before changing or exporting data.

## Admin

### Review registrations

1. Open **Dashboard** or **Administration → User management**.
2. Review the pending registration list and confirm the person's name, email, and department.
3. Select **Approve** to activate the account or **Reject** to leave it unable to sign in.

### Manage users

1. Open **Administration → User management**.
2. Search by name, username, or email; filter by role/status and use pagination as needed.
3. Create a user by entering identity, role, department, and a strong temporary password. Deliver that password through an approved secure channel; the user must change it at sign-in.
4. Edit a user's role/department or activate/deactivate the account. Accounts are disabled rather than hard-deleted.
5. Use the password-reset action only after confirming the user's identity. Share the temporary password securely.
6. An administrator cannot deactivate or demote their own account. Maintain a second approved administrator for continuity.

### Departments, employees, and attendance

1. Manage departments from **Departments**. Do not delete a department while employees or users are assigned to it.
2. Add or edit employees using **Employees**. Excel import requires the worksheet headers shown on the import page; examine every row's result before relying on imported data.
3. Enter attendance from **Records → Daily attendance entry** or use the attendance workbook import.
4. Review pending entries from **Attendance verification**. Verify valid rows, or reject with a clear correction reason. Bulk verify only after reviewing all selected rows.
5. Employee deletion is a soft-delete; attendance history is retained.

### Data quality, reports, and audit

1. Open **Data quality** to review missing mandatory fields, duplicate values, attendance gaps, and invalid dates/times. Follow a Fix/Edit link and re-run the report.
2. Open **Reports & exports** and select Daily, Weekly, Monthly, or Department-wise summary.
3. Set dates, department, and status, then review both the chart and table before exporting Excel or PDF.
4. Open **Administration → Audit logs** to filter by user, action, and inclusive date range, inspect recorded IP/time/details, and export the filtered audit history to Excel.
5. Audit logs can include personal information; restrict downloads to approved administrators and follow company retention policy.

## Manager

1. Sign in and use the Dashboard to review your assigned department's employee count, attendance today, pending verifications, and data issues.
2. Add or edit employees and attendance only within your assigned department.
3. Open **Attendance verification** to review pending submissions; verify correct records or reject them with a specific reason.
4. Open **Data quality** to find missing/duplicate/invalid records within your assigned department. Use each Fix/Edit link to correct the source row.
5. Open **Reports & exports** for daily, weekly, monthly, or department-wise reports. Report routes enforce your assigned department even if a URL contains another department ID.
6. Export Excel/PDF only for approved operational purposes. Export actions are audited.
7. Managers cannot approve users, manage roles, delete employees, or inspect system-wide audit logs.

## Data-entry staff

1. Sign in after an administrator approves your account. If assigned a temporary password, change it immediately.
2. Use **Employees** to view employees in your department. Create employees or edit records you are authorized to maintain.
3. Use **Daily attendance entry** to select the employee, date, and status; provide times/remarks where relevant; save the entry for review.
4. Open **Attendance** to see department records and check whether your submission is Pending, Verified, or Rejected.
5. Edit attendance only while it is Pending. If rejected, use the reason to correct the entry while it remains editable or ask a manager for help.
6. For spreadsheet imports, match the required headers exactly and review the row-by-row success/error report.
7. Data-entry accounts cannot verify attendance, delete records, access Data Quality, export reports, manage users, or view audit logs.

## Troubleshooting

- **Pending approval:** ask an administrator to approve the account.
- **No department data / access denied:** ask the administrator to assign your account to the correct active department.
- **Temporary lock:** wait 15 minutes after the fifth consecutive incorrect password attempt, then try again.
- **Import row failed:** follow the reported row number and reason; fix the source workbook and retry only the failed rows.
- **Attendance already exists:** the database permits one attendance record per employee per date. Edit the existing pending record instead.
- **Report is empty:** check the selected dates, department, status, and whether records have been entered.
- **Session expired:** sign in again; inactivity timeout is 30 minutes.
- **Unexpected server error:** record the time and page, then contact support. Do not email passwords or database credentials.
