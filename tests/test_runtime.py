from job_bot.runtime import vacancy_keyboard


def test_keyboard_contains_original_post_and_edit_buttons() -> None:
    keyboard = vacancy_keyboard("v1", "https://t.me/jobs_feed/7")
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    assert any(
        button.text == "Открыть вакансию"
        and button.url == "https://t.me/jobs_feed/7"
        for button in buttons
    )
    assert any(
        button.text == "Редактировать отклик"
        and button.callback_data == "edit_prompt:v1"
        for button in buttons
    )


def test_keyboard_omits_url_button_without_source_link() -> None:
    keyboard = vacancy_keyboard("v1", None)

    assert all(
        button.url is None
        for row in keyboard.inline_keyboard
        for button in row
    )
