import type { ComponentProps } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { WorkbenchComposer } from '../composer'

function setup(overrides: Partial<ComponentProps<typeof WorkbenchComposer>> = {}) {
  const onSend = vi.fn()
  const onChange = vi.fn()
  render(
    <WorkbenchComposer
      value="设备分析"
      label="消息"
      onChange={onChange}
      onSend={onSend}
      {...overrides}
    />,
  )
  return { onSend, onChange, input: screen.getByRole('textbox', { name: '消息' }) }
}

describe('Workbench text composition', () => {
  it('should submit the exact draft on Enter without clearing it', () => {
    const { input, onSend, onChange } = setup({ value: '  设备分析\n第二行  ' })
    expect(fireEvent.keyDown(input, { key: 'Enter' })).toBe(false)
    expect(onSend).toHaveBeenCalledExactlyOnceWith('  设备分析\n第二行  ')
    expect(onChange).not.toHaveBeenCalled()
    expect(input).toHaveValue('  设备分析\n第二行  ')
  })

  it('should leave Shift+Enter to the textarea newline behavior', () => {
    const { input, onSend } = setup()
    expect(fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })).toBe(true)
    expect(onSend).not.toHaveBeenCalled()
  })

  it('should not send while an IME composition is active', () => {
    const { input, onSend } = setup()
    fireEvent.compositionStart(input)
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSend).not.toHaveBeenCalled()
    fireEvent.compositionEnd(input)
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSend).toHaveBeenCalledOnce()
  })

  it.each([{ isComposing: true }, { keyCode: 229 }])(
    'should respect native IME signals %j',
    (signal) => {
      const { input, onSend } = setup()
      fireEvent.keyDown(input, { key: 'Enter', ...signal })
      expect(onSend).not.toHaveBeenCalled()
    },
  )

  it.each([{ value: ' \n ' }, { busy: true }, { disabled: true }])(
    'should prevent submission when unavailable %j',
    async (props) => {
      const { input, onSend } = setup(props)
      expect(screen.getByRole('button', { name: 'common.operation.send' })).toBeDisabled()
      fireEvent.keyDown(input, { key: 'Enter' })
      await userEvent.setup().click(screen.getByRole('button'))
      expect(onSend).not.toHaveBeenCalled()
    },
  )

  it('should send through the button without a native form navigation', async () => {
    const { onSend } = setup()
    await userEvent.setup().click(screen.getByRole('button', { name: 'common.operation.send' }))
    expect(onSend).toHaveBeenCalledExactlyOnceWith('设备分析')
  })

  it('should preserve typing while the previous message is in flight', () => {
    const { input, onChange, onSend } = setup({ busy: true })
    expect(input).not.toBeDisabled()
    fireEvent.change(input, { target: { value: '下一条消息' } })
    expect(onChange).toHaveBeenCalledWith('下一条消息')
    expect(onSend).not.toHaveBeenCalled()
  })
})
