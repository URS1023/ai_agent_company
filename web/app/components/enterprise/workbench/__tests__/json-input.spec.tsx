import { fireEvent, render, screen } from '@testing-library/react'
import { parseWorkbenchJsonInput } from '../input-json'
import { WorkbenchJsonInput } from '../json-input'

describe('workbench JSON parameter', () => {
  it('should parse nested objects while retaining false, zero and string identifiers', () => {
    expect(
      parseWorkbenchJsonInput('{"batch":"001","flag":false,"count":0,"nested":{"items":[null,1]}}'),
    ).toEqual({
      status: 'valid',
      value: { batch: '001', flag: false, count: 0, nested: { items: [null, 1] } },
    })
  })
  it.each(['{', '[]', 'null', 'true', '0', '"text"', '{"n":1e400}', '{"n":9007199254740993}'])(
    'should reject invalid objects or lossy numbers: %s',
    (text) => {
      expect(parseWorkbenchJsonInput(text)).toEqual({ status: 'invalid' })
    },
  )
  it('should distinguish an omitted value from an empty object', () => {
    expect(parseWorkbenchJsonInput(' \n ')).toEqual({ status: 'empty' })
    expect(parseWorkbenchJsonInput('{}')).toEqual({ status: 'valid', value: {} })
  })
  it('should preserve incomplete edits and associate validation errors with the editor', () => {
    const onChange = vi.fn()
    const { rerender } = render(
      <WorkbenchJsonInput label="Filter" text="{}" required onChange={onChange} />,
    )
    const input = screen.getByRole('textbox', { name: 'Filter' })
    fireEvent.change(input, { target: { value: '{' } })
    expect(onChange).toHaveBeenCalledWith('{')
    rerender(<WorkbenchJsonInput label="Filter" text="{" required onChange={onChange} />)
    expect(input).toHaveValue('{')
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(input).toHaveAccessibleDescription('workflow.errorMsg.invalidJson:{"field":"Filter"}')
  })
  it('should allow omission only for optional parameters and clear errors after correction', () => {
    const { rerender } = render(<WorkbenchJsonInput label="Filter" text="" onChange={vi.fn()} />)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    rerender(<WorkbenchJsonInput label="Filter" text="" required onChange={vi.fn()} />)
    expect(screen.getByRole('alert')).toHaveTextContent('workflow.errorMsg.fieldRequired')
    rerender(<WorkbenchJsonInput label="Filter" text="{}" required disabled onChange={vi.fn()} />)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('textbox')).toBeDisabled()
  })
})
