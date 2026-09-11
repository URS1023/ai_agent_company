-- Enterprise migration 0006: durable workspace-scoped execution enrollment checkpoints.
-- Apply only after verified 0001, 0002, 0003, 0004 and 0005 in the dedicated enterprise_* database.

BEGIN;
SET LOCAL search_path TO public;

CREATE TABLE public.enterprise_workflow_enrollments (
	workspace_id VARCHAR(36) NOT NULL, 
	enrollment_id VARCHAR(36) NOT NULL, 
	provisioning_id VARCHAR(36) NOT NULL, 
	app_id VARCHAR(36) NOT NULL, 
	actor_id VARCHAR(128) NOT NULL, 
	revision INTEGER NOT NULL, 
	state VARCHAR(32) NOT NULL, 
	secret_ref VARCHAR(36) NOT NULL, 
	claim_nonce VARCHAR(36), 
	public_json TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (workspace_id, enrollment_id)
);

ALTER TABLE public.enterprise_workflow_enrollments ADD CONSTRAINT ck_workflow_enrollment_chronology CHECK (updated_at >= created_at);

ALTER TABLE public.enterprise_workflow_enrollments ADD CONSTRAINT ck_workflow_enrollment_nonce CHECK ((state IN ('verification_claimed', 'token_claimed') AND claim_nonce IS NOT NULL) OR (state NOT IN ('verification_claimed', 'token_claimed') AND claim_nonce IS NULL));

ALTER TABLE public.enterprise_workflow_enrollments ADD CONSTRAINT ck_workflow_enrollment_revision CHECK (revision > 0);

ALTER TABLE public.enterprise_workflow_enrollments ADD CONSTRAINT ck_workflow_enrollment_state CHECK (state IN ('pending_verification', 'verification_claimed', 'verified', 'token_claimed', 'token_stored', 'rejected', 'uncertain'));

ALTER TABLE public.enterprise_workflow_enrollments ADD CONSTRAINT uq_workflow_enrollment_provisioning UNIQUE (workspace_id, provisioning_id);

ALTER TABLE public.enterprise_workflow_enrollments ADD CONSTRAINT uq_workflow_enrollment_secret UNIQUE (workspace_id, app_id, secret_ref);

COMMIT;
