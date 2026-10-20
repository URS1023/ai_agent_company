import { api, type Entity, type ListResult } from "../../api/client";

/** 候选资料完整翻页，超过交互预算明确失败，不能把前200项冒充全部。 */
export async function allRecords(path: string): Promise<Entity[]> {
  const items: Entity[] = [];
  for (let offset = 0; ; offset += 200) {
    const result = await api.get<ListResult>(
      `${path}${path.includes("?") ? "&" : "?"}limit=200&offset=${offset}`,
    );
    items.push(...(result.items as Entity[]));
    if (items.length >= result.total) return items;
    if (!result.items.length || items.length >= 2000)
      throw new Error("候选资料过多，请缩小筛选范围");
  }
}
