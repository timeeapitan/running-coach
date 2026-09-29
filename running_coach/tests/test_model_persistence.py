from running_coach.ml.training.trainer import ModelTrainer
from running_coach.schemas.profile import RunnerProfile


def test_model_bundle_round_trip(tmp_path):
    profile = RunnerProfile(max_hr=190, resting_hr=55, runs_per_week=3)
    a = ModelTrainer(profile, model_dir=str(tmp_path / "a"))
    # Minimal synthetic trained state is enough to verify DB-style serialization.
    a.workout_recommender.is_trained = True
    a.workout_recommender._state = {
        "k": 1, "X_train": [[0.0]], "y_train": ["easy"],
        "feature_keys": ["x"], "means": {"x": 0.0}, "stds": {"x": 1.0},
    }
    a.workout_recommender._restore_from_state()
    bundle = a.export_bundle()

    b = ModelTrainer(profile, model_dir=str(tmp_path / "b"))
    status = b.import_bundle(bundle)
    assert status["workout"] is True
    assert b.workout_recommender.is_trained
    assert b.workout_recommender.predict_type({"x": 0.0}) == "easy"
