/*
 * 中文说明（知识工作台的兼容入口）：
 * 保留此文件是为了兼容既有 npm 脚本（旧命名 / 旧断言目标）。
 * 现在的正式验收由 tests/ui/content_governance_workspace.js 承担：它同时覆盖文件治理与紧凑版知识工作台，
 * 而不再去断言已移除的装饰性卡片 UI。
 * 运行方式与前置条件同 content_governance_workspace.js；本文件自身没有断言。
 */
"use strict";

// Compatibility entry point retained for the existing npm script. The
// canonical acceptance now covers both file governance and the compact
// knowledge workspace instead of asserting the removed decorative card UI.
require("./content_governance_workspace.js");
