import type { ExploreMessageListItem } from '@dify/contracts/api/console/installed-apps/types.gen'
import type { WorkbenchTurn } from '../turn'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { WorkbenchConversation } from '../conversation'
import { closeWorkbenchTurn, newWorkbenchTurn } from '../turn'
vi.unmock('foxact/use-clipboard')
vi.mock('@/next/dynamic', () => import('@/__mocks__/next-dynamic'))
beforeAll(async () => {
  await import('@/app/components/base/markdown/streamdown-wrapper')
  await import('@/app/components/base/markdown-blocks/code-block')
})
beforeEach(() => vi.clearAllMocks())
const client = '00000000-0000-4000-8000-000000000001'
function setup(turn: WorkbenchTurn | null = null) {
  const onSend = vi.fn()
  const onChange = vi.fn()
  const onCheckState = vi.fn()
  render(
    <WorkbenchConversation
      title="设备助手"
      messages={turn ? [{ query: '温度如何', turn }] : []}
      draft="检查设备"
      busy={false}
      checking={false}
      onChange={onChange}
      onSend={onSend}
      onCheckState={onCheckState}
    />,
  )
  return { onSend, onCheckState }
}
describe('WorkbenchConversation', () => {
  it('should place historical files with their recorded owner and retain unassigned files', async () => {
    const history: ExploreMessageListItem = {
      id: client,
      conversation_id: client,
      query: 'Question',
      answer: 'Answer',
      inputs: {},
      status: 'normal',
      total_tokens: 0,
      answer_tokens: 0,
      message_tokens: 0,
      provider_response_latency: 0,
      agent_thoughts: [],
      extra_contents: [],
      retriever_resources: [],
      message_files: ['user', 'assistant', 'unknown'].map((owner) => ({
        id: owner,
        filename: `${owner}.pdf`,
        belongs_to: owner,
        type: 'document',
        transfer_method: 'local_file',
        url: `/files/${owner}`,
      })),
    }
    render(
      <WorkbenchConversation
        title="History"
        messages={[]}
        history={[history]}
        draft=""
        busy={false}
        checking={false}
        onChange={vi.fn()}
        onSend={vi.fn()}
        onCheckState={vi.fn()}
      />,
    )
    await act(async () => {
      await vi.dynamicImportSettled()
    })
    const question = screen.getByText('Question')
    expect(within(question).getByRole('link', { name: 'user.pdf' })).toBeInTheDocument()
    expect(within(question).queryByRole('link', { name: 'assistant.pdf' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'assistant.pdf' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'unknown.pdf' })).toBeInTheDocument()
  })
  it('should render native Markdown headings, tables and fenced code in assistant answers', async () => {
    setup({
      ...closeWorkbenchTurn(newWorkbenchTurn(client)),
      answer:
        '## Analysis\n\n| Device | State |\n| --- | --- |\n| A1 | Normal |\n\n```sql\nSELECT 1;\n```',
    })
    await act(async () => {
      await vi.dynamicImportSettled()
    })
    await screen.findByRole('heading', { name: 'Analysis' })
    expect(screen.getByRole('table')).toHaveTextContent('A1')
    await act(async () => {
      await vi.dynamicImportSettled()
    })
    await waitFor(() => expect(document.querySelector('pre')).toHaveTextContent('SELECT 1;'))
    const copy = screen.getByRole('button', { name: 'appOverview.overview.appInfo.embedded.copy' })
    expect(copy.parentElement?.closest('button')).toBeNull()
    const user = userEvent.setup()
    const tabLimit = screen.getAllByRole('button').length + 1
    for (let index = 0; index < tabLimit && document.activeElement !== copy; index += 1)
      await user.tab()
    expect(copy).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(await navigator.clipboard.readText()).toBe('SELECT 1;')
  })
  it('should not turn answer HTML into executable scripts or business-action buttons', async () => {
    setup({
      ...closeWorkbenchTurn(newWorkbenchTurn(client)),
      answer:
        '[Docs](https://example.com/docs)\n\n[Bad](javascript:alert%281%29)\n\n<script>alert(1)</script><button data-message="delete">Delete</button>',
    })
    await act(async () => {
      await vi.dynamicImportSettled()
    })
    const link = await screen.findByRole('link', { name: 'Docs' })
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'))
    expect(screen.queryByRole('link', { name: 'Bad' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument()
    expect(document.querySelector('script')).not.toBeInTheDocument()
  })
  it('should offer a named composer and forward a send for a new conversation', () => {
    const { onSend } = setup()
    expect(screen.getByRole('heading', { name: '设备助手' })).toBeInTheDocument()
    fireEvent.keyDown(
      screen.getByRole('textbox', { name: 'common.enterprise.workbench.message' }),
      { key: 'Enter' },
    )
    expect(onSend).toHaveBeenCalledExactlyOnceWith('检查设备')
  })
  it('should preserve partial text and offer state lookup instead of resend after interruption', async () => {
    const { onSend, onCheckState } = setup({
      ...closeWorkbenchTurn(newWorkbenchTurn(client), true),
      answer: '温度为',
    })
    await act(async () => {
      await vi.dynamicImportSettled()
    })
    expect(await screen.findByText('温度为')).toBeInTheDocument()
    expect(screen.getByText('温度如何')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'common.operation.send' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'common.enterprise.workbench.checkState' }))
    expect(onCheckState).toHaveBeenCalledExactlyOnceWith(client)
    expect(onSend).not.toHaveBeenCalled()
  })
})

describe('WorkbenchConversation terminal gating', () => {
  const finished = (open = false): WorkbenchTurn => ({
    ...newWorkbenchTurn(client),
    connection: open ? 'open' : 'closed',
    answer: '处理完成',
    durable: {
      client_message_id: client,
      revision: 4,
      status: 'accepted',
      conversation_id: '00000000-0000-4000-8000-000000000002',
      message_id: '00000000-0000-4000-8000-000000000003',
      task_id: 'task',
      outcome: 'succeeded',
    },
  })
  it('should allow the next send only after persisted completion and stream closure', () => {
    const { onSend } = setup(finished())
    expect(screen.getByRole('button', { name: 'common.operation.send' })).toBeEnabled()
    expect(
      screen.queryByRole('button', { name: 'common.enterprise.workbench.checkState' }),
    ).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'common.operation.send' }))
    expect(onSend).toHaveBeenCalledOnce()
  })
  it('should keep send locked while an already-persisted response is still streaming', () => {
    setup(finished(true))
    expect(screen.getByRole('button', { name: 'common.operation.send' })).toBeDisabled()
  })
  it('should treat accepted without an outcome as pending rather than completed', () => {
    const turn = finished()
    setup({ ...turn, durable: turn.durable && { ...turn.durable, outcome: null } })
    expect(screen.getByRole('status')).toHaveTextContent('common.enterprise.workbench.waiting')
    expect(screen.getByRole('button', { name: 'common.operation.send' })).toBeDisabled()
  })
})
