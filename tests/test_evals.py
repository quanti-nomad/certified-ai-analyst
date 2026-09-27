from evals.run_evals import numbers_in, score_accuracy


def test_number_parsing_handles_money_suffixes_and_percents():
    vals = numbers_in("Revenue was $4.35M, up 7.2%, across 1,207 members and $812K in fees.")
    assert (4.35e6, False) in vals and (7.2, True) in vals and (1207.0, False) in vals and (812e3, False) in vals


def test_accuracy_scoring_with_tolerance():
    assert score_accuracy({"kind": "number", "tolerance": 0.01}, 4354846.8, "Q2 net revenue was $4.35M.")
    assert not score_accuracy({"kind": "number", "tolerance": 0.01}, 4354846.8, "Q2 net revenue was $3.9M.")
    assert score_accuracy({"kind": "percent", "tolerance": 0.02}, 0.0718, "Churn was 7.2% in June.")
    assert not score_accuracy({"kind": "percent", "tolerance": 0.02}, 0.0718, "Churn was 0.0718 in June.")
    assert score_accuracy({"kind": "text"}, "Lumen - Plano", "The top location was Lumen - Plano.")
