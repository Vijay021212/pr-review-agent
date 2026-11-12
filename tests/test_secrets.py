"""Tests for secret masking: every known pattern is masked, ordinary code is left alone."""

from __future__ import annotations

import pytest

from pr_review_agent.secrets import Masker, find_secrets, mask

GITHUB_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
FINE_GRAINED = "github_pat_" + "11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz"
OPENAI_KEY = "sk-proj-" + "abcdefghijklmnopqrstuvwxyz0123"
ANTHROPIC_KEY = "sk-ant-api03-" + "abcdefghijklmnopqrstuvwxyz-XYZ9"
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
PRIVATE_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEA7bq2examplekeymaterial\n"
    "-----END RSA PRIVATE KEY-----"
)


@pytest.mark.parametrize(
    ("secret", "masked", "kind"),
    [
        (GITHUB_TOKEN, "ghp_...Q7r8", "GitHub token"),
        (FINE_GRAINED, "github_pat_...wxyz", "GitHub token"),
        (OPENAI_KEY, "sk-...0123", "OpenAI-style API key"),
        (ANTHROPIC_KEY, "sk-ant-...XYZ9", "Anthropic API key"),
        (AWS_KEY, "AKIA...MPLE", "AWS access key"),
        (
            PRIVATE_KEY,
            "-----BEGIN RSA PRIVATE KEY-----...masked...-----END RSA PRIVATE KEY-----",
            "private key",
        ),
    ],
)
def test_each_known_token_is_masked(secret: str, masked: str, kind: str) -> None:
    text = f"config = load({secret!r})\n"
    assert mask(text) == f"config = load({masked!r})\n"
    assert [match.kind for match in find_secrets(text)] == [kind]


@pytest.mark.parametrize(
    ("line", "masked"),
    [
        ('password = "hunter2hunter2hunter2"', 'password = "...ter2"'),
        ("PASSWORD='hunter2'", "PASSWORD='****'"),
        ('db_password = "abcd"', 'db_password = "abcd"'),  # only a whole-word name counts
        ('"api_key": "0123456789abcdef0123"', '"api_key": "...0123"'),
        ("apiKey: 'shortvalue'", "apiKey: '****'"),
        ('secret = "s3cr3t-value"', 'secret = "****"'),
        ('TOKEN="Bearer"', 'TOKEN="****"'),
        ('access_key="abcdefgh"', 'access_key="****"'),
        ('pwd = "1234"', 'pwd = "****"'),
    ],
)
def test_passwords_and_keys_in_code_are_masked(line: str, masked: str) -> None:
    assert mask(line) == masked


def test_a_private_key_without_an_end_line_is_masked_to_the_end() -> None:
    text = "key:\n-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC"
    assert mask(text) == "key:\n-----BEGIN PRIVATE KEY-----...masked..."


def test_several_secrets_in_one_text_are_all_masked() -> None:
    text = f"one {GITHUB_TOKEN} two {AWS_KEY} three"
    assert mask(text) == "one ghp_...Q7r8 two AKIA...MPLE three"
    assert [m.kind for m in find_secrets(text)] == ["GitHub token", "AWS access key"]


def test_overlapping_matches_are_masked_once() -> None:
    text = f'token = "{GITHUB_TOKEN}"'
    matches = find_secrets(text)
    assert len(matches) == 1
    assert mask(text) == 'token = "ghp_...Q7r8"'


@pytest.mark.parametrize(
    "text",
    [
        f"value {GITHUB_TOKEN} and {OPENAI_KEY}",
        'password = "hunter2hunter2hunter2"',
        "PASSWORD='hunter2'",
        PRIVATE_KEY + "\nafter the key",
    ],
)
def test_masking_twice_changes_nothing(text: str) -> None:
    assert mask(mask(text)) == mask(text)


@pytest.mark.parametrize(
    "line",
    [
        "password = input('Password: ')",
        "token = get_token(user)",
        'token = ""',
        'secret_name = "database"',
        "tokenizer = Tokenizer(vocab)",
        "sha = '0123456789abcdef0123456789abcdef01234567'",
        "sk-learn is a library; risk-free",
        "Use AKIA keys only in tests",
        "def check_password(password: str) -> bool:",
        "-----BEGIN PUBLIC KEY-----",
    ],
)
def test_ordinary_code_is_left_alone(line: str) -> None:
    assert mask(line) == line
    assert find_secrets(line) == []


def test_known_values_are_masked_even_without_a_known_format() -> None:
    masker = Masker(["my-company-token-0000-9876", "short", ""])
    assert masker("auth my-company-token-0000-9876 and short") == "auth ...9876 and ****"


def test_a_known_value_inside_another_is_masked_as_a_whole() -> None:
    masker = Masker(["abcd", "abcdefghijklmnopqrst"])
    assert masker("x abcdefghijklmnopqrst y") == "x ...qrst y"


def test_known_values_and_patterns_work_together() -> None:
    masker = Masker(["my-company-token-0000-9876"])
    assert masker(f"{GITHUB_TOKEN} my-company-token-0000-9876") == "ghp_...Q7r8 ...9876"
