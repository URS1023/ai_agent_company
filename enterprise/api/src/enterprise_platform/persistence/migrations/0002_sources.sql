-- Enterprise migration 0002: encrypted source heads and immutable read revisions.
-- Apply only after verified 0001 in the dedicated enterprise_* database.

BEGIN;
SET LOCAL search_path TO public;

CREATE TABLE public.enterprise_source_heads (
	workspace_id VARCHAR(256) NOT NULL, 
	source_id VARCHAR(256) NOT NULL, 
	read_id VARCHAR(256) NOT NULL, 
	revision INTEGER NOT NULL, 
	request_key VARCHAR(128) NOT NULL, 
	PRIMARY KEY (workspace_id, source_id)
);

CREATE TABLE public.enterprise_source_versions (
	workspace_id VARCHAR(256) NOT NULL, 
	source_id VARCHAR(256) NOT NULL, 
	revision INTEGER NOT NULL, 
	source_revision VARCHAR(256) NOT NULL, 
	read_id VARCHAR(256) NOT NULL, 
	read_revision VARCHAR(256) NOT NULL, 
	public_json TEXT NOT NULL, 
	encryption_key_id VARCHAR(64) NOT NULL, 
	encryption_nonce VARCHAR(32) NOT NULL, 
	ciphertext TEXT NOT NULL, 
	request_key VARCHAR(128) NOT NULL, 
	request_hash VARCHAR(64) NOT NULL, 
	fingerprint_key_id VARCHAR(64) NOT NULL, 
	PRIMARY KEY (workspace_id, source_id, revision)
);

ALTER TABLE public.enterprise_source_heads ADD CONSTRAINT ck_source_head_revision CHECK (revision > 0);

ALTER TABLE public.enterprise_source_heads ADD CONSTRAINT uq_source_create_request UNIQUE (workspace_id, request_key);

ALTER TABLE public.enterprise_source_heads ADD CONSTRAINT uq_source_read_identity UNIQUE (workspace_id, read_id);

ALTER TABLE public.enterprise_source_versions ADD CONSTRAINT ck_source_version_revision CHECK (revision > 0);

ALTER TABLE public.enterprise_source_versions ADD CONSTRAINT fk_source_version_tenant FOREIGN KEY(workspace_id, source_id) REFERENCES public.enterprise_source_heads (workspace_id, source_id);

ALTER TABLE public.enterprise_source_versions ADD CONSTRAINT uq_source_read_version UNIQUE (workspace_id, source_id, source_revision, read_id, read_revision);

COMMIT;
