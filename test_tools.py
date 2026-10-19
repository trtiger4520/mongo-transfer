import os
import tempfile

import store
import tools


def test():
    assert tools.strip_db("mongodb://h:1") == "mongodb://h:1"
    assert tools.strip_db("mongodb://u:p@h:1/") == "mongodb://u:p@h:1/"
    assert tools.strip_db("mongodb://u:p@h:1/app") == "mongodb://u:p@h:1/?authSource=app"
    assert tools.strip_db("mongodb://u:p@a,b/app?replicaSet=rs") == "mongodb://u:p@a,b/?replicaSet=rs&authSource=app"
    assert tools.strip_db("mongodb+srv://u:p@c.net/app?authSource=admin") == "mongodb+srv://u:p@c.net/?authSource=admin"

    assert tools.file_format("a.JSON") == "json"
    assert tools.file_format("x/users.bson.gz") == "bson"
    assert tools.file_format("users.json.gz") is None
    assert tools.file_format("readme.txt") is None
    assert tools.coll_from_file(r"C:\d\system.users.bson.gz") == "system.users"
    assert tools.coll_from_file("orders.csv") == "orders"

    assert tools.flatten_keys([{"_id": 1, "a": {"b": 1, "c": {}}}, {"d": 2, "_id": 3}]) == ["_id", "a.b", "a.c", "d"]

    assert tools.export_args("db", "c", "csv", "out", fields="a,b") == (
        "mongoexport", ["--db", "db", "--collection", "c", "--out", os.path.join("out", "c.csv"), "--type=csv", "--fields", "a,b"])
    assert tools.export_args("db", "c", "bson", "out", query="{}", gzip=True) == (
        "mongodump", ["--db", "db", "--collection", "c", "--query", "{}", "--out", "out", "--gzip"])
    assert tools.import_args("db", "c", "x.bson.gz", drop=True) == (
        "mongorestore", ["--db", "db", "--collection", "c", "--drop", "--gzip", "x.bson.gz"])

    with tempfile.TemporaryDirectory() as d:
        arr, lines = os.path.join(d, "a.json"), os.path.join(d, "b.json")
        open(arr, "wb").write(b"\xef\xbb\xbf\n [{}]")
        open(lines, "wb").write(b'{"a":1}\n')
        assert tools.import_args("db", "c", arr)[1][-1] == "--jsonArray"
        assert "--jsonArray" not in tools.import_args("db", "c", lines)[1]

    assert tools.mask_credentials("failed to connect to mongodb://u:p%40ss@127.0.0.1:1/?authSource=app, x") == \
        "failed to connect to mongodb://***@127.0.0.1:1/?authSource=app, x"
    assert tools.mask_credentials("mongodb+srv://u:p@ss@c.net/") == "mongodb+srv://***@c.net/"
    assert tools.mask_credentials("connected to: mongodb://[**REDACTED**]@h") == "connected to: mongodb://***@h"
    assert tools.mask_credentials("mongodb://h:1/db") == "mongodb://h:1/db"

    cfg = tools.write_config('mongodb://u:p"w@h/app')
    assert open(cfg, encoding="utf-8").read() == 'uri: "mongodb://u:p\\"w@h/?authSource=app"\n'
    os.remove(cfg)

    assert store.decrypt(store.encrypt("mongodb://u:密碼@h")) == "mongodb://u:密碼@h"
    print("ok")


if __name__ == "__main__":
    test()
