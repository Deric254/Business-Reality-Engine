"""
Security questions: a way back into an account that needs no email, so it works for every user whether or not the
server can send mail. Hardened because answers are guessable:
- the person picks QUESTION_COUNT questions from a fixed list, and ALL answers must be right to reset;
- answers are normalised (case, spacing, accents), then hashed with bcrypt like a password; nothing readable is stored;
- recovery attempts are throttled per source address and locked per email (see app/api/auth.py);
- asking for an email's questions answers the same shape for unknown emails (a repeatable decoy), so it reveals nothing.
"""
import hashlib
import hmac
import re
import unicodedata

from app.core.auth_users import _get_jwt_secret, hash_password, verify_password

QUESTION_COUNT = 3
MIN_ANSWER_CHARS = 3
QUESTIONS = [
    "What was the name of your first school?",
    "What was the first town or village you lived in?",
    "What was your first phone number's last four digits?",
    "What was the name of your childhood best friend?",
    "What was the name of your first pet?",
    "What was your first job?",
    "What is your mother's first name?",
    "What is your oldest cousin's first name?",
    "What was the make of your first vehicle?",
    "What is the name of the street you grew up on?",
]
# Compared against when an account has no questions, so a missing account costs the same time as a real one.
_DECOY_HASH = hash_password("decoy")


def normalize(answer: str) -> str:
    folded = unicodedata.normalize("NFKD", answer).casefold()
    return re.sub(r"\s+", " ", "".join(c for c in folded if not unicodedata.combining(c))).strip()


def _digest(answer: str) -> str:
    """bcrypt reads only 72 bytes; hashing first lets an answer be any length."""
    return hashlib.sha256(normalize(answer).encode()).hexdigest()


def hash_answers(items: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"q": q, "h": hash_password(_digest(a))} for q, a in items]


def answers_match(stored: list[dict[str, str]] | None, answers: list[str]) -> bool:
    """True only when every answer is right. Always does QUESTION_COUNT checks, right or wrong."""
    has_questions = bool(stored) and len(stored) == QUESTION_COUNT
    rows = stored if has_questions else [{"h": _DECOY_HASH}] * QUESTION_COUNT
    given = (answers + [""] * QUESTION_COUNT)[:QUESTION_COUNT]
    results = [verify_password(_digest(a), row["h"]) for row, a in zip(rows, given)]
    return has_questions and len(answers) == QUESTION_COUNT and all(results)


def decoy_questions(email: str) -> list[str]:
    """The same invented questions for the same unknown email every time, so asking twice reveals nothing."""
    seed = hmac.new(_get_jwt_secret().encode(), email.strip().lower().encode(), hashlib.sha256).digest()
    order = sorted(range(len(QUESTIONS)), key=lambda i: seed[i % len(seed)] * 31 + i)
    return [QUESTIONS[i] for i in order[:QUESTION_COUNT]]


def validate_choice(items: list[tuple[str, str]]) -> str | None:
    """None when acceptable, else the reason in plain words."""
    if len(items) != QUESTION_COUNT:
        return f"Choose {QUESTION_COUNT} questions."
    if len({q for q, _ in items}) != QUESTION_COUNT or any(q not in QUESTIONS for q, _ in items):
        return "Choose different questions from the list."
    answers = [normalize(a) for _, a in items]
    if any(len(a) < MIN_ANSWER_CHARS for a in answers):
        return f"Each answer needs at least {MIN_ANSWER_CHARS} characters."
    if len(set(answers)) != QUESTION_COUNT:
        return "Use a different answer for each question."
    return None
