from dataclasses import dataclass, field
from urllib.parse import urlparse

from application.activity.models import ActivitySegment
from application.classification.models import ClassificationDecision


_LEARNING_DOMAINS = {
    "arxiv.org",
    "coursera.org",
    "edx.org",
    "khanacademy.org",
    "ocw.mit.edu",
}
_ENTERTAINMENT_DOMAINS = {
    "disneyplus.com",
    "hulu.com",
    "netflix.com",
    "twitch.tv",
}
_WORK_APPS = {
    "code",
    "intellij idea",
    "pycharm",
    "terminal",
    "xcode",
}
_LEARNING_TERMS = {
    "course",
    "lecture",
    "lesson",
    "tutorial",
    "课程",
    "教程",
    "讲座",
    "学习",
}
_ENTERTAINMENT_TERMS = {
    "gameplay",
    "music video",
    "trailer",
    "vlog",
    "游戏",
    "音乐",
    "预告",
}


def _hostname(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host.removeprefix("www.")


def _matches_domain(host: str, domains: set[str]) -> bool:
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


@dataclass
class RuleClassifier:
    custom_domain_rules: dict[str, str] = field(default_factory=dict)

    def classify(
        self,
        segment: ActivitySegment,
    ) -> ClassificationDecision | None:
        if segment.category == "background_playback":
            return ClassificationDecision(
                "background_playback",
                segment.activity_type,
                1.0,
                "媒体播放期间用户不在座。",
                "presence_rule",
            )

        evidence = segment.evidence
        app = str(evidence.get("app") or "").strip().lower()
        url = str(evidence.get("url") or "")
        host = _hostname(url)
        searchable = " ".join(
            str(evidence.get(key) or "")
            for key in ("title", "page_title", "window_title", "description")
        ).lower()
        headings = evidence.get("headings")
        if isinstance(headings, list):
            searchable += " " + " ".join(str(item) for item in headings)

        for domain, category in self.custom_domain_rules.items():
            if _matches_domain(host, {domain.lower()}):
                return ClassificationDecision(
                    category,
                    segment.activity_type,
                    0.99,
                    f"匹配用户域名规则：{domain}",
                    "user_rule",
                )
        if _matches_domain(host, _LEARNING_DOMAINS):
            return ClassificationDecision(
                "learning",
                segment.activity_type,
                0.95,
                "匹配已知学习平台。",
                "domain_rule",
            )
        if _matches_domain(host, _ENTERTAINMENT_DOMAINS):
            return ClassificationDecision(
                "entertainment",
                segment.activity_type,
                0.95,
                "匹配已知娱乐平台。",
                "domain_rule",
            )
        if app in _WORK_APPS or evidence.get("project") or evidence.get("file"):
            return ClassificationDecision(
                "work",
                segment.activity_type,
                0.82,
                "开发工具或项目文件处于活动状态。",
                "app_rule",
            )
        if any(term in searchable for term in _LEARNING_TERMS):
            return ClassificationDecision(
                "learning",
                segment.activity_type,
                0.84,
                "页面元数据包含明确的学习关键词。",
                "content_rule",
            )
        if any(term in searchable for term in _ENTERTAINMENT_TERMS):
            return ClassificationDecision(
                "entertainment",
                segment.activity_type,
                0.76,
                "页面元数据包含娱乐内容关键词。",
                "content_rule",
            )
        return None
