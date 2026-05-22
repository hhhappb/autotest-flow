"""Phase 1: 自动化测试工作流配置"""

import os

# Anthropic-compatible API (DeepSeek)
ANTHROPIC_AUTH_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")
MODEL = os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-flash")

# Paths
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")

# Test plan generation settings
PLAN_TEMPERATURE = 0.4
PLAN_MAX_TOKENS = 8192
