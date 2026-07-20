from app.waterline import plan_replenish


def test_above_low_no_replenish():
    p = plan_replenish(120, low=100, target=150, emergency=30, max_per_round=100)
    assert p.action == "none"
    assert p.planned == 0


def test_below_low_plans_to_target():
    p = plan_replenish(80, low=100, target=150, emergency=30, max_per_round=100, success_rate=1.0)
    assert p.action == "replenish"
    assert p.deficit == 70
    assert p.planned == 70


def test_emergency_and_cap():
    p = plan_replenish(10, low=100, target=150, emergency=30, max_per_round=50, success_rate=0.2)
    assert p.emergency is True
    assert p.action == "emergency"
    # deficit 140 / 0.2 = 700 capped to 50
    assert p.planned == 50
