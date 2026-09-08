from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from pydantic import BaseModel

from job_bot.post_analysis import is_candidate_resume

if TYPE_CHECKING:
    from job_bot.collector import ChannelPost
    from job_bot.db import Database


class HRLeadCandidate(BaseModel):
    contact: str
    normalized_contact: str
    company: str | None = None
    role: str
    relevance_reason: str
    source_post_url: str


class HRContactCard(BaseModel):
    id: str
    contact: str
    company: str | None = None
    role: str
    relevance_reason: str
    source_post_url: str
    outreach_text: str


HR_OUTREACH_TEXT = """Привет! Ищу позицию Project / Technical Project Manager в iGaming. Подскажите, рассматриваете ли сейчас такого специалиста в вашу команду?

У меня 4+ года в iGaming и affiliate: вёл до 10 проектов одновременно, координировал команду из 7 человек, сопровождал разработку и публикацию 200+ мобильных приложений. Сам занимался медиабаингом, поэтому понимаю affiliate изнутри: работу с трафиком, офферами, воронками, трекингом и атрибуцией, а также задачи байеров и важность быстрых запусков.

Моя сильная сторона — сочетание управления проектами и технической подкованности. Могу самостоятельно проверить API и события аналитики, разобраться в проблемах перед релизом. Работал с Nginx, Cloudflare, VPS и базами данных, с помощью AI-инструментов запустил 150+ небольших веб-проектов.

Могу взять на себя организацию разработки и технических задач под нужды команды: от требований и распределения ресурсов до контроля сроков, качества и запуска. Прикрепляю резюме — буду рад обсудить, где мой опыт может быть полезен!"""


IGAMING_RE = re.compile(
    r"\b(?:i[- ]?gaming|betting|sportsbook|gambling|casino|affiliate|"
    r"беттинг|гемблинг|казино|букмекер|арбитраж)\b",
    re.IGNORECASE,
)
HIRING_RE = re.compile(
    r"\b(?:vacanc(?:y|ies)|we\s+are\s+hiring|hiring|"
    r"ваканси[яи]|ищем|открыта\s+позиция|набираем)\b",
    re.IGNORECASE,
)
HIRING_LABEL_RE = re.compile(
    r"^\s*(?:vacancy|position|role|вакансия)\s*:",
    re.IGNORECASE | re.MULTILINE,
)
ENGLISH_JOB_SEEKING_RE = re.compile(
    r"\b(?:(?:looking\s+for|seeking)\s+(?:[a-z]+\s+){0,4}"
    r"(?:job|role|position|opportunit\w*|work)|"
    r"(?:open|available)\s+(?:(?:to|for)\s+)?(?:[a-z]+\s+){0,4}"
    r"(?:job|role|position|opportunit\w*|work))",
    re.IGNORECASE,
)
CONTACT_CUE_RE = re.compile(
    r"\b(?:contact|recruiter|talent|hr|apply|cv|resume|dm|telegram|tg|"
    r"отклик|писать|пишите|контакт|рекрутер|резюме|телеграм)\b",
    re.IGNORECASE,
)
AD_ADMIN_RE = re.compile(
    r"(?:размещени[яе]\s+(?:рекламы|ваканс)|по\s+вопросам\s+размещения|"
    r"реклам|публикаци|администратор|\badmin\b|advertis|"
    r"post\s+placement|paid\s+placement)",
    re.IGNORECASE,
)
DANGLING_CONTACT_RE = re.compile(
    r"^\s*(?:(?:contact|apply|cv|resume|dm|telegram|tg|контакт|отклик|"
    r"резюме|телеграм)(?:\s+(?:the\s+)?(?:recruiter|hr|рекрутер))?|"
    r"(?:для\s+отклика|для\s+связи))\s*:?\s*$",
    re.IGNORECASE,
)
NON_TELEGRAM_APPLICATION_RE = re.compile(
    r"\b(?:website|site|form|email|e-mail|сайт|форм[аеуы]|почт[аеу])\b",
    re.IGNORECASE,
)
USERNAME_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_]{4,31})\b")
TELEGRAM_LINK_RE = re.compile(
    r"https?://(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{4,31})\b",
    re.IGNORECASE,
)
COMPANY_RE = re.compile(
    r"^\s*(?:компания|company|бренд|brand)\s*:\s*([^\n|]{2,80})\s*$",
    re.IGNORECASE | re.MULTILINE,
)
ROLE_LABEL_RE = re.compile(
    r"^\s*(?:вакансия|vacancy|position|role)\s*:\s*([^\n|]{2,100})\s*$",
    re.IGNORECASE | re.MULTILINE,
)
HIRING_ROLE_RE = re.compile(
    r"\b(?:we\s+are\s+)?hiring\s+(?:an?\s+)?(.{2,80}?)\s+for\s+",
    re.IGNORECASE,
)
KNOWN_ROLE_RE = re.compile(
    r"\b((?:(?:senior|middle|junior|lead|technical|product)\s+)?(?:"
    r"project\s+manager|product\s+manager|program\s+manager|"
    r"delivery\s+manager|product\s+owner|scrum\s+master|tech\s+lead|"
    r"media\s+buyer|aso\s+specialist|backend\s+developer|frontend\s+developer|"
    r"(?:software|qa|data|devops)\s+engineer|product\s+designer|"
    r"(?:business|product|data)\s+analyst|affiliate\s+manager|"
    r"crm\s+manager|user\s+acquisition\s+manager|"
    r"руководитель\s+проектов|проджект[- ]менеджер|медиабайер|"
    r"продакт[- ]менеджер|продуктовый\s+аналитик|бизнес[- ]аналитик|"
    r"разработчик|дизайнер|тестировщик))\b",
    re.IGNORECASE,
)


def discover_hr_contact(
    text: str, source_post_url: str | None
) -> HRLeadCandidate | None:
    if (
        not source_post_url
        or is_candidate_resume(text)
        or ENGLISH_JOB_SEEKING_RE.search(text)
        or not IGAMING_RE.search(text)
        or not (HIRING_RE.search(text) or HIRING_LABEL_RE.search(text))
    ):
        return None

    source_username = _source_username(source_post_url)
    contact = _contact_from_hiring_context(text, source_username)
    if contact is None:
        return None

    role = _extract_role(text)
    if role is None:
        return None
    company_match = COMPANY_RE.search(text)
    company = company_match.group(1).strip(" .—-") if company_match else None
    if "project manager" in role.casefold() or "проект" in role.casefold():
        reason = f"Прямой HR-контакт по iGaming-вакансии {role}."
    else:
        reason = (
            f"Контакт нанимает в iGaming-команду на роль {role}; "
            "релевантен для выхода на Project / Technical PM позиции."
        )
    username, original = contact
    return HRLeadCandidate(
        contact=original,
        normalized_contact=username,
        company=company,
        role=role,
        relevance_reason=reason,
        source_post_url=source_post_url,
    )


class HRDiscoveryService:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def process_post(self, post: ChannelPost) -> bool:
        lead = discover_hr_contact(post.text, post.source_post_url)
        if lead is None:
            return False
        return await self._database.insert_hr_contact(
            lead, f"{post.channel_id}:{post.message_id}"
        )


def _contact_from_hiring_context(
    text: str, source_username: str | None
) -> tuple[str, str] | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if NON_TELEGRAM_APPLICATION_RE.search(line):
            continue
        context = line
        if DANGLING_CONTACT_RE.match(line) and index + 1 < len(lines):
            context = f"{line} {lines[index + 1]}"
        if not CONTACT_CUE_RE.search(context) or AD_ADMIN_RE.search(context):
            continue
        candidates = [
            (match.start(), match.group(1).casefold(), f"@{match.group(1)}")
            for match in USERNAME_RE.finditer(context)
        ]
        candidates.extend(
            (match.start(), match.group(1).casefold(), f"@{match.group(1)}")
            for match in TELEGRAM_LINK_RE.finditer(context)
        )
        scored: list[tuple[int, str, str]] = []
        for position, normalized, original in candidates:
            if normalized == source_username:
                continue
            prefix = context[max(0, position - 70) : position].casefold()
            prefix = re.split(r"[.;!?]", prefix)[-1]
            score = 0
            if re.search(r"(?:recruiter|talent|\bhr\b|рекрутер)", prefix):
                score += 5
            if re.search(
                r"(?:apply|\bcv\b|resume|\bdm\b|отклик|резюме|"
                r"писать|пишите|телеграм|\btg\b)",
                prefix,
            ):
                score += 3
            elif re.search(r"(?:contact|контакт)", prefix):
                score += 1
            if re.search(r"(?:hr|recruit|talent)", normalized):
                score += 2
            if re.search(r"(?:news|channel|admin|sales|advert)", normalized):
                score -= 4
            if re.search(r"(?:follow|subscribe|updates|подпис)", prefix):
                score -= 4
            scored.append((score, normalized, original))
        if scored:
            scored.sort(reverse=True)
            best_score = scored[0][0]
            best = [item for item in scored if item[0] == best_score]
            if best_score >= 2 and len(best) == 1:
                return best[0][1], best[0][2]
    return None


def _source_username(source_post_url: str) -> str | None:
    path = urlparse(source_post_url).path.strip("/")
    if not path or path.startswith("c/"):
        return None
    return path.split("/", 1)[0].casefold()


def _extract_role(text: str) -> str | None:
    match = ROLE_LABEL_RE.search(text)
    if match:
        return _known_role(match.group(1))
    match = HIRING_ROLE_RE.search(text)
    if match:
        return _known_role(match.group(1))
    match = KNOWN_ROLE_RE.search(text)
    if match:
        return _clean_role(match.group(1))
    return None


def _known_role(value: str) -> str | None:
    match = KNOWN_ROLE_RE.search(value)
    return _clean_role(match.group(1)) if match else None


def _clean_role(value: str) -> str:
    role = re.split(
        r"\s+(?:в|для|for)\s+(?=(?:i[- ]?gaming|betting|gambling|"
        r"casino|affiliate|беттинг|гемблинг|казино))",
        value,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return role.strip(" .—-")
