# -*- coding: utf-8 -*-
"""
DOM 元素自动扫描与证据采集。

通过 Playwright Page 注入 JS 遍历 DOM，输出结构化元素清单，
用于辅助「测试用例 → 测试代码」的自动化生成。

用法:
    from core.utils.element_evidence import scan_page, format_evidence_table

    elements = scan_page(page)
    print(format_evidence_table(elements))
"""

from __future__ import annotations

import json
from typing import Any


# —— 扫描范围：需要采集的可交互元素 ——
_INTERACTIVE_SELECTORS = [
    # 标准表单
    "input:not([type='hidden'])",
    "textarea",
    "select",
    "button",
    # 链接和可点击元素
    "a[href]",
    "a[onclick]",
    "a:not([href]):not([onclick])",  # JS 绑定的链接
    # ARIA 交互角色
    "[role='button']",
    "[role='checkbox']",
    "[role='radio']",
    "[role='combobox']",
    "[role='switch']",
    "[role='tab']",
    "[role='menuitem']",
    "[role='link']",
    "[role='textbox']",
    "[role='listbox']",
    "[role='option']",
    # 通用可交互标记
    "[onclick]",
    "[tabindex]",
    "[contenteditable='true']",
    # 飞跨自定义组件
    ".qg-switch-item",
    ".qg-select-item",
    ".qg-more-options",
    ".qg-more-box",
    ".qg-table__row",
    "div.ul_bar_head",
    "a.feikua-btn",
    "a.qgui-qg-btn0",
]

# —— 选择器候选优先级权重 ——
_ID_WEIGHT = 10
_NAME_WEIGHT = 9
_DATA_TESTID_WEIGHT = 8
_DATA_ATTRIBUTE_WEIGHT = 7
_ARIA_LABEL_WEIGHT = 7
_PLACEHOLDER_WEIGHT = 5
_CLASS_COMBO_WEIGHT = 4
_STRUCTURAL_WEIGHT = 2
_TEXT_WEIGHT = 3


def scan_page(
    page,
    *,
    selector_filter: str | None = None,
    include_computed_style: bool = False,
    max_elements: int = 300,
    timeout_ms: int = 10000,
) -> list[dict[str, Any]]:
    """扫描当前页面中所有可交互元素，返回结构化清单。

    参数
    ----
    page : Playwright Page
        目标页面。
    selector_filter : str | None
        可选 CSS 选择器，仅扫描该容器内的元素（如 "#main-content"）。
    include_computed_style : bool
        是否附带 computed style 摘要（默认关闭以减小数据量）。
    max_elements : int
        最多返回的元素数量。
    timeout_ms : int
        页面上等待交互元素出现的超时（毫秒）。

    返回
    ----
    list[dict]
        每个元素包含:
        - index: 序号
        - tag: 标签名（小写）
        - id, name, type, placeholder, value, href
        - role, aria_*: 无障碍属性
        - data_attrs: data-* 属性字典
        - classes: class 列表
        - text: 文本内容（截断至 120 字符）
        - checked, selected, disabled: 状态（如有）
        - visible: 是否可见
        - bounding_box: {x, y, width, height}
        - candidate_selectors: 候选选择器列表，按稳定性降序
        - parent_summary: 父元素简要信息 {tag, id, classes}
        - dom_fragment: 元素 outerHTML 截断（前 300 字符）
    """
    script_path = _build_scan_js(
        selector_filter=selector_filter,
        include_computed_style=include_computed_style,
        max_elements=max_elements,
    )

    # 等待页面至少有一个可交互元素
    first_selector = "input, button, a, select, textarea, [role='button']"
    try:
        page.locator(first_selector).first.wait_for(state="attached", timeout=timeout_ms)
    except Exception:
        pass  # 页面可能确实没有交互元素

    raw = page.evaluate(script_path)
    result = json.loads(raw) if isinstance(raw, str) else raw
    return _post_process(result)


def scan_element(
    page,
    selector: str,
    *,
    capture_state_change: bool = False,
    timeout_ms: int = 5000,
) -> dict[str, Any] | None:
    """对单个已知元素做深度采集，可选 before/after 状态变化记录。

    当 capture_state_change=True 时，返回中会包含 before_state 和 after_state，
    调用方需自行执行交互操作后再调用一次来完成采集。
    此处只采集调用时刻的当前状态。
    """
    script = _build_single_element_js(selector)
    try:
        page.locator(selector).first.wait_for(state="attached", timeout=timeout_ms)
    except Exception:
        return None
    raw = page.evaluate(script)
    result = json.loads(raw) if isinstance(raw, str) else raw
    if not result:
        return None
    return _post_process_single(result)


def format_evidence_table(elements: list[dict[str, Any]]) -> str:
    """将元素清单格式化为 element-evidence-cdp.md 规格的 Markdown 证据表。"""
    header = "| 序号 | 元素 | 用途推断 | DOM 证据 | 候选选择器 | 得分 |"
    sep = "|---|---|---|---|---|---|"
    rows: list[str] = [header, sep]

    for el in elements:
        idx = el.get("index", "?")
        tag = el.get("tag", "")
        el_id = el.get("id", "")
        el_name = el.get("name", "")
        el_type = el.get("type", "")
        el_text = (el.get("text") or "")[:50]

        label_parts = [tag.upper()]
        if el_id:
            label_parts.append(f"#{el_id}")
        if el_name:
            label_parts.append(f"[name={el_name}]")
        if el_type:
            label_parts.append(f"(type={el_type})")
        label = " ".join(label_parts)

        purpose = _infer_purpose(el)

        fragment = (el.get("dom_fragment") or "")[:120].replace("|", "\\|").replace("\n", " ")

        best = el.get("candidate_selectors", [])
        top_selector = best[0]["selector"] if best else "-"
        top_score = str(best[0]["score"]) if best else "-"
        selector_str = f"`{top_selector}`"

        rows.append(
            f"| {idx} | {label}<br>{el_text} | {purpose} | {fragment} | {selector_str} | {top_score} |"
        )

    return "\n".join(rows)


def format_selectors_for_page_object(
    elements: list[dict[str, Any]],
    prefix: str = "",
) -> str:
    """从扫描结果生成 selector 定义代码（Python 类属性格式），可直接粘贴到 Selector 类中。"""
    lines: list[str] = []
    for el in elements:
        best = el.get("candidate_selectors", [])
        if not best:
            continue
        selector = best[0]["selector"]
        el_id = el.get("id", "")
        el_name = el.get("name", "")
        el_text = (el.get("text") or "")[:30]
        tag = el.get("tag", "")

        var_name = _derive_variable_name(el, prefix)
        lines.append(f"    {var_name} = '{selector}'")

        if len(best) > 1:
            alt = best[1]["selector"]
            lines.append(f"    # 备选: {alt}")

        comment_parts = []
        if el_text:
            comment_parts.append(f'"{el_text}"')
        if el_name:
            comment_parts.append(f"name={el_name}")
        if comment_parts:
            lines[-1] += f"  # {', '.join(comment_parts)}"

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════


def _build_scan_js(
    *,
    selector_filter: str | None,
    include_computed_style: bool,
    max_elements: int,
) -> str:
    """构建注入页面的 DOM 扫描 JS 脚本。"""
    selectors_json = json.dumps(_INTERACTIVE_SELECTORS, ensure_ascii=False)
    scope_js = _build_scope_js(selector_filter)
    style_js = _build_computed_style_js() if include_computed_style else "return null;"

    return f"""
() => {{
    const MAX = {max_elements};
    const SELECTORS = {selectors_json};
    const results = [];
    const seen = new Set();

    {scope_js}

    const container = getContainer();
    if (!container) return JSON.stringify(results);

    // 收集所有候选元素
    let elements = [];
    for (const sel of SELECTORS) {{
        try {{
            const nodes = container.querySelectorAll(sel);
            for (const node of nodes) {{
                const key = nodeToKey(node);
                if (!seen.has(key)) {{
                    seen.add(key);
                    elements.push(node);
                }}
            }}
        }} catch (_) {{}}
        if (elements.length >= MAX) break;
    }}

    elements = elements.slice(0, MAX);

    for (let i = 0; i < elements.length; i++) {{
        results.push(extractElement(elements[i], i + 1));
    }}

    return JSON.stringify(results);

    function nodeToKey(el) {{
        const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : {{}};
        const tag = (el.tagName || '').toLowerCase();
        const id = el.id || '';
        const name = el.getAttribute('name') || '';
        const text = (el.textContent || '').trim().slice(0, 40);
        return [tag, id, name, text, Math.round(rect.x||0), Math.round(rect.y||0)].join('|');
    }}

    function extractElement(el, index) {{
        const tag = (el.tagName || '').toLowerCase();
        const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : {{}};

        // 基础属性
        const info = {{
            index: index,
            tag: tag,
            id: el.id || '',
            name: el.getAttribute('name') || '',
            type: el.getAttribute('type') || (tag === 'select' ? (el.multiple ? 'select-multiple' : 'select-one') : ''),
            placeholder: el.getAttribute('placeholder') || '',
            value: el.value !== undefined ? String(el.value).slice(0, 200) : '',
            href: el.getAttribute('href') || '',
            role: el.getAttribute('role') || '',
            aria_label: el.getAttribute('aria-label') || '',
            aria_expanded: el.getAttribute('aria-expanded') || '',
            aria_selected: el.getAttribute('aria-selected') || '',
            aria_checked: el.getAttribute('aria-checked') || '',
            aria_disabled: el.getAttribute('aria-disabled') || '',
            aria_describedby: el.getAttribute('aria-describedby') || '',

            // data-* 属性
            data_attrs: extractDataAttrs(el),

            // class
            classes: Array.from(el.classList || []),

            // 文本
            text: (el.textContent || '').trim().slice(0, 120),

            // 状态
            checked: !!el.checked,
            selected: !!el.selected,
            disabled: !!el.disabled,

            // 可见性
            visible: isVisible(el),
            bounding_box: {{
                x: Math.round(rect.x || 0),
                y: Math.round(rect.y || 0),
                width: Math.round(rect.width || 0),
                height: Math.round(rect.height || 0)
            }},

            // 候选选择器（按稳定性排序）
            candidate_selectors: buildCandidates(el, tag),

            // 父元素摘要
            parent_summary: getParentSummary(el),

            // DOM 片段
            dom_fragment: getDomFragment(el),

            // computed style (可选)
            computed_style: getComputedSummary(el),
        }};

        return info;
    }}

    function extractDataAttrs(el) {{
        const attrs = {{}};
        if (!el.attributes) return attrs;
        for (const attr of el.attributes) {{
            if (attr.name.startsWith('data-')) {{
                attrs[attr.name] = attr.value.slice(0, 120);
            }}
        }}
        return attrs;
    }}

    {style_js}

    function isVisible(el) {{
        if (!el.checkVisibility) {{
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        }}
        return el.checkVisibility({{checkOpacity: true, checkVisibilityCSS: true}});
    }}

    function getParentSummary(el) {{
        const p = el.parentElement;
        if (!p) return null;
        return {{
            tag: (p.tagName || '').toLowerCase(),
            id: p.id || '',
            classes: Array.from(p.classList || []).slice(0, 5)
        }};
    }}

    function getDomFragment(el) {{
        const clone = el.cloneNode(false);
        const tmp = document.createElement('div');
        tmp.appendChild(clone);
        let html = tmp.innerHTML;
        return html.slice(0, 300);
    }}

    function buildCandidates(el, tag) {{
        const candidates = [];
        const id = el.id && el.id.trim();
        const name = el.getAttribute('name');
        const type = el.getAttribute('type');
        const role = el.getAttribute('role');
        const ariaLabel = el.getAttribute('aria-label');
        const placeholder = el.getAttribute('placeholder');

        // 1. id 选择器
        if (id && /^[a-zA-Z_][\\w-]*$/.test(id)) {{
            candidates.push({{selector: '#' + id, score: {_ID_WEIGHT}, reason: 'id 属性，全局唯一性最高'}});
        }}

        // 2. name 选择器
        if (name) {{
            let sel = tag + '[name="' + cssEscape(name) + '"]';
            if (type) sel += '[type="' + cssEscape(type) + '"]';
            candidates.push({{selector: sel, score: {_NAME_WEIGHT}, reason: 'name 属性，表单元素稳定标识'}});
        }}

        // 3. data-testid 或 data-test
        for (const key of ['data-testid', 'data-test', 'data-test-id', 'data-cy', 'data-qa']) {{
            const val = el.getAttribute(key);
            if (val) {{
                candidates.push({{selector: '[' + key + '="' + cssEscape(val) + '"]', score: {_DATA_TESTID_WEIGHT}, reason: key + ' 专为测试设计'}});
                break;
            }}
        }}

        // 4. 其他 data-* 属性
        const dataAttrs = extractDataAttrs(el);
        for (const [key, value] of Object.entries(dataAttrs)) {{
            if (['data-testid', 'data-test', 'data-test-id', 'data-cy', 'data-qa'].includes(key)) continue;
            candidates.push({{
                selector: '[' + key + '="' + cssEscape(value) + '"]',
                score: {_DATA_ATTRIBUTE_WEIGHT},
                reason: key + ' 自定义数据属性'
            }});
            break; // 只取第一个
        }}

        // 5. aria-label + role
        if (role && ariaLabel) {{
            candidates.push({{
                selector: '[role="' + cssEscape(role) + '"][aria-label="' + cssEscape(ariaLabel) + '"]',
                score: {_ARIA_LABEL_WEIGHT},
                reason: 'role + aria-label，无障碍语义稳定'
            }});
        }}

        // 6. placeholder（仅 input/textarea 有效）
        if (placeholder && (tag === 'input' || tag === 'textarea')) {{
            let sel = tag + '[placeholder="' + cssEscape(placeholder) + '"]';
            if (name) sel += '[name="' + cssEscape(name) + '"]';
            candidates.push({{selector: sel, score: {_PLACEHOLDER_WEIGHT}, reason: 'placeholder 文本定位'}});
        }}

        // 7. 独特 class 组合
        const importantClasses = (Array.from(el.classList) || []).filter(c => {{
            return c && !/^(active|hover|focus|open|show|hide|visible|hidden|disabled|selected|checked|loading|error|success|warning|empty)$/i.test(c);
        }});
        if (importantClasses.length > 0) {{
            const classSel = '.' + importantClasses.slice(0, 2).map(c => cssEscape(c)).join('.');
            let sel = tag + classSel;
            if (name) sel += '[name="' + cssEscape(name) + '"]';
            candidates.push({{selector: sel, score: {_CLASS_COMBO_WEIGHT}, reason: '功能 class 组合'}});
        }}

        // 8. 文本定位（链接、按钮）
        const text = (el.textContent || '').trim();
        if (text && text.length <= 60 && (tag === 'a' || tag === 'button' || role === 'button')) {{
            candidates.push({{
                selector: tag + ':has-text("' + cssEscape(text.slice(0, 50)) + '")',
                score: {_TEXT_WEIGHT},
                reason: '文本内容定位'
            }});
        }}

        // 9. 结构选择器（最低优先级，兜底）
        if (id || (name && type)) {{
            // 已有多条高优先级方案，不加结构选择器
        }} else {{
            const parent = el.parentElement;
            if (parent && parent.id) {{
                const childSel = tag + (importantClasses.length ? '.' + importantClasses.slice(0, 1).map(c => cssEscape(c)).join('.') : '');
                candidates.push({{
                    selector: '#' + cssEscape(parent.id) + ' ' + childSel,
                    score: {_STRUCTURAL_WEIGHT},
                    reason: '通过父级 id 定位，可能受 DOM 结构变更影响'
                }});
            }}
        }}

        // 按 score 降序
        candidates.sort((a, b) => b.score - a.score);
        return candidates;
    }}

    function cssEscape(str) {{
        return String(str).replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"').replace(/\\n/g, '\\\\n');
    }}

    function getComputedSummary(el) {{
        return null;
    }}
}}
"""


def _build_scope_js(selector_filter: str | None) -> str:
    if not selector_filter:
        return "function getContainer() { return document; }"
    escaped = selector_filter.replace("\\", "\\\\").replace("`", "\\`")
    return f"""
function getContainer() {{
    try {{
        const c = document.querySelector(`{escaped}`);
        return c || document;
    }} catch (_) {{
        return document;
    }}
}}
"""


def _build_computed_style_js() -> str:
    return """
function getComputedSummary(el) {
    try {
        const style = window.getComputedStyle(el);
        return {
            display: style.display,
            visibility: style.visibility,
            cursor: style.cursor,
            opacity: style.opacity,
            pointer_events: style.pointerEvents,
            position: style.position,
            z_index: style.zIndex,
        };
    } catch (_) {
        return null;
    }
}
"""


def _build_single_element_js(selector: str) -> str:
    escaped = selector.replace("\\", "\\\\").replace("`", "\\`")
    return f"""
() => {{
    const el = document.querySelector(`{escaped}`);
    if (!el) return JSON.stringify(null);
    const info = extractElement(el, 1);
    return JSON.stringify(info);

    // 复用 extractElement 逻辑...
    function extractElement(el, index) {{
        const tag = (el.tagName || '').toLowerCase();
        const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : {{}};
        return {{
            index: index,
            tag: tag,
            id: el.id || '',
            name: el.getAttribute('name') || '',
            type: el.getAttribute('type') || (tag === 'select' ? (el.multiple ? 'select-multiple' : 'select-one') : ''),
            placeholder: el.getAttribute('placeholder') || '',
            value: el.value !== undefined ? String(el.value).slice(0, 200) : '',
            href: el.getAttribute('href') || '',
            role: el.getAttribute('role') || '',
            aria_label: el.getAttribute('aria-label') || '',
            classes: Array.from(el.classList || []),
            text: (el.textContent || '').trim().slice(0, 120),
            checked: !!el.checked,
            selected: !!el.selected,
            disabled: !!el.disabled,
            visible: el.checkVisibility ? el.checkVisibility({{checkOpacity: true, checkVisibilityCSS: true}}) : true,
            bounding_box: {{ x: Math.round(rect.x || 0), y: Math.round(rect.y || 0), width: Math.round(rect.width || 0), height: Math.round(rect.height || 0) }},
            dom_fragment: (function() {{ const c = el.cloneNode(false); const d = document.createElement('div'); d.appendChild(c); return d.innerHTML.slice(0, 300); }})(),
        }};
    }}
}}
"""


def _post_process(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """后处理：过滤、排序、补全。"""
    if not isinstance(raw, list):
        return []
    # 去重：同一位置 + 同一 tag 的只保留候选选择器最多的那个
    deduped: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for el in raw:
        bb = el.get("bounding_box", {})
        key = f"{el.get('tag')}|{bb.get('x')}|{bb.get('y')}|{bb.get('width')}|{bb.get('height')}"
        if key in seen_keys:
            # 保留选择器更多的那个
            for existing in deduped:
                eb = existing.get("bounding_box", {})
                ek = f"{existing.get('tag')}|{eb.get('x')}|{eb.get('y')}|{eb.get('width')}|{eb.get('height')}"
                if ek == key:
                    if len(el.get("candidate_selectors", [])) > len(existing.get("candidate_selectors", [])):
                        existing.update(el)
                    break
            continue
        seen_keys.add(key)
        deduped.append(el)

    # 按位置排序：y 优先，x 其次
    deduped.sort(key=lambda e: (e.get("bounding_box", {}).get("y", 0), e.get("bounding_box", {}).get("x", 0)))

    # 重新编号
    for i, el in enumerate(deduped, 1):
        el["index"] = i

    return deduped


def _post_process_single(raw: dict[str, Any]) -> dict[str, Any]:
    return raw


def _infer_purpose(el: dict[str, Any]) -> str:
    """推断元素在测试中的用途。"""
    tag = el.get("tag", "")
    el_type = el.get("type", "")
    role = el.get("role", "")
    classes = " ".join(el.get("classes", [])).lower()
    text = (el.get("text") or "").lower()

    if tag == "input":
        type_map = {
            "text": "文本输入",
            "password": "密码输入",
            "email": "邮箱输入",
            "number": "数字输入",
            "search": "搜索输入",
            "tel": "电话输入",
            "checkbox": "勾选开关",
            "radio": "单选按钮",
            "submit": "提交按钮",
            "button": "按钮点击",
            "file": "文件上传",
            "date": "日期选择",
        }
        return type_map.get(el_type, f"输入框(type={el_type})")

    if tag == "select":
        return "下拉选择"
    if tag == "textarea":
        return "多行文本输入"
    if tag == "button":
        return "按钮点击"
    if tag == "a":
        return "链接/导航"
    if role == "switch" or "switch" in classes:
        return "开关切换"
    if role == "checkbox":
        return "勾选开关"
    if role == "tab":
        return "标签页切换"
    if "搜索" in text or "search" in text or "查询" in text:
        return "搜索/查询"
    if "保存" in text or "save" in text or "submit" in text:
        return "保存/提交"
    if "删除" in text or "delete" in text:
        return "删除操作"
    if "编辑" in text or "edit" in text:
        return "编辑操作"
    if "取消" in text or "cancel" in text:
        return "取消操作"
    if "确定" in text or "确认" in text or "confirm" in text:
        return "确认操作"
    if "打开" in text:
        return "打开/进入"
    if "关闭" in text or "close" in text:
        return "关闭"
    return "交互元素"


def _derive_variable_name(el: dict[str, Any], prefix: str = "") -> str:
    """从元素属性推导 Python 变量名。"""
    el_id = (el.get("id") or "").strip()
    el_name = (el.get("name") or "").strip()
    tag = (el.get("tag") or "").strip().upper()

    if el_id:
        # loginBtn → LOGIN_BTN
        import re
        parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)", el_id)
        name = "_".join(p.upper() for p in parts if p)
    elif el_name:
        name = el_name.upper().replace("-", "_").replace(".", "_").replace("[", "_").replace("]", "")
        name = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    else:
        text = (el.get("text") or "")[:20]
        name = "".join(c if c.isalnum() else "_" for c in text.upper())[:30]

    if not name or name.strip("_") == "":
        name = "ELEMENT"

    if prefix:
        return f"{prefix.upper()}_{name.strip('_')}"
    return name.strip("_")
