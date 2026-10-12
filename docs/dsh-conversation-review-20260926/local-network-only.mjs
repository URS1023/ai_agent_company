// 本次离线验证仅允许连接本进程创建的 IPv4 回环模型桩。
import net from 'node:net';
const connect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function (...args) {
    const first = Array.isArray(args[0]) ? args[0][0] : args[0];
    const host = first && typeof first === 'object' ? first.host : args[1];
    if (host !== '127.0.0.1') throw new Error('离线审查禁止非指定回环连接');
    return connect.apply(this, args);
};
