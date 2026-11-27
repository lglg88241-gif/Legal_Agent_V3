"""在命令行演示项目现有的违法解除劳动合同赔偿计算。"""

import argparse
import math

from app.logic.calculators import build_claim_payload
from app.schemas.case_state import CaseState


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--salary", type=float, default=15000, help="月薪，单位为元（默认 15000）")
    parser.add_argument("--years", type=float, default=3, help="工作年限，单位为年（默认 3）")
    args = parser.parse_args()
    if not math.isfinite(args.salary) or args.salary < 0:
        parser.error("月薪必须是大于或等于 0 的有限数值")
    if not 0 <= args.years <= 50:
        parser.error("工作年限必须在 0 到 50 年之间")

    case = CaseState(monthly_salary=args.salary, service_years=args.years, is_illegal_dismissal=True)
    trace = build_claim_payload(case).trace
    if not math.isfinite(trace.total):
        parser.error("金额超出计算器可表示的范围")

    print(f"示例条件：违法解除劳动合同，月薪 {args.salary:,.2f} 元，工作 {args.years:g} 年")
    for step in trace.steps:
        print(f"{step.item}：{step.formula} = {step.result:,.2f} 元")
    print(f"赔偿合计：{trace.total:,.2f} 元")
    if args.years % 1 == 0.5:
        print("工龄恰好含半年：当前实现将这半年折算为 0.5 个月，与模块中的规则说明有差异。")


if __name__ == "__main__":
    main()
