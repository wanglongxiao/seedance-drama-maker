# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.

import logging
import sys
from typing import Optional
from app.config import config

class AgentLogger:
    """Agent日志工具类"""
    
    def __init__(self, name: str):
        self.name = name
        self.logger = self._setup_logger()
        self.enabled = config.logging_enabled
        self.log_agent_calls = config.log_agent_calls
        self.log_llm_io = config.log_llm_io
    
    def _setup_logger(self) -> logging.Logger:
        """设置日志器"""
        logger = logging.getLogger(self.name)
        
        if not logger.handlers:
            level = getattr(logging, config.logging_level.upper(), logging.INFO)
            logger.setLevel(level)
            
            # 控制台处理器
            if config.get('logging.console_output', True):
                console_handler = logging.StreamHandler(sys.stdout)
                console_handler.setLevel(level)
                formatter = logging.Formatter(
                    config.get('logging.format', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
                )
                console_handler.setFormatter(formatter)
                logger.addHandler(console_handler)
        
        return logger
    
    def debug(self, message: str, *args, **kwargs):
        if self.enabled:
            self.logger.debug(message, *args, **kwargs)
    
    def info(self, message: str, *args, **kwargs):
        if self.enabled:
            self.logger.info(message, *args, **kwargs)
    
    def warning(self, message: str, *args, **kwargs):
        if self.enabled:
            self.logger.warning(message, *args, **kwargs)
    
    def error(self, message: str, *args, **kwargs):
        if self.enabled:
            self.logger.error(message, *args, **kwargs)
    
    def log_agent_call(self, agent_name: str, action: str, details: Optional[dict] = None):
        """记录Agent调用"""
        if self.enabled and self.log_agent_calls:
            msg = f"[AGENT CALL] {agent_name} - {action}"
            if details:
                msg += f" | Details: {details}"
            self.logger.info(msg)
    
    def log_llm_input(self, model: str, prompt: str):
        """记录LLM输入"""
        if self.enabled and self.log_llm_io:
            self.logger.debug(f"[LLM INPUT] Model: {model} | Prompt: {prompt[:500]}...")
    
    def log_llm_output(self, model: str, output: str):
        """记录LLM输出"""
        if self.enabled and self.log_llm_io:
            self.logger.debug(f"[LLM OUTPUT] Model: {model} | Output: {output[:500]}...")
    
    def log_heartbeat(self, component: str, status: str):
        """记录心跳"""
        if self.enabled and config.get('logging.log_heartbeat', True):
            self.logger.debug(f"[HEARTBEAT] {component} - {status}")
    
    def log_progress(self, step: str, progress: int, total: int = 100):
        """记录进度"""
        if self.enabled:
            percentage = int((progress / total) * 100)
            self.logger.info(f"[PROGRESS] {step}: {percentage}% ({progress}/{total})")

def get_logger(name: str) -> AgentLogger:
    """获取日志器"""
    return AgentLogger(name)
