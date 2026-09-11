import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ScheduleForm } from '../schedule-form'

describe('schedule creation form', () => {
  const onSave = vi.fn()
  beforeEach(() => vi.clearAllMocks())
  function mount(pending = false) {
    return render(
      <ScheduleForm
        actors={[{ actor_id: 'worker-1', display_name: 'Factory worker' }]}
        bindingRevision={7}
        pending={pending}
        onSave={onSave}
      />,
    )
  }
  function start() {
    fireEvent.change(screen.getByLabelText('common.enterprise.schedule.anchor'), {
      target: { value: '2026-09-11T08:30' },
    })
  }
  it('should submit typed configuration with a UTC instant and the displayed binding revision', async () => {
    const user = userEvent.setup()
    mount()
    start()
    await user.click(screen.getByRole('button', { name: 'common.operation.save' }))
    expect(onSave).toHaveBeenCalledOnce()
    expect(onSave).toHaveBeenCalledWith({
      service_actor_id: 'worker-1',
      expected_binding_revision: 7,
      anchor_at: new Date('2026-09-11T08:30').toISOString(),
      interval_seconds: 60,
      window_seconds: 300,
      grace_seconds: 10,
      missed_policy: 'skip',
    })
  })
  it('should reject grace equal to the interval before submission', async () => {
    const user = userEvent.setup()
    mount()
    start()
    fireEvent.change(screen.getByLabelText('common.enterprise.schedule.grace'), {
      target: { value: '60' },
    })
    await user.click(screen.getByRole('button', { name: 'common.operation.save' }))
    await screen.findByRole('alert')
    expect(onSave).not.toHaveBeenCalled()
  })
  it('should disable fields and submission during an unresolved write', () => {
    mount(true)
    expect(screen.getByLabelText('common.enterprise.schedule.anchor')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'common.operation.save' })).toBeDisabled()
  })
  it('should not choose an invented account when the candidate list is empty', () => {
    render(<ScheduleForm actors={[]} bindingRevision={7} pending={false} onSave={onSave} />)
    expect(screen.getByRole('button', { name: 'common.operation.save' })).toBeDisabled()
  })

  it('should reject a selected actor removed from the current server candidates', async () => {
    const user = userEvent.setup()
    const view = mount()
    start()
    view.rerender(
      <ScheduleForm
        actors={[{ actor_id: 'replacement', display_name: 'Replacement' }]}
        bindingRevision={7}
        pending={false}
        onSave={onSave}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'common.operation.save' }))
    await screen.findByRole('alert')
    expect(onSave).not.toHaveBeenCalled()
  })

  it('should submit the explicitly selected missed-run policy', async () => {
    const user = userEvent.setup()
    mount()
    start()
    await user.click(screen.getByRole('combobox', { name: 'common.enterprise.schedule.policy' }))
    await user.click(
      await screen.findByRole('option', { name: 'common.enterprise.schedule.coalesce' }),
    )
    await user.click(screen.getByRole('button', { name: 'common.operation.save' }))
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ missed_policy: 'coalesce' }))
  })
})
