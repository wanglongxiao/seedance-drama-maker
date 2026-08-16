# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

"""集中管理阻塞式 IO 的线程池，实现「生成管线」与「交互式请求」的资源隔离。

背景与动机
----------
云端 veFaaS 单实例通常仅 2 vCPU，Python 3.9+ `asyncio.to_thread` 使用的默认
`ThreadPoolExecutor` 线程数为 `min(32, cpu_count + 4)`（≈6）。当一个项目的参考图/
视频生成管线并发发起多个阻塞式外部 IO（生图 / 生视频 requests，单次 timeout 可达
900s）时，会瞬间占满默认线程池；此时另一个浏览器窗口的 `/upload`、`/asr` 等交互式
请求若也走默认线程池，将长时间排队直至撞网关 60s 超时，前端表现为「上传失败 /
连接 TOS 失败」。

解决方案
--------
提供两个独立线程池：
- interactive_executor：服务用户实时交互（上传、ASR 等），容量小但独享，永不被生成任务饿死；
- generation_executor：承载生成管线的阻塞 IO，容量随并发配置放大。

两者都通过 `run_in_*` 协程封装，供 async 端点/编排代码使用。
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional, TypeVar

from app.config import config
from app.utils.logger import get_logger

logger = get_logger("thread_pools")

T = TypeVar("T")

_interactive_executor: Optional[ThreadPoolExecutor] = None
_generation_executor: Optional[ThreadPoolExecutor] = None


def _interactive_max_workers() -> int:
    # 交互式请求（上传/ASR）短小但要求低延迟，给一个稳定的独立容量。
    return max(4, int(config.get("server.interactive_pool_workers", 16) or 16))


def _generation_max_workers() -> int:
    # 生成管线的阻塞 IO 并发上限：与参考图/视频并发配置对齐并留出余量。
    reference_concurrency = int(config.get("video_generation.reference_images.max_concurrency", 10) or 10)
    scene_concurrency = int(config.get("video_generation.scene_max_concurrency", 10) or 10)
    configured = int(config.get("server.generation_pool_workers", 0) or 0)
    # 允许多个项目同时跑，取两类并发之和再乘以一个并行项目系数，并给下限保护。
    derived = (reference_concurrency + scene_concurrency) * 2
    return max(16, configured or derived)


def get_interactive_executor() -> ThreadPoolExecutor:
    global _interactive_executor
    if _interactive_executor is None:
        workers = _interactive_max_workers()
        _interactive_executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="interactive",
        )
        logger.info(f"Interactive thread pool created: max_workers={workers}")
    return _interactive_executor


def get_generation_executor() -> ThreadPoolExecutor:
    global _generation_executor
    if _generation_executor is None:
        workers = _generation_max_workers()
        _generation_executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="generation",
        )
        logger.info(f"Generation thread pool created: max_workers={workers}")
    return _generation_executor


async def run_interactive(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """在交互式线程池中执行阻塞函数（上传、ASR 等实时请求）。"""
    loop = asyncio.get_running_loop()
    if kwargs:
        from functools import partial
        func = partial(func, **kwargs)
    return await loop.run_in_executor(get_interactive_executor(), func, *args)


async def run_generation(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """在生成线程池中执行阻塞函数（生图 / 生视频 / 审核等管线 IO）。"""
    loop = asyncio.get_running_loop()
    if kwargs:
        from functools import partial
        func = partial(func, **kwargs)
    return await loop.run_in_executor(get_generation_executor(), func, *args)


def shutdown_pools() -> None:
    """进程退出时优雅关闭线程池。"""
    global _interactive_executor, _generation_executor
    if _interactive_executor is not None:
        _interactive_executor.shutdown(wait=False)
        _interactive_executor = None
    if _generation_executor is not None:
        _generation_executor.shutdown(wait=False)
        _generation_executor = None
