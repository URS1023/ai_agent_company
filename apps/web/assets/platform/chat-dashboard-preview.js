/* 聊天大屏预览：自动展示已保存快照，同源鉴权由业务子应用继承。 */
(function (global) {
  "use strict";
  const mounted = new WeakMap();
  const active = new Set();
  const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

  function routes(previewPath) {
    try {
      const url = new URL(previewPath, global.location.origin);
      if (url.origin !== global.location.origin || !/^\/report\/view\/[A-Za-z0-9_-]+$/.test(url.pathname)) return null;
      url.searchParams.delete("chat_preview");
      const open = url.pathname + url.search;
      url.searchParams.set("chat_preview", "1");
      return { open, frame: "/analytics-app?path=" + encodeURIComponent(url.pathname + url.search) };
    } catch { return null; }
  }

  function markup(data) {
    const path = routes(data.previewPath);
    if (!path) return "";
    const key = path.open + "@" + String(data.version || 1);
    return '<div class="dashboard-inline-preview" data-dashboard-preview-key="' + escape(key) + '" data-dashboard-preview-src="' + escape(path.frame) + '" data-dashboard-preview-title="' + escape(data.title || "数字大屏") + '"><div class="dashboard-preview-bar"><span><i></i>大屏预览 · 已保存数据</span><a href="' + escape(path.open) + '" target="_blank" rel="noopener">独立打开</a></div><span data-dashboard-frame class="sr-only">打开交互预览</span><div class="dashboard-preview-stage" aria-busy="true"><div class="dashboard-preview-feedback" role="status"><span data-dashboard-preview-message>正在加载大屏…</span><button type="button" data-dashboard-preview-retry hidden>重新加载</button></div></div></div>';
  }

  function hydrate(root) {
    if (!root) return;
    // 切换会话后释放尚未进入视口的预览观察器与加载计时器。
    active.forEach(container => {
      if (container.isConnected) return;
      const entry = mounted.get(container);
      entry?.observer?.disconnect();
      if (entry?.timer) global.clearTimeout(entry.timer);
      mounted.delete(container);
      active.delete(container);
    });
    root.querySelectorAll("[data-dashboard-preview-key]").forEach(container => {
      if (mounted.has(container)) return;
      const stage = container.querySelector(".dashboard-preview-stage");
      const feedback = container.querySelector(".dashboard-preview-feedback");
      const message = container.querySelector("[data-dashboard-preview-message]");
      const retry = container.querySelector("[data-dashboard-preview-retry]");
      if (!stage || !feedback || !message || !retry) return;
      const entry = { frame: null, timer: null, observer: null };
      mounted.set(container, entry);
      active.add(container);
      function status(state, text) {
        if (entry.timer) global.clearTimeout(entry.timer);
        container.dataset.previewState = state;
        stage.setAttribute("aria-busy", String(state === "loading"));
        feedback.hidden = state === "ready";
        feedback.setAttribute("role", state === "error" ? "alert" : "status");
        message.textContent = text || "";
        retry.hidden = state !== "error";
      }
      function load() {
        entry.observer?.disconnect();
        entry.frame?.remove();
        status("loading", "正在加载大屏…");
        const frame = document.createElement("iframe");
        entry.frame = frame;
        frame.title = container.dataset.dashboardPreviewTitle + " · 已保存数据预览";
        frame.dataset.testid = "dashboard-inline-frame";
        frame.allowFullscreen = true;
        frame.src = container.dataset.dashboardPreviewSrc;
        frame.addEventListener("load", () => {
          // iframe 内事件不会冒泡到聊天区；只转发意图，实际位移沿用浏览器滚动链。
          try {
            const doc = frame.contentDocument;
            const intent = direction => {
              if (direction) frame.dispatchEvent(new CustomEvent("chat-preview-scroll-intent", { bubbles: true, detail: { direction } }));
            };
            doc?.addEventListener("wheel", event => intent(Math.sign(event.deltaY)), { passive: true, capture: true });
            let touchY = null;
            doc?.addEventListener("touchstart", event => { touchY = event.touches[0]?.clientY ?? null; }, { passive: true, capture: true });
            doc?.addEventListener("touchmove", event => {
              const next = event.touches[0]?.clientY;
              if (touchY != null && next != null) intent(Math.sign(touchY - next));
              touchY = next ?? null;
            }, { passive: true, capture: true });
          } catch { /* 预览地址必须同源；导航异常由加载状态提示处理。 */ }
        });
        frame.addEventListener("error", () => status("error", "大屏加载失败，请重新加载或独立打开。"));
        stage.append(frame);
        entry.timer = global.setTimeout(() => {
          if (!container.isConnected) return;
          status("error", "大屏暂时未能加载，请重新加载或独立打开。");
        }, 20000);
      }
      entry.status = status;
      retry.addEventListener("click", load);
      // 屏幕之外的历史大屏延后加载，避免长对话同时挂载大量图表。
      if (global.IntersectionObserver) {
        entry.observer = new IntersectionObserver(items => {
          if (items.some(item => item.isIntersecting)) load();
        }, { rootMargin: "160px" });
        entry.observer.observe(container);
      } else load();
    });
  }

  global.addEventListener("message", event => {
    if (event.origin !== global.location.origin) return;
    const data = event.data;
    if (!data || !["cloud-brain-chat-preview", "cloud-brain-session-expired"].includes(data.type)) return;
    document.querySelectorAll("[data-dashboard-preview-key]").forEach(container => {
      const entry = mounted.get(container);
      if (!entry?.frame || entry.frame.contentWindow !== event.source) return;
      if (data.type === "cloud-brain-session-expired") entry.status("error", "登录已失效，请重新登录后打开大屏。");
      else if (data.status === "ready") entry.status("ready");
      else if (data.status === "error") entry.status("error", String(data.message || "大屏加载失败，请重新加载或独立打开。"));
    });
  });
  global.ChatDashboardPreview = Object.freeze({ markup, hydrate, routes });
})(window);
