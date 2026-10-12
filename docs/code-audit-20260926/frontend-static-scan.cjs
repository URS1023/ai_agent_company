/* 中文说明：扫描全部维护中的前端源码语法、调用特征及当前构建入口的资源依赖，不执行应用代码。 */
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const ts = require(path.join(root, 'apps/analytics/node_modules/typescript'));
const compiler = require(path.join(root, 'apps/analytics/node_modules/@vue/compiler-sfc'));
const rows = [];
const walk = folder => fs.readdirSync(folder, { withFileTypes: true }).flatMap(entry => entry.isDirectory() ? walk(path.join(folder, entry.name)) : [path.join(folder, entry.name)]);
const files = [...walk(path.join(root, 'apps/analytics/src')), ...walk(path.join(root, 'apps/web/assets/platform')), ...fs.readdirSync(path.join(root, 'apps/web')).map(name => path.join(root, 'apps/web', name)).filter(file => fs.statSync(file).isFile()), ...walk(path.join(root, 'tests/ui'))].filter(file => /\.(?:js|mjs|cjs|ts|vue|html|css|json|py)$/.test(file));
for (const file of files) {
  const source = fs.readFileSync(file, 'utf8');
  const relative = path.relative(root, file).replaceAll('\\', '/');
  const extension = path.extname(file);
  let scripts = /\.(?:js|mjs|cjs|ts)$/.test(extension) ? [source] : [];
  let errors = [];
  if (extension === '.vue') {
    const parsed = compiler.parse(source, { filename: file });
    errors = parsed.errors.map(String);
    scripts = [parsed.descriptor.script?.content, parsed.descriptor.scriptSetup?.content].filter(Boolean);
  }
  for (const script of scripts) {
    const ast = ts.createSourceFile(file, script, ts.ScriptTarget.Latest, true, extension === '.ts' || extension === '.vue' ? ts.ScriptKind.TS : ts.ScriptKind.JS);
    errors.push(...ast.parseDiagnostics.map(error => ts.flattenDiagnosticMessageText(error.messageText, ' ')));
  }
  rows.push({ path: relative, lines: source.split('\n').length, errors, requests: (source.match(/\b(?:fetch|post|put|del|get|request)\(/g) || []).length, htmlSinks: (source.match(/innerHTML|outerHTML|insertAdjacentHTML|v-html/g) || []).length, storage: (source.match(/localStorage|sessionStorage/g) || []).length, timers: (source.match(/setInterval|setTimeout/g) || []).length });
}
const web = path.join(root, 'apps/web');
const visited = new Set(), missing = [];
function scanAsset(file) {
  if (visited.has(file)) return;
  visited.add(file);
  if (!fs.existsSync(file)) { missing.push(path.relative(web, file)); return; }
  if (!/\.(?:js|css|html)$/.test(file)) return;
  const source = fs.readFileSync(file, 'utf8');
  for (const match of source.matchAll(/["']([^"'\s]+\.(?:js|css))(?:\?[^"']*)?["']/g)) {
    const reference = match[1];
    if (/^(?:https?:|data:|@)/.test(reference)) continue;
    if (!reference.startsWith('.') && !reference.startsWith('/') && !reference.startsWith('assets/')) continue;
    const resolved = reference.startsWith('/') ? path.join(web, reference) : reference.startsWith('assets/') ? path.join(web, 'assets/analytics', reference) : path.resolve(path.dirname(file), reference);
    scanAsset(resolved);
  }
}
scanAsset(path.join(web, 'assets/analytics/index.html'));
const output = { count: rows.length, syntaxErrorFiles: rows.filter(row => row.errors.length), reachableCurrentBuildFiles: visited.size, currentBuildFiles: [...visited].map(file => path.relative(web, file).replaceAll('\\', '/')), missingCurrentBuildFiles: missing, rows };
fs.writeFileSync(path.join(__dirname, 'frontend-static-scan.json'), JSON.stringify(output, null, 2));
console.log(JSON.stringify({ count: output.count, syntaxErrorFiles: output.syntaxErrorFiles, reachableCurrentBuildFiles: output.reachableCurrentBuildFiles, missingCurrentBuildFiles: missing }, null, 2));
