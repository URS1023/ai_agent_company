import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { deflateSync } from 'node:zlib';

const root = resolve(fileURLToPath(new URL('../../..', import.meta.url)));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
function pngFixture() {
    const crc = bytes => { let value = 0xffffffff; for (const byte of bytes) { value ^= byte; for (let i = 0; i < 8; i++) value = (value >>> 1) ^ (value & 1 ? 0xedb88320 : 0); } return (value ^ 0xffffffff) >>> 0; };
    const chunk = (type, data) => { const length = Buffer.alloc(4), check = Buffer.alloc(4), payload = Buffer.concat([Buffer.from(type), data]);
        length.writeUInt32BE(data.length); check.writeUInt32BE(crc(payload)); return Buffer.concat([length, payload, check]); };
    const header = Buffer.alloc(13); header.writeUInt32BE(1, 0); header.writeUInt32BE(1, 4); header[8] = 8; header[9] = 2;
    return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', deflateSync(Buffer.from([0, 255, 0, 0]))), chunk('IEND', Buffer.alloc(0))]).toString('base64');
}

test('真实长驻 DSH：工具交付、跨轮历史、队列取消、工作区隔离与重启恢复', { timeout: 90000 }, async t => {
    const home = await mkdtemp(join(tmpdir(), 'company-dsh-host-'));
    // 注入独立的个人目录和预装目录，证明宿主不会泄漏与业务无关的部署机技能。
    const personalSkills = join(home, 'personal', 'skills', 'unregistered-personal-skill');
    const bundledSkills = join(home, 'bundled', 'unregistered-bundled-skill');
    for (const [directory, name] of [[personalSkills, 'unregistered-personal-skill'], [bundledSkills, 'unregistered-bundled-skill']]) {
        await mkdir(directory, { recursive: true });
        await writeFile(join(directory, 'SKILL.md'), `---\nname: ${name}\ndescription: 不应进入企业会话的个人技能\n---\n无关的个人技能。\n`);
    }
    const requests = [];
    const submittedPrompts = new Set(['json auxiliary', 'delegated business check', 'slow delegated business check']);
    const messageText = message => typeof message?.content === 'string' ? message.content
        : (message?.content || []).filter(block => block.type === 'text').map(block => block.text).join('');
    const model = createServer(async (req, res) => {
        let raw = ''; for await (const chunk of req) raw += chunk;
        const body = JSON.parse(raw); requests.push({ body, authorization: req.headers.authorization });
        let latest = messageText(body.messages.findLast(item => item.role === 'user' && submittedPrompts.has(messageText(item)))
            || body.messages.filter(item => item.role === 'user').at(-1));
        // 原生后台委派会把任务封装成带来源说明的用户消息。
        if (body.messages.some(item => item.role === 'user' && messageText(item).includes('slow delegated business check'))) latest = 'slow delegated business check';
        if (req.url.endsWith('/messages')) {
            res.writeHead(200, { 'content-type': 'text/event-stream' });
            for (const item of [
                { type: 'message_start', message: { id: 'msg_1', model: 'fake-model', usage: { input_tokens: 12, output_tokens: 1 } } },
                { type: 'content_block_start', index: 0, content_block: { type: 'text', text: '' } },
                { type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: '原生Messages成功' } },
                { type: 'content_block_stop', index: 0 },
                { type: 'message_delta', delta: { stop_reason: 'end_turn' }, usage: { output_tokens: 5 } },
                { type: 'message_stop' },
            ]) res.write(`event: ${item.type}\ndata: ${JSON.stringify(item)}\n\n`);
            res.end(); return;
        }
        if (String(latest).includes('intentional failure')) {
            res.writeHead(400, { 'content-type': 'application/json' }); res.end('{"error":{"message":"故意模型失败","type":"invalid_request_error"}}'); return;
        }
        if (String(latest).includes('crash active')) await delay(2000);
        if (String(latest).includes('slow')) await delay(600);
        res.writeHead(200, { 'content-type': 'text/event-stream' });
        const loadSkill = String(latest).includes('load skill');
        const readStored = String(latest).includes('read stored result');
        let tool = (String(latest).includes('tool') || loadSkill || readStored) && body.messages.at(-1)?.role !== 'tool';
        let delta = tool ? { role: 'assistant', tool_calls: [{ index: 0, id: `call-${requests.length}`, type: 'function', function: loadSkill
            ? { name: 'skill', arguments: '{"name":"knowledge-answer"}' } : readStored
                ? { name: 'read_tool_result', arguments: JSON.stringify({ reference: JSON.stringify(body).match(/company-tool-result:[0-9a-f-]{36}/)[0], offset: 0, limit: 120 }) }
                : { name: 'company_echo', arguments: JSON.stringify({ value: String(latest).includes('large tool') ? 'LARGE_RESULT' : '真实业务结果' }) } }] }
            : { role: 'assistant', content: String(latest).includes('json auxiliary') ? '{"answer":"ok"}' : '已完成真实回答' };
        const toolSteps = body.messages.filter(message => message.role === 'tool').length;
        let scripted;
        if (String(latest).startsWith('native question') && !toolSteps) scripted = ['ask_user_question', {
            questions: [{ id: 'period', question: '请选择统计月份', options: [{ label: '八月' }, { label: '七月' }] }] }];
        if (latest === 'native compound') scripted = [
            ['todo_write', { todos: [{ content: '查询应收', status: 'in_progress' }, { content: '生成报表', status: 'pending' }] }],
            ['company_echo', { value: '明细与范围' }], ['company_echo', { value: '经核验的报表' }],
            ['todo_write', { todos: [{ content: '查询应收', status: 'completed' }, { content: '生成报表', status: 'completed' }] }],
        ][toolSteps];
        if (latest === 'native delegate' && !toolSteps) scripted = ['subagent', { description: '复核业务回执', prompt: 'delegated business check', run_in_background: false }];
        if (latest === 'native background' && !toolSteps) scripted = ['subagent', { description: '后台复核业务回执', prompt: 'slow delegated business check', run_in_background: true }];
        if (latest === 'delegated business check' && !toolSteps) scripted = ['company_echo', { value: '子代理真实业务回执' }];
        if (latest === 'slow delegated business check' && !toolSteps) scripted = ['company_echo', { value: '后台子代理真实业务回执' }];
        if (scripted) {
            tool = true;
            delta = { role: 'assistant', tool_calls: [{ index: 0, id: `call-${requests.length}`, type: 'function',
                function: { name: scripted[0], arguments: JSON.stringify(scripted[1]) } }] };
        }
        const chunk = { id: `chat-${requests.length}`, object: 'chat.completion.chunk', created: 1, model: 'fake-model', choices: [{ index: 0, delta, finish_reason: null }] };
        res.write(`data: ${JSON.stringify(chunk)}\n\n`);
        if (String(latest).includes('stream delayed')) await delay(500);
        res.write(`data: ${JSON.stringify({ ...chunk, choices: [{ index: 0, delta: {}, finish_reason: tool ? 'tool_calls' : 'stop' }], usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 } })}\n\n`);
        res.end('data: [DONE]\n\n');
    });
    await new Promise(resolve => model.listen(0, '127.0.0.1', resolve));
    let child, logs = '', base;
    async function start() {
        logs = '';
        child = spawn(process.execPath, [join(root, 'services/dsh_host/launch.mjs')], { cwd: root, windowsHide: true,
            env: { ...process.env, DSH_HOME: home, DSH_TEST_WORKER: join(root, 'services/dsh_host/tests/fake-worker.mjs'),
                DSH_PROJECT_ROOT: root, HOST: '127.0.0.1', PORT: '0', FAKE_MODEL_URL: `http://127.0.0.1:${model.address().port}/v1`,
                FAKE_IMAGE_BASE64: pngFixture(), FAKE_STREAM_HISTORY_MAX_RUNS: '1',
                DSH_AGENTS_HOME: join(home, 'personal'), DSH_BUNDLED_SKILL_DIR: join(home, 'bundled'),
                SSL_CERTFILE: '', SSL_KEYFILE: '' }, stdio: ['pipe', 'pipe', 'pipe'] });
        child.stdout.on('data', chunk => { logs += chunk; }); child.stderr.on('data', chunk => { logs += chunk; });
        for (let index = 0; index < 300; index++) {
            const match = logs.match(/\{"event":"dsh.ready"[^\n]*\}/);
            if (match) { base = `http://127.0.0.1:${JSON.parse(match[0]).port}`; return; }
            if (child.exitCode !== null) throw new Error(logs);
            await delay(50);
        }
        throw new Error(`启动超时\n${logs}`);
    }
    async function stop() {
        if (!child || child.exitCode !== null) return;
        const finished = new Promise(resolve => child.once('exit', resolve));
        child.stdin.end('{"method":"shutdown"}\n');
        await Promise.race([finished, delay(5000)]);
        if (child.exitCode === null) { child.kill(); await finished; }
    }
    t.after(async () => { await stop(); model.closeAllConnections(); await new Promise(resolve => model.close(resolve)); await rm(home, { recursive: true, force: true }); });
    await start();
    const send = async (conversation, content, requestId, extra = {}) => {
        submittedPrompts.add(content);
        const response = await fetch(`${base}/api/chat/conversations/${conversation}/send`, { method: 'POST',
            headers: { 'content-type': 'application/json', accept: 'application/json' }, body: JSON.stringify({ content, request_id: requestId, mode: 'standard', ...extra }) });
        const result = await response.json(); assert.equal(response.status, 202, JSON.stringify(result)); return result;
    };
    const waitRun = async run => {
        for (let index = 0; index < 200; index++) {
            const detail = await (await fetch(`${base}/api/chat/conversations/${run.session_id.replace('company-test-', '')}`)).json();
            const task = detail.projections.tasks.find(task => task.id === `task-${run.run_id.slice(4)}`);
            // Worker 结算先落库，随后宿主才发布终态；检查事件顺序时必须等对应 done。
            if (task && !['queued', 'running'].includes(task.status)
                && detail.events.some(entry => entry.event === 'done' && entry.data.run_id === run.run_id)) return { detail, task };
            await delay(30);
        }
        throw new Error(`运行超时\n${logs}\n${await readFile(join(home, 'fake-worker.json'), 'utf8')}`);
    };
    const first = await send('one', 'tool please', 'request-1');
    const completed = await waitRun(first);
    assert.equal(completed.task.status, 'completed', JSON.stringify(completed.detail) + logs);
    assert.equal(completed.detail.projections.deliveries.length, 1);
    assert.ok(completed.detail.events.some(entry => entry.event === 'task.progress'));
    assert.equal(requests.length, 2);
    assert.ok(JSON.stringify(requests[0].body).includes('knowledge-answer'), '真实技能目录应进入原生会话');
    assert.ok(!JSON.stringify(requests[0].body).includes('unregistered-personal-skill'), '不加载个人技能目录');
    assert.ok(!JSON.stringify(requests[0].body).includes('unregistered-bundled-skill'), '不加载环境注入的全局技能目录');
    assert.ok(!logs.includes('leaving no pressure budget'), '32K 原生会话实际装配业务压缩预算，不回落到 64K 默认预留');
    await send('one', 'tool please', 'request-1');
    assert.equal(requests.length, 2, '相同request_id不可重复原生请求');
    const second = await send('one', 'second message', 'request-2');
    assert.equal((await waitRun(second)).task.status, 'completed');
    assert.ok(requests.at(-1).body.messages.filter(item => item.role === 'user').length >= 2);
    const other = await send('two', 'different workspace', 'request-3');
    assert.equal((await waitRun(other)).task.status, 'completed');
    assert.equal(requests.at(-1).authorization, 'Bearer key-two');
    assert.ok(!JSON.stringify(requests.at(-1).body).includes('tool please'));
    const skill = await send('two', 'load skill', 'request-load-skill');
    const skillResult = await waitRun(skill);
    assert.equal(skillResult.task.status, 'completed');
    assert.ok(skillResult.detail.events.some(entry => entry.event === 'tool.completed' && entry.data.tool_name === 'skill'), '原生Skill读取也应进入业务事件流');
    const slow = await send('one', 'slow active', 'request-4');
    const queued = await send('one', 'never execute', 'request-5');
    const removed = await fetch(`${base}/api/chat/conversations/one/queue/${queued.run_id}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{"action":"remove"}' });
    assert.equal(removed.status, 200);
    await waitRun(slow);
    assert.ok(!requests.some(item => JSON.stringify(item.body).includes('never execute')));
    const slowEdit = await send('one', 'slow editing', 'request-edit-active');
    const editable = await send('one', 'original queued', 'request-edit');
    assert.equal((await fetch(`${base}/api/chat/conversations/one/queue/${editable.run_id}`, { method: 'POST',
        headers: { 'content-type': 'application/json' }, body: '{"action":"edit","content":"edited queued"}' })).status, 200);
    await waitRun(slowEdit); await waitRun(editable);
    assert.ok(JSON.stringify(requests.at(-1).body).includes('edited queued'));
    assert.ok(!JSON.stringify(requests.at(-1).body).includes('original queued'));
    const editedHistory = await (await fetch(`${base}/api/chat/conversations/one`)).json();
    const editEvent = editedHistory.events.find(entry => entry.event === 'queue.updated' && entry.data.user_message?.run_id === editable.run_id);
    assert.equal(editEvent.data.user_message.content, 'edited queued');
    assert.equal(editEvent.data.task.title, 'edited queued');
    const failed = await send('one', 'intentional failure', 'request-failure');
    assert.equal((await waitRun(failed)).task.status, 'failed');
    const afterFailure = await send('one', 'continue after error', 'request-after-failure');
    assert.equal((await waitRun(afterFailure)).task.status, 'completed');
    const observer = new AbortController();
    const stream = await fetch(`${base}/api/chat/conversations/one/events`, { signal: observer.signal });
    const streamReader = stream.body.getReader();
    assert.ok(new TextDecoder().decode((await streamReader.read()).value).includes('session.snapshot'));
    const streamed = await send('one', 'stream delayed', 'request-stream');
    let wire = '';
    while (!wire.includes('message.delta')) wire += new TextDecoder().decode((await streamReader.read()).value);
    assert.ok(!wire.includes('event: done'), '正文增量必须早于模型终态送达');
    observer.abort();
    assert.equal((await waitRun(streamed)).task.status, 'completed', '订阅断开不能取消执行');
    const native = await send('native', 'messages protocol', 'request-native', { protocol: 'deepseek-messages', thinking: 'disabled' });
    const nativeResult = await waitRun(native);
    assert.equal(nativeResult.task.status, 'completed', JSON.stringify(nativeResult.detail.events.filter(entry => entry.event === 'run.failed')) + logs);
    assert.deepEqual(requests.at(-1).body.thinking, { type: 'disabled' });
    assert.equal(requests.at(-1).body.max_tokens, 4096);
    const noThinking = await send('plain', 'no reasoning', 'request-plain', { thinking: 'disabled' });
    assert.equal((await waitRun(noThinking)).task.status, 'completed');
    assert.ok(!requests.at(-1).body.reasoning_effort);
    const auxiliary = await (await fetch(`${base}/api/test/aux`)).json();
    assert.deepEqual(JSON.parse(auxiliary.content), { answer: 'ok' });
    assert.equal(requests.at(-1).authorization, 'Bearer key-auxiliary');
    assert.equal(requests.at(-1).body.max_tokens, 2048);
    const vision = await (await fetch(`${base}/api/test/vision`)).json();
    assert.equal(vision.content, '已完成真实回答', JSON.stringify(vision));
    const imagePart = requests.at(-1).body.messages.flatMap(message => Array.isArray(message.content) ? message.content : []).find(part => part.type === 'image_url');
    assert.match(imagePart.image_url.url, /^data:image\/(png|jpeg);base64,/);
    assert.ok(Buffer.from(imagePart.image_url.url.split(',')[1], 'base64').length > 40);
    assert.equal(requests.at(-1).body.max_tokens, 1024);
    const imageChat = await send('image-chat', 'native chat image', 'request-image-chat', { image: true });
    assert.equal((await waitRun(imageChat)).task.status, 'completed');
    assert.ok(requests.at(-1).body.messages.some(message => Array.isArray(message.content) && message.content.some(part => part.type === 'image_url')),
        '声明视觉能力的主对话应携带原生已登记图片');
    const plugin = await (await fetch(`${base}/api/test/plugin`)).json();
    assert.equal(plugin.status, 'completed');
    assert.equal(plugin.content, '已完成真实回答');
    const large = await send('large', 'large tool', 'request-large');
    assert.equal((await waitRun(large)).task.status, 'completed');
    const preview = requests.at(-1).body.messages.filter(message => message.role === 'tool').at(-1).content;
    assert.ok(preview.length < 12000, `大工具结果进入下一轮应限量，实际${preview.length}`);
    assert.match(preview, /company-tool-result:[0-9a-f-]{36}/);
    assert.match(preview, /read_tool_result/);
    const rawDatabase = JSON.parse(await readFile(join(home, 'fake-worker.json'), 'utf8'));
    assert.ok(rawDatabase.runs.find(run => run.run_id === large.run_id).raw_tool_result.content.length > 100000, '业务原始结果应保留');
    const page = await send('large', 'read stored result', 'request-read-large');
    assert.equal((await waitRun(page)).task.status, 'completed');
    const pageResult = JSON.parse(requests.at(-1).body.messages.filter(message => message.role === 'tool').at(-1).content);
    assert.equal(pageResult.source.toolName, 'company_echo');
    assert.equal(pageResult.next_offset, 120);
    assert.equal(pageResult.content.length, 120);
    const legacy = await send('legacy', 'migrate history', 'request-legacy');
    assert.equal((await waitRun(legacy)).task.status, 'completed');
    assert.equal(JSON.stringify(requests.at(-1).body).split('LEGACY_UNIQUE_USER').length - 1, 1);
    const largeLegacy = await send('legacy-large', 'migrate large history', 'request-legacy-large');
    assert.equal((await waitRun(largeLegacy)).task.status, 'completed');
    const largeLegacyWire = JSON.stringify(requests.at(-1).body);
    // 原生标准预设包含完整工具目录，预算只约束迁移历史，不把工具说明误计为历史膨胀。
    const legacyBlocks = requests.at(-1).body.messages.filter(message => messageText(message).includes('旧聊天记录已迁移'));
    assert.ok(legacyBlocks.length > 0 && legacyBlocks.every(message => messageText(message).length < 15000));
    assert.match(largeLegacyWire, /company-tool-result:[0-9a-f-]{36}/);
    assert.ok(!JSON.stringify(requests.at(-1).body.messages.filter(message => message.role === 'system')).includes('LEGACY_UNIQUE_USER'));
    const legacyAgain = await send('legacy', 'continue migration', 'request-legacy-again');
    assert.equal((await waitRun(legacyAgain)).task.status, 'completed');
    assert.equal(JSON.stringify(requests.at(-1).body).split('LEGACY_UNIQUE_USER').length - 1, 1);
    const compound = await send('compound', 'native compound', 'request-compound');
    const compoundResult = await waitRun(compound);
    assert.equal(compoundResult.task.status, 'completed');
    assert.equal(compoundResult.detail.runs.length, 1, '一句话的多个步骤必须留在同一原生运行');
    assert.equal(compoundResult.detail.events.filter(entry => entry.event === 'tool.completed' && entry.data.tool_name === 'company_echo').length, 2);
    assert.deepEqual(compoundResult.detail.projections.todos.map(item => item.status), ['completed', 'completed']);
    const delegated = await send('delegate', 'native delegate', 'request-delegate');
    const delegatedResult = await waitRun(delegated);
    assert.equal(delegatedResult.task.status, 'completed');
    assert.ok(delegatedResult.detail.events.some(entry => entry.event === 'tool.completed' && entry.data.result?.content === '子代理真实业务回执'),
        JSON.stringify(delegatedResult.detail.events.filter(entry => entry.event.startsWith('tool.'))) + logs);
    const background = await send('background', 'native background', 'request-background');
    const backgroundResult = await waitRun(background);
    assert.equal(backgroundResult.task.status, 'completed');
    assert.ok(backgroundResult.detail.events.some(entry => entry.event === 'tool.completed' && entry.data.result?.content === '后台子代理真实业务回执'),
        '宿主必须等原生后台子代理完成，不能在父代理暂时空闲时提前结算\n' + JSON.stringify(backgroundResult.detail.events.filter(entry => entry.event.startsWith('tool.'))) + logs);
    const backgroundEvents = backgroundResult.detail.events;
    assert.ok(backgroundEvents.findIndex(entry => entry.event === 'run.completed') > backgroundEvents.findIndex(entry => entry.data.result?.content === '后台子代理真实业务回执'));
    const waitQuestion = async cid => {
        for (let index = 0; index < 200; index++) {
            const value = await (await fetch(`${base}/api/chat/conversations/${cid}`)).json();
            if (value.projections.pending_questions.length) return value.projections.pending_questions[0];
            await delay(30);
        }
        throw new Error(`原生提问未送达 ${logs}`);
    };
    const questioning = await send('question', 'native question', 'request-question');
    const question = await waitQuestion('question'), questionCalls = requests.length;
    const answer = { answers: [{ id: 'period', selected: ['八月'] }] };
    const postAnswer = (cid, value) => fetch(`${base}/api/chat/conversations/${cid}/questions/${question.question_id}`, {
        method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(value) });
    assert.equal((await postAnswer('different', answer)).status, 404);
    assert.equal((await postAnswer('question', { answers: [{ id: 'period', selected: ['不存在'] }] })).status, 422);
    assert.equal(requests.length, questionCalls, '回答之前不能自动选择选项或继续模型');
    assert.equal((await postAnswer('question', answer)).status, 200);
    assert.equal((await waitRun(questioning)).task.status, 'completed');
    assert.equal((await postAnswer('question', answer)).status, 200, '重复提交同一回答必须幂等');
    const cancelQuestion = await send('question-cancel', 'native question cancel', 'request-question-cancel');
    await waitQuestion('question-cancel');
    assert.equal((await fetch(`${base}/api/chat/runs/${cancelQuestion.run_id}/cancel`, { method: 'POST' })).status, 200);
    const cancelledQuestion = await waitRun(cancelQuestion);
    assert.equal(cancelledQuestion.task.status, 'cancelled');
    assert.equal(cancelledQuestion.detail.projections.pending_questions.length, 0);
    const steering = await send('steering', 'slow original task', 'request-steering-original');
    for (let index = 0; index < 200 && !requests.some(item => item.body.messages.some(message => messageText(message) === 'slow original task')); index++) await delay(10);
    const steered = await send('steering', '改为七月份', 'request-steering-change', { delivery_mode: 'steer' });
    assert.equal(steered.run_id, steering.run_id);
    const steeredResult = await waitRun(steering);
    assert.equal(steeredResult.task.status, 'completed');
    assert.equal(steeredResult.detail.runs.length, 1);
    assert.equal(steeredResult.detail.messages.filter(item => item.role === 'user').length, 2);
    assert.ok(requests.at(-1).body.messages.some(message => messageText(message) === '改为七月份'));
    const cancelActive = await send('one', 'slow active cancel', 'request-cancel-active');
    await delay(80);
    assert.equal((await fetch(`${base}/api/chat/runs/${cancelActive.run_id}/cancel`, { method: 'POST' })).status, 200);
    assert.equal((await waitRun(cancelActive)).task.status, 'cancelled');
    const deleteActive = await send('archive', 'slow delete active', 'request-delete-active');
    const deleteQueued = await send('archive', 'never archive queued', 'request-delete-queued');
    const mismatch = await fetch(`${base}/api/chat/conversations/one/queue/${deleteQueued.run_id}`, { method: 'POST',
        headers: { 'content-type': 'application/json' }, body: '{"action":"remove"}' });
    assert.equal(mismatch.status, 409);
    assert.equal((await fetch(`${base}/api/chat/conversations/archive`, { method: 'DELETE' })).status, 200);
    assert.equal((await waitRun(deleteActive)).task.status, 'cancelled');
    assert.equal((await waitRun(deleteQueued)).task.status, 'cancelled');
    assert.ok(!requests.some(item => JSON.stringify(item.body).includes('never archive queued')));
    const beforeRestart = await (await fetch(`${base}/api/chat/conversations/one`)).json();
    assert.notEqual(beforeRestart.runs.at(-1).run_id, afterFailure.run_id,
        '回放目标必须在单运行窗口之外，才能覆盖旧终态运行的连接收尾');
    await stop(); await start();
    const resumedOldStream = await fetch(`${base}/api/chat/runs/${afterFailure.run_id}/events?after=${beforeRestart.cursor}`, {
        signal: AbortSignal.timeout(3000),
    });
    const resumedOldText = await resumedOldStream.text();
    assert.match(resumedOldText, /event: done/);
    const oldDone = resumedOldText.split('\n').find(line => line.startsWith('data: '));
    assert.equal(JSON.parse(oldDone.slice(6)).status, 'completed', '窗口外已完成任务也必须正确收尾，不能借用最新取消任务的终态');
    const legacyRestart = await send('legacy', 'migration after restart', 'request-legacy-restart');
    assert.equal((await waitRun(legacyRestart)).task.status, 'completed');
    assert.equal(JSON.stringify(requests.at(-1).body).split('LEGACY_UNIQUE_USER').length - 1, 1);
    const resumed = await send('one', 'after restart', 'request-6');
    assert.equal((await waitRun(resumed)).task.status, 'completed');
    assert.ok(JSON.stringify(requests.at(-1).body).includes('second message'));
    const crashing = await send('one', 'crash active', 'request-crash');
    const pending = await send('one', 'pending restart', 'request-pending');
    for (let index = 0; index < 100 && !JSON.stringify(requests.at(-1).body).includes('crash active'); index++) await delay(10);
    const exits = new Promise(resolve => child.once('exit', resolve));
    child.kill(); await exits; await start();
    assert.equal((await waitRun(crashing)).task.status, 'interrupted');
    assert.equal((await waitRun(pending)).task.status, 'completed');
    assert.equal(requests.filter(item => String(item.body.messages.filter(message => message.role === 'user').at(-1)?.content).includes('crash active')).length, 1,
        '崩溃后不得自动重放已经进入原生会话的操作');
});
