import tomllib
from pathlib import Path
from types import ModuleType
from uuid import NAMESPACE_URL, uuid5

import pytest

EXAMPLE = (
    Path(__file__).resolve().parents[3] / "scripts/label_eval_personas/example.toml"
)


def _fields(**values: str) -> dict[str, str | None]:
    keys = ("app_bundle_id", "app_name", "window_title", "tab_title", "web_host")
    return {key: values.get(key) for key in keys}


def _persona(module: ModuleType, rules: list[dict[str, object]]):
    return module.parse_persona(
        {"name": "p", "labels": ["공부", "휴식", "소통"], "rules": rules}
    )


def test_example_persona_takes_the_first_matching_rule(
    label_eval_cases: ModuleType,
) -> None:
    persona = label_eval_cases.load_persona(EXAMPLE)

    lecture = _fields(web_host="www.youtube.com", tab_title="파이썬 강의 1편")
    assert persona.gold(lecture) == "공부"
    assert persona.gold(_fields(web_host="www.youtube.com", tab_title="음악")) == "휴식"
    assert persona.gold(_fields(app_name="Slack")) == "소통"
    assert persona.gold(_fields(app_name="Notion", window_title="메모")) is None
    assert persona.gold(_fields(app_name="Finder")) is None
    assert persona.label_id("공부") == str(
        uuid5(NAMESPACE_URL, "mosemo-label-eval/example/공부")
    )


def test_rule_conditions_are_and_and_lists_are_or(label_eval_cases: ModuleType) -> None:
    persona = _persona(
        label_eval_cases,
        [
            {
                "app_bundle_id": ["com.google.Chrome", "com.apple.Safari"],
                "web_host_suffix": ["youtube.com"],
                "title_contains_any": ["Tutorial", "강의"],
                "label": "공부",
            },
            {"web_host_suffix": ["youtube.com"], "label": "휴식"},
        ],
    )
    browser = {"app_bundle_id": "com.apple.Safari", "web_host": "m.youtube.com"}

    assert persona.gold(_fields(**browser, window_title="a TUTORIAL")) == "공부"
    assert persona.gold(_fields(**browser, tab_title="좋은 강의")) == "공부"
    assert persona.gold(_fields(**browser, tab_title="음악")) == "휴식"
    other_app = {**browser, "app_bundle_id": "org.mozilla.firefox"}
    assert persona.gold(_fields(**other_app, tab_title="강의")) == "휴식"
    # A host suffix matches whole domain labels only.
    assert persona.gold(_fields(web_host="notyoutube.com")) is None
    assert persona.gold(_fields(web_host="youtube.com")) == "휴식"


def test_rules_read_fields_so_a_title_with_newlines_cannot_fake_a_field(
    label_eval_cases: ModuleType,
) -> None:
    persona = _persona(
        label_eval_cases,
        [
            {"app_name": ["Slack"], "label": "소통"},
            {"title_contains_any": ["강의"], "label": "공부"},
        ],
    )

    faked = _fields(app_name="Finder", window_title="노트\napp_name: Slack")
    assert persona.gold(faked) is None
    assert persona.gold(_fields(app_name="Finder", window_title="1장\n강의")) == "공부"


def test_null_label_rule_makes_abstention_the_gold(
    label_eval_cases: ModuleType,
) -> None:
    persona = _persona(
        label_eval_cases,
        [
            {"app_name": ["Notion"], "label": "null"},
            {"app_name": ["Notion", "Slack"], "label": "소통"},
        ],
    )

    assert persona.gold(_fields(app_name="Notion")) is None
    assert persona.gold(_fields(app_name="Slack")) == "소통"


LABELS = ["공부", "휴식"]
RULE = {"app_name": ["Slack"], "label": "공부"}


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"name": "p", "labels": LABELS, "rules": [], "extra": 1}, "unknown persona"),
        (
            {"name": "p", "labels": LABELS, "rules": [{**RULE, "url": ["x"]}]},
            "unknown rule 0",
        ),
        (
            {"name": "p", "labels": LABELS, "rules": [{**RULE, "label": "업무"}]},
            "not in labels",
        ),
        ({"name": "p", "labels": LABELS, "rules": [{"label": "공부"}]}, "no condition"),
        ({"name": "p", "labels": ["공부", "null"]}, "reserved"),
        ({"name": "p", "labels": ["공부", "none"]}, "reserved"),
        ({"name": "p", "labels": ["공부", "공부"]}, "duplicate"),
        ({"name": "p", "labels": ["공부"]}, "2 to 254"),
        ({"name": "p", "labels": [f"l{i}" for i in range(255)]}, "2 to 254"),
        ({"name": "a/b", "labels": LABELS}, "without '/'"),
    ],
)
def test_invalid_personas_are_rejected(
    label_eval_cases: ModuleType, data: dict[str, object], message: str
) -> None:
    with pytest.raises(label_eval_cases.InputError, match=message):
        label_eval_cases.parse_persona(data)


def test_label_count_bounds_are_inclusive(label_eval_cases: ModuleType) -> None:
    for count in (2, 254):
        persona = label_eval_cases.parse_persona(
            {"name": "p", "labels": [f"l{i}" for i in range(count)]}
        )
        assert len(persona.labels) == count


def test_owner_label_names_have_no_rules_and_are_named_owner(
    label_eval_cases: ModuleType,
) -> None:
    owner = label_eval_cases.parse_persona({"labels": LABELS}, with_rules=False)

    assert owner.name == "owner"
    assert owner.rules == ()
    with pytest.raises(label_eval_cases.InputError, match="unknown persona"):
        label_eval_cases.parse_persona(
            {"labels": LABELS, "rules": [RULE]}, with_rules=False
        )
    with pytest.raises(label_eval_cases.InputError, match="unknown persona"):
        label_eval_cases.parse_persona(
            {"name": "me", "labels": LABELS}, with_rules=False
        )


def test_non_string_rule_label_is_named_by_type_not_value(
    label_eval_cases: ModuleType,
) -> None:
    text = f"""
name = "p"
labels = ["공부", "휴식"]
[[rules]]
app_name = ["Slack"]
label = 0x{"F" * 4000}
"""
    data = tomllib.loads(text)

    with pytest.raises(label_eval_cases.InputError) as excinfo:
        label_eval_cases.parse_persona(data)
    assert str(excinfo.value) == "rule 0 label is not in labels: a int value"
