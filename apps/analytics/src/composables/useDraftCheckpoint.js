/**
 * 表单草稿检查点：判断"当前内容相对于上次已提交快照是否被改动"，用于离开页面/切换项时提示未保存。
 * 用法：readDraft 为读取当前草稿的函数；返回 capture（取快照字符串）、hasUnsavedChanges（计算属性）、markSaved（记录已提交快照）。
 * 约束：markSaved 必须接收"异步请求发起时"的快照，否则请求返回后再比较会把用户期间的新改动误判为已保存。
 */
import { computed, ref } from 'vue'

// Save the submitted snapshot, not the live state when an async request finishes.
/** 记录已提交的快照；不传参时以"当前草稿"作为快照（用于同步保存场景）。 */
export function useDraftCheckpoint(readDraft) {
  const checkpoint = ref(null)
  const capture = () => JSON.stringify(readDraft())
  const hasUnsavedChanges = computed(() => checkpoint.value !== null && capture() !== checkpoint.value)
  const markSaved = (submitted = capture()) => { checkpoint.value = submitted }
  return { capture, hasUnsavedChanges, markSaved }
}
