-- Enterprise migration 0004: encrypted workspace/app-scoped workflow credentials.
-- Apply only after verified 0001, 0002 and 0003 in the dedicated enterprise_* database.

BEGIN;
SET LOCAL search_path TO public;

CREATE TABLE public.enterprise_workflow_credentials (
	workspace_id VARCHAR(128) NOT NULL, 
	app_id VARCHAR(128) NOT NULL, 
	secret_ref VARCHAR(128) NOT NULL, 
	revision INTEGER NOT NULL, 
	active BOOLEAN NOT NULL, 
	key_id VARCHAR(64) NOT NULL, 
	nonce VARCHAR(16) NOT NULL, 
	ciphertext TEXT NOT NULL, 
	public_json TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (workspace_id, app_id, secret_ref)
);

ALTER TABLE public.enterprise_workflow_credentials ADD CONSTRAINT ck_workflow_credential_key CHECK (length(key_id) BETWEEN 1 AND 64);

ALTER TABLE public.enterprise_workflow_credentials ADD CONSTRAINT ck_workflow_credential_nonce CHECK (length(nonce) = 16);

ALTER TABLE public.enterprise_workflow_credentials ADD CONSTRAINT ck_workflow_credential_payload CHECK (length(ciphertext) BETWEEN 24 AND 5484);

ALTER TABLE public.enterprise_workflow_credentials ADD CONSTRAINT ck_workflow_credential_revision CHECK (revision > 0);

CREATE INDEX ix_workflow_credential_active ON public.enterprise_workflow_credentials (workspace_id, app_id, active);

COMMIT;
