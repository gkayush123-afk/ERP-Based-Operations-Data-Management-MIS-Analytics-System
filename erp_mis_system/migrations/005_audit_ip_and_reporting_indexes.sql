ALTER TABLE audit_logs
  ADD COLUMN ip_address VARCHAR(45) NULL,
  ADD KEY ix_audit_actor_occurred (actor_user_id, occurred_at),
  ADD KEY ix_audit_action_occurred (action, occurred_at);

ALTER TABLE attendance_records
  ADD KEY ix_attendance_date_status (attendance_date, status);

ALTER TABLE employees
  ADD KEY ix_employees_department_active (department_id, status, deleted_at);
