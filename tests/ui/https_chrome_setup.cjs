/*
 * 中文说明（HTTPS 浏览器环境的证书固定辅助，非独立用例）：
 * 用途：把测试服务器的公钥指纹写入一次性 Chrome 用户目录（仅该临时 profile 生效），
 *   使浏览器信任测试证书；证书链与 IP 由 Python 侧（tests/ui/lan_https_suite.py）预先校验。
 * 约束：绝不修改系统证书库或用户长期使用的浏览器配置，只操作一次性 profile。
 */
"use strict";
// Pin this test server's public key in disposable Chrome profiles only.
// Python validates the actual certificate chain and IP before launching tests.
const fs = require("node:fs");
const crypto = require("node:crypto");
const puppeteer = require("puppeteer-core");
const certificatePath = process.env.TEST5_E2E_TLS_CERTFILE;
if (certificatePath) {
  const cert = new crypto.X509Certificate(fs.readFileSync(certificatePath));
  const spki = cert.publicKey.export({ type: "spki", format: "der" });
  const pin = crypto.createHash("sha256").update(spki).digest("base64");
  const launch = puppeteer.launch.bind(puppeteer);
  require.cache[require.resolve("puppeteer-core")].exports = {
    ...puppeteer,
    launch: (options = {}) => launch({
      ...options,
      args: [...(options.args || []), "--no-proxy-server", "--ignore-certificate-errors-spki-list=" + pin],
    }),
  };
}
