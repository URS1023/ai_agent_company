-- Enterprise migration 0014: Office file permissions, history and edit receipts.
-- Apply only after verified 0001-0013 in the dedicated enterprise_* database.

BEGIN;
SET LOCAL search_path TO public;

CREATE TABLE public.enterprise_office_files (
	workspace_id VARCHAR(256) NOT NULL,
	file_id VARCHAR(36) NOT NULL,
	current_revision BIGINT NOT NULL,
	acl_revision BIGINT NOT NULL,
	created_by VARCHAR(256) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (workspace_id, file_id)
);

ALTER TABLE public.enterprise_office_files ADD CONSTRAINT ck_office_acl_revision CHECK (acl_revision > 0);

ALTER TABLE public.enterprise_office_files ADD CONSTRAINT ck_office_current_revision CHECK (current_revision > 0);

CREATE TABLE public.enterprise_office_grants (
	workspace_id VARCHAR(256) NOT NULL,
	file_id VARCHAR(36) NOT NULL,
	actor_id VARCHAR(256) NOT NULL,
	can_read BOOLEAN NOT NULL,
	can_edit BOOLEAN NOT NULL,
	PRIMARY KEY (workspace_id, file_id, actor_id)
);

ALTER TABLE public.enterprise_office_grants ADD CONSTRAINT ck_office_edit_requires_read CHECK (NOT can_edit OR can_read);

ALTER TABLE public.enterprise_office_grants ADD CONSTRAINT fk_office_grant_file FOREIGN KEY(workspace_id, file_id) REFERENCES public.enterprise_office_files (workspace_id, file_id);

CREATE TABLE public.enterprise_office_revisions (
	workspace_id VARCHAR(256) NOT NULL,
	file_id VARCHAR(36) NOT NULL,
	revision BIGINT NOT NULL,
	document_json TEXT NOT NULL,
	document_hash VARCHAR(64) NOT NULL,
	actor_id VARCHAR(256) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (workspace_id, file_id, revision)
);

ALTER TABLE public.enterprise_office_revisions ADD CONSTRAINT ck_office_document_hash CHECK (length(document_hash) = 64);

ALTER TABLE public.enterprise_office_revisions ADD CONSTRAINT ck_office_history_revision CHECK (revision > 0);

ALTER TABLE public.enterprise_office_revisions ADD CONSTRAINT fk_office_revision_file FOREIGN KEY(workspace_id, file_id) REFERENCES public.enterprise_office_files (workspace_id, file_id);

CREATE TABLE public.enterprise_office_edit_receipts (
	workspace_id VARCHAR(256) NOT NULL,
	file_id VARCHAR(36) NOT NULL,
	actor_id VARCHAR(256) NOT NULL,
	request_id VARCHAR(36) NOT NULL,
	command_hash VARCHAR(64) NOT NULL,
	result_revision BIGINT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (workspace_id, file_id, actor_id, request_id)
);

ALTER TABLE public.enterprise_office_edit_receipts ADD CONSTRAINT ck_office_command_hash CHECK (length(command_hash) = 64);

ALTER TABLE public.enterprise_office_edit_receipts ADD CONSTRAINT ck_office_receipt_revision CHECK (result_revision > 1);

ALTER TABLE public.enterprise_office_edit_receipts ADD CONSTRAINT fk_office_receipt_revision FOREIGN KEY(workspace_id, file_id, result_revision) REFERENCES public.enterprise_office_revisions (workspace_id, file_id, revision);

COMMIT;
