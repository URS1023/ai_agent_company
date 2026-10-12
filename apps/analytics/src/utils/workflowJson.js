/**
 * workflowJson —— 工作流配置的 JSON 读写工具（宽松解析 + 精确数值/常量格式化）。
 *
 * 位置：节点表单的"原始配置/表达式 JSON"编辑、规则常量输入、HTTP 请求体与工具参数解析。
 * 关键约束：
 * - 解析必须保持 JSON 类型不变：超长小数与编号类字符串不能被转成浮点数（浏览器 Number 会静默丢精度），
 *   decimalIdentity 用字符串规范化来识别"会被浏览器改变的数值"并拒绝；
 * - 精度异常必须抛错让用户改用引号包裹，绝不"吞掉"精度问题后照常提交；
 * - 本模块为纯函数，无副作用、无网络依赖。
 */

/** Reject JSON numbers that the browser would silently change before submission. */
function decimalIdentity(token) {
  const match = /^(-?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(token);
  const fraction = match[3] || '';
  const digits = (match[2] + fraction).replace(/^0+/, '');
  if (!digits) return '0';
  const coefficient = digits.replace(/0+$/, '');
  const exponent = BigInt(match[4] || '0') - BigInt(fraction.length) + BigInt(digits.length - coefficient.length);
  return match[1] + coefficient + 'e' + exponent;
}

/**
 * 解析工作流 JSON 文本，并在解析后复核每个数值 token 的精度是否被浏览器改变。
 * @param {string} text JSON 文本（上限 1 MB）
 * @returns {any} 解析结果，类型保持与 JSON 一致
 * @throws {SyntaxError} JSON 语法错误；{RangeError} 体积超限或数值精度不安全（提示用户用引号包裹）
 */

/**
 * Keep JSON types unchanged. Exact decimal/identifier strings remain strings.
 * Numeric operators in the Python engine accept quoted decimal strings; typed
 * equality deliberately distinguishes those strings from JSON numbers.
 */
export function parseWorkflowJson(text) {
  if (text.length > 1024 * 1024) throw new RangeError('JSON 内容不能超过 1 MB');
  const value = JSON.parse(text);
  // Valid JSON is tokenized only after syntax checking. Quoted values (including
  // escaped quotes) consume one token, so their digits are never reinterpreted.
  const tokens = text.matchAll(/"(?:[^"\\]|\\[\s\S])*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/g);
  for (const [token] of tokens) {
    if (token.startsWith('"')) continue;
    const numeric = Number(token);
    if (token.length > 2048 || !Number.isFinite(numeric) ||
      (Number.isInteger(numeric) && !Number.isSafeInteger(numeric)) ||
      decimalIdentity(token) !== decimalIdentity(String(numeric))) {
      throw new RangeError('数值超出浏览器可安全传递的精度，未应用该输入。请用双引号包裹精确小数或编号；数值比较支持小数字符串，等于/不等于会区分文本与数值类型。');
    }
  }
  return value;
}

/**
 * 解析规则常量：普通未加引号的文本按原样返回（语法错误时退化为字符串），
 * 但精度类错误必须继续抛出，不能被当成普通文本吞掉。
 */

/** Ordinary unquoted text is allowed, but precision errors are never swallowed. */
export function parseRuleConstant(text) {
  try { return parseWorkflowJson(text); }
  catch (cause) { if (cause instanceof SyntaxError) return text; throw cause; }
}

/**
 * 把常量格式化回输入框文本：必须保留引号，否则 "123" 这类文本常量再次提交时会变成数字。
 */

/** Quotes must survive editing so text such as "123" cannot turn into a number. */
export function formatRuleConstant(value) {
  return JSON.stringify(value) ?? '';
}
