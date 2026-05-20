from src.moments.marengo_impl import _Clip, _conflicts_with_accepted, _enforce_constraints


def _clip(
    start: float, end: float, score: float = 1.0, confidence: str = "medium"
) -> _Clip:
    return _Clip(start=start, end=end, score=score, confidence=confidence)


class TestConflictsWithAccepted:
    def test_no_conflict_when_accepted_list_is_empty(self) -> None:
        assert _conflicts_with_accepted(_clip(0.0, 10.0), []) is False

    def test_overlapping_clips_conflict(self) -> None:
        assert _conflicts_with_accepted(_clip(10.0, 25.0), [_clip(15.0, 30.0)]) is True

    def test_contained_clip_conflicts(self) -> None:
        # clip entirely inside accepted
        assert _conflicts_with_accepted(_clip(5.0, 10.0), [_clip(0.0, 20.0)]) is True

    def test_gap_below_minimum_is_a_conflict(self) -> None:
        # accepted ends at 20, clip starts at 24 → gap 4 < 5
        assert _conflicts_with_accepted(_clip(24.0, 34.0), [_clip(0.0, 20.0)]) is True

    def test_gap_exactly_at_minimum_is_not_a_conflict(self) -> None:
        # accepted ends at 20, clip starts at 25 → gap 5 (not < 5)
        assert _conflicts_with_accepted(_clip(25.0, 35.0), [_clip(0.0, 20.0)]) is False

    def test_gap_above_minimum_is_not_a_conflict(self) -> None:
        assert _conflicts_with_accepted(_clip(30.0, 40.0), [_clip(0.0, 20.0)]) is False

    def test_clip_before_accepted_with_small_gap_is_a_conflict(self) -> None:
        # clip ends at 10, accepted starts at 14 → gap 4 < 5
        assert _conflicts_with_accepted(_clip(0.0, 10.0), [_clip(14.0, 24.0)]) is True

    def test_clip_before_accepted_with_sufficient_gap_is_not_a_conflict(self) -> None:
        # clip ends at 10, accepted starts at 15 → gap 5
        assert _conflicts_with_accepted(_clip(0.0, 10.0), [_clip(15.0, 25.0)]) is False

    def test_conflict_against_any_accepted_clip(self) -> None:
        # Two accepted clips; new clip conflicts only with the second
        accepted = [_clip(0.0, 10.0), _clip(100.0, 120.0)]
        assert _conflicts_with_accepted(_clip(104.0, 115.0), accepted) is True


class TestEnforceConstraints:
    def test_empty_input_returns_empty(self) -> None:
        assert _enforce_constraints([], max_duration=None) == []

    def test_drops_clips_shorter_than_minimum_duration(self) -> None:
        result = _enforce_constraints([_clip(0.0, 4.9)], max_duration=None)
        assert result == []

    def test_keeps_clips_at_exactly_minimum_duration(self) -> None:
        clip = _clip(0.0, 5.0)
        result = _enforce_constraints([clip], max_duration=None)
        assert result == [clip]

    def test_drops_clips_exceeding_max_duration(self) -> None:
        result = _enforce_constraints([_clip(0.0, 200.0)], max_duration=180.0)
        assert result == []

    def test_keeps_clips_at_exactly_max_duration(self) -> None:
        clip = _clip(0.0, 180.0)
        result = _enforce_constraints([clip], max_duration=180.0)
        assert result == [clip]

    def test_keeps_long_clips_when_no_max_duration(self) -> None:
        clip = _clip(0.0, 300.0)
        result = _enforce_constraints([clip], max_duration=None)
        assert result == [clip]

    def test_higher_score_wins_when_clips_overlap(self) -> None:
        low = _clip(0.0, 30.0, score=0.6)
        high = _clip(20.0, 50.0, score=0.9)
        result = _enforce_constraints([low, high], max_duration=None)
        # high accepted first; low conflicts with it → only high remains
        assert result == [high]

    def test_non_conflicting_clips_all_accepted(self) -> None:
        a = _clip(0.0, 20.0)
        b = _clip(30.0, 50.0)   # 10s gap from a
        c = _clip(60.0, 80.0)   # 10s gap from b
        result = _enforce_constraints([a, b, c], max_duration=None)
        assert len(result) == 3

    def test_result_is_sorted_by_start_time(self) -> None:
        # Input sorted by score (desc), output must be sorted by start
        a = _clip(60.0, 80.0, score=0.9)
        b = _clip(0.0, 20.0, score=0.7)
        c = _clip(30.0, 50.0, score=0.5)
        result = _enforce_constraints([a, b, c], max_duration=None)
        starts = [cl.start for cl in result]
        assert starts == sorted(starts)
        assert starts[0] == 0.0

    def test_clips_within_gap_threshold_only_best_accepted(self) -> None:
        # 3s gap between clips — below the 5s minimum
        first = _clip(0.0, 20.0, score=0.9)
        second = _clip(23.0, 40.0, score=0.7)
        result = _enforce_constraints([first, second], max_duration=None)
        assert len(result) == 1
        assert result[0].start == 0.0   # higher score wins

    def test_all_clips_dropped_when_all_too_short(self) -> None:
        clips = [_clip(float(i), float(i) + 3.0) for i in range(5)]
        result = _enforce_constraints(clips, max_duration=None)
        assert result == []

    def test_mix_of_valid_and_short_clips(self) -> None:
        valid = _clip(0.0, 30.0, score=0.9)
        short = _clip(50.0, 53.0, score=0.8)  # 3s — too short
        result = _enforce_constraints([valid, short], max_duration=None)
        assert result == [valid]
