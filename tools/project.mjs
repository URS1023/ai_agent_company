/** 本机平台的安装、构建和进程入口；原生引擎使用独立部署包。 */
import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const windows = process.platform === "win32";
const python = join(
  root,
  ".venv",
  windows ? "Scripts/python.exe" : "bin/python",
);
const npm =
  process.env.npm_execpath ||
  join(dirname(process.execPath), "node_modules/npm/bin/npm-cli.js");
const env = {
  ...process.env,
  PYTHONPATH: join(root, "services/platform"),
  PYTHONIOENCODING: "utf-8",
};
const children = new Set();
let stopping = false;

function run(command, args, options = {}) {
  return new Promise((resolveRun, reject) => {
    const child = spawn(command, args, {
      cwd: root,
      env,
      stdio: "inherit",
      ...options,
    });
    children.add(child);
    child.once("error", reject);
    child.once("exit", (code) => {
      children.delete(child);
      code === 0 || stopping
        ? resolveRun()
        : reject(new Error(`命令退出，状态码 ${code}`));
    });
  });
}

function requireReady(frontend = false) {
  if (!existsSync(python))
    throw new Error("请先运行 npm run setup 创建项目独立环境");
  if (frontend && !existsSync(join(root, "apps/web/node_modules")))
    throw new Error("缺少前端依赖，请先运行 npm run setup");
  if (
    frontend &&
    !existsSync(join(root, "packages/openwebui-chat/node_modules"))
  )
    throw new Error("缺少聊天组件依赖，请先运行 npm run setup");
}

function stop() {
  if (stopping) return;
  stopping = true;
  for (const child of children) {
    if (!child.pid) continue;
    // Windows必须关闭整棵进程树，避免npm/Vite留下监听进程。
    if (windows)
      spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
        windowsHide: true,
        stdio: "ignore",
      });
    else child.kill("SIGTERM");
  }
}
process.once("SIGINT", stop);
process.once("SIGTERM", stop);

async function main() {
  const action = process.argv[2];
  if (action === "setup") {
    const [major, minor] = process.versions.node.split(".").map(Number);
    if (major < 22 || (major === 22 && minor < 12))
      throw new Error("平台前端需要Node.js 22.12或更新版本");
    if (!existsSync(npm))
      throw new Error("无法定位npm，请通过 npm run setup 运行");
    if (!existsSync(python))
      await run(process.env.AGENT_PYTHON || "python", ["-m", "venv", ".venv"]);
    await run(python, [
      "-c",
      'import sys; assert (3,12) <= sys.version_info < (3,15), "需要Python 3.12—3.14"',
    ]);
    await run(python, [
      "-m",
      "pip",
      "install",
      "-r",
      "services/platform/requirements.lock.txt",
    ]);
    await run(process.execPath, [
      npm,
      "--prefix",
      "packages/openwebui-chat",
      "ci",
      "--ignore-scripts",
      "--no-audit",
      "--no-fund",
      "--fetch-retries=0",
    ]);
    await run(process.execPath, [npm, "--prefix", "apps/web", "ci"]);
    console.log(
      "平台依赖准备完成。开发运行 npm run dev；本机正式入口运行 npm run build 后 npm start。",
    );
  } else if (action === "dev" || action === "start") {
    requireReady(action === "dev");
    if (
      action === "start" &&
      !existsSync(join(root, "apps/web/dist/index.html"))
    )
      throw new Error("请先运行 npm run build");
    const api = run(python, [
      "-m",
      "uvicorn",
      "agent_platform.main:create_app",
      "--factory",
      "--host",
      "127.0.0.1",
      "--port",
      "8080",
      ...(action === "dev"
        ? ["--reload", "--reload-dir", "services/platform/agent_platform"]
        : []),
    ]);
    if (action === "dev")
      await Promise.race([
        api,
        run(process.execPath, [npm, "--prefix", "apps/web", "run", "dev"]),
      ]);
    else await api;
  } else if (action === "build") {
    requireReady(true);
    await run(process.execPath, [npm, "--prefix", "apps/web", "run", "build"]);
  } else if (action === "check") {
    requireReady(true);
    await run(python, [
      "-m",
      "compileall",
      "-q",
      "services/platform/agent_platform",
      "services/platform/migrations",
    ]);
    await run(process.execPath, [
      npm,
      "--prefix",
      "packages/openwebui-chat",
      "run",
      "typecheck",
    ]);
    await run(process.execPath, [
      npm,
      "--prefix",
      "apps/web",
      "run",
      "typecheck",
    ]);
    await run(process.execPath, ["--check", "tools/render_report.mjs"]);
  } else if (action === "test") {
    requireReady();
    await run(python, [
      "-m",
      "pytest",
      "services/platform/tests",
      "-q",
      "--tb=short",
    ]);
  } else if (action === "db-check" || action === "db-upgrade") {
    requireReady();
    await run(python, [
      "tools/manage_database.py",
      action === "db-check" ? "check" : "upgrade",
    ]);
  } else
    throw new Error(
      "支持 setup、dev、start、build、check、test、db-check、db-upgrade",
    );
}

try {
  await main();
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
} finally {
  stop();
}
