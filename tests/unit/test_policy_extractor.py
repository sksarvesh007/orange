"""Unit tests for structural semantic extractor and memory command parser.

Verifies deterministic extraction without regex, edge cases, currency variants,
and command boundaries.
"""

from errand_agent.domain.enums import Intent
from errand_agent.policy.extractor import (
    CorrectFactCommand,
    ForgetAllMemoryCommand,
    ForgetFactCommand,
    InspectMemoryCommand,
    extract_intent_and_slots,
    parse_memory_command,
    tokenize,
)


def test_tokenize_clean_and_symbols() -> None:
    tokens = tokenize("Find a plumber under ₹1,500, not before 10 a.m.!")
    assert "find" in tokens
    assert "plumber" in tokens
    assert "under" in tokens
    assert "1,500" in tokens
    assert "not" in tokens
    assert "before" in tokens
    assert "10" in tokens


def test_extract_slots_full_utterance() -> None:
    res = extract_intent_and_slots(
        "Find a plumber for my kitchen tap this week in Indiranagar under ₹1,500."
    )
    assert res.intent == Intent.BOOK
    assert res.service == "plumber"
    assert res.area == "Indiranagar"
    assert res.date_range == "this week"
    assert res.budget == 1500
    assert res.reference_last_provider is False


def test_extract_slots_earliest_time_and_reference() -> None:
    res = extract_intent_and_slots("Book the same electrician as last time, but not before 10 a.m.")
    assert res.intent == Intent.BOOK
    assert res.service == "electrician"
    assert res.reference_last_provider is True
    assert res.earliest_time == "10:00 AM"


def test_extract_slots_reschedule_intent() -> None:
    res = extract_intent_and_slots("Move my AC servicing to Saturday morning.")
    assert res.intent == Intent.RESCHEDULE
    assert res.service == "ac servicing"
    assert res.date_range == "Saturday morning"


def test_extract_slots_cancel_intent() -> None:
    res = extract_intent_and_slots("Please cancel my booking.")
    assert res.intent == Intent.CANCEL


def test_extract_budget_variants() -> None:
    # "budget of 1200"
    b1 = extract_intent_and_slots("Need a carpenter with a budget of 1200").budget
    assert b1 == 1200

    # "max 2500"
    b2 = extract_intent_and_slots("cleaning max 2500").budget
    assert b2 == 2500

    # "under 800"
    b3 = extract_intent_and_slots("plumber under 800").budget
    assert b3 == 800

    # "below rs 1000"
    b4 = extract_intent_and_slots("electrician below rs 1000").budget
    assert b4 == 1000


def test_parse_memory_command_inspect() -> None:
    cmd = parse_memory_command("What do you remember about me?")
    assert isinstance(cmd, InspectMemoryCommand)

    cmd2 = parse_memory_command("my preferences")
    assert isinstance(cmd2, InspectMemoryCommand)


def test_parse_memory_command_forget_all() -> None:
    cmd = parse_memory_command("forget everything")
    assert isinstance(cmd, ForgetAllMemoryCommand)

    cmd2 = parse_memory_command("delete all memory")
    assert isinstance(cmd2, ForgetAllMemoryCommand)


def test_parse_memory_command_forget_specific() -> None:
    cmd = parse_memory_command("forget area")
    assert isinstance(cmd, ForgetFactCommand)
    assert cmd.key == "area"

    cmd2 = parse_memory_command("forget preferred technician")
    assert isinstance(cmd2, ForgetFactCommand)
    assert cmd2.key == "preferred technician"


def test_parse_memory_command_correct() -> None:
    cmd = parse_memory_command("correct area to Indiranagar 100 Feet Rd")
    assert isinstance(cmd, CorrectFactCommand)
    assert cmd.key == "area"
    assert cmd.value == "Indiranagar 100 Feet Rd"  # Preserves case and spaces!


def test_parse_memory_command_non_command() -> None:
    assert parse_memory_command("Find a plumber this week") is None
    assert parse_memory_command("yes") is None
