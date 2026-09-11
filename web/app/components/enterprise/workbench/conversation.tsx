'use client'

import type {
  ExploreMessageListItem,
  MessageFile,
} from '@dify/contracts/api/console/installed-apps/types.gen'
import type { WorkbenchTurn } from './turn'
import { Button } from '@langgenius/dify-ui/button'
import { useTranslation } from 'react-i18next'
import { Markdown } from '@/app/components/base/markdown'
import { SkeletonRectangle } from '@/app/components/base/skeleton'
import { WorkbenchComposer } from './composer'
import { WorkbenchHistoryFiles } from './history-files'

export type ConversationMessage = Readonly<{
  query: string
  turn: WorkbenchTurn
  completedFiles?: readonly MessageFile[]
  filesLoading?: boolean
  filesError?: boolean
}>
const disallowedAnswerElements = ['button', 'form', 'input', 'textarea']

/** Controlled transcript: the route owns scoped transport, durable reads and the draft.
 * An unresolved turn locks new sends, but leaves the draft editable and offers a read-only check.
 */
export function WorkbenchConversation({
  title,
  messages,
  history = [],
  draft,
  busy,
  checking,
  onChange,
  onSend,
  onCheckState,
  onRetryFiles,
}: {
  title: string
  messages: readonly ConversationMessage[]
  history?: readonly ExploreMessageListItem[]
  draft: string
  busy: boolean
  checking: boolean
  onChange: (value: string) => void
  onSend: (value: string) => void
  onCheckState: (clientMessageId: string) => void
  onRetryFiles?: (clientMessageId: string) => void
}) {
  const { t } = useTranslation('common')
  const unresolved = messages.some(
    ({ turn }) => turn.durable?.outcome == null || turn.connection === 'open',
  )
  return (
    <section className="flex h-full min-h-0 min-w-0 flex-1 flex-col bg-background-body">
      <header className="shrink-0 border-b border-divider-subtle px-6 py-4">
        <h1 className="truncate system-md-semibold text-text-primary" title={title}>
          {title}
        </h1>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl space-y-8 px-4 py-8 sm:px-6">
          {history.map((message) => (
            <article key={`native-${message.id}`} className="space-y-5">
              <div className="ml-auto w-fit max-w-full rounded-2xl bg-background-section px-4 py-3 break-words whitespace-pre-wrap text-text-primary">
                {message.query}
                <WorkbenchHistoryFiles
                  files={message.message_files.filter((file) => file.belongs_to === 'user')}
                />
              </div>
              <div className="chat-answer-container min-w-0 break-words text-text-primary">
                <Markdown
                  content={message.answer}
                  mode="static"
                  isAnimating={false}
                  customDisallowedElements={disallowedAnswerElements}
                />
                <WorkbenchHistoryFiles
                  files={message.message_files.filter((file) => file.belongs_to === 'assistant')}
                />
              </div>
              <WorkbenchHistoryFiles
                files={message.message_files.filter(
                  (file) => file.belongs_to !== 'user' && file.belongs_to !== 'assistant',
                )}
              />
            </article>
          ))}
          {messages.map(({ query, turn, completedFiles, filesLoading, filesError }) => (
            <article key={turn.clientMessageId} className="space-y-5">
              <div className="ml-auto w-fit max-w-full rounded-2xl bg-background-section px-4 py-3 break-words whitespace-pre-wrap text-text-primary">
                {query}
                <WorkbenchHistoryFiles
                  files={(completedFiles ?? turn.files).filter(
                    (file) => file.belongs_to === 'user',
                  )}
                />
              </div>
              <div
                className="chat-answer-container min-w-0 break-words text-text-primary"
                aria-busy={turn.connection === 'open'}
              >
                <Markdown
                  content={turn.answer}
                  isAnimating={turn.connection === 'open'}
                  mode={turn.connection === 'open' ? 'streaming' : 'static'}
                  customDisallowedElements={disallowedAnswerElements}
                />
                <WorkbenchHistoryFiles
                  files={(completedFiles ?? turn.files).filter(
                    (file) => file.belongs_to === 'assistant',
                  )}
                />
              </div>
              <WorkbenchHistoryFiles
                files={(completedFiles ?? turn.files).filter(
                  (file) => file.belongs_to !== 'user' && file.belongs_to !== 'assistant',
                )}
              />
              {filesLoading && (
                <div aria-busy="true">
                  <SkeletonRectangle className="h-10 w-48" />
                </div>
              )}
              {filesError && (
                <div className="flex items-center gap-3">
                  <p role="alert">{t(($) => $['fileUploader.uploadFromComputerReadError'])}</p>
                  {onRetryFiles && (
                    <Button
                      variant="secondary"
                      disabled={filesLoading}
                      onClick={() => onRetryFiles(turn.clientMessageId)}
                    >
                      {t(($) => $['operation.retry'])}
                    </Button>
                  )}
                </div>
              )}
              <div className="flex flex-wrap items-center gap-3">
                <p role="status" className="system-xs-regular text-text-tertiary">
                  {t(
                    ($) =>
                      $[
                        turn.durable?.outcome === 'succeeded'
                          ? 'enterprise.devices.state.succeeded'
                          : turn.durable?.outcome === 'failed'
                            ? 'enterprise.devices.state.failed'
                            : turn.durable?.outcome === 'stopped'
                              ? 'enterprise.workbench.stopped'
                              : turn.durable?.outcome === 'partial-succeeded'
                                ? 'enterprise.workbench.partial'
                                : 'enterprise.workbench.waiting'
                      ],
                  )}
                </p>
                {turn.durable?.outcome == null && turn.connection !== 'open' && (
                  <Button
                    variant="secondary"
                    disabled={checking || busy}
                    onClick={() => onCheckState(turn.clientMessageId)}
                  >
                    {t(($) => $['enterprise.workbench.checkState'])}
                  </Button>
                )}
              </div>
            </article>
          ))}
        </div>
      </div>
      <div className="mx-auto w-full max-w-3xl shrink-0 px-4 pb-5 sm:px-6">
        <WorkbenchComposer
          value={draft}
          label={t(($) => $['enterprise.workbench.message'])}
          busy={busy || unresolved || checking}
          onChange={onChange}
          onSend={onSend}
        />
      </div>
    </section>
  )
}
