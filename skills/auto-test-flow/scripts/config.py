"""Phase 1: 自动化测试工作流配置"""

import os

# Anthropic-compatible API (DeepSeek)
ANTHROPIC_AUTH_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")
MODEL = os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-pro")

# Paths
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")

# Boost prompt settings
BOOST_TEMPERATURE = 0.7
BOOST_MAX_TOKENS = 2048

# Test plan generation settings
PLAN_TEMPERATURE = 0.4
PLAN_MAX_TOKENS = 8192

SYSTEM_PROMPT_BOOST = """你是一个提示词优化专家。用户会给你一个粗略的自动化测试需求描述。
请你在保留原意的基础上，优化并补充以下维度，输出一份更完善的测试需求描述：

1. 明确测试对象（功能/模块/接口/页面）
2. 补充业务背景
3. 细化核心需求点
4. 识别涉及的用户角色
5. 列出输入条件和前置条件
6. 明确预期结果
7. 列举可能的异常情况

注意：
- 如果用户原始输入缺少某些维度，根据经验合理推断并补充
- 不要完全编造不存在的业务细节
- 输出保持自然语言格式，不要用表格
- 优化后的描述应该是原始输入的增强版，给测试工程师看的

请直接输出优化后的测试需求描述，不要输出其他内容。"""
