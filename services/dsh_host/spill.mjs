import { createHash, randomUUID } from 'node:crypto';
import { lstat, mkdir, open, readFile, readdir, realpath, rmdir, unlink, utimes } from 'node:fs/promises';
import { dirname, join, relative, resolve } from 'node:path';
import { upstream } from './models.mjs';

const state = globalThis[Symbol.for('company.dsh.host')];
const { SpillStore } = await upstream(state.upstreamRoot, 'packages/spill/spill');
const DEFAULT_PAGE_CHARS = 12000;
const MAX_PAGE_CHARS = 24000;
const SUMMARY_CHARS = 7200;
// 工具回执按会话设置硬上限，避免异常工具或恶意结果把宿主磁盘持续写满。
const DEFAULT_MAX_ENTRY_BYTES = 32 * 1024 * 1024;
const DEFAULT_MAX_SESSION_BYTES = 256 * 1024 * 1024;
const DEFAULT_MAX_SESSION_FILES = 512;
const DEFAULT_RETENTION_MS = 7 * 24 * 60 * 60 * 1000;
const boundedEnv = (name, fallback, maximum = Number.MAX_SAFE_INTEGER) => {
    const parsed = Number.parseInt(process.env[name] || '', 10);
    return Number.isSafeInteger(parsed) && parsed > 0 ? Math.min(parsed, maximum) : fallback;
};
const pointerKey = key => String(key).replaceAll('~', '~0').replaceAll('/', '~1');
const pageIdentity = (reference, pointer, fields) => createHash('sha256')
    .update(JSON.stringify([reference, pointer ?? null, fields ?? null])).digest('hex');
const pageCursor = (identity, offset) => Buffer.from(JSON.stringify({ version: 1, identity, offset })).toString('base64url');

/** 只摘取已有字段；计数限定于返回集合，完整数据始终通过原引用读取。 */
export function structuredOverview(value, reference, source, totalChars) {
    const overview = { format: 'structured_tool_result', reference, source, total_chars: totalChars,
        notice: '这是原结果的结构化概览，样例不是全部记录；集合 complete=true 时 items 已包含该集合全部返回项，无须重读。表索引仅覆盖本次返回的候选表，不能据此断言全库不存在某类业务。read_tool_result 支持 json_pointers 批量读取相关表字段；字段说明通常为 comment，选列须使用回执实际字段名。分页优先使用 next_read，改变字段选择必须从 0 开始。',
        values: {}, collections: [], available_fields: [] };
    const fits = () => JSON.stringify(overview).length <= SUMMARY_CHARS;
    const addValue = (path, candidate) => {
        overview.values[path] = candidate;
        if (!fits()) delete overview.values[path];
    };
    const objects = [['', value]];
    if (value?.data && typeof value.data === 'object' && !Array.isArray(value.data)) objects.push(['/data', value.data]);
    // 宽表的字段会挤掉整个样例；先保留候选表索引，让模型能直接定位单表而非反复翻页。
    for (const [path, object] of objects) {
        if (!Array.isArray(object.tables)) continue;
        const pointer = `${path}/tables`;
        const collection = { json_pointer: pointer, returned_items: object.tables.length, complete: false,
            index: [], index_complete: false, fields: ['tableName', 'tableComment', 'columns'],
            column_fields: ['columnName', 'dataType', 'comment'], batch_read_supported: true };
        overview.collections.push(collection);
        if (!fits()) { overview.collections.pop(); continue; }
        for (const [index, table] of object.tables.entries()) {
            if (!table || typeof table !== 'object') continue;
            const item = { tableName: table.tableName, tableComment: table.tableComment,
                columnCount: Array.isArray(table.columns) ? table.columns.length : undefined,
                json_pointer: `${pointer}/${index}`, columns_pointer: `${pointer}/${index}/columns` };
            collection.index.push(item);
            // 给覆盖范围、状态和口径留一半空间，不能让长索引挤掉检索边界。
            if (JSON.stringify(overview).length > SUMMARY_CHARS / 2) { collection.index.pop(); break; }
        }
        collection.index_complete = collection.index.length === object.tables.length;
    }
    // 状态、口径、来源和交付引用优先于表格展示文本，避免头尾截断漏掉关键字段。
    for (const [path, object] of objects) {
        for (const key of ['status', 'error_code', 'error_summary', 'resultType', 'title',
            'queryId', 'totalCount', 'truncated', 'coverage', 'catalogCoverage', 'scope', 'guidance',
            'summary', 'provenance', 'artifacts', 'statistics', 'retrievalEvidence', 'analysis']) {
            if (Object.hasOwn(object, key)) addValue(`${path}/${pointerKey(key)}`, object[key]);
        }
    }
    for (const [path, object] of objects) {
        for (const [key, candidate] of Object.entries(object)) {
            const pointer = `${path}/${pointerKey(key)}`;
            if (Object.hasOwn(overview.values, pointer) || (path === '' && key === 'data')) continue;
            if (Array.isArray(candidate)) {
                if (key === 'tables' && overview.collections.some(item => item.json_pointer === pointer)) continue;
                const collection = { json_pointer: pointer, returned_items: candidate.length,
                    fields: candidate[0] && typeof candidate[0] === 'object' ? Object.keys(candidate[0]).slice(0, 30) : [] };
                overview.collections.push(collection);
                if (!fits()) { overview.collections.pop(); continue; }
                // 小结果直接保留完整数据，避免四条库存也为读剩余两条多走一轮模型。
                if (candidate.length <= 20 && ['data', 'rows', 'reports', 'problems', 'items'].includes(key)) {
                    collection.items = candidate;
                    collection.complete = true;
                    if (fits()) continue;
                    delete collection.items;
                    delete collection.complete;
                }
                collection.complete = false;
                collection.sample = candidate.slice(0, 2);
                if (!fits()) delete collection.sample;
            } else if (JSON.stringify(candidate).length <= 800) addValue(pointer, candidate);
            else {
                const field = { json_pointer: pointer, type: typeof candidate,
                    ...(typeof candidate === 'string' ? { chars: Array.from(candidate).length } : { fields: Object.keys(candidate).slice(0, 30) }) };
                overview.available_fields.push(field);
                if (!fits()) overview.available_fields.pop();
            }
        }
    }
    return overview;
}

/** JSON Pointer 仅访问自有字段，不能解释代码、继承属性或越过原结果范围。 */
function selectJson(raw, pointer, fields) {
    if (typeof pointer !== 'string' || (pointer !== '' && !pointer.startsWith('/')) || /~(?![01])/u.test(pointer)) throw new Error('json_pointer 必须是有效 JSON Pointer');
    let value = JSON.parse(raw);
    for (const part of pointer === '' ? [] : pointer.slice(1).split('/')) {
        const key = part.replaceAll('~1', '/').replaceAll('~0', '~');
        if (value === null || typeof value !== 'object' || !Object.hasOwn(value, key)) throw new Error('指定 JSON 字段不存在');
        value = value[key];
    }
    if (fields !== undefined) {
        if (!Array.isArray(fields) || fields.length < 1 || fields.length > 30 || fields.some(key => typeof key !== 'string')) throw new Error('fields 需要 1 至 30 个字段名');
        const project = row => {
            if (!row || typeof row !== 'object' || Array.isArray(row)) throw new Error('fields 只适用于对象或对象数组');
            // 兼容模型按 tableComment 推断出的 columnComment，保留已有真实注释而非静默丢失。
            return Object.fromEntries(fields.flatMap(key => Object.hasOwn(row, key) ? [[key, row[key]]]
                : key === 'columnComment' && Object.hasOwn(row, 'columnName') && Object.hasOwn(row, 'comment') ? [[key, row.comment]] : []));
        };
        value = Array.isArray(value) ? value.map(project) : project(value);
    }
    return JSON.stringify(value);
}

/** 大工具结果保存全文，以会话专属引用分页读取，不需要通用文件或 shell 工具。 */
export default class BusinessSpillStore extends SpillStore {
    static inject = ['tools'];
    constructor(ctx) {
        super(ctx);
        this.home = resolve(state.home);
        this.root = join(this.home, 'tool-results');
        this.sessionQueues = new Map();
        // 清理与读写共用会话队列；清理取得队列后必须重新检查最后使用时间。
        this.cleanupTask = this.pruneExpired().catch(error => ctx.logger.warn(`工具回执过期清理失败：${String(error)}`));
        ctx.tools.register({ name: 'read_tool_result', description: '按会话引用读取完整工具结果。可用 json_pointers 在一次调用中读取多个相关表的 columns；fields 只返回所选字段，列说明为 comment。续页直接使用 next_read；改变引用、指针或选列后必须从offset=0读取。未指定指针时读取原始全文。',
            parameters: { type: 'object', properties: { reference: { type: 'string', description: 'company-tool-result: 开头的结果引用' },
                json_pointer: { type: 'string', description: 'JSON Pointer，例如 /data/reports 或 /data/summary；空字符串表示整个 JSON。' },
                json_pointers: { type: 'array', items: { type: 'string' }, minItems: 1, maxItems: 16, description: '同一回执中多个 JSON Pointer，批量返回各指针的内容；不能与 json_pointer 同时使用。' },
                fields: { type: 'array', items: { type: 'string' }, minItems: 1, maxItems: 30, description: '只读取目标对象或对象数组的这些字段，需同时指定 json_pointer。' },
                offset: { type: 'integer', minimum: 0, description: '所选结果的 Unicode 字符偏移，默认 0。' },
                cursor: { type: 'string', description: '上一页返回的 next_cursor；只适用于同一引用、json_pointer和fields，使用时不再填写offset。' },
                limit: { type: 'integer', minimum: 1, maximum: MAX_PAGE_CHARS, description: `单页字符数，默认 ${DEFAULT_PAGE_CHARS}。` } }, required: ['reference'] },
            output: { schema: {}, render: (_, value) => [{ type: 'text', text: JSON.stringify(value) }] },
            isConcurrencySafe: () => true,
            execute: (args, exec) => this.readPage(exec.agent.id, args.reference, args.offset, args.limit, args.json_pointer, args.fields, args.cursor, args.json_pointers) });
        ctx.on('tools/post-execute', async (exec, result, next) => {
            const decision = await next();
            if (decision.kind !== 'accept' || result.isError || Object.hasOwn(decision, 'value') || Object.hasOwn(decision, 'content')) return decision;
            // 读取本身已受单页上限约束；原值回传使原生截断器不再把这一页保存成新的引用。
            if (exec.name === 'read_tool_result') return { ...decision, value: result.value };
            if (exec.parent !== undefined || !result.value || Array.isArray(result.value) || typeof result.value !== 'object') return decision;
            const rendered = result.content;
            if (rendered.length !== 1 || rendered[0].type !== 'text' || rendered[0].text.length <= 8000) return decision;
            let value;
            try { value = JSON.parse(rendered[0].text); } catch { return decision; }
            if (!value || Array.isArray(value) || typeof value !== 'object') return decision;
            const source = { kind: 'tool', toolName: exec.name, callId: exec.callId, label: 'result' };
            try {
                const stored = await this.saveText({ owner: { sessionId: exec.agent.session.header.id }, source, content: rendered[0].text });
                return { ...decision, content: [{ type: 'text', text: JSON.stringify(structuredOverview(value, stored.locator, source, Array.from(rendered[0].text).length)) }] };
            } catch (error) {
                ctx.logger.warn(`结构化工具结果保存失败，保留原始回执：${String(error)}`);
                return decision;
            }
        });
    }
    directory(sessionId) { return join(this.root, createHash('sha256').update(String(sessionId)).digest('hex')); }
    async withSession(directory, action) {
        const path = resolve(directory);
        if (resolve(this.root) !== join(this.home, 'tool-results') || dirname(path) !== this.root || !/^[0-9a-f]{64}$/.test(relative(this.root, path))) {
            throw new Error('工具回执目录超出允许的 tool-results 范围');
        }
        const previous = this.sessionQueues.get(path) || Promise.resolve();
        const operation = previous.catch(() => {}).then(action);
        this.sessionQueues.set(path, operation);
        try { return await operation; }
        finally { if (this.sessionQueues.get(path) === operation) this.sessionQueues.delete(path); }
    }
    async safeDirectory(path) {
        const metadata = await lstat(path);
        // Windows junction 也会被 lstat 标记为符号链接；realpath 同时检查祖先目录跳转。
        if (!metadata.isDirectory() || metadata.isSymbolicLink() || relative(resolve(path), await realpath(path)) !== '') {
            throw new Error('工具回执目录不能包含符号链接或 junction');
        }
        return metadata;
    }
    async ensureRoot() {
        await this.safeDirectory(this.home);
        await mkdir(this.root).catch(error => { if (error?.code !== 'EEXIST') throw error; });
        await this.safeDirectory(this.root);
    }
    async pruneExpired(now = Date.now()) {
        const retention = boundedEnv('DSH_SPILL_RETENTION_MS', DEFAULT_RETENTION_MS, 365 * 24 * 60 * 60 * 1000);
        let entries;
        try {
            await this.safeDirectory(this.root);
            entries = await readdir(this.root, { withFileTypes: true });
        } catch (error) {
            if (error?.code === 'ENOENT') return;
            throw error;
        }
        for (const entry of entries) {
            if (!entry.isDirectory() || entry.isSymbolicLink() || !/^[0-9a-f]{64}$/.test(entry.name)) continue;
            const directory = join(this.root, entry.name);
            await this.withSession(directory, async () => {
                await this.safeDirectory(this.root);
                const metadata = await this.safeDirectory(directory).catch(error => { if (error?.code !== 'ENOENT') throw error; });
                if (!metadata || now - metadata.mtimeMs <= retention) return;
                const children = await readdir(directory, { withFileTypes: true });
                // 仅删除本模块生成的普通文件；不用递归删除，遇到链接、子目录或未知文件保留现场。
                if (children.some(child => !child.isFile() || child.isSymbolicLink() || !/^[0-9a-f-]{36}\.(txt|json)$/.test(child.name))) return;
                for (const child of children) {
                    const path = join(directory, child.name);
                    const file = await lstat(path);
                    if (!file.isFile() || file.isSymbolicLink()) return;
                    await unlink(path);
                }
                await rmdir(directory);
            });
        }
    }
    async sessionUsage(directory) {
        let entries;
        try { entries = await readdir(directory, { withFileTypes: true }); } catch (error) {
            if (error?.code === 'ENOENT') return { bytes: 0, files: 0 };
            throw error;
        }
        let bytes = 0;
        for (const entry of entries) {
            const metadata = await lstat(join(directory, entry.name));
            if (!metadata.isFile() || metadata.isSymbolicLink()) throw new Error('工具回执会话目录包含非普通文件');
            bytes += metadata.size;
        }
        // 元数据、历史遗留文件均占用磁盘；不能只统计正文而让大 source 绕过配额。
        return { bytes, files: entries.length };
    }
    async writeEntryFile(path, content, created) {
        const file = await open(path, 'wx', 0o600);
        created.push(path);
        try { await file.writeFile(content, 'utf8'); }
        finally { await file.close(); }
    }
    async saveText(input) {
        if (!input?.owner?.sessionId || typeof input.content !== 'string') throw new Error('工具回执缺少会话标识或文本内容');
        const bytes = Buffer.byteLength(input.content, 'utf8');
        const maxEntry = boundedEnv('DSH_SPILL_MAX_ENTRY_BYTES', DEFAULT_MAX_ENTRY_BYTES, 256 * 1024 * 1024);
        const maxSession = boundedEnv('DSH_SPILL_MAX_SESSION_BYTES', DEFAULT_MAX_SESSION_BYTES, 1024 * 1024 * 1024);
        const maxFiles = boundedEnv('DSH_SPILL_MAX_SESSION_FILES', DEFAULT_MAX_SESSION_FILES, 10000);
        const metadata = JSON.stringify({ source: input.source, created_at: new Date().toISOString(), bytes });
        const storedBytes = bytes + Buffer.byteLength(metadata, 'utf8');
        if (storedBytes > maxEntry) throw new Error(`工具回执超过单项存储上限（${maxEntry} 字节），请改用分页查询或字段投影`);
        const directory = this.directory(input.owner.sessionId);
        // 同一会话的读、写和过期清理串行执行，避免半份回执或配额并发绕过。
        return this.withSession(directory, async () => {
            await this.ensureRoot();
            await mkdir(directory).catch(error => { if (error?.code !== 'EEXIST') throw error; });
            await this.safeDirectory(directory);
            const usage = await this.sessionUsage(directory);
            if (usage.files + 2 > maxFiles || usage.bytes + storedBytes > maxSession) {
                throw new Error(`当前会话的工具回执存储已达到上限（${maxFiles} 个文件或 ${maxSession} 字节），请先缩小查询结果或分页读取`);
            }
            const id = randomUUID();
            const created = [];
            try {
                await this.writeEntryFile(join(directory, `${id}.txt`), input.content, created);
                await this.writeEntryFile(join(directory, `${id}.json`), metadata, created);
                const accessed = new Date();
                await utimes(directory, accessed, accessed);
            } catch (error) {
                // 只回收本次成功创建的文件，不删除 wx 碰撞时已有的其他回执。
                await Promise.all(created.map(path => unlink(path).catch(cleanupError => {
                    if (cleanupError?.code !== 'ENOENT') throw cleanupError;
                })));
                throw error;
            }
            return { locator: `company-tool-result:${id}`, bytes,
                retrievalHint: `仅在需要缺失明细时使用 read_tool_result；优先 json_pointer 定位字段和 fields 选列。分页从 0 开始，limit 默认 ${DEFAULT_PAGE_CHARS}、最多 ${MAX_PAGE_CHARS}；续页使用 next_read，改变字段选择后不能沿用旧偏移。` };
        });
    }
    async readPage(sessionId, reference, offset = 0, limit = DEFAULT_PAGE_CHARS, jsonPointer, fields, cursor, jsonPointers) {
        const match = /^company-tool-result:([0-9a-f-]{36})$/.exec(String(reference));
        if (!match || !Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > MAX_PAGE_CHARS) throw new Error('工具结果引用或分页范围无效');
        if (jsonPointers !== undefined && (jsonPointer !== undefined || !Array.isArray(jsonPointers) || !jsonPointers.length || jsonPointers.length > 16 || jsonPointers.some(item => typeof item !== 'string'))) throw new Error('json_pointers 须包含 1 至 16 个指针，不能同时指定 json_pointer');
        if (fields !== undefined && jsonPointer === undefined && jsonPointers === undefined) throw new Error('使用 fields 时必须同时指定 json_pointer 或 json_pointers');
        const identity = pageIdentity(reference, jsonPointers ?? jsonPointer, fields);
        const selection = jsonPointers ? { json_pointers: jsonPointers } : jsonPointer === undefined ? {} : { json_pointer: jsonPointer };
        if (cursor !== undefined) {
            let parsed;
            try { parsed = JSON.parse(Buffer.from(String(cursor), 'base64url').toString('utf8')); } catch { throw new Error('分页游标无效，请从 offset=0 重新读取'); }
            if (offset !== 0 || parsed?.version !== 1 || !Number.isSafeInteger(parsed.offset) || parsed.offset < 0) throw new Error('分页游标无效或同时指定了offset，请从 offset=0 重新读取');
            if (parsed.identity !== identity) throw new Error('分页游标不属于当前引用或字段选择；修改 json_pointer/fields 后必须从 offset=0 重新读取');
            offset = parsed.offset;
        }
        const directory = this.directory(sessionId), id = match[1];
        const [raw, metadata] = await this.withSession(directory, async () => {
            await this.safeDirectory(this.root);
            await this.safeDirectory(directory);
            const paths = [join(directory, `${id}.txt`), join(directory, `${id}.json`)];
            for (const path of paths) {
                const file = await lstat(path);
                if (!file.isFile() || file.isSymbolicLink()) throw new Error('工具回执引用不能指向符号链接或非普通文件');
            }
            const result = await Promise.all(paths.map(path => readFile(path, 'utf8')));
            const accessed = new Date();
            await utimes(directory, accessed, accessed);
            return result;
        });
        const projected = jsonPointers ? JSON.stringify(Object.fromEntries(jsonPointers.map(pointer => [pointer, JSON.parse(selectJson(raw, pointer, fields))]))) : jsonPointer === undefined ? raw : selectJson(raw, jsonPointer, fields);
        const points = Array.from(projected);
        if (offset > points.length) throw new Error(`分页偏移超过所选结果长度（${points.length}字符）；字段投影改变后旧偏移失效。恢复参数：${JSON.stringify({ reference, ...selection, ...(fields ? { fields } : {}), offset: 0, limit })}`);
        const next = Math.min(points.length, offset + limit);
        const nextCursor = next < points.length ? pageCursor(identity, next) : null;
        return { reference, source: JSON.parse(metadata).source, offset, next_offset: next < points.length ? next : null,
            next_cursor: nextCursor, next_read: nextCursor ? { reference,
                ...selection, ...(fields ? { fields } : {}),
                cursor: nextCursor, limit } : null,
            ...selection, ...(fields ? { fields } : {}),
            total_chars: points.length, content: points.slice(offset, next).join('') };
    }
}
