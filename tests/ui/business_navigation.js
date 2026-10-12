/*
 * 中文说明（兼容入口）：仅转发到当前的 enterprise_layout.js 企业导航用例。
 * 保留此文件是为了兼容旧的测试调用方式（历史脚本按 business_navigation.js 命名执行）。
 * 运行方式与前置条件同 tests/ui/enterprise_layout.js；本文件自身没有断言。
 */
/* Backward-compatible entry point for the current enterprise navigation checks. */
require('./enterprise_layout.js');
