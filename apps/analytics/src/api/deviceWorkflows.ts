/**
 * 设备工作流入口接口层（/api/v1/workflows/device-entry）。
 * 职责：业务页面点击"配置工作流"时，按 deviceId + domain 打开（必要时自动创建）设备专属工作流，
 *   返回可编辑的工作流与路由路径，前端据此跳转编辑页。
 * 约束：domain 只能是 'alert' | 'quality'，与服务端校验保持一致；created=true 表示本次新建，需提示用户。
 */
import { post } from './request'
import type { AlertRecord } from './alerts'
import type { Workflow } from './workflows'

export type BusinessDeviceDomain = 'alert' | 'quality'

export interface DeviceWorkflowEntry {
  bindingId: string
  device: AlertRecord
  domain: BusinessDeviceDomain
  provider: 'platform' | 'dify'
  providerAppId: string | null
  workflow: Workflow
  editorPath: string
  created: boolean
}

export const deviceWorkflowPath = (domain: BusinessDeviceDomain, deviceId: string) =>
  `/${domain === 'alert' ? 'alerts' : 'quality'}/devices/${encodeURIComponent(deviceId)}/workflow`

export const deviceWorkflows = {
  /**
   * 打开（必要时创建）设备工作流：POST /api/v1/workflows/device-entry。
   * 参数：deviceId（台账 id）、domain（'alert' | 'quality'，决定归属域与权限）、signal（组件卸载时取消）。
   * 返回：DeviceWorkflowEntry，含 workflow、editorPath（跳转用）、created（本次是否新建，需提示"已自动创建工作流"）。
   */
  open: (deviceId: string, domain: BusinessDeviceDomain, signal?: AbortSignal) =>
    post<{ data: DeviceWorkflowEntry }>('/api/v1/workflows/device-entry', { deviceId, domain }, { signal }),
}
