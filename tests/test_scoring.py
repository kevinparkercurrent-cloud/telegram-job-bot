from pathlib import Path

from job_bot.config import load_candidate_profile
from job_bot.domain import MatchClass
from job_bot.scoring import ScoringPolicy, score_vacancy


def test_explicit_salary_below_floor_is_rejected(vacancy) -> None:
    profile = load_candidate_profile(Path("config/candidate-profile.full.json"))
    result = score_vacancy(
        vacancy.model_copy(update={"salary_present": True, "salary_min_rub": 99_999}),
        profile,
        ScoringPolicy(),
    )
    assert result.match_class == MatchClass.REJECTED


def test_missing_salary_remains_eligible(vacancy) -> None:
    profile = load_candidate_profile(Path("config/candidate-profile.full.json"))
    result = score_vacancy(
        vacancy.model_copy(
            update={
                "salary_present": False,
                "salary_min_rub": None,
                "remote": True,
                "raw_text": "Technical Project Manager mobile web delivery QA API",
            }
        ),
        profile,
        ScoringPolicy(),
    )
    assert result.hard_rejection is None
    assert result.match_class == MatchClass.STRONG


def test_english_above_b1_caps_strong_match(vacancy) -> None:
    profile = load_candidate_profile(Path("config/candidate-profile.full.json"))
    result = score_vacancy(
        vacancy.model_copy(
            update={
                "salary_present": False,
                "remote": True,
                "english_required": "C1",
                "raw_text": "Technical Project Manager mobile web delivery QA API",
            }
        ),
        profile,
        ScoringPolicy(),
    )
    assert result.match_class == MatchClass.BORDERLINE
    assert result.score == 74
    assert any("B1" in item for item in result.warnings)
