-- Enterprise migration 0008: single-owner schedules and revision-fenced lifecycle.
-- Apply only after verified 0001, 0002, 0003, 0004, 0005, 0006 and 0007 in the dedicated enterprise_* database.

BEGIN;
SET LOCAL search_path TO public;

CREATE TABLE public.enterprise_schedules (
	workspace_id VARCHAR(256) NOT NULL, 
	schedule_id VARCHAR(256) NOT NULL, 
	binding_id VARCHAR(256) NOT NULL, 
	binding_revision INTEGER NOT NULL, 
	device_id VARCHAR(256) NOT NULL, 
	scenario VARCHAR(64) NOT NULL, 
	service_actor_id VARCHAR(256) NOT NULL, 
	revision INTEGER NOT NULL, 
	enabled BOOLEAN NOT NULL, 
	next_due_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	public_json TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (workspace_id, schedule_id)
);

ALTER TABLE public.enterprise_schedules ADD CONSTRAINT ck_schedule_chronology CHECK (updated_at >= created_at);

ALTER TABLE public.enterprise_schedules ADD CONSTRAINT ck_schedule_revisions CHECK (revision > 0 AND binding_revision > 0);

ALTER TABLE public.enterprise_schedules ADD CONSTRAINT ck_schedule_scenario CHECK (scenario IN ('alert', 'quality'));

ALTER TABLE public.enterprise_schedules ADD CONSTRAINT uq_schedule_binding_owner UNIQUE (workspace_id, binding_id);

ALTER TABLE public.enterprise_schedules ADD CONSTRAINT uq_schedule_lane_owner UNIQUE (workspace_id, device_id, scenario);

CREATE INDEX ix_schedule_due ON public.enterprise_schedules (enabled, next_due_at);

COMMIT;
