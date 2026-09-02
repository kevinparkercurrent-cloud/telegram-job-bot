from __future__ import annotations

import re


MAX_SUMMARY_LENGTH = 350

RESUME_HASHTAG_RE = re.compile(
    r"(?<!\w)#(?:резюме|cv|resume|opentowork)\b", re.IGNORECASE
)
RESUME_HEADING_RE = re.compile(
    r"^\s*(?:резюме|cv|resume)\s*:", re.IGNORECASE | re.MULTILINE
)
JOB_SEEKING_RE = re.compile(
    r"\b(?:"
    r"ищу\s+(?:работу|проект|ваканси[юи]|позици[юи])|"
    r"в\s+поиске\s+(?:работы|проекта|вакансии|позиции)|"
    r"открыт[аы]?\s+к\s+(?:новым\s+)?предложени|"
    r"рассматриваю\s+(?:новые\s+)?предложения"
    r")",
    re.IGNORECASE,
)
RESUME_PROFILE_RE = re.compile(
    r"(?:^|\n)\s*(?:обо\s+мне|желаемая\s+(?:должность|сфера|позиция)|"
    r"ожидания\s+по\s+доходу|формат\s+работы|контакты)\s*:",
    re.IGNORECASE,
)
VACANCY_HASHTAG_RE = re.compile(
    r"(?<!\w)#(?:вакансия|vacancy|job)\b", re.IGNORECASE
)
HIRING_RE = re.compile(
    r"\b(?:мы\s+ищем|ищем\s+(?:в\s+)?команду|открыта\s+вакансия|"
    r"приглашаем\s+в\s+команду)\b",
    re.IGNORECASE,
)
VACANCY_SECTION_RE = re.compile(
    r"(?:^|\n)\s*(?:задачи|обязанности|требования|условия|"
    r"что\s+предлагаем)\s*:",
    re.IGNORECASE,
)

SECTION_PATTERNS = (
    (
        "Задачи",
        re.compile(
            r"^(?:задачи|обязанности|что\s+нужно\s+делать|"
            r"чем\s+предстоит\s+заниматься)\s*:?\s*(.*)$",
            re.IGNORECASE,
        ),
    ),
    (
        "Требования",
        re.compile(
            r"^(?:требования|кого\s+ищем|что\s+мы\s+жд[её]м)\s*:?\s*(.*)$",
            re.IGNORECASE,
        ),
    ),
    (
        "Условия",
        re.compile(
            r"^(?:условия|что\s+предлагаем|мы\s+предлагаем)\s*:?\s*(.*)$",
            re.IGNORECASE,
        ),
    ),
)
CONTACT_RE = re.compile(
    r"(?:^|\s)(?:контакт|отклик|писать|резюме)\s*:|"
    r"@[A-Za-z][A-Za-z0-9_]{4,31}|https?://|\b\S+@\S+\.\S+\b",
    re.IGNORECASE,
)
BULLET_RE = re.compile(r"^[\s•●▪▫*\-–—]+")


def is_candidate_resume(text: str) -> bool:
    resume_score = 0
    if RESUME_HASHTAG_RE.search(text):
        resume_score += 3
    if RESUME_HEADING_RE.search(text):
        resume_score += 3
    if JOB_SEEKING_RE.search(text):
        resume_score += 3
    resume_score += min(2, len(RESUME_PROFILE_RE.findall(text)))

    vacancy_score = 0
    if VACANCY_HASHTAG_RE.search(text):
        vacancy_score += 3
    if HIRING_RE.search(text):
        vacancy_score += 3
    vacancy_score += min(3, len(VACANCY_SECTION_RE.findall(text)))

    return resume_score >= 3 and resume_score > vacancy_score


def summarize_vacancy(text: str, max_length: int = MAX_SUMMARY_LENGTH) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    selected: dict[str, str] = {}
    current_section: str | None = None

    for raw_line in lines:
        line = BULLET_RE.sub("", raw_line).strip()
        if not line:
            continue
        matched_heading = False
        for label, pattern in SECTION_PATTERNS:
            match = pattern.match(line)
            if match is None:
                continue
            current_section = label
            tail = match.group(1).strip()
            if tail and not CONTACT_RE.search(tail):
                selected.setdefault(label, tail)
            matched_heading = True
            break
        if matched_heading:
            continue
        if (
            current_section is not None
            and current_section not in selected
            and not CONTACT_RE.search(line)
        ):
            selected[current_section] = line

    if selected:
        summary = " ".join(
            f"{label}: {selected[label]}"
            for label, _ in SECTION_PATTERNS
            if label in selected
        )
        return _truncate(summary, max_length)

    candidates: list[str] = []
    for part in re.split(r"(?<=[.!?])\s+|\n+", text):
        sentence = BULLET_RE.sub("", " ".join(part.split())).strip()
        if (
            len(sentence) >= 12
            and not sentence.startswith("#")
            and not CONTACT_RE.search(sentence)
        ):
            candidates.append(sentence)
        if len(candidates) == 2:
            break
    return _truncate(" ".join(candidates), max_length)


def _truncate(text: str, max_length: int) -> str:
    if len(text) <= max_length:
        return text
    shortened = text[: max_length - 1].rsplit(" ", 1)[0].rstrip(" ,;:.-")
    return f"{shortened}…"
