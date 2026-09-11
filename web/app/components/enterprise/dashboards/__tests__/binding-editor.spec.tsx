import type {
  DashboardBindingView,
  DashboardQueryChoice,
} from '@enterprise/business-contracts/types'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BindingEditor } from '../binding-editor'

const view: DashboardBindingView = {
  dashboard_id: 'screen',
  revision: 3,
  design_identity: 'a'.repeat(64),
  bindings: [],
  slots: [{ slot_id: 'metric', columns: [{ name: 'value', kind: 'decimal', required: true }] }],
}
const choices: DashboardQueryChoice[] = [
  {
    query_ref: 'temperature',
    query_revision: 'v1',
    device_id: 'device-1',
    columns: [{ name: 'reading', kind: 'decimal' }],
    parameters: [{ input_key: 'threshold', kind: 'decimal', nullable: false }],
  },
]

describe('Binding editor', () => {
  it('should preselect a unique compatible field while leaving it editable', async () => {
    const user = userEvent.setup()
    const save = vi.fn()
    render(<BindingEditor view={view} choices={choices} onSave={save} />)
    await user.click(screen.getByRole('button', { name: /temperature/ }))
    expect(screen.getByRole('combobox', { name: 'value' })).toHaveTextContent('reading')
    await user.type(screen.getByRole('textbox', { name: 'threshold' }), '10.00')
    await user.click(screen.getByRole('button', { name: 'common.operation.save' }))
    expect(save.mock.calls[0]?.[0].bindings[0].field_map).toEqual({ value: 'reading' })
    expect(screen.getByRole('combobox', { name: 'value' })).toBeEnabled()
  })

  it('should retain entered parameters when clicking the already selected query', async () => {
    const user = userEvent.setup()
    render(<BindingEditor view={view} choices={choices} onSave={vi.fn()} />)
    const choice = screen.getByRole('button', { name: /temperature/ })
    await user.click(choice)
    await user.type(screen.getByRole('textbox', { name: 'threshold' }), '98.2500')
    await user.click(choice)
    expect(screen.getByRole('textbox', { name: 'threshold' })).toHaveValue('98.2500')
  })

  it('should disable selection and submission while the parent is saving', async () => {
    const user = userEvent.setup()
    const save = vi.fn()
    render(<BindingEditor view={view} choices={choices} onSave={save} disabled />)
    const selection = screen.getByRole('button', { name: /temperature/ })
    const submit = screen.getByRole('button', { name: 'common.operation.save' })
    expect(selection).toBeDisabled()
    expect(submit).toBeDisabled()
    await user.click(submit)
    expect(save).not.toHaveBeenCalled()
  })

  it('should restore saved mapping and decimal text without modifying the loaded view', async () => {
    const user = userEvent.setup()
    const save = vi.fn()
    const saved = {
      ...view,
      bindings: [
        {
          slot_id: 'metric',
          query_ref: 'temperature',
          query_revision: 'v1',
          field_map: { value: 'reading' },
          parameters: { threshold: '98.2500' },
        },
      ],
    }
    render(<BindingEditor view={saved} choices={choices} onSave={save} />)
    expect(screen.getByRole('textbox', { name: 'threshold' })).toHaveValue('98.2500')
    await user.click(screen.getByRole('button', { name: 'common.operation.save' }))
    expect(save.mock.calls[0]?.[0].bindings).toEqual(saved.bindings)
  })

  it('should select a query, map a column and submit a versioned command', async () => {
    const user = userEvent.setup()
    const save = vi.fn()
    render(<BindingEditor view={view} choices={choices} onSave={save} />)
    await user.click(screen.getByRole('button', { name: /temperature.*v1.*device-1/ }))
    await user.click(screen.getByRole('combobox', { name: 'value' }))
    await user.click(await screen.findByRole('option', { name: 'reading' }))
    await user.type(screen.getByRole('textbox', { name: 'threshold' }), '98.2500')
    await user.click(screen.getByRole('button', { name: 'common.operation.save' }))
    await waitFor(() => expect(save).toHaveBeenCalledOnce())
    expect(save.mock.calls[0]?.[0]).toEqual({
      expected_revision: 3,
      expected_design_identity: 'a'.repeat(64),
      bindings: [
        {
          slot_id: 'metric',
          query_ref: 'temperature',
          query_revision: 'v1',
          field_map: { value: 'reading' },
          parameters: { threshold: '98.2500' },
        },
      ],
    })
  })

  it('should show validation feedback without submitting an incomplete mapping', async () => {
    const user = userEvent.setup()
    const save = vi.fn()
    const ambiguous = choices.map((query) => ({
      ...query,
      columns: [...query.columns, { name: 'other', kind: 'decimal' as const }],
    }))
    render(<BindingEditor view={view} choices={ambiguous} onSave={save} />)
    await user.click(screen.getByRole('button', { name: /temperature/ }))
    await user.type(screen.getByRole('textbox', { name: 'threshold' }), '10')
    await user.click(screen.getByRole('button', { name: 'common.operation.save' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(save).not.toHaveBeenCalled()
  })

  it('should keep an unavailable saved binding until explicitly removed', async () => {
    const user = userEvent.setup()
    const save = vi.fn()
    const saved = {
      ...view,
      bindings: [
        {
          slot_id: 'metric',
          query_ref: 'revoked',
          query_revision: 'old',
          field_map: { value: 'reading' },
          parameters: {},
        },
      ],
    }
    render(<BindingEditor view={saved} choices={[]} onSave={save} />)
    expect(screen.getByText(/revoked/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'common.operation.save' }))
    expect(save).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'common.operation.remove metric' }))
    await user.click(screen.getByRole('button', { name: 'common.operation.save' }))
    expect(save.mock.calls[0]?.[0].bindings).toEqual([])
  })
})
