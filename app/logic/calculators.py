"""
Pure-Python claim calculation and layered answer builder.

Every function in this module is deterministic — no LLM, no IO,
no network. Input is a CaseState; output is a structured PayloadResult.

The LLM's only job downstream is to polish the prose and weave in
RAG-retrieved citations. It must NEVER override the numbers produced here.

Surgery 7: CalculationTrace added.  PayloadResult now carries a full
audit trail (steps, totals, correction notices) so the Advisor node can
feed the LLM the *why* behind each number without giving it license to
recalculate.
"""
from __future__ import annotations

import re
from typing import Any, Final

from pydantic import BaseModel, Field

from app.schemas.case_state import CaseState


# ======================================================================
# Calculation trace models (Surgery 7)
# ======================================================================

class CalculationStep(BaseModel):
    """Audit record for a single deterministic claim calculation."""

    item: str = Field(description="索赔项目名称，如'二倍工资差额'")
    formula: str = Field(description="人类可读计算公式，如'月工资 12000 × 可主张月数 11'")
    result: float = Field(ge=0, description="计算结果（元）")
    cap_rule: str | None = Field(default=None, description="法定上限说明")
    legal_basis: str | None = Field(default=None, description="法律依据名称（无条文编号）")


class CorrectionNotice(BaseModel):
    """
    Auto-generated correction when the user's claimed amount exceeds the
    statutory calculation.  All text is Python-generated; the LLM must
    reproduce it verbatim.
    """

    item: str = Field(description="纠错针对的索赔项目，如'二倍工资差额'")
    user_claimed: float = Field(ge=0, description="用户主张金额（元）")
    legal_limit: float = Field(ge=0, description="法定正确金额（元）")
    discrepancy: float = Field(ge=0, description="差额 = user_claimed − legal_limit（元）")
    is_overclaim: bool = Field(description="True 表示用户超额主张")
    explanation: str = Field(description="预制纠错文案（Python 生成，LLM 必须逐字转述）")


class CalculationTrace(BaseModel):
    """Complete audit trail produced by build_claim_payload()."""

    steps: list[CalculationStep] = Field(default_factory=list)
    total: float = Field(ge=0, description="各 step.result 之和")
    correction_notices: list[CorrectionNotice] = Field(
        default_factory=list,
        description="超额/非法主张纠错列表（支持多项）",
    )


# ======================================================================
# Output contract
# ======================================================================

class PayloadResult(BaseModel):
    """Structured output of the deterministic claim analysis pipeline."""

    answer: str = Field(description="Full layered answer text")
    needs_core_followup: bool = Field(description="True if missing critical fields")
    followup: str = Field(default="", description="Sandwich-style follow-up question")
    constructive_next_step: str = Field(default="", description="Proactive next action proposal")
    unknowns: list[str] = Field(default_factory=list, description="Missing core fields (max 2)")
    trace: CalculationTrace = Field(
        default_factory=lambda: CalculationTrace(total=0.0),
        description="Deterministic calculation audit trail (Surgery 7)",
    )


# ======================================================================
# Overclaim detection (context-aware, Surgery 7)
# ======================================================================

# Keywords that make a nearby number relevant to double-salary claims.
_DOUBLE_SALARY_CONTEXT_TOKENS = (
    "双倍工资", "二倍工资", "双薪", "双倍薪", "赔偿", "差额",
    "补偿", "要求", "主张", "能要", "应得", "索赔",
)

# Lookahead/lookbehind window (characters) for context matching.
_CONTEXT_WINDOW = 30

# Pattern for extracting numbers in a string.
_NUM_RE = re.compile(r"\d+(?:[,，]\d{3})*(?:\.\d+)?")

# Overclaim tolerance: only flag when user's claim exceeds legal limit by
# more than this factor.  1.01 = 1 % buffer absorbs minor rounding in
# the user's mental arithmetic without swamping genuine overclaims.
_OVERCLAIM_TOLERANCE: Final[float] = 1.01


def _strip_number_separators(raw: str) -> str:
    """Remove Chinese/Western thousand separators before parsing."""
    return raw.replace(",", "").replace("，", "")


def _extract_contextual_numbers(text: str, context_tokens: tuple[str, ...]) -> list[float]:
    """
    Extract numbers that appear within _CONTEXT_WINDOW characters of at
    least one keyword in context_tokens.  Numbers near unrelated content
    (case numbers, deposit amounts without salary context) are ignored.
    """
    results: list[float] = []
    for match in _NUM_RE.finditer(text):
        start, end = match.start(), match.end()
        window = text[max(0, start - _CONTEXT_WINDOW): end + _CONTEXT_WINDOW]
        if any(tok in window for tok in context_tokens):
            try:
                value = float(_strip_number_separators(match.group()))
                if value >= 1000:
                    results.append(value)
            except ValueError:
                pass
    return results


def _detect_overclaim(cs: CaseState) -> list[CorrectionNotice]:
    """
    Detect overclaim situations where the user's stated amount exceeds
    the deterministically computed statutory ceiling.

    Context-aware: only numbers appearing near double-salary / compensation
    keywords in cs.question are compared.  Unrelated large numbers (deposits,
    case numbers) are ignored.

    Returns a list of CorrectionNotice objects (one per detected overclaim).
    Currently covers: double-salary-gap overclaim.
    """
    notices: list[CorrectionNotice] = []

    # --- Double salary overclaim ---
    if (
        cs.has_unsigned_contract
        and cs.monthly_salary is not None
        and cs.work_months is not None
        and cs.double_salary_gap > 0
    ):
        candidate_numbers = _extract_contextual_numbers(
            cs.question, _DOUBLE_SALARY_CONTEXT_TOKENS
        )
        for user_val in candidate_numbers:
            # Tolerance: _OVERCLAIM_TOLERANCE to absorb float rounding in user's mental math.
            if user_val > cs.double_salary_gap * _OVERCLAIM_TOLERANCE:
                discrepancy = round(user_val - cs.double_salary_gap, 2)
                explanation = (
                    f"您提到的 {user_val:.0f} 元与法定计算有出入。"
                    f"根据《劳动合同法》关于未签书面劳动合同二倍工资的规定，"
                    f"可主张月数从实际用工满一个月后起算，首月不计入，"
                    f"且法定上限为 11 个月。"
                    f"按您的情况，法定可主张月数为 {cs.compensable_months} 个月"
                    f"（而非 {cs.work_months} 个月），"
                    f"正确金额为 {cs.monthly_salary:.0f} 元 × {cs.compensable_months} 个月"
                    f" = {cs.double_salary_gap:.0f} 元，"
                    f"而非 {user_val:.0f} 元。"
                    f"建议按 {cs.double_salary_gap:.0f} 元申请，"
                    f"以避免仲裁委因超出法定上限而驳回该项请求。"
                )
                notices.append(CorrectionNotice(
                    item="二倍工资差额",
                    user_claimed=user_val,
                    legal_limit=cs.double_salary_gap,
                    discrepancy=discrepancy,
                    is_overclaim=True,
                    explanation=explanation,
                ))

    return notices


# ======================================================================
# Text utilities (no IO)
# ======================================================================

def _normalize_text(text: str) -> str:
    return " ".join((text or "").strip().split())


def _split_fact_lines(case_summary: str, question: str, graph_context: str) -> list[str]:
    """Extract up to 8 distinct fact sentences from raw text inputs."""
    facts: list[str] = []
    for raw in (case_summary, question, graph_context):
        clean = _normalize_text(raw)
        if not clean:
            continue
        for part in re.split(r"[；;。！？\n]+", clean):
            item = part.strip(" ，,")
            if not item or item in facts:
                continue
            facts.append(item)
    return facts[:8]


def _merged_text(cs: CaseState) -> str:
    return " ".join(
        p for p in [cs.case_summary, cs.question, cs.graph_context] if p
    ).strip()


# ======================================================================
# Keyword detectors (pure, no IO)
# ======================================================================

_ENTITY_CHANGE_TOKENS = (
    "查封", "保全", "资产", "新公司", "人格混同", "关联公司",
    "主体变更", "更名", "注销", "换皮", "关门", "搬走", "换了个名",
)

_OWED_TOKENS = ("拖欠工资", "欠薪", "未发工资", "克扣工资", "拖欠")

_DISMISSAL_TOKENS = ("辞退", "开除", "解除劳动关系", "逼离职", "被迫离职")

_ASSET_TOKENS = ("新公司", "注销", "关联公司", "查封", "保全", "换皮", "关门", "搬走")

_LABOR_RELATION_TOKENS = ("劳动关系", "入职", "考勤", "工资流水", "发工资")


def _has_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(tok in text for tok in tokens)


def _detect_scenario(cs: CaseState) -> str:
    """
    Detect one primary scenario for checklist generation.

    Priority:
    1. labor_arbitration
    2. loan_dispute
    3. divorce
    4. criminal
    5. general
    """
    merged = _merged_text(cs)
    labor_keywords = ("劳动", "辞退", "工资", "仲裁", "社保")
    loan_keywords = ("借款", "借条", "欠款", "还款", "利息")
    divorce_keywords = ("离婚", "婚前", "夫妻共同", "财产分割")
    criminal_keywords = ("刑法", "故意伤害", "量刑", "缓刑", "拘留", "逮捕")

    if (
        cs.has_unsigned_contract
        or cs.has_owed_wages
        or _has_any(merged, _DISMISSAL_TOKENS)
        or any(tok in merged for tok in labor_keywords)
    ):
        return "labor_arbitration"
    if any(tok in merged for tok in loan_keywords) and not any(tok in merged for tok in labor_keywords):
        return "loan_dispute"
    if any(tok in merged for tok in divorce_keywords):
        return "divorce"
    if any(tok in merged for tok in criminal_keywords):
        return "criminal"
    return "general"


def _build_action_checklist(cs: CaseState, merged: str) -> list[str]:
    """
    Surgery Q-2: 根据案件类型生成具体的行动清单。
    返回一个清单列表，每项都是具体的、可执行的指导。
    覆盖：证据收集（按优先级）、维权路径（仲裁/诉讼）、时限管理。
    """
    checklist: list[str] = []
    scenario = _detect_scenario(cs)

    if scenario == "labor_arbitration":
        checklist.append(
            "**证据收集清单**（按优先级排序）：\n"
            "  1. **核心证据**：劳动合同（如有）、入职登记表、工牌/工服\n"
            "  2. **工资证据**：银行工资流水（最关键，建议打印近12个月）、工资条、个税APP记录\n"
            "  3. **考勤证据**：打卡记录、排班表、加班审批单\n"
            "  4. **沟通证据**：与HR/上级的微信/钉钉/邮件聊天记录（截图+导出原文件）\n"
            "  5. **辞退证据**（如适用）：辞退通知书、解除劳动合同证明、谈话录音\n"
            "  6. **社保证据**：社保缴纳记录（可在\"国家社会保险公共服务平台\"查询）"
        )
        checklist.append(
            "**劳动仲裁流程**：\n"
            "  - **管辖机构**：用人单位所在地或劳动合同履行地的劳动人事争议仲裁委员会\n"
            "  - **时效限制**：自知道权利被侵害之日起 1 年内必须申请仲裁（拖欠工资为劳动关系存续期间+终止后1年）\n"
            "  - **仲裁费用**：劳动仲裁不收费\n"
            "  - **办理时长**：一般 45 天内审结，复杂案件可延长 15 天\n"
            "  - **所需材料**：仲裁申请书（一式三份）、身份证复印件、证据材料目录、证据复印件"
        )
    elif scenario == "loan_dispute":
        checklist.append(
            "**借贷纠纷材料清单**：\n"
            "  1. 借条/借款合同原件\n"
            "  2. 转账凭证（银行流水、微信/支付宝转账截图，证明实际交付）\n"
            "  3. 催款记录（短信、微信聊天、电话录音）\n"
            "  4. 借款人身份信息\n"
            "  5. 担保材料（如有保证人或抵押物）"
        )
        checklist.append(
            "**民事诉讼路径**：\n"
            "  - **管辖法院**：被告住所地或合同履行地基层人民法院\n"
            "  - **诉讼时效**：3 年（自约定还款期届满之日起算）\n"
            "  - **诉讼费**：标的额 1 万以下 50 元；超过部分按比例（详见诉讼费缴纳办法）\n"
            "  - **建议**：金额较小可考虑小额诉讼程序（一审终审，标的额低于当地年平均工资 30%）"
        )
    elif scenario == "divorce":
        checklist.append(
            "**财产分割材料清单**：\n"
            "  1. 结婚证、身份证\n"
            "  2. 房产证（区分婚前/婚后取得）、购房合同、按揭还款记录\n"
            "  3. 车辆登记证、行驶证、购车发票\n"
            "  4. 银行流水（双方名下，建议提供近 3 年）\n"
            "  5. 投资账户、股票、基金对账单\n"
            "  6. 公司股权（如有）、营业执照"
        )
        checklist.append(
            "**财产分割原则**：\n"
            "  - **婚前个人财产**：归个人所有，不参与分割（《民法典》第一千零六十三条）\n"
            "  - **婚后共同财产**：原则上均等分割（《民法典》第一千零八十七条）\n"
            "  - **协议路径**：双方达成一致 → 民政局协议离婚（30 天冷静期）\n"
            "  - **诉讼路径**：协商不成 → 起诉至原告住所地或被告住所地法院"
        )
    elif scenario == "criminal":
        checklist.append(
            "**刑事辩护材料清单**：\n"
            "  1. 立案决定书、拘留通知书、逮捕通知书\n"
            "  2. 起诉书（案件移送审查起诉后）\n"
            "  3. 相关物证、书证、证人证言\n"
            "  4. 鉴定意见（如伤情鉴定、酒精检测报告）\n"
            "  5. 自首/立功/认罪认罚等从轻情节证据"
        )
        checklist.append(
            "**刑事程序要点**：\n"
            "  - **辩护人介入时机**：侦查阶段即可委托律师（建议尽早）\n"
            "  - **可争取的从宽情节**：自首、坦白、立功、认罪认罚、积极赔偿取得谅解\n"
            "  - **取保候审**：可能判处管制、拘役或独立适用附加刑的，可申请取保候审"
        )
    else:
        checklist.append(
            "**风险提示**：\n"
            "  - 所有证据建议保留原件，复印件加盖\"与原件一致\"印章\n"
            "  - 电子证据（微信、邮件）需公证或当庭演示原始载体\n"
            "  - 录音录像证据需本人在场且不违反法律强制性规定\n"
            "  - 注意各项时效，超过时效将丧失胜诉权"
        )

    if checklist and scenario != "general":
        checklist.append(
            "**风险提示**：\n"
            "  - 所有证据建议保留原件，复印件加盖\"与原件一致\"印章\n"
            "  - 电子证据（微信、邮件）需公证或当庭演示原始载体\n"
            "  - 录音录像证据需本人在场且不违反法律强制性规定\n"
            "  - 注意各项时效，超过时效将丧失胜诉权"
        )

    return checklist


# Core builder
# ======================================================================

def build_claim_payload(cs: CaseState) -> PayloadResult:
    """
    Build the full layered answer from a CaseState.

    This is the authoritative pure-Python replacement for the legacy
    module-level functions in legal_agent.py (Surgery 5).
    It produces structured PayloadResult output consumed by all nodes.
    Surgery 7 adds a CalculationTrace for white-box LLM context.
    """
    merged = _merged_text(cs)
    scenario = _detect_scenario(cs)
    facts = _split_fact_lines(cs.case_summary, cs.question, cs.graph_context)

    calculation_lines: list[str] = []
    strategy_lines: list[str] = []
    action_lines: list[str] = []
    unknowns: list[str] = []

    # --- Trace steps accumulator ---
    steps: list[CalculationStep] = []

    # --- Unsigned contract / double salary ---
    if cs.has_unsigned_contract:
        calculation_lines.append(
            "只要已经形成实际用工关系，即使没有书面劳动合同，"
            "也可以先主张未签书面劳动合同的二倍工资差额。"
        )
        if cs.monthly_salary is not None and cs.work_months is not None:
            # Surgery Q-1: 显式化 cap_rule，让规则进入 answer 文本而非仅在 trace
            calculation_lines.append(
                f"按现有信息，计算公式可以先写为：月工资 {cs.monthly_salary:.0f} 元"
                f" × 可主张月数 {cs.compensable_months}"
                f" = 二倍工资差额约 {cs.double_salary_gap:.0f} 元。"
            )
            rule_explanation = (
                f"（计算规则说明：根据《劳动合同法》第八十二条，二倍工资差额自实际用工"
                f"满一个月的次日起算，首月不计入；法定可主张上限为 11 个月。"
                f"您实际工作 {cs.work_months} 个月，可主张月数为 {cs.compensable_months} 个月。）"
            )
            calculation_lines.append(rule_explanation)
            steps.append(CalculationStep(
                item="二倍工资差额",
                formula=(
                    f"月工资 {cs.monthly_salary:.0f} 元"
                    f" × 可主张月数 {cs.compensable_months}"
                ),
                result=round(cs.double_salary_gap, 2),
                cap_rule=(
                    f"首月不计入，法定上限 11 个月"
                    f"（实际工作 {cs.work_months} 个月，可主张 {cs.compensable_months} 个月）"
                ) if cs.work_months > 1 else "法定上限 11 个月",
                legal_basis="《劳动合同法》关于未签书面劳动合同二倍工资的规定",
            ))
        else:
            if cs.monthly_salary is None:
                unknowns.append("月工资标准")
            if cs.work_months is None:
                unknowns.append("未签合同持续月数")

    # --- Owed wages ---
    if cs.owed_wages > 0:
        calculation_lines.append(f"拖欠工资部分：约 {cs.owed_wages:.0f} 元。")
        steps.append(CalculationStep(
            item="拖欠工资",
            formula=f"欠薪金额 {cs.owed_wages:.0f} 元",
            result=round(cs.owed_wages, 2),
            cap_rule=None,
            legal_basis="《劳动合同法》关于工资支付的规定",
        ))
    elif cs.has_owed_wages and cs.monthly_salary is not None:
        calculation_lines.append("存在工资拖欠事实，待确认具体欠薪月数后可精确计算。")

    # --- Deposit ---
    if cs.deposit > 0:
        calculation_lines.append(
            f"押金/保证金部分：{cs.deposit:.0f} 元，"
            "用人单位收取押金本身违反《劳动合同法》第九条，可依法要求全额退还。"
        )
        steps.append(CalculationStep(
            item="押金退还",
            formula=f"押金/保证金 {cs.deposit:.0f} 元",
            result=round(cs.deposit, 2),
            cap_rule=None,
            legal_basis="《劳动合同法》第九条（禁止收取押金）",
        ))
    elif cs.has_deposit_dispute:
        calculation_lines.append(
            "存在押金争议，用人单位向劳动者收取押金违反《劳动合同法》第九条，可要求退还。"
        )

    # --- Claim total ---
    if cs.claim_total > 0:
        parts: list[str] = []
        if cs.double_salary_gap > 0:
            parts.append(f"二倍工资差额 {cs.double_salary_gap:.0f} 元")
        if cs.owed_wages > 0:
            parts.append(f"拖欠工资 {cs.owed_wages:.0f} 元")
        if cs.deposit > 0:
            parts.append(f"押金退还 {cs.deposit:.0f} 元")
        calculation_lines.append(
            f"以上各项合计可主张总额约 {cs.claim_total:.0f} 元"
            f"（{' + '.join(parts)}）。"
        )

    # --- Labor relationship ---
    if _has_any(merged, _LABOR_RELATION_TOKENS):
        calculation_lines.append(
            "现有事实已经足以先从劳动关系成立、工资支付事实和未签约责任三个层面同时主张权利。"
        )

    # --- Strategy layer ---
    if cs.has_entity_change or _has_any(merged, _ENTITY_CHANGE_TOKENS):
        strategy_lines.append(
            "如果目标是查封新公司或关联主体，现阶段不要先卡在\u201c证据够不够\u201d"
            "这个问题上，而是先按人格混同、责任承继、实际用工主体三个方向布置材料。"
        )
        strategy_lines.append(
            "下一步应优先准备：工商档案、股东和法定代表人信息、工资发放账户、"
            "社保公积金缴纳主体、办公地点、业务连续性、聊天记录和财务往来材料。"
        )

    if not strategy_lines:
        strategy_lines.append(
            "高风险诉求可以先拆成两步走：先固定债权和责任主体，"
            "再根据对方资产和主体结构决定是否申请保全、追加被申请人或单独起诉。"
        )

    # --- Action layer ---
    if cs.has_unsigned_contract:
        action_lines.append(
            "先把入职时间、工资标准、考勤记录、工资流水、聊天记录整理成时间轴，"
            "这些足以支撑第一轮仲裁请求。"
        )
    if _has_any(merged, _OWED_TOKENS):
        action_lines.append(
            "欠薪部分建议同步准备工资条、银行流水、催要记录和考勤，"
            "便于把工资、经济补偿或赔偿一并主张。"
        )
    if _has_any(merged, _DISMISSAL_TOKENS):
        action_lines.append(
            "如果还涉及辞退或被迫离职，解除通知、谈话录音、"
            "岗位调整记录和绩效材料要单独归档。"
        )
    if _has_any(merged, _ASSET_TOKENS):
        action_lines.append(
            "如果担心对方转移资产，可在仲裁或起诉前同步申请财产保全，"
            "而不是等全部证据齐全后再行动。"
        )
    if not action_lines:
        action_lines.append(
            "先把可用证据按\u201c身份关系、金额、时间、对方主体\u201d四类归档，"
            "后续维权路径就能快速落地。"
        )

    # Surgery Q-2: 追加结构化行动清单
    action_checklist = _build_action_checklist(cs, merged)

    # --- Follow-up decision ---
    needs_core_followup = False
    if cs.has_unsigned_contract and (cs.monthly_salary is None or cs.work_months is None):
        needs_core_followup = True
    elif not (cs.case_summary or "").strip():
        needs_core_followup = True

    if unknowns:
        sandwich_followup = (
            "你现在已经提供了有法律价值的关键事实，这些信息足以先帮你判断维权方向。"
            "按目前情况看，权利主张是有空间的，不需要因为材料还没凑齐就停在原地。"
            f"为了把金额和请求做得更准，我还需要你补 1 到 2 个核心信息：{'、'.join(unknowns[:2])}。"
        )
    else:
        sandwich_followup = (
            "你现有的信息已经能支持初步维权判断了。"
            "按现在的案情，主张路径已经比较清楚。"
            "如果你愿意，我接下来更建议直接帮你整理仲裁请求清单、证据目录，"
            "或者起草一份劳动仲裁申请书草稿。"
        )

    constructive_next_step = (
        "目前账目和维权方向已经基本清楚。接下来你可以选择先去劳动监察投诉，"
        "或者直接申请劳动仲裁。"
        "如果你愿意，我下一步可以直接帮你起草《劳动仲裁申请书》草稿，"
        "或者把证据目录整理成可提交版本。"
    )

    if not unknowns:
        if scenario == "loan_dispute":
            sandwich_followup = (
                "你现有的信息已经能支持初步判断债务维权路径了。"
                "按现在的案情，借条、转账凭证和催款记录是最值得先固定的材料。"
                "如果你愿意，我接下来可以帮你整理民事起诉材料清单，或者起草催款函、起诉状草稿。"
            )
        elif scenario == "divorce":
            sandwich_followup = (
                "你现有的信息已经足以先判断财产分割的基本方向。"
                "按现在的案情，先区分婚前个人财产和婚后共同财产是最关键的一步。"
                "如果你愿意，我接下来可以帮你整理财产清单、证据目录，或者按协议离婚和诉讼离婚两条路径分别拆开。"
            )
        elif scenario == "criminal":
            sandwich_followup = (
                "你现有的信息已经足以先判断刑事应对的基本方向。"
                "按现在的案情，先固定案卷材料、鉴定意见和从宽情节证据，会直接影响后续辩护策略。"
                "如果你愿意，我接下来可以帮你列阶段性应对清单，或者把需要尽快调取的材料先拆出来。"
            )
        elif scenario == "general":
            sandwich_followup = (
                "你现有的信息已经足以先做初步判断。"
                "按现在的案情，先把事实时间线、关键证据和对方主体信息固定下来，后面的处理路径就会更清楚。"
                "如果你愿意，我接下来可以帮你把手头材料整理成一份可执行的处理清单。"
            )

    if scenario == "loan_dispute":
        constructive_next_step = (
            "目前债权基础和维权方向已经基本清楚。接下来你可以先固定借条、转账凭证和催款记录，"
            "再根据标的额选择催款协商、调解或直接提起民事诉讼。"
            "如果你愿意，我下一步可以直接帮你整理起诉材料清单，或者起草催款函、起诉状草稿。"
        )
    elif scenario == "divorce":
        constructive_next_step = (
            "目前财产分割的基本方向已经比较清楚。接下来你可以先把房产、车辆、存款和其他财产按“婚前、婚后”分类整理，"
            "再决定是先协商分割还是直接走离婚诉讼路径。"
            "如果你愿意，我下一步可以直接帮你列财产清单和证据目录，或者按两条路径拆开处理步骤。"
        )
    elif scenario == "criminal":
        constructive_next_step = (
            "目前案件的基本应对目标已经可以往前推进。接下来你可以先固定案卷材料、鉴定意见和从宽情节证据，"
            "同时尽快评估是否需要申请取保候审或提前准备谅解、赔偿工作。"
            "如果你愿意，我下一步可以直接帮你拆出辩护材料清单和阶段性应对顺序。"
        )
    elif scenario == "general":
        constructive_next_step = (
            "目前可以先把事实时间线、关键证据和对方主体信息整理出来，再根据争议类型选择协商、调解或正式程序推进。"
            "如果你愿意，我下一步可以帮你把手头材料整理成一份可执行的处理清单。"
        )

    # --- Assemble answer ---
    answer_parts: list[str] = []
    if facts:
        answer_parts.append("已知事实：\n- " + "\n- ".join(facts))
    if calculation_lines:
        answer_parts.append("算账与确权层：\n- " + "\n- ".join(calculation_lines))
    else:
        answer_parts.append(
            "算账与确权层：\n- 现有信息虽然还不完整，"
            "但已经足以先明确可主张的权利方向，不需要整体停住。"
        )
    answer_parts.append("高风险执行层：\n- " + "\n- ".join(strategy_lines))
    # Surgery Q-2: 合并 action_lines 和 action_checklist
    combined_actions = action_lines.copy()
    if action_checklist:
        combined_actions.extend(action_checklist)
    answer_parts.append("下一步落地动作：\n\n" + "\n\n".join(combined_actions))
    if needs_core_followup:
        answer_parts.append("柔性追问：\n- " + sandwich_followup)
    else:
        answer_parts.append("建设性推进：\n- " + constructive_next_step)

    # --- Build trace ---
    trace = CalculationTrace(
        steps=steps,
        total=round(cs.claim_total, 2),
        correction_notices=_detect_overclaim(cs),
    )

    return PayloadResult(
        answer="\n\n".join(answer_parts),
        needs_core_followup=needs_core_followup,
        followup=sandwich_followup,
        constructive_next_step=constructive_next_step,
        unknowns=unknowns[:2],
        trace=trace,
    )
