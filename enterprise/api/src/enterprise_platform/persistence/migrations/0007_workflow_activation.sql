-- Enterprise migration 0007: revision-fenced workflow activation and device binding receipts.
-- Apply only after verified 0001, 0002, 0003, 0004, 0005 and 0006 in the dedicated enterprise_* database.

BEGIN;
SET LOCAL search_path TO public;

CREATE TABLE public.enterprise_workflow_activations (
	workspace_id VARCHAR(36) NOT NULL, 
	activation_id VARCHAR(36) NOT NULL, 
	enrollment_id VARCHAR(36) NOT NULL, 
	app_id VARCHAR(36) NOT NULL, 
	workflow_id VARCHAR(36) NOT NULL, 
	key_id VARCHAR(67) NOT NULL, 
	binding_id VARCHAR(256) NOT NULL, 
	binding_revision INTEGER NOT NULL, 
	revision INTEGER NOT NULL, 
	active BOOLEAN NOT NULL, 
	public_json TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (workspace_id, activation_id)
);

ALTER TABLE public.enterprise_workflow_activations ADD CONSTRAINT ck_workflow_activation_binding_revision CHECK (binding_revision > 0);

ALTER TABLE public.enterprise_workflow_activations ADD CONSTRAINT ck_workflow_activation_chronology CHECK (updated_at >= created_at);

ALTER TABLE public.enterprise_workflow_activations ADD CONSTRAINT ck_workflow_activation_state CHECK ((active AND revision = 1) OR (NOT active AND revision = 2));

ALTER TABLE public.enterprise_workflow_activations ADD CONSTRAINT uq_workflow_activation_enrollment UNIQUE (workspace_id, enrollment_id);

ALTER TABLE public.enterprise_workflow_activations ADD CONSTRAINT uq_workflow_activation_key_version UNIQUE (key_id, workflow_id);

COMMIT;
