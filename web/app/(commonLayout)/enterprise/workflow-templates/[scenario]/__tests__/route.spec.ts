import { GET } from '../route'

const flag = vi.hoisted(() => ({ enabled: true }))
vi.mock('@/env', () => ({
  env: {
    get NEXT_PUBLIC_ENABLE_ENTERPRISE_PORTAL() {
      return flag.enabled
    },
  },
}))

beforeEach(() => {
  flag.enabled = true
})

it.each(['alert', 'quality'])(
  'should download the editable %s template without creating an application',
  async (scenario) => {
    const response = await GET(new Request('http://localhost'), {
      params: Promise.resolve({ scenario }),
    })
    const dsl = JSON.parse(await response.text())

    expect(response.status).toBe(200)
    expect(response.headers.get('Content-Disposition')).toBe(
      `attachment; filename="default-${scenario}.yml"`,
    )
    expect(response.headers.get('Cache-Control')).toBe('private, no-store')
    expect(dsl.app.mode).toBe('workflow')
    expect(dsl.workflow.graph.nodes[1].data.credential_id).toBeUndefined()
    expect(dsl.workflow.graph.nodes[2].data.outputs[0]).toEqual({
      variable: 'result',
      value_type: 'string',
      value_selector: ['assessment', 'text'],
    })
  },
)

it('should keep template routes disabled when the enterprise feature is off', async () => {
  flag.enabled = false
  const response = await GET(new Request('http://localhost'), {
    params: Promise.resolve({ scenario: 'alert' }),
  })
  expect(response.status).toBe(404)
})

it.each(['other', '../alert', 'alert.yml', 'ALERT'])(
  'should reject an unsupported scenario %s',
  async (scenario) => {
    const response = await GET(new Request('http://localhost'), {
      params: Promise.resolve({ scenario }),
    })
    expect(response.status).toBe(404)
  },
)
