/** 本机辅助脚本只从标准输入读一次账号，解析失败不回显输入内容。 */
export function parseLocalUiInput(text) {
  let input;
  try {
    input = JSON.parse(text);
  } catch {
    throw new Error("验收输入格式无效");
  }
  if (
    !input ||
    typeof input.username !== "string" ||
    !input.username ||
    typeof input.password !== "string" ||
    !input.password ||
    typeof input.base_url !== "string"
  )
    throw new Error("验收输入缺少账号或地址");
  let target;
  try {
    target = new URL(input.base_url);
  } catch {
    throw new Error("验收地址无效");
  }
  if (
    target.protocol !== "http:" ||
    !["127.0.0.1", "localhost", "[::1]"].includes(target.hostname) ||
    target.username ||
    target.password ||
    target.search ||
    target.hash ||
    !["", "/"].includes(target.pathname)
  )
    throw new Error("验收地址必须是本机HTTP根地址");
  return {
    base_url: target.origin,
    username: input.username,
    password: input.password,
  };
}
export async function readLocalUiInput() {
  let text = "";
  for await (const chunk of process.stdin) {
    text += chunk.toString();
    if (text.length > 16384) throw new Error("验收输入过长");
  }
  return parseLocalUiInput(text);
}
/** 浏览器错误只保留诊断文本，避免上游异常把输入账号带进收据。 */
export function redactUiMessage(value, input) {
  let text = String(value);
  for (const secret of [input.username, input.password].filter(Boolean))
    text = text.replaceAll(secret, "[已隐藏]");
  return text.slice(0, 2000);
}
