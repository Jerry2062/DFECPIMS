-- =============================================================================
-- DFECPIMS — Audit Log INSERT-ONLY Trigger
-- migrations/audit_trigger.sql
--
-- Run this ONCE after the initial table creation (after `alembic upgrade head`
-- or equivalent). It is NOT managed by Alembic because Alembic doesn't track
-- trigger DDL well across databases.
--
-- What this does:
--   Attaches a BEFORE UPDATE and BEFORE DELETE trigger to `audit_log`.
--   If anything — application code, a DBA, a superuser — attempts to UPDATE
--   or DELETE a row, Postgres raises an exception and the operation is aborted.
--
--   This is a hard forensic guarantee: audit records, once written, cannot be
--   altered through any normal database pathway. It doesn't protect against
--   someone with physical access to the data files, but nothing does.
--
-- To verify it's working after installation:
--   UPDATE audit_log SET action = 'TAMPERED' WHERE id = '<any_id>';
--   -- Should raise: ERROR: audit_log is append-only: UPDATE is not permitted
--
-- =============================================================================


-- Step 1: Create the trigger function
-- This function is called by both the UPDATE and DELETE triggers.

CREATE OR REPLACE FUNCTION enforce_audit_log_immutability()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION
            'audit_log is append-only: UPDATE is not permitted. '
            'Attempted to modify row id=%. '
            'This event has been noted.',
            OLD.id;
    ELSIF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'audit_log is append-only: DELETE is not permitted. '
            'Attempted to delete row id=%. '
            'This event has been noted.',
            OLD.id;
    END IF;
    -- RETURN NULL aborts the operation (required for BEFORE triggers on rows)
    RETURN NULL;
END;
$$;


-- Step 2: Attach the UPDATE trigger

DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log;

CREATE TRIGGER audit_log_no_update
    BEFORE UPDATE ON audit_log
    FOR EACH ROW
    EXECUTE FUNCTION enforce_audit_log_immutability();


-- Step 3: Attach the DELETE trigger

DROP TRIGGER IF EXISTS audit_log_no_delete ON audit_log;

CREATE TRIGGER audit_log_no_delete
    BEFORE DELETE ON audit_log
    FOR EACH ROW
    EXECUTE FUNCTION enforce_audit_log_immutability();


-- Step 4: Prevent TRUNCATE as well (covers all remaining bulk-delete paths)
-- Note: TRUNCATE fires statement-level triggers, so FOR EACH STATEMENT is used.

CREATE OR REPLACE FUNCTION enforce_audit_log_no_truncate()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'audit_log is append-only: TRUNCATE is not permitted.';
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log;

CREATE TRIGGER audit_log_no_truncate
    BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT
    EXECUTE FUNCTION enforce_audit_log_no_truncate();


-- =============================================================================
-- Verification queries (run manually to confirm triggers are installed):
--
--   SELECT tgname, tgenabled, tgtype
--   FROM pg_trigger
--   WHERE tgrelid = 'audit_log'::regclass;
--
-- Expected output: 3 rows for audit_log_no_update, audit_log_no_delete,
--                  audit_log_no_truncate — all with tgenabled = 'O' (origin)
-- =============================================================================