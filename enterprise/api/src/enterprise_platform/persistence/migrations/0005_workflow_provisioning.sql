-- Enterprise migration 0005: durable workspace-scoped provisioning phase journals.
-- Apply only after verified 0001, 0002, 0003 and 0004 in the dedicated enterprise_* database.

BEGIN;
SET LOCAL search_path TO public;

CREATE TABLE public.enterprise_workflow_provisioning (
	workspace_id VARCHAR(36) NOT NULL, 
	provisioning_id VARCHAR(36) NOT NULL, 
	app_id VARCHAR(36) NOT NULL, 
	setup_id VARCHAR(128) NOT NULL, 
	setup_revision INTEGER NOT NULL, 
	actor_id VARCHAR(128) NOT NULL, 
	device_id VARCHAR(128) NOT NULL, 
	scenario VARCHAR(64) NOT NULL, 
	source_id VARCHAR(128) NOT NULL, 
	source_revision VARCHAR(128) NOT NULL, 
	read_id VARCHAR(128) NOT NULL, 
	read_revision VARCHAR(128) NOT NULL, 
	expected_source_revision INTEGER NOT NULL, 
	expected_binding_revision INTEGER, 
	config_ref VARCHAR(128) NOT NULL, 
	config_revision INTEGER NOT NULL, 
	revision INTEGER NOT NULL, 
	state VARCHAR(32) NOT NULL, 
	active_phase VARCHAR(32), 
	phase_state VARCHAR(16), 
	phase_count INTEGER NOT NULL, 
	request_key VARCHAR(128) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	claim_nonce VARCHAR(36), 
	public_json TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (workspace_id, provisioning_id)
);

ALTER TABLE public.enterprise_workflow_provisioning ADD CONSTRAINT ck_workflow_provisioning_binding_revision CHECK (expected_binding_revision IS NULL OR expected_binding_revision > 0);

ALTER TABLE public.enterprise_workflow_provisioning ADD CONSTRAINT ck_workflow_provisioning_chronology CHECK (updated_at >= created_at);

ALTER TABLE public.enterprise_workflow_provisioning ADD CONSTRAINT ck_workflow_provisioning_nonce CHECK ((phase_state IS NOT NULL AND phase_state = 'claimed' AND claim_nonce IS NOT NULL) OR ((phase_state IS NULL OR phase_state <> 'claimed') AND claim_nonce IS NULL));

ALTER TABLE public.enterprise_workflow_provisioning ADD CONSTRAINT ck_workflow_provisioning_phase CHECK (active_phase IS NULL OR active_phase IN ('read_draft', 'prepare_credential', 'bind_credential', 'publish'));

ALTER TABLE public.enterprise_workflow_provisioning ADD CONSTRAINT ck_workflow_provisioning_phase_count CHECK (phase_count BETWEEN 0 AND 4);

ALTER TABLE public.enterprise_workflow_provisioning ADD CONSTRAINT ck_workflow_provisioning_phase_presence CHECK ((phase_count = 0 AND active_phase IS NULL AND phase_state IS NULL) OR (phase_count > 0 AND active_phase IS NOT NULL AND phase_state IS NOT NULL));

ALTER TABLE public.enterprise_workflow_provisioning ADD CONSTRAINT ck_workflow_provisioning_phase_state CHECK (phase_state IS NULL OR phase_state IN ('queued', 'claimed', 'succeeded', 'rejected', 'uncertain'));

ALTER TABLE public.enterprise_workflow_provisioning ADD CONSTRAINT ck_workflow_provisioning_revisions CHECK (revision > 0 AND setup_revision > 0 AND config_revision > 0 AND expected_source_revision > 0);

ALTER TABLE public.enterprise_workflow_provisioning ADD CONSTRAINT ck_workflow_provisioning_state CHECK (state IN ('in_progress', 'rejected', 'uncertain', 'published_pending_enrollment'));

ALTER TABLE public.enterprise_workflow_provisioning ADD CONSTRAINT uq_workflow_provisioning_request UNIQUE (workspace_id, request_key);

CREATE INDEX ix_workflow_provisioning_setup ON public.enterprise_workflow_provisioning (workspace_id, setup_id, created_at, provisioning_id);

COMMIT;
