-- 006: covering index for the employee list sort (WHERE deleted_at IS NULL
-- ORDER BY employee_code), avoiding a filesort on large employee tables.
ALTER TABLE employees
  ADD KEY ix_employees_not_deleted_code (deleted_at, employee_code);
