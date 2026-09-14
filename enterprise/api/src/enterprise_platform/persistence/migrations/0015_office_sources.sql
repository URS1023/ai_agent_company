-- Enterprise migration 0015: Office source grants and immutable snapshot bindings.
-- Apply only after verified 0001-0014 in the dedicated enterprise_* database.

BEGIN;
SET LOCAL search_path TO public;

CREATE TABLE public.enterprise_office_sources (
	workspace_id VARCHAR(256) NOT NULL,
	source_id VARCHAR(256) NOT NULL,
	enabled BOOLEAN NOT NULL,
	acl_revision BIGINT NOT NULL,
	PRIMARY KEY (workspace_id, source_id)
);

ALTER TABLE public.enterprise_office_sources ADD CONSTRAINT ck_office_source_acl_revision CHECK (acl_revision > 0);

CREATE TABLE public.enterprise_office_snapshots (
	workspace_id VARCHAR(256) NOT NULL,
	snapshot_id VARCHAR(256) NOT NULL,
	source_id VARCHAR(256) NOT NULL,
	source_revision VARCHAR(256) NOT NULL,
	payload_json TEXT NOT NULL,
	payload_hash VARCHAR(64) NOT NULL,
	PRIMARY KEY (workspace_id, snapshot_id)
);

ALTER TABLE public.enterprise_office_snapshots ADD CONSTRAINT ck_office_snapshot_hash CHECK (length(payload_hash) = 64);

ALTER TABLE public.enterprise_office_snapshots ADD CONSTRAINT fk_office_snapshot_source FOREIGN KEY(workspace_id, source_id) REFERENCES public.enterprise_office_sources (workspace_id, source_id);

CREATE TABLE public.enterprise_office_source_grants (
	workspace_id VARCHAR(256) NOT NULL,
	source_id VARCHAR(256) NOT NULL,
	actor_id VARCHAR(256) NOT NULL,
	can_read BOOLEAN NOT NULL,
	PRIMARY KEY (workspace_id, source_id, actor_id)
);

ALTER TABLE public.enterprise_office_source_grants ADD CONSTRAINT fk_office_source_grant FOREIGN KEY(workspace_id, source_id) REFERENCES public.enterprise_office_sources (workspace_id, source_id);

COMMIT;
