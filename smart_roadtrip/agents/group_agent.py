"""GroupAgent：多人协同（偏好合并/投票/费用分摊）。

当前为确定性占位实现：按同行人偏好做简单合并与等额分摊，不接入真实投票服务。
"""

from __future__ import annotations

from collections import Counter

from smart_roadtrip.agents.base import AgentContext, BaseAgent, placeholder_result
from smart_roadtrip.schemas.trip import AgentName, AgentResult, GroupPlan


class GroupAgent(BaseAgent):
    name = AgentName.GROUP

    def _run(self, ctx: AgentContext) -> AgentResult:
        request = ctx.request
        voters = request.travelers

        counts: Counter[str] = Counter(request.preferences.interests)
        for traveler in voters:
            counts.update(traveler.preferences)

        merged_interests = list(dict.fromkeys(counts.keys()))
        votes = dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

        cost_split: dict[str, float] = {}
        total_budget = request.budget_cny or sum(
            traveler.budget_cny or 0.0 for traveler in voters
        )
        if total_budget > 0:
            per_person = round(total_budget / len(voters), 2)
            cost_split = {traveler.name: per_person for traveler in voters}

        plan = GroupPlan(
            group_size=len(voters),
            merged_interests=merged_interests,
            votes=votes,
            cost_split=cost_split,
            notes="费用分摊为等额占位方案，实际金额需结合路线与住宿确认。",
        )
        return placeholder_result(
            AgentName.GROUP,
            f"已合并 {len(voters)} 名同行人的偏好，费用按等额分摊。",
            round=ctx.round,
            group_plan=plan,
        )
