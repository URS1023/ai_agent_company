import type { DeviceCreate } from '@enterprise/business-contracts/types'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { DeviceForm } from '../device-form'

describe('DeviceForm', () => {
  beforeEach(() => vi.clearAllMocks())

  it('should preserve leading zeros and deliberate clearing of optional fields', async () => {
    const save = vi.fn<(value: DeviceCreate) => void>()
    render(
      <DeviceForm
        initial={{ device_code: '0001', name: 'Pump', department: 'A' }}
        onSave={save}
        onCancel={vi.fn()}
        pending={false}
      />,
    )
    await userEvent.clear(
      screen.getByRole('textbox', { name: 'common.enterprise.devices.department' }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.save' }))
    await waitFor(() =>
      expect(save).toHaveBeenCalledWith({
        device_code: '0001',
        name: 'Pump',
        department: '',
        description: '',
      }),
    )
  })

  it('should reject whitespace-only identity fields', async () => {
    const save = vi.fn()
    render(
      <DeviceForm
        initial={{ device_code: ' ', name: 'Pump' }}
        onSave={save}
        onCancel={vi.fn()}
        pending={false}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.save' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(save).not.toHaveBeenCalled()
  })

  it('should retain the form and prevent a second submit while pending', async () => {
    const save = vi.fn()
    render(
      <DeviceForm
        initial={{ device_code: '0001', name: 'Pump' }}
        onSave={save}
        onCancel={vi.fn()}
        pending
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: 'common.operation.save' }))
    expect(save).not.toHaveBeenCalled()
    expect(screen.getByRole('textbox', { name: 'common.enterprise.devices.code' })).toHaveValue(
      '0001',
    )
  })
})
