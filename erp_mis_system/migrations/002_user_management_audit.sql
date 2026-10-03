ALTER TABLE users
  ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE audit_logs (
  id BIGINT NOT NULL AUTO_INCREMENT,
  actor_user_id BIGINT NULL,
  action VARCHAR(80) NOT NULL,
  entity VARCHAR(80) NOT NULL,
  entity_id BIGINT NULL,
  details JSON NOT NULL,
  occurred_at DATETIME NOT NULL,
  PRIMARY KEY (id),
  KEY ix_audit_logs_actor_user_id (actor_user_id),
  KEY ix_audit_logs_action (action),
  KEY ix_audit_logs_entity_id (entity_id),
  KEY ix_audit_logs_occurred_at (occurred_at),
  CONSTRAINT fk_audit_logs_actor_user_id
    FOREIGN KEY (actor_user_id) REFERENCES users (id)
    ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
