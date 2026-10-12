import { pathToFileURL } from 'node:url';
import { join } from 'node:path';

/** 进程内共享初始化与通信对象，不向磁盘写入模型密钥。 */
export const state: any = (globalThis as any)[Symbol.for('company.harness.runtime')];

/** 从校验过的上游安装加载公开包入口，避免加载另一份 Cordis。 */
export async function upstream(relative: string): Promise<any> {
    return import(pathToFileURL(join(state.upstreamRoot, relative, 'lib/index.js')).href);
}
