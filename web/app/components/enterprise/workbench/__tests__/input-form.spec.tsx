import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { WorkbenchInputForm } from '../input-form'
import { decodeWorkbenchInputs } from '../input-schema'

const labels = {
  submit: 'Continue',
  required: 'Required',
  invalid: 'Invalid value',
  pending: 'Uploading',
  configMissing: 'Upload configuration required',
}
const fields = decodeWorkbenchInputs([
  { 'text-input': { variable: 'batch', label: 'Batch', required: true } },
  { checkbox: { variable: 'flag', label: 'Flag', hide: true, default: false } },
  { json_object: { variable: 'filter', label: 'Filter', default: { count: 0 } } },
])

describe('workbench parameter form', () => {
  it('should treat prototype-like variable names without defaults as empty', () => {
    const onSubmit = vi.fn()
    const named = decodeWorkbenchInputs([
      { 'text-input': { variable: '__proto__', label: 'Value' } },
    ])
    render(
      <WorkbenchInputForm scopeKey="app-1" fields={named} labels={labels} onSubmit={onSubmit} />,
    )
    expect(screen.getByRole('textbox', { name: 'Value' })).toHaveValue('')
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(onSubmit).toHaveBeenCalledWith({})
  })
  it('should render defaults, prevent incomplete submission and submit edited native inputs', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(
      <WorkbenchInputForm scopeKey="app-1" fields={fields} labels={labels} onSubmit={onSubmit} />,
    )
    expect(screen.getByRole('textbox', { name: 'Filter' })).toHaveValue('{\n  "count": 0\n}')
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('Batch: Required')
    await user.type(screen.getByRole('textbox', { name: 'Batch' }), '001')
    fireEvent.change(screen.getByRole('textbox', { name: 'Filter' }), {
      target: { value: '{"count":2}' },
    })
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    expect(onSubmit).toHaveBeenCalledWith({ batch: '001', flag: false, filter: { count: 2 } })
  })
  it('should retain invalid JSON drafts and prevent submission', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(
      <WorkbenchInputForm scopeKey="app-1" fields={fields} labels={labels} onSubmit={onSubmit} />,
    )
    fireEvent.change(screen.getByRole('textbox', { name: 'Batch' }), { target: { value: '001' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Filter' }), { target: { value: '{' } })
    await user.click(screen.getByRole('button', { name: 'Continue' }))
    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByRole('textbox', { name: 'Filter' })).toHaveValue('{')
  })
  it('should reset form state on scope changes and disable editing during launch', () => {
    const { rerender } = render(
      <WorkbenchInputForm scopeKey="app-1" fields={fields} labels={labels} onSubmit={vi.fn()} />,
    )
    fireEvent.change(screen.getByRole('textbox', { name: 'Batch' }), {
      target: { value: 'private draft' },
    })
    rerender(
      <WorkbenchInputForm
        scopeKey="app-2"
        fields={fields}
        labels={labels}
        busy
        onSubmit={vi.fn()}
      />,
    )
    expect(screen.getByRole('textbox', { name: 'Batch' })).toHaveValue('')
    expect(screen.getByRole('textbox', { name: 'Batch' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled()
  })
  it('should not enable launch when file configuration has not loaded', () => {
    const files = decodeWorkbenchInputs([{ file: { variable: 'report', label: 'Report' } }])
    render(
      <WorkbenchInputForm scopeKey="app-1" fields={files} labels={labels} onSubmit={vi.fn()} />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent(labels.configMissing)
    expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled()
  })
})
