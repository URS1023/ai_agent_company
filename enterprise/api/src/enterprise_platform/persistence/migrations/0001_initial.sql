-- Enterprise migration 0001: four independent business tables.
-- Dedicated empty enterprise_* database only.

BEGIN;
SET LOCAL search_path TO public;

CREATE TABLE public.enterprise_devices (
	workspace_id VARCHAR(256) NOT NULL, 
	device_id VARCHAR(256) NOT NULL, 
	device_code VARCHAR(256) NOT NULL, 
	revision INTEGER NOT NULL, 
	device_json TEXT NOT NULL, 
	deleted BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (workspace_id, device_id)
);

CREATE TABLE public.enterprise_bindings (
	workspace_id VARCHAR(256) NOT NULL, 
	binding_id VARCHAR(256) NOT NULL, 
	device_id VARCHAR(256) NOT NULL, 
	scenario VARCHAR(64) NOT NULL, 
	revision INTEGER NOT NULL, 
	binding_json TEXT NOT NULL, 
	active_run_id VARCHAR(256), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (workspace_id, binding_id)
);

CREATE TABLE public.enterprise_runs (
	sequence SERIAL NOT NULL, 
	workspace_id VARCHAR(256) NOT NULL, 
	run_id VARCHAR(256) NOT NULL, 
	actor_id VARCHAR(256) NOT NULL, 
	revision INTEGER NOT NULL, 
	binding_id VARCHAR(256) NOT NULL, 
	device_id VARCHAR(256) NOT NULL, 
	scenario VARCHAR(64) NOT NULL, 
	request_key VARCHAR(256) NOT NULL, 
	payload_hash VARCHAR(64) NOT NULL, 
	spec_json TEXT NOT NULL, 
	input_json TEXT, 
	input_digest VARCHAR(64), 
	state VARCHAR(32) NOT NULL, 
	dispatch_nonce VARCHAR(256), 
	dify_run_id VARCHAR(256), 
	reason_code VARCHAR(128), 
	result_json TEXT, 
	result_digest VARCHAR(64), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (sequence)
);

CREATE TABLE public.enterprise_audit_events (
	sequence SERIAL NOT NULL, 
	workspace_id VARCHAR(256) NOT NULL, 
	resource_type VARCHAR(32) NOT NULL, 
	resource_id VARCHAR(256) NOT NULL, 
	run_id VARCHAR(256), 
	actor_id VARCHAR(256) NOT NULL, 
	event_type VARCHAR(64) NOT NULL, 
	detail_json TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (sequence)
);

ALTER TABLE public.enterprise_devices ADD CONSTRAINT ck_device_revision CHECK (revision > 0);

ALTER TABLE public.enterprise_devices ADD CONSTRAINT uq_device_code UNIQUE (workspace_id, device_code);

ALTER TABLE public.enterprise_bindings ADD CONSTRAINT ck_binding_revision CHECK (revision > 0);

ALTER TABLE public.enterprise_bindings ADD CONSTRAINT fk_binding_device_tenant FOREIGN KEY(workspace_id, device_id) REFERENCES public.enterprise_devices (workspace_id, device_id);

ALTER TABLE public.enterprise_bindings ADD CONSTRAINT uq_binding_lane UNIQUE (workspace_id, device_id, scenario);

ALTER TABLE public.enterprise_runs ADD CONSTRAINT ck_input_evidence_pair CHECK ((input_json IS NULL AND input_digest IS NULL) OR (input_json IS NOT NULL AND input_digest IS NOT NULL));

ALTER TABLE public.enterprise_runs ADD CONSTRAINT ck_result_evidence_pair CHECK ((result_json IS NULL AND result_digest IS NULL) OR (result_json IS NOT NULL AND result_digest IS NOT NULL));

ALTER TABLE public.enterprise_runs ADD CONSTRAINT ck_run_revision CHECK (revision > 0);

ALTER TABLE public.enterprise_runs ADD CONSTRAINT ck_run_state CHECK (state IN ('queued', 'claimed', 'dispatched', 'uncertain', 'succeeded', 'failed', 'cancelled'));

ALTER TABLE public.enterprise_runs ADD CONSTRAINT fk_run_binding_tenant FOREIGN KEY(workspace_id, binding_id) REFERENCES public.enterprise_bindings (workspace_id, binding_id);

ALTER TABLE public.enterprise_runs ADD CONSTRAINT uq_run_dispatch_nonce UNIQUE (workspace_id, dispatch_nonce);

ALTER TABLE public.enterprise_runs ADD CONSTRAINT uq_run_request UNIQUE (workspace_id, request_key);

ALTER TABLE public.enterprise_runs ADD CONSTRAINT uq_run_tenant_identity UNIQUE (workspace_id, run_id);

ALTER TABLE public.enterprise_audit_events ADD CONSTRAINT fk_audit_run_tenant FOREIGN KEY(workspace_id, run_id) REFERENCES public.enterprise_runs (workspace_id, run_id);

CREATE INDEX ix_run_fifo ON public.enterprise_runs (workspace_id, binding_id, state, sequence);

CREATE INDEX ix_audit_run_sequence ON public.enterprise_audit_events (workspace_id, run_id, sequence);

COMMIT;
