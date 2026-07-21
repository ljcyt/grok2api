import time
import unittest

from app.demotion import (
    CLASS_HARD,
    CLASS_HALF_OPEN,
    CLASS_NONE,
    CLASS_SOFT,
    DemotionPolicy,
    apply_probe_evidence,
    maybe_enter_half_open,
)


class DemotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = DemotionPolicy(
            soft_debt_threshold=2.0,
            hard_debt_threshold=4.5,
            debt_fail_401=1.5,
            debt_success_decay=1.0,
            hard_streak_threshold=3,
            soft_priority=0,
            hard_priority=-100,
            half_open_success_threshold=2,
            cooldown_hours=(1, 2, 4),
        )
        self.now = time.time()

    def test_debt_decays_on_success(self) -> None:
        state, _ = apply_probe_evidence(
            prev={},
            classification="suspect_dead",
            status_code=401,
            current_priority=1,
            bot_flagged=False,
            policy=self.policy,
            now=self.now,
        )
        self.assertAlmostEqual(state["debt_score"], 1.5)
        state, _ = apply_probe_evidence(
            prev=state,
            classification="alive",
            status_code=200,
            current_priority=1,
            bot_flagged=False,
            policy=self.policy,
            now=self.now + 1,
        )
        self.assertAlmostEqual(state["debt_score"], 0.5)
        self.assertEqual(state["hard_streak"], 0)

    def test_soft_demotion(self) -> None:
        state: dict = {}
        decision = None
        for i in range(2):
            state, decision = apply_probe_evidence(
                prev=state,
                classification="suspect_dead",
                status_code=401,
                current_priority=1,
                bot_flagged=False,
                policy=self.policy,
                now=self.now + i,
            )
        assert decision is not None
        self.assertEqual(decision.class_name, CLASS_SOFT)
        self.assertTrue(decision.write_priority)
        self.assertEqual(decision.target_priority, 0)
        self.assertEqual(state["baseline_priority"], 1)

    def test_hard_streak(self) -> None:
        # lower debt thresholds so streak alone drives hard without soft first
        policy = DemotionPolicy(
            soft_enabled=False,
            hard_streak_threshold=3,
            hard_debt_threshold=100,
            debt_fail_401=0,
            soft_priority=0,
            hard_priority=-100,
        )
        state: dict = {}
        decision = None
        for i in range(3):
            state, decision = apply_probe_evidence(
                prev=state,
                classification="suspect_dead",
                status_code=401,
                current_priority=5,
                bot_flagged=False,
                policy=policy,
                now=self.now + i,
            )
        assert decision is not None
        self.assertEqual(decision.class_name, CLASS_HARD)
        self.assertEqual(decision.target_priority, -100)

    def test_soft_upgrades_to_hard(self) -> None:
        state: dict = {}
        for i in range(2):
            state, _ = apply_probe_evidence(
                prev=state,
                classification="suspect_dead",
                status_code=401,
                current_priority=1,
                bot_flagged=False,
                policy=self.policy,
                now=self.now + i,
            )
        self.assertEqual(state["demotion_class"], CLASS_SOFT)
        state, decision = apply_probe_evidence(
            prev=state,
            classification="suspect_dead",
            status_code=401,
            current_priority=0,
            bot_flagged=False,
            policy=self.policy,
            now=self.now + 3,
        )
        self.assertEqual(decision.class_name, CLASS_HARD)
        self.assertTrue(decision.write_priority)

    def test_half_open_restore(self) -> None:
        state = {
            "demotion_class": CLASS_HARD,
            "debt_score": 5.0,
            "hard_streak": 3,
            "baseline_priority": 1,
            "demoted_at": self.now - 7200,
            "cooldown_step": 0,
        }
        state, decision = maybe_enter_half_open(
            state, policy=self.policy, now=self.now, bot_flagged=False
        )
        self.assertEqual(decision.class_name, CLASS_HALF_OPEN)
        self.assertTrue(decision.enter_half_open)
        self.assertEqual(decision.target_priority, 0)

        # two successes restore
        for i in range(2):
            state, decision = apply_probe_evidence(
                prev=state,
                classification="alive",
                status_code=200,
                current_priority=0,
                bot_flagged=False,
                policy=self.policy,
                now=self.now + 10 + i,
            )
        self.assertEqual(decision.class_name, CLASS_NONE)
        self.assertTrue(decision.restore_baseline)
        self.assertEqual(decision.target_priority, 1)

    def test_half_open_fail_back_to_hard(self) -> None:
        state = {
            "demotion_class": CLASS_HALF_OPEN,
            "debt_score": 1.0,
            "hard_streak": 0,
            "baseline_priority": 1,
            "half_open_since": self.now - 10,
            "half_open_successes": 0,
            "cooldown_step": 0,
        }
        state, decision = apply_probe_evidence(
            prev=state,
            classification="suspect_dead",
            status_code=401,
            current_priority=0,
            bot_flagged=False,
            policy=self.policy,
            now=self.now,
        )
        self.assertEqual(decision.class_name, CLASS_HARD)
        self.assertEqual(decision.target_priority, -100)
        self.assertEqual(state["cooldown_step"], 1)

    def test_skip_bot_half_open(self) -> None:
        state = {
            "demotion_class": CLASS_HARD,
            "demoted_at": self.now - 99999,
            "cooldown_step": 0,
            "baseline_priority": 1,
        }
        _, decision = maybe_enter_half_open(
            state, policy=self.policy, now=self.now, bot_flagged=True
        )
        self.assertEqual(decision.reason, "skip_bot")
        self.assertFalse(decision.write_priority)


if __name__ == "__main__":
    unittest.main()
