import sys, json
try:
    with open("app/api/routes/observability.py", "rb") as f:
        source = f.read()
    compile(source, "app/api/routes/observability.py", "exec")
    result = {"status": "ok"}
except SyntaxError as e:
    result = {
        "msg": e.msg,
        "line": e.lineno,
        "offset": e.offset,
        "text": e.text
    }
except Exception as e:
    result = {"error": str(e)}

with open("error.json", "w") as f:
    json.dump(result, f, indent=2)
