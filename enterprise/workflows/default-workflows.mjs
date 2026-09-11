// Editable native DSL assets. Importing does not install, publish or bind a workflow.
export function buildDefaultWorkflow(scenario, { credentialId } = {}) {
  if (scenario !== 'alert' && scenario !== 'quality') throw new Error('unsupported scenario')
  if (
    credentialId !== undefined &&
    (typeof credentialId !== 'string' ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(credentialId) ||
      credentialId === '00000000-0000-0000-0000-000000000000')
  )
    throw new Error('a canonical nonzero credential UUID is required')
  const title = scenario === 'alert' ? '设备预警' : '设备质检'
  const node = (id, x, data) => ({
    id,
    type: 'custom',
    width: 244,
    height: 98,
    position: { x, y: 180 },
    positionAbsolute: { x, y: 180 },
    sourcePosition: 'right',
    targetPosition: 'left',
    data: { desc: '', selected: false, ...data },
  })
  const edge = (source, target, sourceType, targetType) => ({
    id: `${source}-source-${target}-target`,
    source,
    target,
    sourceHandle: 'source',
    targetHandle: 'target',
    type: 'custom',
    zIndex: 0,
    data: { sourceType, targetType, isInIteration: false, isInLoop: false },
  })
  return {
    version: '0.6.0',
    kind: 'app',
    dependencies: [],
    app: {
      name: `${title} · 默认流程`,
      mode: 'workflow',
      icon: scenario === 'alert' ? '🔔' : '🔬',
      icon_background: '#E0F2FE',
      use_icon_as_answer_icon: false,
      description:
        '读取绑定的数据源并按登记规则评估，原样返回完整结果。使用前安装企业设备评估插件、选择凭据、发布并登记此工作流版本；编辑器试运行不代替企业运行。',
    },
    workflow: {
      conversation_variables: [],
      environment_variables: [],
      features: {
        file_upload: { enabled: false },
        opening_statement: '',
        suggested_questions: [],
        suggested_questions_after_answer: { enabled: false },
        speech_to_text: { enabled: false },
        text_to_speech: { enabled: false },
        retriever_resource: { enabled: false },
        sensitive_word_avoidance: { enabled: false },
      },
      graph: {
        nodes: [
          node('start', 40, { type: 'start', title: '开始', variables: [] }),
          node('assessment', 380, {
            type: 'tool',
            title: `${title}评估`,
            provider_id: 'enterprise/enterprise_device_assessment/enterprise_device',
            provider_name: 'enterprise_device',
            provider_type: 'builtin',
            tool_name: 'evaluate_device',
            tool_label: '评估登记设备',
            tool_node_version: '2',
            tool_configurations: {},
            tool_parameters: {},
            ...(credentialId === undefined ? {} : { credential_id: credentialId }),
          }),
          node('end', 720, {
            type: 'end',
            title: '返回业务结果',
            outputs: [
              { variable: 'result', value_type: 'string', value_selector: ['assessment', 'text'] },
            ],
          }),
        ],
        edges: [
          edge('start', 'assessment', 'start', 'tool'),
          edge('assessment', 'end', 'tool', 'end'),
        ],
        viewport: { x: 0, y: 0, zoom: 0.85 },
      },
    },
  }
}
