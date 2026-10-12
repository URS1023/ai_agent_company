"""DSH 业务 Worker 的显式初始化、操作路由和后台维护生命周期。"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from . import application, chat, context, ledger, registry, tools, transport, receipts


class Worker:
    """宿主是唯一生命周期发起者，构造本对象不建表或启动后台任务。"""
    def __init__(self, rpc):
        self.rpc = rpc
        self.ready = False
        self.activated = False
        self.stop = threading.Event()
        self.initialize_lock = threading.Lock()
        self.background = []
        transport.install(rpc)

    def describe(self):
        from app.config import settings
        return {"protocol_version": 2, "receipt_protocol": 1, "app_name": settings.APP_NAME, "project_root": str(registry.ROOT),
                "storage_root": str(Path(settings.STORAGE_ROOT).resolve()),
                "home_root": str(Path(settings.STORAGE_ROOT).resolve() / "dsh"),
                "static_root": str(registry.ROOT / "apps" / "web"), "routes": application.routes(),
                "plugins": registry.catalog()["plugins"], "skills": registry.skills(),
                "capabilities": registry.capabilities()}

    def initialize(self):
        """显式初始化数据表与种子，只在宿主启动流程调用一次。"""
        with self.initialize_lock:
            if not self.ready:
                from app.seed import bootstrap
                bootstrap()
                from app.db import session_factory
                from app.modules.knowledge_graph.extraction_recovery import recover_interrupted
                recover_interrupted(session_factory())
                application.application()
                self.ready = True
        return {"ready": True, "protocol_version": 2}

    def activate(self):
        """宿主装好反向模型处理器后才启动调度，避免启动顺序丢失请求。"""
        with self.initialize_lock:
            if not self.ready:
                raise RuntimeError("业务 Worker 尚未初始化")
            if not self.activated:
                self._start_maintenance()
                self.activated = True
        return {"ready": True, "activated": True}

    def _start_maintenance(self):
        from app.config import settings
        from app.modules.analytics.connection_pool import reap_idle_pools

        def maintain():
            while not self.stop.wait(30):
                try:
                    reap_idle_pools()
                except Exception:
                    logging.exception("业务连接池维护失败")
        maintenance = threading.Thread(target=maintain, name="business-pool-maintenance", daemon=True)
        maintenance.start()
        self.background.append(maintenance)
        if not settings.SCHEDULER_ENABLED:
            return

        def schedule():
            from app.modules.system_tools.scheduler_lease import SchedulerLease
            from app.modules.system_tools.schedules import run_due_jobs
            from app.modules.runtime.task_control import current, TaskControl
            control = TaskControl(cancelled=self.stop)
            token = current.set(control)
            lease = SchedulerLease()
            acquired = False
            try:
                while not self.stop.is_set():
                    try:
                        if not acquired:
                            acquired = lease.acquire()
                        if acquired:
                            lease.run(run_due_jobs)
                    except Exception:
                        logging.exception("业务定时任务派发失败")
                    self.stop.wait(settings.SCHEDULER_POLL_SECONDS)
            finally:
                if acquired:
                    lease.release()
                current.reset(token)
        scheduler = threading.Thread(target=schedule, name="business-scheduler", daemon=True)
        scheduler.start()
        self.background.append(scheduler)

    def shutdown(self):
        self.ready = False
        self.stop.set()
        context.cancel_all()
        for thread in self.background:
            thread.join(timeout=2)
        from app.modules.analytics.connection_pool import dispose_read_pools
        dispose_read_pools()
        return {"stopped": True}

    def handle(self, method, params):
        methods = {
            "system.describe": self.describe, "system.initialize": self.initialize, "system.activate": self.activate,
            "chat.prepare": chat.prepare, "chat.authorize": chat.authorize, "chat.steer": chat.steer,
            "chat.event": chat.append_event, "chat.settle": chat.settle, "chat.history": chat.history,
            "chat.recover": chat.recover, "chat.queue.update": chat.queue_update,
            "tool.execute": tools.execute, "tool.receipt.get": receipts.get,
            "model.authorize": ledger.authorize, "model.settle": ledger.settle,
            "run.cancel": context.cancel, "shutdown": self.shutdown,
        }
        if method == "api.dispatch":
            return application.dispatch(params["request"])
        if method == "delivery.present":
            from .native_deliveries import present
            return present(**params)
        if method == "delivery.reference":
            from .native_deliveries import reference_saved
            return reference_saved(**params)
        handler = methods.get(method)
        if handler is None:
            from app.errors import NotFoundError
            raise NotFoundError("Worker 操作不存在")
        return handler(**params)
