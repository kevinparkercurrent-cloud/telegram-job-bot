from datetime import datetime, timezone

import pytest

from job_bot.collector import ChannelPost
from job_bot.db import Database
from job_bot.domain import VacancyStatus
from job_bot.hr_discovery import HRDiscoveryService, discover_hr_contact


def test_discovers_recruiter_from_direct_igaming_pm_vacancy() -> None:
    lead = discover_hr_contact(
        """
        #vacancy #igaming
        Компания: Lucky Product
        Вакансия: Technical Project Manager
        Ищем специалиста в betting-команду.
        Для отклика пишите @Recruiter_Anna
        """,
        "https://t.me/igaming_jobs/101",
    )

    assert lead is not None
    assert lead.normalized_contact == "recruiter_anna"
    assert lead.contact == "@Recruiter_Anna"
    assert lead.company == "Lucky Product"
    assert lead.role == "Technical Project Manager"
    assert lead.source_post_url == "https://t.me/igaming_jobs/101"
    assert "Project Manager" in lead.relevance_reason


def test_discovers_recruiter_from_adjacent_igaming_role() -> None:
    lead = discover_hr_contact(
        """
        We are hiring a Senior Media Buyer for our iGaming affiliate team.
        Contact our recruiter: https://t.me/hr_betting_team
        """,
        "https://t.me/affiliate_jobs/5",
    )

    assert lead is not None
    assert lead.normalized_contact == "hr_betting_team"
    assert lead.role == "Senior Media Buyer"
    assert "iGaming" in lead.relevance_reason


def test_does_not_treat_candidate_resume_as_hiring_contact() -> None:
    lead = discover_hr_contact(
        """
        #резюме #opentowork #igaming
        Ищу работу Project Manager в betting.
        Обо мне: 4 года опыта. Контакт: @candidate_pm
        """,
        "https://t.me/igaming_jobs/102",
    )

    assert lead is None


def test_does_not_treat_advertising_admin_as_recruiter() -> None:
    lead = discover_hr_contact(
        """
        Вакансия: ASO Specialist в iGaming-команду.
        Отклик через форму: https://example.com/apply
        По вопросам размещения рекламы пишите администратору @channel_admin
        """,
        "https://t.me/igaming_jobs/103",
    )

    assert lead is None


def test_does_not_invent_contact_when_username_is_missing() -> None:
    lead = discover_hr_contact(
        """
        Компания: Betting Labs
        Вакансия: Backend Developer
        Открыта позиция в iGaming-команде. Отклик по ссылке в форме.
        """,
        "https://t.me/igaming_jobs/104",
    )

    assert lead is None


def test_company_is_empty_when_not_explicitly_named() -> None:
    lead = discover_hr_contact(
        """
        Вакансия: Product Designer
        Ищем дизайнера в gambling-продукт.
        Пишите рекрутеру @design_hr
        """,
        "https://t.me/igaming_jobs/105",
    )

    assert lead is not None
    assert lead.company is None


def test_source_channel_username_is_not_selected_as_recruiter() -> None:
    lead = discover_hr_contact(
        """
        Вакансия: QA Engineer в iGaming.
        Новости и вакансии: @igaming_jobs
        """,
        "https://t.me/igaming_jobs/106",
    )

    assert lead is None


def test_english_candidate_post_is_not_treated_as_hiring() -> None:
    lead = discover_hr_contact(
        "Looking for a Project Manager role in iGaming. Contact: @candidate_pm",
        "https://t.me/igaming_jobs/107",
    )

    assert lead is None


def test_english_candidate_seeking_new_opportunities_is_not_hiring() -> None:
    lead = discover_hr_contact(
        (
            "Seeking new opportunities.\n"
            "Role: Project Manager\n"
            "iGaming experience. Contact @anna_hr"
        ),
        "https://t.me/igaming_jobs/1071",
    )

    assert lead is None


@pytest.mark.parametrize(
    "opening",
    ["Open for new opportunities.", "Seeking exciting opportunities."],
)
def test_english_candidate_modifiers_do_not_bypass_resume_filter(
    opening: str,
) -> None:
    lead = discover_hr_contact(
        (
            f"{opening}\n"
            "Role: Project Manager\n"
            "iGaming experience. Contact @anna_hr"
        ),
        "https://t.me/igaming_jobs/1072",
    )

    assert lead is None


def test_unrelated_igaming_role_is_not_treated_as_pm_adjacent() -> None:
    lead = discover_hr_contact(
        "Vacancy: Office Cleaner in iGaming. Contact recruiter @office_hr",
        "https://t.me/igaming_jobs/108",
    )

    assert lead is None


def test_explicit_recruiter_wins_over_other_handle_in_contact_line() -> None:
    lead = discover_hr_contact(
        (
            "Vacancy: Project Manager in iGaming.\n"
            "Contact us on @company_news or send CV to recruiter @real_hr"
        ),
        "https://t.me/igaming_jobs/109",
    )

    assert lead is not None
    assert lead.normalized_contact == "real_hr"


def test_english_advertising_admin_is_not_a_hiring_contact() -> None:
    lead = discover_hr_contact(
        (
            "Vacancy: QA Engineer in betting. Apply using the external form.\n"
            "For advertising and post placement contact admin @sales_admin"
        ),
        "https://t.me/igaming_jobs/110",
    )

    assert lead is None


def test_concise_cv_instruction_is_an_explicit_contact_cue() -> None:
    lead = discover_hr_contact(
        "Vacancy: ASO Specialist, iGaming affiliate. CV @aso_recruiter",
        "https://t.me/igaming_jobs/111",
    )

    assert lead is not None
    assert lead.normalized_contact == "aso_recruiter"


def test_website_application_does_not_leak_score_to_follow_handle() -> None:
    lead = discover_hr_contact(
        (
            "Vacancy: Project Manager in iGaming.\n"
            "Apply via our website form.\n"
            "Follow @luckycompany for updates"
        ),
        "https://t.me/igaming_jobs/112",
    )

    assert lead is None


def hiring_post(message_id: int = 201) -> ChannelPost:
    return ChannelPost(
        channel_id=-100123,
        message_id=message_id,
        published_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
        text=(
            "Компания: Betting Labs\n"
            "Вакансия: Backend Developer в iGaming-команду.\n"
            "Для отклика пишите рекрутеру @Betting_HR"
        ),
        source_post_url=f"https://t.me/igaming_jobs/{message_id}",
    )


@pytest.mark.asyncio
async def test_service_stores_each_normalized_contact_only_once(tmp_path) -> None:
    db = await Database.open(tmp_path / "hr-dedupe.sqlite3")
    service = HRDiscoveryService(db)
    try:
        assert await service.process_post(hiring_post(201)) is True
        duplicate = hiring_post(202)
        duplicate = ChannelPost(
            **{
                **duplicate.__dict__,
                "text": duplicate.text.replace("@Betting_HR", "@betting_hr"),
            }
        )

        assert await service.process_post(duplicate) is False
        stored = await db.list_hr_contacts(("new",))
        assert len(stored) == 1
        assert stored[0].normalized_contact == "betting_hr"
        assert stored[0].role == "Backend Developer"
        assert stored[0].source_post_url == "https://t.me/igaming_jobs/201"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_service_excludes_contact_from_sent_application_history(
    tmp_path, vacancy
) -> None:
    db = await Database.open(tmp_path / "hr-history.sqlite3")
    service = HRDiscoveryService(db)
    previously_contacted = vacancy.model_copy(
        update={"recruiter_username": "Betting_HR"}
    )
    try:
        await db.insert_vacancy(previously_contacted)
        await db.set_vacancy_status(
            previously_contacted.id, VacancyStatus.SENT.value
        )

        assert await service.process_post(hiring_post()) is False
        assert await db.list_hr_contacts(("new",)) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_hr_decision_moves_contact_from_queue_to_history(tmp_path) -> None:
    db = await Database.open(tmp_path / "hr-decision.sqlite3")
    service = HRDiscoveryService(db)
    try:
        assert await service.process_post(hiring_post())

        await db.record_hr_decision("betting_hr", "written")

        assert await db.list_hr_contacts(("new",)) == []
        history = await db.list_hr_contacts(("written", "not_relevant"))
        assert len(history) == 1
        assert history[0].status == "written"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_successful_legacy_application_closes_matching_hr_contact(
    tmp_path, vacancy
) -> None:
    db = await Database.open(tmp_path / "hr-legacy-send.sqlite3")
    service = HRDiscoveryService(db)
    linked = vacancy.model_copy(update={"recruiter_username": "Betting_HR"})
    try:
        assert await service.process_post(hiring_post())
        await db.insert_vacancy(linked)

        await db.finish_send(linked.id, telegram_message_id=99)

        assert await db.list_hr_contacts(("new",)) == []
        history = await db.list_hr_contacts(("written",))
        assert len(history) == 1
        assert history[0].normalized_contact == "betting_hr"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_legacy_send_overrides_previous_not_relevant_hr_decision(
    tmp_path, vacancy
) -> None:
    db = await Database.open(tmp_path / "hr-legacy-override.sqlite3")
    service = HRDiscoveryService(db)
    linked = vacancy.model_copy(update={"recruiter_username": "Betting_HR"})
    try:
        assert await service.process_post(hiring_post())
        await db.record_hr_decision("betting_hr", "not_relevant")
        await db.insert_vacancy(linked)

        await db.finish_send(linked.id, telegram_message_id=100)

        assert await db.list_hr_contacts(("not_relevant",)) == []
        assert len(await db.list_hr_contacts(("written",))) == 1
    finally:
        await db.close()
