-- Enterprise migration 0012: scoped chat send intent ledger.
-- Apply only after verified 0001-0011 in the dedicated enterprise_* database.

BEGIN;
SET LOCAL search_path TO public;

CREATE TABLE public.enterprise_chat_send_intents (
	workspace_id VARCHAR(128) NOT NULL, 
	actor_id VARCHAR(128) NOT NULL, 
	installed_app_id VARCHAR(36) NOT NULL, 
	branch_id VARCHAR(128) NOT NULL, 
	client_message_id VARCHAR(36) NOT NULL, 
	revision INTEGER NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	payload_hash VARCHAR(64) NOT NULL, 
	document_json TEXT NOT NULL, 
	document_hash VARCHAR(64) NOT NULL, 
	PRIMARY KEY (workspace_id, actor_id, installed_app_id, branch_id, client_message_id)
);

ALTER TABLE public.enterprise_chat_send_intents ADD CONSTRAINT ck_chat_send_revision CHECK (revision > 0);

ALTER TABLE public.enterprise_chat_send_intents ADD CONSTRAINT ck_chat_send_status CHECK (status IN ('queued', 'dispatched', 'uncertain', 'accepted'));

COMMIT;
