-- Enterprise migration 0010: version-pinned SQL draft snapshots for review.
-- Apply only after verified 0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008 and 0009 in the dedicated enterprise_* database.

BEGIN;
SET LOCAL search_path TO public;

CREATE TABLE public.enterprise_dashboard_sql_drafts (
	workspace_id VARCHAR(256) NOT NULL, 
	draft_id VARCHAR(256) NOT NULL, 
	dashboard_id VARCHAR(256) NOT NULL, 
	dashboard_revision INTEGER NOT NULL, 
	actor_id VARCHAR(256) NOT NULL, 
	document_json TEXT NOT NULL, 
	document_hash VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (workspace_id, draft_id)
);

ALTER TABLE public.enterprise_dashboard_sql_drafts ADD CONSTRAINT ck_sql_draft_dashboard_revision CHECK (dashboard_revision > 0);

CREATE INDEX ix_sql_draft_dashboard ON public.enterprise_dashboard_sql_drafts (workspace_id, dashboard_id, created_at, draft_id);

COMMIT;
