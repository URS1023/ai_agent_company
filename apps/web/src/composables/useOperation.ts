import { ref, shallowRef } from "vue";
import { ElMessage } from "element-plus";
import { ApiError } from "../api/client";
let feedback: ReturnType<typeof ElMessage> | undefined;
function showFeedback(message: string, type: "success" | "error") {
  feedback?.close();
  feedback = ElMessage({
    message,
    type,
    duration: type === "success" ? 1800 : 5000,
    showClose: true,
  });
}

/** 统一繁忙、字段错误和失败反馈；仅实际请求完成后才显示成功。 */
export function useOperation() {
  const busy = shallowRef(false);
  const error = shallowRef("");
  const fields = ref<Record<string, string>>({});
  async function run<T>(
    action: () => Promise<T>,
    success?: string,
  ): Promise<T | undefined> {
    if (busy.value) return;
    busy.value = true;
    error.value = "";
    fields.value = {};
    try {
      const result = await action();
      if (success) showFeedback(success, "success");
      return result;
    } catch (e) {
      if (e === "cancel" || e === "close") return;
      error.value = e instanceof Error ? e.message : "操作失败";
      if (e instanceof ApiError) fields.value = e.fields;
      showFeedback(error.value, "error");
    } finally {
      busy.value = false;
    }
  }
  return { busy, error, fields, run };
}
