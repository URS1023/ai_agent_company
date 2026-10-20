"""智能体应用服务：配置管理 + 带知识检索的流式回答。"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_platform.core.datetimes import utc_storage_iso
from agent_platform.core.context import Actor
from agent_platform.core.errors import DomainError

from ..chat.service import ChatService
from ..knowledge.service import KnowledgeService
from ..knowledge.evidence import knowledge_evidence
from .models import AgentApp

DEFAULT_PROMPT = '你是制造业现场助手。回答要具体、可执行；资料未覆盖时明确说明，不要编造参数。'


class KnowledgeAnswerStream:
    """来源事件与原模型流共享同一连接；即使首块前取消也能显式关闭上游。"""

    def __init__(self, opened, metadata: dict):
        self.opened = opened
        self.metadata = metadata
        self.iterator = self._events()

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.iterator)

    def _events(self):
        try:
            yield ('event: knowledge_sources\ndata: ' + json.dumps(self.metadata, ensure_ascii=False) + '\n\n').encode()
            yield from self.opened
        finally:
            self.opened.close()

    def close(self):
        self.opened.close()
        self.iterator.close()


class AgentService:
    def __init__(self, session: Session, actor: Actor) -> None:
        self.session = session
        self.actor = actor

    # ---- 配置 ----
    def list(self) -> dict:
        rows = self.session.scalars(select(AgentApp).where(AgentApp.workspace_id == self.actor.workspace_id)
                                    .order_by(AgentApp.updated_at.desc())).all()
        items = [{'id': r.id, 'name': r.name, 'description': r.description, 'enabled': r.enabled,
                  'revision': r.revision, 'space_count': len(r.space_ids or []),
                  'updated_at': utc_storage_iso(r.updated_at)} for r in rows]
        return {'items': items, 'total': len(items)}

    def get(self, agent_id: str) -> AgentApp:
        row = self.session.get(AgentApp, agent_id)
        if not row or row.workspace_id != self.actor.workspace_id:
            raise DomainError(404, 'agent_not_found', '智能体应用不存在或已删除')
        return row

    def detail(self, agent_id: str) -> dict:
        row = self.get(agent_id)
        return {'id': row.id, 'name': row.name, 'description': row.description, 'prompt': row.prompt,
                'space_ids': row.space_ids or [], 'top_k': row.top_k, 'max_tokens': row.max_tokens,
                'temperature': row.temperature, 'enabled': row.enabled, 'revision': row.revision,
                'updated_at': utc_storage_iso(row.updated_at)}

    def _apply(self, row: AgentApp, name: str, description: str, prompt: str,
               space_ids: list[str], top_k: int, max_tokens: int, temperature: float) -> None:
        name = name.strip()
        if not name:
            raise DomainError(422, 'name_required', '请填写应用名称', {'name': '必填'})
        # 保存时核绑定，运行时仍按当前主体复核；不把创建者权限继承给调用者。
        KnowledgeService(self.session, self.actor).authorize_spaces(space_ids)
        row.name = name
        row.description = description.strip()
        row.prompt = (prompt or DEFAULT_PROMPT).strip()
        row.space_ids = [str(x) for x in (space_ids or [])][:10]
        row.top_k = max(1, min(int(top_k or 3), 10))
        row.max_tokens = max(64, min(int(max_tokens or 1200), 4096))
        row.temperature = max(0.0, min(float(temperature if temperature is not None else 0.3), 2.0))

    def create(self, name: str, description: str, prompt: str, space_ids: list[str],
               top_k: int, max_tokens: int, temperature: float) -> dict:
        row = AgentApp(workspace_id=self.actor.workspace_id, created_by=self.actor.user_id)
        self._apply(row, name, description, prompt, space_ids, top_k, max_tokens, temperature)
        if not row.prompt:
            row.prompt = DEFAULT_PROMPT
        self.session.add(row)
        self.session.flush()
        return self.detail(row.id)

    def update(self, agent_id: str, name: str, description: str, prompt: str, space_ids: list[str],
               top_k: int, max_tokens: int, temperature: float) -> dict:
        row = self.get(agent_id)
        self._apply(row, name, description, prompt, space_ids, top_k, max_tokens, temperature)
        row.revision += 1
        self.session.flush()
        return self.detail(agent_id)

    def delete(self, agent_id: str) -> dict:
        row = self.get(agent_id)
        self.session.delete(row)
        return {'deleted': True, 'id': agent_id}

    # ---- 回答 ----
    def _context(self, agent: AgentApp, question: str) -> tuple[str, dict]:
        """从绑定的知识空间检索资料，拼成提示词附录；未绑定则跳过。"""
        if not agent.space_ids:
            return '', {'items': [], 'knowledge_sources': knowledge_evidence(self.actor, [])}
        found = KnowledgeService(self.session, self.actor).search(agent.space_ids, question, agent.top_k)
        if not found['items']:
            return '', found
        blocks = [f"[{item['document_name']} 第{item['ordinal'] + 1}段] {item['content']}"
                  for item in found['items']]
        return '\n\n参考资料：\n' + '\n\n'.join(blocks), found

    def stream(self, agent_id: str, question: str):
        """组合 system 提示词与检索资料后流式回答。"""
        agent = self.get(agent_id)
        if not agent.enabled:
            raise DomainError(409, 'agent_disabled', '该应用已停用')
        question = question.strip()
        if not question:
            raise DomainError(422, 'question_required', '请输入问题', {'question': '必填'})
        context, found = self._context(agent, question)
        system = (agent.prompt or DEFAULT_PROMPT) + context
        opened = ChatService().open_stream(
            [{'role': 'system', 'content': system}, {'role': 'user', 'content': question}],
            max_tokens=agent.max_tokens, temperature=agent.temperature, session=f'agent-{agent.id}')
        # 上游连接已经确认成功才开始输出；旧delta解析器可忽略这个结构化来源事件。
        return KnowledgeAnswerStream(opened,
            {'citations': found['items'], 'knowledge_sources': found['knowledge_sources']})

    def answer(self, agent_id: str, question: str) -> dict:
        """一次性回答，供需要完整文本的调用方使用。"""
        agent = self.get(agent_id)
        if not agent.enabled:
            raise DomainError(409, 'agent_disabled', '该应用已停用')
        question = question.strip()
        if not question:
            raise DomainError(422, 'question_required', '请输入问题', {'question': '必填'})
        context, found = self._context(agent, question)
        system = (agent.prompt or DEFAULT_PROMPT) + context
        data = ChatService().complete(
            [{'role': 'system', 'content': system}, {'role': 'user', 'content': question}],
            max_tokens=agent.max_tokens, temperature=agent.temperature, session=f'agent-{agent.id}')
        return {'answer': (data['choices'][0].get('message') or {}).get('content', ''),
            'citations': found['items'], 'knowledge_sources': found['knowledge_sources']}
