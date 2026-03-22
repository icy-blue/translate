from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TagDefinition:
    category_code: str
    category_label: str
    tag_code: str
    tag_label: str

    @property
    def path(self) -> str:
        return f"{self.category_label}/{self.tag_label}"


TAG_TREE: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    (
        "L",
        "学习范式",
        (
            ("L1", "有监督学习"),
            ("L2", "无监督学习"),
            ("L3", "自监督学习"),
            ("L4", "半监督学习"),
            ("L5", "迁移学习"),
            ("L6", "强化学习"),
            ("L7", "元学习"),
            ("L8", "主动学习"),
            ("L9", "少样本学习"),
            ("L10", "零样本学习"),
            ("L11", "持续学习"),
            ("L12", "在线学习"),
            ("L13", "多任务学习"),
            ("L14", "课程学习"),
            ("L15", "模仿学习"),
            ("L16", "逆强化学习"),
            ("L17", "域适应"),
            ("L18", "领域泛化"),
        ),
    ),
    (
        "M",
        "数据模态",
        (
            ("M1", "图像"),
            ("M2", "视频"),
            ("M3", "点云"),
            ("M4", "多模态"),
            ("M5", "文本"),
            ("M6", "音频"),
            ("M7", "图数据"),
            ("M8", "时序"),
            ("M9", "3D网格"),
            ("M10", "体素"),
            ("M11", "神经辐射场"),
            ("M12", "RGBD"),
            ("M13", "事件相机"),
            ("M14", "遥感数据"),
            ("M15", "医学影像"),
            ("M16", "表格数据"),
            ("M17", "传感器融合"),
            ("M18", "轨迹数据"),
            ("M19", "文档"),
            ("M20", "代码"),
        ),
    ),
    (
        "T",
        "任务",
        (
            ("T1", "分类识别"),
            ("T2", "目标检测"),
            ("T3", "语义分割"),
            ("T4", "实例分割"),
            ("T5", "点云法向估计"),
            ("T6", "点云配准"),
            ("T7", "三维重建"),
            ("T8", "深度估计"),
            ("T9", "姿态估计"),
            ("T10", "跟踪"),
            ("T11", "检索"),
            ("T12", "生成"),
            ("T13", "问答对话"),
            ("T14", "预测规划"),
            ("T15", "关键点检测"),
            ("T16", "匹配对齐"),
            ("T17", "场景理解"),
            ("T18", "图像描述"),
            ("T19", "视觉定位"),
            ("T20", "SLAM"),
            ("T21", "动作识别"),
            ("T22", "动作生成"),
            ("T23", "异常检测"),
            ("T24", "推荐"),
            ("T25", "排序"),
            ("T26", "OCR"),
            ("T27", "文档理解"),
            ("T28", "信息抽取"),
            ("T29", "机器翻译"),
            ("T30", "文本摘要"),
            ("T31", "代码生成"),
            ("T32", "图像编辑"),
            ("T33", "超分辨率"),
            ("T34", "去噪去模糊"),
            ("T35", "图像增强"),
            ("T36", "补全修复"),
            ("T37", "新视角合成"),
            ("T38", "轨迹预测"),
            ("T39", "时间序列预测"),
            ("T40", "控制决策"),
            ("T41", "机器人操作"),
            ("T42", "导航"),
            ("T43", "配准重定位"),
            ("T44", "开放词汇识别"),
            ("T45", "开放词汇检测"),
            ("T46", "开放词汇分割"),
            ("T47", "指代表达理解"),
            ("T48", "多模态检索"),
            ("T49", "视觉问答"),
            ("T50", "3D理解"),
        ),
    ),
    (
        "S",
        "模型范式",
        (
            ("S1", "世界模型"),
            ("S2", "扩散模型"),
            ("S3", "图神经网络"),
            ("S4", "Transformer"),
            ("S5", "卷积网络"),
            ("S6", "视觉语言模型"),
            ("S7", "检索增强"),
            ("S8", "NeRF3DGS"),
            ("S9", "自回归模型"),
            ("S10", "FlowMatching"),
            ("S11", "状态空间模型"),
            ("S12", "Mamba"),
            ("S13", "GAN"),
            ("S14", "VAE"),
            ("S15", "能量模型"),
            ("S16", "对比学习"),
            ("S17", "图注意力网络"),
            ("S18", "稀疏专家模型"),
            ("S19", "大语言模型"),
            ("S20", "多模态大模型"),
            ("S21", "Token压缩"),
            ("S22", "记忆增强"),
            ("S23", "工具调用Agent"),
            ("S24", "神经符号方法"),
            ("S25", "隐式表示"),
            ("S26", "占据场"),
            ("S27", "高斯表示"),
            ("S28", "PointNet系"),
            ("S29", "体渲染"),
            ("S30", "一致性模型"),
        ),
    ),
    (
        "A",
        "应用领域",
        (
            ("A1", "自动驾驶"),
            ("A2", "机器人"),
            ("A3", "具身智能"),
            ("A4", "遥感"),
            ("A5", "医学影像"),
            ("A6", "工业质检"),
            ("A7", "安防监控"),
            ("A8", "智能交通"),
            ("A9", "ARVR"),
            ("A10", "游戏图形学"),
            ("A11", "推荐广告"),
            ("A12", "教育"),
            ("A13", "金融"),
            ("A14", "生物信息"),
            ("A15", "科学计算"),
            ("A16", "文档智能"),
            ("A17", "代码智能"),
            ("A18", "人机交互"),
            ("A19", "数字人"),
            ("A20", "内容生成"),
        ),
    ),
    (
        "P",
        "能力性质",
        (
            ("P1", "可解释性"),
            ("P2", "鲁棒性"),
            ("P3", "泛化能力"),
            ("P4", "高效推理"),
            ("P5", "轻量化"),
            ("P6", "模型压缩"),
            ("P7", "蒸馏"),
            ("P8", "量化"),
            ("P9", "剪枝"),
            ("P10", "低延迟"),
            ("P11", "隐私保护"),
            ("P12", "联邦学习"),
            ("P13", "安全对齐"),
            ("P14", "可控生成"),
            ("P15", "不确定性估计"),
            ("P16", "因果学习"),
            ("P17", "公平性"),
            ("P18", "可复现性"),
            ("P19", "数据高效"),
            ("P20", "参数高效微调"),
        ),
    ),
)

TAG_MAP: dict[str, TagDefinition] = {
    tag_code: TagDefinition(category_code=category_code, category_label=category_label, tag_code=tag_code, tag_label=tag_label)
    for category_code, category_label, tag_items in TAG_TREE
    for tag_code, tag_label in tag_items
}

TAG_PROMPT_LIBRARY = "\n".join(
    f"{category_code}:{' '.join(f'{tag_code}{tag_label}' for tag_code, tag_label in tag_items)}"
    for category_code, _, tag_items in TAG_TREE
)

CATEGORY_PROMPT_LIBRARY = ",".join(
    f"{category_code}={category_label}"
    for category_code, category_label, _ in TAG_TREE
)


def extract_abstract_for_tagging(message_content: str, max_chars: int = 1200) -> str:
    if not message_content:
        return ""

    text = re.sub(r"```.*?```", " ", message_content, flags=re.DOTALL)
    text = text.replace("\r\n", "\n")
    lines = [line.strip() for line in text.splitlines()]

    kept_lines: list[str] = []
    for line in lines:
        if not line:
            continue
        if re.fullmatch(r"#{1,6}\s*(摘要|abstract)\s*", line, flags=re.IGNORECASE):
            continue
        if re.fullmatch(r"(摘要|abstract)\s*[:：]?\s*", line, flags=re.IGNORECASE):
            continue
        if re.match(r"^#{1,6}\s+", line) and kept_lines:
            break
        kept_lines.append(re.sub(r"^[*-]\s*", "", line))

    compact_text = " ".join(kept_lines) if kept_lines else " ".join(line for line in lines if line)
    compact_text = re.sub(r"\s+", " ", compact_text).strip()
    if len(compact_text) <= max_chars:
        return compact_text

    trimmed = compact_text[:max_chars].rstrip()
    if " " in trimmed:
        trimmed = trimmed.rsplit(" ", 1)[0]
    return trimmed


def build_category_selection_prompt(title: str, abstract: str) -> str:
    safe_title = _compact_text(title, max_chars=240) or "-"
    safe_abstract = _compact_text(abstract, max_chars=1200) or "-"
    return (
        "你是论文标签分类器。先判断相关分类组。\n"
        "规则:只选最相关的2到4组;不确定就省略。\n"
        "输出:仅一行字母代码,逗号分隔,如M,T,S。\n"
        f"分类组:{CATEGORY_PROMPT_LIBRARY}\n"
        f"题:{safe_title}\n"
        f"摘:{safe_abstract}"
    )


def build_tagging_followup_prompt(category_codes: list[str]) -> str:
    selected_codes = [code for code in category_codes if code in {item[0] for item in TAG_TREE}]
    selected_library = "\n".join(
        f"{category_code}:{' '.join(f'{tag_code}{tag_label}' for tag_code, tag_label in tag_items)}"
        for category_code, _, tag_items in TAG_TREE
        if category_code in selected_codes
    )
    return (
        "基于上文论文内容，仅在下列分类组中选标签。\n"
        "规则:只从库中选;每组最多2个;总数<=8;不确定就省略。\n"
        "输出:仅一行逗号分隔标签代码,如L3,M4,T7,S2。\n"
        f"标签库:\n{selected_library or TAG_PROMPT_LIBRARY}"
    )


def parse_category_codes(raw_response: str) -> list[str]:
    if not raw_response:
        return []

    allowed = {category_code for category_code, _, _ in TAG_TREE}
    seen: set[str] = set()
    ordered_codes: list[str] = []
    for match in re.finditer(r"\b([A-Z])\b", raw_response.upper()):
        code = match.group(1)
        if code not in allowed or code in seen:
            continue
        seen.add(code)
        ordered_codes.append(code)
    return ordered_codes


def parse_tag_codes(raw_response: str) -> list[str]:
    if not raw_response:
        return []

    seen: set[str] = set()
    ordered_codes: list[str] = []
    for match in re.finditer(r"\b([LMTSAP]\d{1,2})\b", raw_response.upper()):
        code = match.group(1)
        if code not in TAG_MAP or code in seen:
            continue
        seen.add(code)
        ordered_codes.append(code)
    return ordered_codes


def resolve_tag_codes(tag_codes: list[str]) -> list[TagDefinition]:
    return [TAG_MAP[code] for code in tag_codes if code in TAG_MAP]


def build_tag_payloads(tag_codes: list[str], source: str = "poe") -> list[dict]:
    payloads: list[dict] = []
    for tag in resolve_tag_codes(tag_codes):
        payloads.append(
            {
                "category_code": tag.category_code,
                "category_label": tag.category_label,
                "tag_code": tag.tag_code,
                "tag_label": tag.tag_label,
                "tag_path": tag.path,
                "source": source,
            }
        )
    return payloads


def _compact_text(value: str, max_chars: int) -> str:
    compact = re.sub(r"\s+", " ", (value or "")).strip()
    if len(compact) <= max_chars:
        return compact
    trimmed = compact[:max_chars].rstrip()
    if " " in trimmed:
        trimmed = trimmed.rsplit(" ", 1)[0]
    return trimmed
