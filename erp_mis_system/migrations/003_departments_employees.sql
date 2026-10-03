ALTER TABLE departments
  ADD COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  ON UPDATE CURRENT_TIMESTAMP;

CREATE TABLE employees (
  id BIGINT NOT NULL AUTO_INCREMENT,
  employee_code VARCHAR(40) NOT NULL,
  full_name VARCHAR(160) NOT NULL,
  email VARCHAR(254) NOT NULL,
  phone VARCHAR(30) NULL,
  designation VARCHAR(120) NOT NULL,
  department_id BIGINT NOT NULL,
  joining_date DATE NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'active',
  created_by BIGINT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  deleted_at DATETIME NULL,
  deleted_by BIGINT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY ix_employees_employee_code (employee_code),
  KEY ix_employees_department_id (department_id),
  KEY ix_employees_status (status),
  KEY ix_employees_created_by (created_by),
  KEY ix_employees_deleted_at (deleted_at),
  CONSTRAINT ck_employees_status CHECK (status IN ('active', 'inactive')),
  CONSTRAINT fk_employees_department_id
    FOREIGN KEY (department_id) REFERENCES departments (id)
    ON DELETE RESTRICT,
  CONSTRAINT fk_employees_created_by
    FOREIGN KEY (created_by) REFERENCES users (id)
    ON DELETE SET NULL,
  CONSTRAINT fk_employees_deleted_by
    FOREIGN KEY (deleted_by) REFERENCES users (id)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
