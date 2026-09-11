-- Enterprise migration 0003: durable draft-only workflow imports.
-- Apply only after verified 0001 and 0002 in the dedicated enterprise_* database.

BEGIN;
SET LOCAL search_path TO public;

CREATE TABLE public.enterprise_workflow_setups (
	workspace_id VARCHAR(128) NOT NULL, 
	setup_id VARCHAR(128) NOT NULL, 
	device_id VARCHAR(128) NOT NULL, 
	scenario VARCHAR(64) NOT NULL, 
	source_id VARCHAR(128) NOT NULL, 
	source_revision VARCHAR(128) NOT NULL, 
	read_id VARCHAR(128) NOT NULL, 
	read_revision VARCHAR(128) NOT NULL, 
	expected_source_revision INTEGER NOT NULL, 
	expected_binding_revision INTEGER, 
	revision INTEGER NOT NULL, 
	state VARCHAR(32) NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	app_id VARCHAR(128), 
	import_id VARCHAR(128), 
	reason_code VARCHAR(128), 
	actor_id VARCHAR(128) NOT NULL, 
	request_key VARCHAR(128) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	import_nonce VARCHAR(128), 
	public_json TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (workspace_id, setup_id)
);

ALTER TABLE public.enterprise_workflow_setups ADD CONSTRAINT ck_workflow_setup_binding_revision CHECK (expected_binding_revision IS NULL OR expected_binding_revision > 0);

ALTER TABLE public.enterprise_workflow_setups ADD CONSTRAINT ck_workflow_setup_confirmation_id CHECK (state <> 'confirmation_required' OR import_id IS NOT NULL);

ALTER TABLE public.enterprise_workflow_setups ADD CONSTRAINT ck_workflow_setup_draft_ids CHECK (state <> 'draft_ready' OR (app_id IS NOT NULL AND import_id IS NOT NULL));

ALTER TABLE public.enterprise_workflow_setups ADD CONSTRAINT ck_workflow_setup_nonce CHECK ((state = 'importing' AND import_nonce IS NOT NULL) OR (state <> 'importing' AND import_nonce IS NULL));

ALTER TABLE public.enterprise_workflow_setups ADD CONSTRAINT ck_workflow_setup_pending_ids CHECK (state NOT IN ('queued', 'importing') OR (app_id IS NULL AND import_id IS NULL));

ALTER TABLE public.enterprise_workflow_setups ADD CONSTRAINT ck_workflow_setup_revision CHECK (revision > 0);

ALTER TABLE public.enterprise_workflow_setups ADD CONSTRAINT ck_workflow_setup_source_revision CHECK (expected_source_revision > 0);

ALTER TABLE public.enterprise_workflow_setups ADD CONSTRAINT ck_workflow_setup_state CHECK (state IN ('queued', 'importing', 'draft_ready', 'confirmation_required', 'uncertain', 'failed'));

ALTER TABLE public.enterprise_workflow_setups ADD CONSTRAINT uq_workflow_setup_request UNIQUE (workspace_id, request_key);

CREATE INDEX ix_workflow_setup_device_scenario ON public.enterprise_workflow_setups (workspace_id, device_id, scenario, created_at, setup_id);

COMMIT;
