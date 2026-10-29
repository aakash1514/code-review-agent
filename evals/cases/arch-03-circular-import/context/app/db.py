_TABLES = {"orders": {}}


def fetch_one(table, key):
    return _TABLES[table].get(key)


def execute(op, key, value):
    if op == "update_status":
        _TABLES["orders"][key]["status"] = value
