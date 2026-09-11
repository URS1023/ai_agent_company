-- Enterprise migration 0009: data-only dashboards and revision-fenced refresh persistence.
-- Apply only after verified 0001, 0002, 0003, 0004, 0005, 0006, 0007 and 0008 in the dedicated enterprise_* database.

BEGIN;
SET LOCAL search_path TO public;

CREATE TABLE public.enterprise_dashboards (
	workspace_id VARCHAR(256) NOT NULL, 
	dashboard_id VARCHAR(256) NOT NULL, 
	revision INTEGER NOT NULL, 
	design_identity VARCHAR(64) NOT NULL, 
	bindings_hash VARCHAR(64) NOT NULL, 
	document_json TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (workspace_id, dashboard_id)
);

ALTER TABLE public.enterprise_dashboards ADD CONSTRAINT ck_dashboard_revision CHECK (revision > 0);

COMMIT;
