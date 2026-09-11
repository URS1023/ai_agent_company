import type { WorkbenchInputField } from '../input-schema'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { InputVarType } from '@/app/components/workflow/types'
import { WorkbenchScalarInput } from '../scalar-input'

function field(
  type: WorkbenchInputField['type'],
  extra: Partial<WorkbenchInputField> = {},
): WorkbenchInputField {
  return {
    type,
    variable: 'batch',
    label: 'Batch',
    required: false,
    hidden: false,
    definition: {},
    ...extra,
  }
}

describe('workbench scalar input', () => {
  it('should emit multiline edits as data without leaking primitive event details', () => {
    const onChange = vi.fn()
    render(
      <WorkbenchScalarInput field={field(InputVarType.paragraph)} value="" onChange={onChange} />,
    )
    fireEvent.change(screen.getByRole('textbox', { name: 'Batch' }), { target: { value: 'a\nb' } })
    expect(onChange).toHaveBeenCalledWith('a\nb')
  })
  it('should retain false when a checked checkbox is cleared', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(
      <WorkbenchScalarInput
        field={field(InputVarType.checkbox)}
        value={true}
        onChange={onChange}
      />,
    )
    await user.click(screen.getByRole('checkbox', { name: 'Batch' }))
    expect(onChange).toHaveBeenCalledWith(false)
  })
  it('should associate a text label, preserve leading zeros and forward edits without coercion', () => {
    const onChange = vi.fn()
    render(
      <WorkbenchScalarInput
        field={field(InputVarType.textInput, { required: true, definition: { max_length: 8 } })}
        value="001"
        onChange={onChange}
      />,
    )
    const input = screen.getByRole('textbox', { name: 'Batch' })
    expect(input).toHaveValue('001')
    expect(input).toHaveAttribute('maxlength', '8')
    expect(input).toBeRequired()
    fireEvent.change(input, { target: { value: '002' } })
    expect(onChange).toHaveBeenCalledWith('002')
  })
  it('should display zero and retain a cleared number as empty rather than inventing zero', () => {
    const onChange = vi.fn()
    render(
      <WorkbenchScalarInput field={field(InputVarType.number)} value={0} onChange={onChange} />,
    )
    const input = screen.getByRole('spinbutton', { name: 'Batch' })
    expect(input).toHaveValue(0)
    fireEvent.change(input, { target: { value: '' } })
    expect(onChange).toHaveBeenCalledWith('')
  })
  it('should edit multiline text and keep controls disabled when requested', () => {
    const { rerender } = render(
      <WorkbenchScalarInput
        field={field(InputVarType.paragraph)}
        value={'a\nb'}
        onChange={vi.fn()}
      />,
    )
    expect(screen.getByRole('textbox')).toHaveValue('a\nb')
    rerender(
      <WorkbenchScalarInput
        field={field(InputVarType.paragraph)}
        value=""
        disabled
        onChange={vi.fn()}
      />,
    )
    expect(screen.getByRole('textbox')).toBeDisabled()
  })
  it('should treat required false as a supplied boolean, not require the checkbox to be true', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <WorkbenchScalarInput
        field={field(InputVarType.checkbox, { required: true })}
        value={false}
        onChange={onChange}
      />,
    )
    const input = screen.getByRole('checkbox', { name: 'Batch' })
    expect(input).not.toBeChecked()
    expect(input).not.toBeRequired()
    await user.click(input)
    expect(onChange).toHaveBeenCalledWith(true)
  })
  it('should render real select options and emit their exact string values', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <WorkbenchScalarInput
        field={field(InputVarType.select, { definition: { options: ['01', '02'] } })}
        value="01"
        onChange={onChange}
      />,
    )
    await user.click(screen.getByRole('combobox', { name: 'Batch' }))
    await user.click(await screen.findByRole('option', { name: '02' }))
    expect(onChange).toHaveBeenCalledWith('02')
  })
  it('should hide hidden fields without emitting edits or dropping their defaults', () => {
    const onChange = vi.fn()
    const { container } = render(
      <WorkbenchScalarInput
        field={field(InputVarType.number, { hidden: true })}
        value={0}
        onChange={onChange}
      />,
    )
    expect(container).toBeEmptyDOMElement()
    expect(onChange).not.toHaveBeenCalled()
  })
})
