// 原生平台验收只允许本机桩和本轮临时宿主，不访问在用平台端口。
import net from 'node:net';
const connect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function (...args) {
    const first = Array.isArray(args[0]) ? args[0][0] : args[0];
    const target = first && typeof first === 'object' ? first : { port: first, host: args[1] };
    if (!['127.0.0.1', '::1'].includes(target.host) || [9, 8011, 8012].includes(Number(target.port))) {
        throw new Error('DSH 隔离验收禁止外部网络及在用平台连接');
    }
    return connect.apply(this, args);
};
