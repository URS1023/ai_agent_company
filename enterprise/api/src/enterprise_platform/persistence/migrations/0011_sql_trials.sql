-- Enterprise migration 0011: append-only SQL trial captures for review.
-- Apply only after verified 0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009 and 0010 in the dedicated enterprise_* database.

BEGIN;
SET LOCAL search_path TO public;

CREATE TABLE public.enterprise_dashboard_sql_trials (
	workspace_id VARCHAR(256) NOT NULL, 
	trial_id VARCHAR(256) NOT NULL, 
	dashboard_id VARCHAR(256) NOT NULL, 
	draft_id VARCHAR(256) NOT NULL, 
	actor_id VARCHAR(256) NOT NULL, 
	document_json TEXT NOT NULL, 
	document_hash VARCHAR(64) NOT NULL, 
	recorded_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (workspace_id, trial_id)
);

CREATE INDEX ix_sql_trial_draft ON public.enterprise_dashboard_sql_trials (workspace_id, dashboard_id, draft_id, recorded_at, trial_id);

COMMIT;
