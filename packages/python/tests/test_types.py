import pytest

from murakumo.ir.types import TypeName, parse_type


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("string", "string"),
        ("STRING", "string"),
        ("decimal(10,2)", "decimal(10,2)"),
        ("decimal( 38 , 4 )", "decimal(38,4)"),
        ("timestamp_tz", "timestamp_tz"),
        ("array<bigint>", "array<bigint>"),
        ("array<decimal(10,2)>", "array<decimal(10,2)>"),
    ],
)
def test_parse_type_normalises(text, expected):
    assert str(parse_type(text)) == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("varchar", "unknown logical type"),
        ("integer(4)", "at most"),
        ("decimal(2,5)", "scale cannot exceed precision"),
        ("decimal(a,b)", "must be integers"),
        ("array", "needs an element type"),
        ("string<int>", "does not take an element type"),
    ],
)
def test_parse_type_rejects(text, message):
    with pytest.raises(ValueError, match=message):
        parse_type(text)


def test_families_group_joinable_types():
    assert parse_type("integer").family == parse_type("bigint").family
    assert parse_type("money").family == parse_type("decimal(10,2)").family
    assert parse_type("string").family != parse_type("integer").family
    assert parse_type("json").name is TypeName.JSON
