/**
 * 设备工作流入口的组合式函数：进入设备工作流页时打开（或创建）该设备的工作流。
 * 状态：entry（DeviceWorkflowEntry，含 workflow 与 editorPath）、loading、error。
 * 加载/取消策略：持有单一 AbortController，deviceId/domain 变化时先 abort 再重发；
 *   响应回来时校验 controller 引用与 stopped，避免旧请求覆盖新设备的数据。
 * 副作用：open 接口会在后端自动创建草稿工作流；created=true 时提示"已为此设备创建工作流草稿"。
 * 错误暴露：error 字符串（页面提示条）；不抛出，页面据 entry 为空渲染空态。
 */
import { onBeforeUnmount, shallowRef, watch, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import { deviceWorkflows, type BusinessDeviceDomain, type DeviceWorkflowEntry } from '../api/deviceWorkflows'

export function useDeviceWorkflowEntry(deviceId: Ref<string>, domain: Ref<BusinessDeviceDomain>) {
  const entry = shallowRef<DeviceWorkflowEntry | null>(null)
  const loading = shallowRef(false)
  const error = shallowRef('')
  let controller: AbortController | null = null
  let stopped = false

  async function load() {
    controller?.abort()
    controller = new AbortController()
    const active = controller
    loading.value = true
    error.value = ''
    entry.value = null
    try {
      const response = await deviceWorkflows.open(deviceId.value, domain.value, active.signal)
      if (stopped || active !== controller) return
      entry.value = response.data
      if (response.data.created) ElMessage.success('已为此设备创建工作流草稿')
    } catch (cause) {
      if (!active.signal.aborted && !stopped) error.value = (cause as Error).message
    } finally {
      if (!stopped && active === controller) loading.value = false
    }
  }

  watch([deviceId, domain], () => void load(), { immediate: true })
  onBeforeUnmount(() => { stopped = true; controller?.abort() })

  return { entry, loading, error, load }
}
