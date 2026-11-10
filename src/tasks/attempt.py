"""MCP 开发运行（ok-script >= 2.0.16）的通用适配层。

ok-script 的 MCP runner（``ok.core.mcp_runner``）对**提交的任务文件**有硬契约：

    class Task(BaseTask):            # 文件里必须有这个类，且实现下面三个方法
        def reset_to_start(self):    # 返回 False 算本次运行失败
        def verify_start_state(self):# 必须 assert 或返回 True，否则失败
        def run(self):               # 返回 False 算本次运行失败

runner 会先把 ``task.artifact_directory`` 指向本次运行目录，再依次调用
``reset_to_start()`` → ``verify_start_state()`` → ``run()``，最后 ``on_destroy()``。
GUI / CLI / 计划任务这三种入口直接调 ``run()``，不经过这些钩子。

所以任务类要同时支持两条路径：
- **MCP**：runner 已经重置并校验过，任务只负责产物目录与执行；
- **GUI/CLI**：任务自己在 run() 里重置、校验、建产物目录（失败只告警，不中断）。

本模块只提供「知道这次被谁拉起、产物往哪写」和安全的默认实现，
具体任务的起始状态判据放在各自的类里。**落盘细节全部在
``src/tasks/evidence.py``**（2026-11-10 归口），这里只管「本次运行的产物根」。
"""

from src.tasks.evidence import EvidenceSink


class AttemptSupport:
    """MCP / GUI 双路径运行支持，作为任务基类最左边的 mixin 使用。

    只依赖 ``self.log_info`` / ``self.log_debug`` / ``self.frame``，不假设任务有
    OCR 能力；需要界面判据的（大厅、比赛 HUD）在 BaseGirlsTask 里覆盖。
    """

    # ---------- 运行身份与产物根 ----------

    @property
    def mcp_driven(self):
        """这次运行是不是 MCP runner 拉起的。

        runner 在调用任何钩子之前就注入 ``artifact_directory``；
        GUI/CLI 路径没有这个属性。
        """
        return bool(getattr(self, "artifact_directory", None))

    @property
    def evidence(self):
        """存证口（单例挂在任务实例上，每局换目录）。

        MCP：产物根 = runner 注入的 ``artifact_directory``（框架只认这里的文件
        作为本次运行的证据）；GUI/CLI：产物根 = ``logs/``，局目录直接挂在下面。
        """
        sink = getattr(self, "_evidence", None)
        if sink is None:
            root = str(self.artifact_directory) if self.mcp_driven else None
            sink = EvidenceSink().set_logger(self.log_debug)
            sink.begin_run(root)
            self._evidence = sink
        return sink

    def begin_attempt(self):
        """在 run() 最开头（以及 MCP 的 reset 钩子里）调用：定下本次运行的产物根。

        真正「开一局」是 ``begin_round()``（每局一次）；这里只是运行级初始化，
        重复调用无副作用，TriggerTask 每拍都会走到。
        """
        sink = self.evidence
        sink.begin_run(str(self.artifact_directory) if self.mcp_driven else None)
        if not getattr(self, "_attempt_logged", False):
            self._attempt_logged = True
            self.log_info(f"本次运行产物根：{sink.root}"
                          f"（入口：{'MCP' if self.mcp_driven else 'GUI/CLI'}）")
        return sink.root

    # ---------- 局外存证 ----------

    def attempt_dump(self, tag, texts=None, frame=None):
        """把一帧 + OCR 文本存进当前局目录的 ``debug/``（局还没开始时落 ``pre/``）。

        与 ``dump_debug`` 的区别：这里不带 actions/threads（reset 阶段没有那些
        上下文）；两者共用同一套目录规则，见 ``evidence.EvidenceSink``。
        """
        if texts is None:
            try:
                texts = self._ocr_region()
            except Exception:                # noqa: BLE001
                texts = []
        path = self.evidence.dump_debug(texts, tag, frame if frame is not None
                                       else getattr(self, "frame", None),
                                       summary="attempt")
        if path:
            self.log_info(f"存证 {path}")
        return path

    # ---------- MCP 契约默认实现 ----------

    def reset_to_start(self):
        """默认：不改游戏状态的任务（纯读取/演示类）用这个。

        会改游戏状态的任务必须覆盖它，回到可复现的基线。
        """
        self.begin_attempt()
        return True

    def verify_start_state(self):
        """默认：没有可断言的基线，直接放行。覆盖时请 assert，别 return False。"""
        assert self.next_frame() is not None, "起始校验失败：拿不到画面"
        return True

    def on_destroy(self):
        """清掉本次运行的状态；MCP 和 GUI 都会在任务结束时调用。"""
        for attr in ("_attempt_logged",):
            if hasattr(self, attr):
                try:
                    delattr(self, attr)
                except AttributeError:
                    pass
        super_method = getattr(super(), "on_destroy", None)
        if callable(super_method):
            super_method()
