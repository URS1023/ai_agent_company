-- Enterprise migration 0013: isolated chat branch contexts.
-- Apply only after verified 0001-0012 in the dedicated enterprise_* database.

BEGIN;
SET LOCAL search_path TO public;

CREATE TABLE public.enterprise_chat_branches (
	workspace_id VARCHAR(128) NOT NULL, 
	actor_id VARCHAR(128) NOT NULL, 
	installed_app_id VARCHAR(36) NOT NULL, 
	branch_id VARCHAR(128) NOT NULL, 
	revision INTEGER NOT NULL, 
	state VARCHAR(16) NOT NULL, 
	conversation_id VARCHAR(36), 
	head_message_id VARCHAR(36), 
	inflight_client_message_id VARCHAR(36), 
	document_json TEXT NOT NULL, 
	document_hash VARCHAR(64) NOT NULL, 
	PRIMARY KEY (workspace_id, actor_id, installed_app_id, branch_id)
);

ALTER TABLE public.enterprise_chat_branches ADD CONSTRAINT ck_chat_branch_head CHECK (head_message_id IS NULL OR conversation_id IS NOT NULL);

ALTER TABLE public.enterprise_chat_branches ADD CONSTRAINT ck_chat_branch_revision CHECK (revision > 0);

ALTER TABLE public.enterprise_chat_branches ADD CONSTRAINT ck_chat_branch_state CHECK (state IN ('preparing', 'ready', 'archived'));

ALTER TABLE public.enterprise_chat_branches ADD CONSTRAINT uq_chat_branch_conversation UNIQUE (workspace_id, installed_app_id, conversation_id);

COMMIT;
