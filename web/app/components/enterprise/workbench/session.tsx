'use client'

import type { ExploreMessageListItem } from '@dify/contracts/api/console/installed-apps/types.gen'
import type { BranchContext, JsonObject } from '@enterprise/business-contracts/types'
import type { ConversationMessage } from './conversation'
import { zBranchContext, zJsonObject } from '@enterprise/business-contracts/zod'
import { skipToken, useMutation, useQueries, useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { v4 as uuidv4 } from 'uuid'
import { consoleQuery } from '@/service/client'
import { sendWorkbenchMessage } from '@/service/enterprise-business/workbench-send'
import { loadCompletedWorkbenchFiles } from './completed-files'
import { WorkbenchConversation } from './conversation'
import { useWorkbenchDraft } from './draft-state'
import { resolveWorkbenchHistory } from './history'
import {
  applyWorkbenchEvent,
  applyWorkbenchSendState,
  closeWorkbenchTurn,
  newWorkbenchTurn,
} from './turn'

type Props = {
  title: string
  branch: BranchContext
  inputs: JsonObject
  history?: readonly ExploreMessageListItem[]
}

/** Remount on identity changes so draft, stream and cached observations cannot cross scopes.
 * The caller supplies a native-authorized branch and configured app inputs. History loading
 * is separate; a known in-flight ID is represented as pending, never resent on mount.
 */
export function WorkbenchSession(props: Props) {
  const scope = props.branch.scope
  return (
    <SessionSnapshot
      key={JSON.stringify([
        scope.workspace_id,
        scope.actor_id,
        scope.installed_app_id,
        scope.branch_id,
      ])}
      {...props}
    />
  )
}

function SessionSnapshot(props: Props) {
  const { t } = useTranslation('common')
  const [snapshot] = useState(() => {
    try {
      const branch = zBranchContext.parse(props.branch)
      let history: ExploreMessageListItem[] = []
      if (branch.head_message_id) {
        if (!branch.conversation_id) return null
        const resolved = resolveWorkbenchHistory(
          props.history ?? [],
          branch.conversation_id,
          branch.head_message_id,
          false,
        )
        if (resolved.status !== 'ready') return null
        history = resolved.messages
      } else if (props.history?.length) return null
      return { ...props, branch, inputs: zJsonObject.parse(structuredClone(props.inputs)), history }
    } catch {
      return null
    }
  })
  if (!snapshot) return <p role="alert">{t(($) => $['enterprise.loadError'])}</p>
  return <Session {...snapshot} />
}

function Session({ title, branch, inputs, history }: Props) {
  const scope = branch.scope
  const [draft, setDraft] = useWorkbenchDraft(scope)
  const [messages, setMessages] = useState<ConversationMessage[]>(() =>
    branch.inflight_client_message_id
      ? [
          {
            query: '',
            turn: closeWorkbenchTurn(
              newWorkbenchTurn(branch.inflight_client_message_id, branch.conversation_id ?? null),
              true,
            ),
          },
        ]
      : [],
  )
  const activeRef = useRef<AbortController | null>(null)
  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      activeRef.current?.abort()
    }
  }, [])
  const last = messages.at(-1)
  const stateQuery = useQuery(
    consoleQuery.business.workbench.apps.byInstalledAppId.branches.byBranchId.messages.byClientMessageId.get.queryOptions(
      {
        input: last
          ? {
              params: {
                installed_app_id: scope.installed_app_id,
                branch_id: scope.branch_id,
                client_message_id: last.turn.clientMessageId,
              },
            }
          : skipToken,
        queryKey: [
          'enterprise-workbench-state',
          scope.workspace_id,
          scope.actor_id,
          scope.installed_app_id,
          scope.branch_id,
          last?.turn.clientMessageId,
        ],
        enabled: false,
        retry: false,
      },
    ),
  )
  const fileQueries = useQueries({
    queries: messages.map(({ turn }) => {
      const receipt = turn.durable
      return {
        queryKey: [
          'enterprise-workbench-completed-files',
          scope,
          turn.clientMessageId,
          receipt?.revision,
        ],
        queryFn:
          receipt?.status === 'accepted' && receipt.outcome !== null && turn.connection !== 'open'
            ? ({ signal }: { signal: AbortSignal }) =>
                loadCompletedWorkbenchFiles(scope, receipt, signal)
            : skipToken,
        retry: false,
        staleTime: Infinity,
        gcTime: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
      }
    }),
  })
  const generation = useMutation({
    retry: false,
    mutationFn: async ({
      message,
      payload,
      controller,
    }: {
      message: ConversationMessage
      payload: JsonObject
      controller: AbortController
    }) => {
      let turn = message.turn
      const publish = () => {
        const snapshot = turn
        if (mountedRef.current)
          setMessages((previous) =>
            previous.map((item) =>
              item.turn.clientMessageId === snapshot.clientMessageId
                ? { ...item, turn: snapshot }
                : item,
            ),
          )
      }
      try {
        for await (const event of sendWorkbenchMessage(
          {
            path: { installed_app_id: scope.installed_app_id, branch_id: scope.branch_id },
            body: { client_message_id: turn.clientMessageId, payload },
          },
          { signal: controller.signal },
        )) {
          turn = applyWorkbenchEvent(turn, event)
          publish()
        }
        turn = closeWorkbenchTurn(turn)
      } catch {
        turn = closeWorkbenchTurn(turn, true)
      } finally {
        publish()
        if (activeRef.current === controller) activeRef.current = null
      }
      if (mountedRef.current && turn.durable?.status === 'accepted')
        setDraft((current) => (current === message.query ? '' : current))
    },
  })

  function send(query: string) {
    if (
      activeRef.current ||
      generation.isPending ||
      stateQuery.isFetching ||
      branch.state !== 'ready' ||
      !query.trim() ||
      messages.some(({ turn }) => turn.durable?.outcome == null || turn.connection === 'open')
    )
      return
    const previous = last?.turn.durable
    const conversationId = previous?.conversation_id ?? branch.conversation_id ?? null
    const parentMessageId = previous?.message_id ?? branch.head_message_id ?? null
    const payload: JsonObject = {
      inputs: structuredClone(inputs),
      query,
      ...(conversationId ? { conversation_id: conversationId } : {}),
      ...(parentMessageId ? { parent_message_id: parentMessageId } : {}),
    }
    const message = { query, turn: newWorkbenchTurn(uuidv4(), conversationId) }
    const controller = new AbortController()
    activeRef.current = controller
    setMessages((previousMessages) => [...previousMessages, message])
    generation.mutate({ message, payload, controller })
  }

  async function checkState(clientMessageId: string) {
    if (
      !last ||
      last.turn.clientMessageId !== clientMessageId ||
      activeRef.current ||
      stateQuery.isFetching
    )
      return
    const result = await stateQuery.refetch()
    if (!mountedRef.current || !result.data) return
    try {
      const turn = applyWorkbenchSendState(last.turn, result.data)
      setMessages((previous) =>
        previous.map((item) =>
          item.turn.clientMessageId === clientMessageId ? { ...item, turn } : item,
        ),
      )
      if (turn.durable?.status === 'accepted')
        setDraft((current) => (current === last.query ? '' : current))
    } catch {
      // A foreign/conflicting observation leaves the pending turn intact; never unlock a send.
    }
  }

  return (
    <WorkbenchConversation
      officeScope={{ actor: scope.actor_id, workspace: scope.workspace_id }}
      title={title}
      history={history}
      messages={messages.map((message, index) => ({
        ...message,
        completedFiles: fileQueries[index]?.data,
        filesLoading: fileQueries[index]?.isFetching,
        filesError: fileQueries[index]?.isError,
      }))}
      draft={draft}
      busy={generation.isPending || branch.state !== 'ready'}
      checking={stateQuery.isFetching}
      onChange={setDraft}
      onSend={send}
      onRetryFiles={(clientMessageId) => {
        const index = messages.findIndex(
          (message) => message.turn.clientMessageId === clientMessageId,
        )
        void fileQueries[index]?.refetch()
      }}
      onCheckState={(id) => {
        void checkState(id)
      }}
    />
  )
}
