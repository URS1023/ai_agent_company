"""显式单空间知识检索；复用现有向量与原件授权，不把原生知识库当平台资料。"""

from datetime import datetime, timezone
import time

from sqlalchemy import select

from agent_platform.core.embeddings import EmbeddingClient
from agent_platform.core.errors import DomainError
from agent_platform.modules.chat.native_models import NativeChatTurn
from agent_platform.modules.knowledge.models import KnowledgeSpace
from agent_platform.modules.knowledge.service import KnowledgeService

OPERATIONS = frozenset({'list_knowledge_spaces', 'search_platform_knowledge'})
MAX_CANDIDATES = 2000


class TurnDeadline:
    """从已验委托的准确Turn取得绝对时限，调用方不能传入自定超时。"""

    def __init__(self, session, actor, context):
        turn = session.get(NativeChatTurn, context.get('turn_id')) if isinstance(context, dict) else None
        if (turn is None or turn.user_id != actor.user_id or turn.workspace_id != actor.workspace_id
                or turn.status not in {'running', 'waiting_approval'}
                or turn.conversation_id != context.get('chat_id') or turn.identity_id != context.get('identity_id')):
            raise DomainError(403, 'native_delegation_denied', '对话检索任务已结束或归属不一致')
        deadline = turn.deadline_at
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        self.expires = time.monotonic() + (deadline - datetime.now(timezone.utc)).total_seconds()
        self.remaining()

    def remaining(self):
        """预留两秒完成来源记录与响应，不把剩余时限回退为默认60秒。"""
        remaining = self.expires - time.monotonic() - 2
        if remaining <= 0:
            raise DomainError(504, 'delegated_knowledge_deadline', '本次对话检索时限已耗尽')
        return remaining


class DeadlineEmbeddings:
    """每次工具只允许一条查询、一次向量请求，严格收紧到Turn剩余时间。"""

    def __init__(self, client, deadline):
        self.client, self.deadline, self.called = client, deadline, False

    def embed(self, texts):
        if self.called or len(texts) != 1:
            raise DomainError(422, 'delegated_knowledge_embedding_budget', '本次知识检索请求超出预算')
        self.called = True
        result = self.client.embed(texts, timeout_seconds=min(15.0, self.deadline.remaining()))
        self.deadline.remaining()
        return result


def source_receipt(actor, space_id, evidence=None):
    """沿用委托来源外层格式；知识快照只带空间、片段摘要和原件版本，不带正文。"""
    resource = {'kind': 'knowledge_space' if evidence is None else 'knowledge_search', 'id': space_id}
    if evidence is not None:
        resource['evidence'] = evidence
    return {'version': 1, 'workspace_id': actor.workspace_id, 'resource': resource,
            'queries': [], 'files': []}


def execute_knowledge(session, actor, operation, args, *, context=None, settings=None, embeddings=None):
    """只检索明确指定的一个知识空间；空命中同样留空间权限证据。"""
    actor.require('m08.read')
    if operation == 'list_knowledge_spaces':
        limit, page = args.get('limit', 20), args.get('page', 1)
        rows = session.scalars(select(KnowledgeSpace).where(KnowledgeSpace.workspace_id == actor.workspace_id)
            .order_by(KnowledgeSpace.created_at.desc(), KnowledgeSpace.id).limit(limit).offset((page - 1) * limit))
        items = [{'id': row.id, 'name': row.name, 'description': row.description} for row in rows]
        return {'items': items, 'returned_count': len(items)}, [source_receipt(actor, item['id']) for item in items]
    query = args.get('query', '').strip()
    if not query:
        raise DomainError(422, 'delegated_knowledge_query_required', '请输入知识检索内容')
    deadline = TurnDeadline(session, actor, context)
    bounded = DeadlineEmbeddings(embeddings or EmbeddingClient(), deadline)
    service = KnowledgeService(session, actor, bounded, settings=settings)
    space_id = args['space_id']
    result = service.search([space_id], query, args.get('top_k', 5), candidate_limit=MAX_CANDIDATES)
    deadline.remaining()
    evidence = result['knowledge_sources']
    return {'items': result['items'], 'returned_count': len(result['items'])}, [source_receipt(actor, space_id, evidence)]
