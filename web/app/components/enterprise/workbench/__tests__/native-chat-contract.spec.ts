import { zPostInstalledAppsByInstalledAppIdChatMessagesBody } from '@dify/contracts/api/console/installed-apps/zod.gen'

describe('Native installed-chat request contract', () => {
  it('should use the installed-app default without debugger fields', () => {
    const body = zPostInstalledAppsByInstalledAppIdChatMessagesBody.parse({
      inputs: {},
      query: 'hello',
    })
    expect(body).toEqual({ inputs: {}, query: 'hello', retriever_from: 'explore_app' })
  })

  it('should preserve conversation and parent context', () => {
    const body = {
      inputs: { department: 'quality' },
      query: 'hello',
      conversation_id: 'fd79994d-4973-44ce-b388-79d303ab9717',
      parent_message_id: '8a1465a4-7ee9-43dd-ac7c-9f73bd3eb3a1',
      files: [{ type: 'image', transfer_method: 'local_file', upload_file_id: 'file-1' }],
    }
    expect(zPostInstalledAppsByInstalledAppIdChatMessagesBody.parse(body)).toEqual({
      ...body,
      retriever_from: 'explore_app',
    })
  })

  it('should reject primitive files accepted by the debugger schema', () => {
    expect(
      zPostInstalledAppsByInstalledAppIdChatMessagesBody.safeParse({
        inputs: {},
        query: 'hello',
        files: ['file-1'],
      }).success,
    ).toBe(false)
  })
})
