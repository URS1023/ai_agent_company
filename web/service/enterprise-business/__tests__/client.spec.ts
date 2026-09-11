import type {
  BusinessAccess,
  Device,
  ProvisioningAdvanceRequest,
  ProvisioningStartRequest,
} from '@enterprise/business-contracts/types'
import { QueryClient } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { consoleClient, consoleQuery } from '@/service/client'

const { requestMock } = vi.hoisted(() => ({ requestMock: vi.fn() }))

vi.mock('@/service/base', () => ({ request: requestMock }))
vi.mock('@/config', () => ({
  API_PREFIX: 'https://console-api.example/console/api',
  APP_VERSION: 'test',
  IS_MARKETPLACE: false,
  MARKETPLACE_API_PREFIX: 'https://marketplace.example',
}))
vi.mock('@/utils/client', () => ({ isClient: true, isServer: false }))

const device: Device = {
  id: 'device-1',
  workspace_id: 'workspace-1',
  device_code: '0001',
  name: 'Pump',
  revision: 1,
  created_at: '2026-09-08T00:00:00Z',
  updated_at: '2026-09-08T00:00:00Z',
}

// The generated business routes use the browser origin, independently of the native API prefix.
describe('consoleClient.business', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('should load specification choices through an exact read-only enrollment route', async () => {
    requestMock.mockResolvedValue(Response.json({ items: ['spec-1', 'spec-2'] }))
    const result =
      await consoleClient.business.workflowEnrollments.byEnrollmentId.activationSpecifications.get({
        params: { enrollment_id: 'enrollment-1' },
      })
    expect(result.items).toEqual(['spec-1', 'spec-2'])
    const [url, , options] = requestMock.mock.calls[0]!
    const request: Request = options.request
    expect(url).toBe(
      `${window.location.origin}/enterprise/api/v1/workflow-enrollments/enrollment-1/activation-specifications`,
    )
    expect(request.method).toBe('GET')
    expect(request.body).toBeNull()
    expect(requestMock).toHaveBeenCalledTimes(1)
  })

  it('should restore activation through the nullable generated enrollment lookup', async () => {
    requestMock.mockResolvedValue(Response.json(null))
    expect(
      await consoleClient.business.workflowEnrollments.byEnrollmentId.activation.get({
        params: { enrollment_id: 'enrollment-1' },
      }),
    ).toBeNull()
    const [url, , options] = requestMock.mock.calls[0]!
    const request: Request = options.request
    expect(url).toBe(
      `${window.location.origin}/enterprise/api/v1/workflow-enrollments/enrollment-1/activation`,
    )
    expect(request.method).toBe('GET')
    expect(request.body).toBeNull()
    expect(requestMock).toHaveBeenCalledTimes(1)
  })

  it('should create activation with only the selected specification revision', async () => {
    requestMock.mockResolvedValue(Response.json({ active: true }))
    await consoleClient.business.workflowEnrollments.byEnrollmentId.activation.post({
      params: { enrollment_id: 'enrollment-1' },
      headers: { Origin: window.location.origin },
      body: { specification_revision: 'spec-1' },
    })
    const [url, , options] = requestMock.mock.calls[0]!
    const request: Request = options.request
    expect(url).toBe(
      `${window.location.origin}/enterprise/api/v1/workflow-enrollments/enrollment-1/activation`,
    )
    expect(request.method).toBe('POST')
    expect(await request.json()).toEqual({ specification_revision: 'spec-1' })
    expect(requestMock).toHaveBeenCalledTimes(1)
  })

  it('should not resubmit an ambiguous activation revocation', async () => {
    requestMock.mockResolvedValue(
      Response.json({ code: 'dependency_unavailable' }, { status: 503 }),
    )
    await expect(
      consoleClient.business.workflowActivations.byActivationId.revoke.post({
        params: { activation_id: 'activation-1' },
        headers: { Origin: window.location.origin },
        body: { expected_revision: 1 },
      }),
    ).rejects.toMatchObject({ status: 503 })
    const [url, , options] = requestMock.mock.calls[0]!
    const request: Request = options.request
    expect(url).toBe(
      `${window.location.origin}/enterprise/api/v1/workflow-activations/activation-1/revoke`,
    )
    expect(await request.json()).toEqual({ expected_revision: 1 })
    expect(requestMock).toHaveBeenCalledTimes(1)
  })

  it('should restore enrollment by provisioning using a read-only nullable query', async () => {
    requestMock.mockResolvedValue(Response.json(null))
    const result =
      await consoleClient.business.workflowProvisioning.byProvisioningId.enrollment.get({
        params: { provisioning_id: 'provisioning-1' },
      })
    expect(result).toBeNull()
    const [url, , options] = requestMock.mock.calls[0]!
    const request: Request = options.request
    expect(url).toBe(
      `${window.location.origin}/enterprise/api/v1/workflow-provisioning/provisioning-1/enrollment`,
    )
    expect(request.method).toBe('GET')
    expect(request.body).toBeNull()
    expect(requestMock).toHaveBeenCalledTimes(1)
  })

  it('should create enrollment with an empty generated command at the exact provisioning path', async () => {
    requestMock.mockResolvedValue(Response.json({ state: 'pending_verification' }))
    await consoleClient.business.workflowProvisioning.byProvisioningId.enrollment.post({
      params: { provisioning_id: 'provisioning-1' },
      headers: { Origin: window.location.origin },
      body: {},
    })
    const [url, , options] = requestMock.mock.calls[0]!
    expect(url).toBe(
      `${window.location.origin}/enterprise/api/v1/workflow-provisioning/provisioning-1/enrollment`,
    )
    const request: Request = options.request
    expect(await request.json()).toEqual({})
    expect(requestMock).toHaveBeenCalledTimes(1)
  })

  it('should read enrollment without sending credentials in its body', async () => {
    requestMock.mockResolvedValue(Response.json({ state: 'verified' }))
    await consoleClient.business.workflowEnrollments.byEnrollmentId.get({
      params: { enrollment_id: 'enrollment-1' },
    })
    const [url, , options] = requestMock.mock.calls[0]!
    const request: Request = options.request
    expect(url).toBe(
      `${window.location.origin}/enterprise/api/v1/workflow-enrollments/enrollment-1`,
    )
    expect(request.method).toBe('GET')
    expect(request.body).toBeNull()
  })

  it('should not retry an ambiguous enrollment advance response', async () => {
    requestMock.mockResolvedValue(
      Response.json({ code: 'dependency_unavailable' }, { status: 503 }),
    )
    await expect(
      consoleClient.business.workflowEnrollments.byEnrollmentId.advance.post({
        params: { enrollment_id: 'enrollment-1' },
        headers: { Origin: window.location.origin },
        body: { expected_revision: 3 },
      }),
    ).rejects.toMatchObject({ status: 503 })
    expect(requestMock).toHaveBeenCalledTimes(1)
    const [url, , options] = requestMock.mock.calls[0]!
    expect(url).toBe(
      `${window.location.origin}/enterprise/api/v1/workflow-enrollments/enrollment-1/advance`,
    )
    const request: Request = options.request
    expect(await request.json()).toEqual({ expected_revision: 3 })
  })

  it('should read generated provisioning profiles without a body or caller scope', async () => {
    requestMock.mockResolvedValue(Response.json({ items: [] }))

    await consoleClient.business.workflowSetups.bySetupId.provisioningProfiles.get({
      params: { setup_id: 'setup-1' },
    })

    expect(requestMock).toHaveBeenCalledTimes(1)
    const [url, , options] = requestMock.mock.calls[0]!
    const request: Request = options.request
    expect(url).toBe(
      `${window.location.origin}/enterprise/api/v1/workflow-setups/setup-1/provisioning-profiles`,
    )
    expect(request.method).toBe('GET')
    expect(request.body).toBeNull()
    expect(request.headers.has('x-workspace-id')).toBe(false)
  })

  it('should read generated provisioning history with exact setup and pagination', async () => {
    requestMock.mockResolvedValue(Response.json({ items: [], offset: 20, limit: 10, total: 0 }))

    const result = await consoleClient.business.workflowSetups.bySetupId.provisioning.get({
      params: { setup_id: 'setup-1' },
      query: { offset: 20, limit: 10 },
    })

    expect(result).toEqual({ items: [], offset: 20, limit: 10, total: 0 })
    expect(requestMock).toHaveBeenCalledTimes(1)
    const [url, , options] = requestMock.mock.calls[0]!
    const request: Request = options.request
    const target = new URL(url)
    expect(target.origin).toBe(window.location.origin)
    expect(target.pathname).toBe('/enterprise/api/v1/workflow-setups/setup-1/provisioning')
    expect(Object.fromEntries(target.searchParams)).toEqual({ offset: '20', limit: '10' })
    expect(request.method).toBe('GET')
    expect(request.body).toBeNull()
  })

  it('should start provisioning with only the generated public config reference and idempotency key', async () => {
    requestMock.mockResolvedValue(Response.json({ id: 'provisioning-1' }))
    const body: ProvisioningStartRequest = { config_ref: 'config-1', config_revision: 2 }

    await consoleClient.business.workflowSetups.bySetupId.provisioning.post({
      params: { setup_id: 'setup-1' },
      headers: { 'idempotency-key': 'provision-request-1', Origin: window.location.origin },
      body,
    })

    expect(requestMock).toHaveBeenCalledTimes(1)
    const [url, , options] = requestMock.mock.calls[0]!
    const request: Request = options.request
    expect(url).toBe(
      `${window.location.origin}/enterprise/api/v1/workflow-setups/setup-1/provisioning`,
    )
    expect(request.method).toBe('POST')
    expect(request.headers.get('idempotency-key')).toBe('provision-request-1')
    expect(await request.json()).toEqual(body)
  })

  it('should read provisioning using its exact generated detail route', async () => {
    requestMock.mockResolvedValue(Response.json({ id: 'provisioning-1' }))

    await consoleClient.business.workflowProvisioning.byProvisioningId.get({
      params: { provisioning_id: 'provisioning-1' },
    })

    expect(requestMock).toHaveBeenCalledTimes(1)
    const [url, , options] = requestMock.mock.calls[0]!
    const request: Request = options.request
    expect(url).toBe(
      `${window.location.origin}/enterprise/api/v1/workflow-provisioning/provisioning-1`,
    )
    expect(request.method).toBe('GET')
    expect(request.body).toBeNull()
  })

  it('should advance once with the expected revision and retain uncertain errors without retry', async () => {
    requestMock.mockResolvedValue(
      Response.json({ code: 'dependency_unavailable' }, { status: 503 }),
    )
    const body: ProvisioningAdvanceRequest = { expected_revision: 3 }

    await expect(
      consoleClient.business.workflowProvisioning.byProvisioningId.advance.post({
        params: { provisioning_id: 'provisioning-1' },
        headers: { Origin: window.location.origin },
        body,
      }),
    ).rejects.toMatchObject({ status: 503 })

    expect(requestMock).toHaveBeenCalledTimes(1)
    const [url, , options] = requestMock.mock.calls[0]!
    const request: Request = options.request
    expect(url).toBe(
      `${window.location.origin}/enterprise/api/v1/workflow-provisioning/provisioning-1/advance`,
    )
    expect(request.method).toBe('POST')
    expect(await request.json()).toEqual(body)
  })

  it('should fetch real generated device routes through the native request boundary', async () => {
    requestMock.mockResolvedValue(Response.json(device))

    const result = await consoleClient.business.devices.byDeviceId.get({
      params: { device_id: 'device-1' },
    })

    expect(result).toEqual(device)
    expect(requestMock).toHaveBeenCalledWith(
      `${window.location.origin}/enterprise/api/v1/devices/device-1`,
      expect.anything(),
      expect.objectContaining({ fetchCompat: true, request: expect.any(Request) }),
    )
  })

  it('should preserve body, revision header, abort signal and silent context for updates', async () => {
    requestMock.mockResolvedValue(Response.json(device))
    const controller = new AbortController()

    await consoleClient.business.devices.byDeviceId.put(
      {
        params: { device_id: 'device-1' },
        headers: { 'if-match': '"1"', Origin: window.location.origin },
        body: { device_code: '0001', name: 'Pump' },
      },
      { signal: controller.signal, context: { silent: true } },
    )

    const [url, , options] = requestMock.mock.calls[0]!
    const request = options.request as Request
    expect(url).toBe(`${window.location.origin}/enterprise/api/v1/devices/device-1`)
    expect(request.method).toBe('PUT')
    expect(request.headers.get('if-match')).toBe('"1"')
    expect(await request.json()).toEqual({ device_code: '0001', name: 'Pump' })
    expect(options.silent).toBe(true)
    controller.abort()
    expect(request.signal.aborted).toBe(true)
  })

  it('should expose generated query options with a separate business cache namespace', async () => {
    requestMock.mockResolvedValue(
      Response.json({ items: [device], offset: 0, limit: 20, total: 1 }),
    )
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const options = consoleQuery.business.devices.get.queryOptions({
      input: { query: { offset: 0, limit: 20, q: '0001', department: 'Operations' } },
    })

    const result = await client.fetchQuery(options)

    expect(result.items).toEqual([device])
    expect(options.queryKey[0]).toEqual(['console', 'business', 'devices', 'get'])
    const requestURL = new URL(requestMock.mock.calls[0]![0])
    expect(requestURL.pathname).toBe('/enterprise/api/v1/devices')
    expect(requestURL.searchParams.get('limit')).toBe('20')
    expect(requestURL.searchParams.get('q')).toBe('0001')
    expect(requestURL.searchParams.get('department')).toBe('Operations')
    client.clear()
  })

  it('should read server-derived workspace permissions through the generated identity route', async () => {
    const access: BusinessAccess = {
      actor_id: 'actor-1',
      workspace_id: 'workspace-1',
      display_name: 'Viewer',
      permissions: { read: true, manage: false, run: false, review: false },
    }
    requestMock.mockResolvedValue(Response.json(access))

    expect(await consoleClient.business.me.get({})).toEqual(access)

    expect(requestMock.mock.calls[0]?.[0]).toBe(`${window.location.origin}/enterprise/api/v1/me`)
  })

  it('should keep native generated routes on their original console link', async () => {
    requestMock.mockResolvedValue(Response.json([]))

    await consoleClient.tags.get({})

    expect(requestMock.mock.calls[0]![0]).toBe('https://console-api.example/console/api/tags')
  })

  it('should surface a dependency error without fabricating data or resubmitting', async () => {
    requestMock.mockResolvedValue(
      Response.json({ code: 'dependency_unavailable' }, { status: 503 }),
    )

    await expect(consoleClient.business.devices.get({})).rejects.toMatchObject({ status: 503 })

    expect(requestMock).toHaveBeenCalledTimes(1)
  })
})
