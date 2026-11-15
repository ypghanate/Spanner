from backend.speech import clean_line, narrates_lookup, similar


def test_lookup_narration_is_removed():
    for line in ["Let me consult the manual for that.", "I'm checking the repair guide for this one.", "Pulling up the instructions now.",
                 "Let me look that up in the manual.", "I'll go through the guide for that specific model.", "Reading the manual for that."]:
        assert narrates_lookup(line), line
        assert clean_line(line, []) == "", line


def test_real_content_that_mentions_a_manual_stays():
    assert clean_line("The manual says 25 Nm for that bolt.", []) == "The manual says 25 Nm for that bolt."
    assert clean_line("That sounds like a worn ear tip. When did it start?", []) == "That sounds like a worn ear tip. When did it start?"


def test_only_the_lookup_sentence_is_dropped():
    assert clean_line("A quiet earbud often means clogged mesh. Let me consult the manual for that.", []) == "A quiet earbud often means clogged mesh."


def test_the_same_point_in_other_words_is_dropped():
    said = ["I'm finding the photo and reading the repair guides for your AirPods now."]
    assert clean_line("I am still reading the repair guides for your AirPods.", said) == ""
    assert similar("Is the right earbud quieter than the left?", "Is the left earbud quieter than the right one?")


def test_a_new_point_is_kept_and_short_lines_are_not_blocked():
    said = ["Does the right earbud work at all?"]
    assert clean_line("When did the sound get quiet?", said) == "When did the sound get quiet?"
    assert clean_line("Okay.", ["Okay."]) == ""            # an exact repeat of a short line is still a repeat
    assert clean_line("Right.", ["Okay."]) == "Right."


def test_repeats_inside_one_line_are_dropped():
    assert clean_line("Check the charging case contacts. Check the contacts on the charging case.", []) == "Check the charging case contacts."
