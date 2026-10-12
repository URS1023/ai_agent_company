const artifactUrl = /^\/api\/artifacts\/[A-Za-z0-9_-]+\/download$/;

/** 已登记业务工件直接复用下载引用，不能把接口 URL 交给原生文件系统查找。 */
export function installArtifactPresent(ctx, { active, reference, publish,
    boundary = exec => ctx.sessionProjections.stateOf(exec.agent.session, 'turnBoundary') }) {
    const saved = new WeakMap();
    const disposeExecute = ctx.on('tools/execute', async (exec, next) => {
        if (exec.name !== 'present' || !Array.isArray(exec.arguments?.files)) return next();
        const files = exec.arguments.files;
        const referenced = files.filter(file => artifactUrl.test(String(file?.path || '')));
        if (!referenced.length) return next();
        exec.signal.throwIfAborted();
        if (!active()) throw new Error('该文件交付所属业务运行已结束');
        if (referenced.length !== files.length) throw new Error('业务下载链接已对应文件卡片；请仅对本地文件另行调用 present，无须查找或复制已生成的业务工件。');
        const turn = boundary(exec);
        if (!turn || turn.openTurnStartSeq === null) throw new Error('工件交付需要当前活动对话轮次');
        const delivery = await reference(files);
        for (const artifact of delivery.artifacts || []) await publish(artifact);
        exec.signal.throwIfAborted();
        saved.set(exec, delivery);
        // 沿用原生 present 的输出协议，补充回执在 post-execute 阶段展示。
        return { isError: false, value: { turn: turn.lastTurn, files }, content: [] };
    });
    const disposeResult = ctx.on('tools/post-execute', async (exec, result, next) => {
        const decision = await next(), delivery = saved.get(exec);
        saved.delete(exec);
        if (!delivery || result.isError || decision.kind !== 'accept' || Object.hasOwn(decision, 'value') || Object.hasOwn(decision, 'content')) return decision;
        return { ...decision, content: [{ type: 'text', text: JSON.stringify({ ...delivery, already_presented: true,
            guidance: '这些业务工件已显示为可下载文件卡片。无需再次 present，也无需使用 list_files、glob 或 shell 查找。' }) }] };
    });
    return () => { disposeExecute(); disposeResult(); };
}
