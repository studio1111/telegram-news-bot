from app.main import max_new_items

def test_max_new_items_defaults_to_three():
    assert max_new_items({}) == 3

def test_max_new_items_accepts_positive_environment_value():
    assert max_new_items({"MAX_NEW_ITEMS": "1"}) == 1

def test_max_new_items_rejects_invalid_environment_value():
    assert max_new_items({"MAX_NEW_ITEMS": "nope"}) == 3
