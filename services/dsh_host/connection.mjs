import { upstream } from './models.mjs';
const state = globalThis[Symbol.for('company.dsh.host')];
const { HostConnectionService } = await upstream(state.upstreamRoot, 'packages/client/connection');
export const name = 'company-private-native-connection';
/** 只提供原生附件/RPC内部注册表，企业HTTP身份由业务Worker统一验证。 */
export function apply(ctx) {
    new HostConnectionService(ctx, [], {
        isAuthenticated: () => false,
        authorizeIndex: (_request, response) => { response.writeHead(404); response.end(); return false; },
        authenticatedUrl: () => { throw new Error('企业宿主不公开原生浏览器RPC入口'); },
    });
}
