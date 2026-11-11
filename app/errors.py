class PlanError(ValueError):
    """采集配置或文档不满足生成只读计划的要求。"""


class PolicyError(PlanError):
    """违反只读策略或来源范围策略。"""
