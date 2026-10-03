CREATE TABLE attendance_records (
  id BIGINT NOT NULL AUTO_INCREMENT,
  employee_id BIGINT NOT NULL,
  attendance_date DATE NOT NULL,
  status VARCHAR(20) NOT NULL,
  in_time TIME NULL,
  out_time TIME NULL,
  remarks VARCHAR(1000) NULL,
  verification_status VARCHAR(20) NOT NULL DEFAULT 'pending',
  rejection_reason VARCHAR(500) NULL,
  verified_by BIGINT NULL,
  verified_at DATETIME NULL,
  created_by BIGINT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_attendance_employee_date (employee_id, attendance_date),
  KEY ix_attendance_records_employee_id (employee_id),
  KEY ix_attendance_records_attendance_date (attendance_date),
  KEY ix_attendance_records_verification_status (verification_status),
  KEY ix_attendance_date_verification (attendance_date, verification_status),
  KEY ix_attendance_records_verified_by (verified_by),
  KEY ix_attendance_records_created_by (created_by),
  CONSTRAINT ck_attendance_status
    CHECK (status IN ('present', 'absent', 'leave', 'half_day')),
  CONSTRAINT ck_attendance_verification_status
    CHECK (verification_status IN ('pending', 'verified', 'rejected')),
  CONSTRAINT ck_attendance_time_order
    CHECK (in_time IS NULL OR out_time IS NULL OR out_time >= in_time),
  CONSTRAINT fk_attendance_records_employee_id
    FOREIGN KEY (employee_id) REFERENCES employees (id)
    ON DELETE RESTRICT,
  CONSTRAINT fk_attendance_records_verified_by
    FOREIGN KEY (verified_by) REFERENCES users (id)
    ON DELETE SET NULL,
  CONSTRAINT fk_attendance_records_created_by
    FOREIGN KEY (created_by) REFERENCES users (id)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
