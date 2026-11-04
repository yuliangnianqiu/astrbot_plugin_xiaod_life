"""V0.5 mathematical model, durable evidence and interruption recovery."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from core import Store
from interests import MAX_INTEREST_CONTEXT, interest_context, normalize_state, parse_feedback


def record(activity_id, domain="音乐", sentiment="positive"):
    return {
        "id": activity_id,
        "status": "success",
        "started_at": "2026-11-04T02:00:00+00:00",
        "finished_at": "2026-11-04T02:01:00+00:00",
        "topic": "自然声景与电子音乐",
        "sources": [{"id": 1, "title": "真实来源", "url": "https://example.com/music"}],
        "model_findings": [{"source_id": 1, "text": "文章讨论自然采样"}],
        "subjective_evaluation": "想多了解自然采样",
        "interest_feedback": {
            "domain": domain,
            "sentiment": sentiment,
            "reason": "阅读自然采样的内容后对这个领域形成了主观评价",
            "source_id": 1,
        },
    }


class InterestModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)

    def state(self):
        return self.store.load()[0]

    def test_initial_decimal_step_and_unvisited_domains(self):
        initial = self.state()
        self.assertTrue(all(w == 3 for w in initial["weights"].values()))
        for i, (sentiment, expected) in enumerate(
            [("positive", 3.25), ("neutral", 3.25), ("negative", 3.0), ("negative", 2.75)]
        ):
            self.store.append(record(str(i), sentiment=sentiment))
            state = self.state()
            self.assertEqual(state["weights"]["音乐"], expected)
            self.assertEqual(state["weights"]["自然"], 3)

    def test_weight_clipping_at_both_boundaries(self):
        for i in range(12):
            self.store.append(record(f"plus{i}"))
        self.assertEqual(self.state()["weights"]["音乐"], 5)
        for i in range(20):
            self.store.append(record(f"minus{i}", sentiment="negative"))
        self.assertEqual(self.state()["weights"]["音乐"], 1)
        self.store.append(record("leave_floor"))
        self.assertEqual(self.state()["weights"]["音乐"], 1.25)

    def test_new_domain_requires_two_distinct_successes_and_starts_at_three(self):
        first = record("first", "电子音乐")
        self.store.append(first)
        self.store.append(first)
        state = self.state()
        self.assertNotIn("电子音乐", state["interests"])
        self.assertEqual(state["pending_interests"]["电子音乐"], ["first"])
        self.store.append(record("second", "电子音乐"))
        state = self.state()
        self.assertEqual(state["weights"]["电子音乐"], 3)
        self.assertEqual(state["formation"]["电子音乐"], ["first", "second"])
        self.assertNotIn("电子音乐", state["pending_interests"])
        self.store.append(record("third", "电子音乐"))
        self.assertEqual(self.state()["weights"]["电子音乐"], 3.25)

    def test_negative_offsets_pending_evidence_and_count_stops_at_zero(self):
        for activity_id, sentiment, expected in (
            ("n0", "negative", 0),
            ("p1", "positive", 1),
            ("neutral", "neutral", 1),
            ("n1", "negative", 0),
            ("n2", "negative", 0),
            ("p2", "positive", 1),
        ):
            self.store.append(record(activity_id, "电子音乐", sentiment))
            self.assertEqual(len(self.state()["pending_interests"]["电子音乐"]), expected)
            self.assertNotIn("电子音乐", self.state()["weights"])
        self.store.append(record("p3", "电子音乐"))
        self.assertEqual(self.state()["formation"]["电子音乐"], ["p2", "p3"])

    def test_normalization_merges_cultural_suffix_and_unicode_case_space(self):
        for first, second in (
            ("电子音乐", "电子音乐文化"),
            ("电子音乐文化", "电子音乐"),
            ("ＪＡＺＺ", " jazz "),
        ):
            with self.subTest(first=first), tempfile.TemporaryDirectory() as tmp:
                store = Store(tmp)
                store.append(record("one", first))
                store.append(record("two", second))
                state, _ = store.load()
                names = [n for n in state["interests"] if n not in self.state()["interests"]]
                self.assertEqual(len(names), 1)
                self.assertEqual(state["weights"][names[0]], 3)
                self.assertEqual(state["formation"][names[0]], ["one", "two"])

    def test_specific_subdomain_stays_distinct_from_parent(self):
        self.store.append(record("one", "电子音乐"))
        self.assertEqual(self.state()["weights"]["音乐"], 3)
        self.assertEqual(self.state()["pending_interests"]["电子音乐"], ["one"])

    def test_non_successes_and_absent_feedback_leave_weights_unchanged(self):
        self.store.append(record("baseline"))
        before = (Path(self.tmp.name) / "interests.json").read_bytes()
        for status in ("failed", "skipped", "cancelled", "running"):
            self.store.append(dict(record(status), status=status))
        self.store.append(dict(record("empty"), interest_feedback=None))
        old = record("old")
        del old["interest_feedback"]
        self.store.append(old)
        self.assertEqual((Path(self.tmp.name) / "interests.json").read_bytes(), before)

    def test_legacy_state_is_read_only_until_update_and_preserves_custom_fields(self):
        legacy = {"interests": ["自定兴趣"], "custom": {"note": "保留"}}
        self.store.write("interests.json", legacy)
        before = (Path(self.tmp.name) / "interests.json").read_bytes()
        state = self.state()
        self.assertEqual(state["weights"], {"自定兴趣": 3})
        self.assertEqual((Path(self.tmp.name) / "interests.json").read_bytes(), before)
        self.store.append(record("custom", "自定兴趣"))
        state = self.state()
        self.assertEqual(state["weights"]["自定兴趣"], 3.25)
        self.assertEqual(state["custom"], legacy["custom"])

    def test_reads_and_time_passing_do_not_decay_weights(self):
        self.store.append(record("one"))
        before = self.state()
        for _ in range(3):
            self.assertEqual(Store(self.tmp.name).load()[0], before)

    def test_replay_after_reload_and_archive_is_idempotent(self):
        first = record("first")
        self.store.append(first)
        self.store.write("activities.json", [first] + [{"id": str(i)} for i in range(999)])
        self.store.append(record("rotate"))
        self.assertEqual(len(list(Path(self.tmp.name).glob("archive-*.json"))), 1)
        state, logs = self.store.load()
        self.assertNotIn(first, logs)
        Store(self.tmp.name).append(first)
        self.assertEqual(self.store.load(), (state, logs))

    def test_feedback_is_structured_and_provenance_survives_rotation(self):
        self.store.append(record("one", "电子音乐"))
        self.store.append(record("two", "电子音乐"))
        state, logs = self.store.load()
        self.assertEqual(logs[0]["interest_feedback"]["sentiment"], "positive")
        self.assertEqual(
            state["feedback_events"]["two"]["source"]["url"], "https://example.com/music"
        )
        self.assertTrue(state["feedback_events"]["two"]["formed"])
        self.assertEqual(state["feedback_events"]["two"]["weight_after"], 3)
        self.store.write("activities.json", [])
        text = interest_context(self.state(), "为什么喜欢电子音乐")
        self.assertIn('"activity_id": "one"', text)
        self.assertIn('"activity_id": "two"', text)

    def test_model_feedback_boundary_rejects_multiple_or_unread_feedback(self):
        valid = record("one")["interest_feedback"]
        for value in (
            [valid],
            [valid, valid],
            dict(valid, sentiment="maybe"),
            dict(valid, source_id=2),
            dict(valid, source_id=True),
            dict(valid, reason=""),
            dict(valid, domain="https://example.com"),
        ):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                parse_feedback(value, {1}, self.state())
        self.assertIsNone(parse_feedback(None, {1}, self.state()))

    def test_corrupt_state_fails_without_replacing_files(self):
        for weight in (True, 0, 6, float("nan"), float("inf"), "3"):
            state = normalize_state({"interests": ["音乐"]})
            state["weights"]["音乐"] = weight
            self.store.write("interests.json", state)
            before = (Path(self.tmp.name) / "interests.json").read_bytes()
            with self.subTest(weight=weight), self.assertRaises(ValueError):
                self.store.load()
            self.assertEqual((Path(self.tmp.name) / "interests.json").read_bytes(), before)

    def test_persisted_formation_requires_distinct_positive_same_domain_evidence(self):
        self.store.append(record("one", "电子音乐"))
        self.store.append(record("two", "电子音乐"))
        valid = self.state()
        for change in ("duplicate", "single", "negative", "other_domain"):
            state = json.loads(json.dumps(valid))
            if change == "duplicate":
                state["formation"]["电子音乐"] = ["one", "one"]
            elif change == "single":
                state["formation"]["电子音乐"] = ["one"]
            elif change == "negative":
                state["feedback_events"]["one"]["sentiment"] = "negative"
            else:
                state["feedback_events"]["one"]["domain"] = "电影"
            with self.subTest(change=change), self.assertRaises(ValueError):
                normalize_state(state)

    def test_interest_query_exposes_weights_and_initial_basis_only_when_requested(self):
        state = self.state()
        self.assertEqual(interest_context(state, "三加五等于几"), "")
        text = interest_context(state, "你的兴趣是什么")
        self.assertIn('"weight": 3.0', text)
        self.assertIn("初始或用户配置兴趣", text)

    def test_interest_context_budget_handles_long_formation_links(self):
        for activity_id in ("one", "two", "three"):
            item = record(activity_id, "电子音乐")
            item["sources"][0]["url"] = "https://example.com/" + "a" * 1750
            self.store.append(item)
        text = interest_context(self.state(), "电子音乐兴趣形成依据")
        self.assertLessEqual(len(text), MAX_INTEREST_CONTEXT)
        self.assertIn('"weight": 3.25', text)
        self.assertIn('"activity_id": "one"', text)
        self.assertIn('"activity_id": "two"', text)
        for line in text.splitlines():
            if line.startswith("{"):
                json.loads(line)


class RecoveryTests(unittest.TestCase):
    def test_interruptions_at_each_file_commit_boundary_recover_once(self):
        for failed_file in ("pending_commit.json", "activities.json", "interests.json"):
            for after_replace in (False, True):
                with (
                    self.subTest(file=failed_file, after=after_replace),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    store = Store(tmp)
                    store.append(record("baseline"))
                    original = store.write

                    def interrupted(name, value):
                        if name == failed_file:
                            if after_replace:
                                original(name, value)
                            raise OSError("simulated interrupted commit")
                        original(name, value)

                    with patch.object(store, "write", side_effect=interrupted):
                        with self.assertRaises(OSError):
                            store.append(record("next"))
                    restarted = Store(tmp)
                    state, logs = restarted.load()
                    committed = failed_file != "pending_commit.json" or after_replace
                    self.assertEqual(state["weights"]["音乐"], 3.5 if committed else 3.25)
                    self.assertEqual(len(logs), 2 if committed else 1)
                    self.assertFalse((Path(tmp) / "pending_commit.json").exists())
                    restarted.append(record("next"))
                    final_state, final_logs = restarted.load()
                    self.assertEqual(final_state["weights"]["音乐"], 3.5)
                    self.assertEqual(len(final_logs), 2)

    def test_interruption_before_journal_cleanup_replays_without_weight_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            original = Path.unlink

            def interrupted(path, *args, **kwargs):
                if path.name == "pending_commit.json":
                    raise OSError("interrupted cleanup")
                return original(path, *args, **kwargs)

            with patch.object(Path, "unlink", interrupted), self.assertRaises(OSError):
                store.append(record("one"))
            state, logs = Store(tmp).load()
            self.assertEqual(state["weights"]["音乐"], 3.25)
            self.assertEqual(len(logs), 1)

    def test_interrupted_formation_keeps_two_distinct_evidence_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            store.append(record("one", "电子音乐"))
            original = store.write

            def interrupted(name, value):
                if name == "interests.json":
                    raise OSError("interrupted formation")
                original(name, value)

            with patch.object(store, "write", side_effect=interrupted), self.assertRaises(OSError):
                store.append(record("two", "电子音乐文化"))
            restarted = Store(tmp)
            state, logs = restarted.load()
            self.assertEqual(state["weights"]["电子音乐"], 3)
            self.assertEqual(state["formation"]["电子音乐"], ["one", "two"])
            restarted.append(record("two", "电子音乐文化"))
            self.assertEqual(restarted.load(), (state, logs))

    def test_corrupt_journal_fails_loud_and_preserves_originals(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            store.append(record("one"))
            before = {p.name: p.read_bytes() for p in Path(tmp).iterdir()}
            path = Path(tmp) / "pending_commit.json"
            path.write_bytes(b"{broken")
            with self.assertRaises(json.JSONDecodeError):
                store.load()
            self.assertEqual(path.read_bytes(), b"{broken")
            for name, content in before.items():
                self.assertEqual((Path(tmp) / name).read_bytes(), content)

    def test_reader_waits_until_activity_and_interest_snapshot_agree(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            store.append(record("one"))
            original = store.write
            half_saved, release = threading.Event(), threading.Event()
            read_started, read_finished = threading.Event(), threading.Event()
            result = []

            def paused(name, value):
                original(name, value)
                if name == "activities.json":
                    half_saved.set()
                    self.assertTrue(release.wait(3))

            def read():
                read_started.set()
                result.append(store.load())
                read_finished.set()

            with patch.object(store, "write", side_effect=paused):
                writer = threading.Thread(target=store.append, args=(record("two"),))
                writer.start()
                self.assertTrue(half_saved.wait(3))
                reader = threading.Thread(target=read)
                reader.start()
                self.assertTrue(read_started.wait(3))
                self.assertFalse(read_finished.wait(0.02))
                release.set()
                writer.join(3)
                reader.join(3)
            self.assertFalse(writer.is_alive())
            self.assertFalse(reader.is_alive())
            state, logs = result[0]
            self.assertEqual(state["weights"]["音乐"], 3.5)
            self.assertEqual(len(logs), 2)

    def test_archive_interruption_preserves_history_and_single_feedback(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            old = [record(f"old{i}") for i in range(1000)]
            store.write("activities.json", old)
            original = store.write

            def interrupted(name, value):
                if name == "activities.json":
                    raise OSError("after archive before active file")
                original(name, value)

            with patch.object(store, "write", side_effect=interrupted), self.assertRaises(OSError):
                store.append(record("rotate"))
            state, logs = Store(tmp).load()
            self.assertEqual(state["weights"]["音乐"], 3.25)
            self.assertEqual(len(logs), 6)
            self.assertEqual(len(list(Path(tmp).glob("archive-*.json"))), 1)
            self.assertEqual(store.read("archive-rotate.json", []), old)
