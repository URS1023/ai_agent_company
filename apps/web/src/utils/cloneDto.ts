/** 服务端DTO均为JSON值；解除Vue代理后复制编辑草稿，避免修改列表和历史快照。 */
export function cloneDto<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
