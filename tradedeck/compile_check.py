import py_compile
import sys
import traceback

try:
    py_compile.compile('app/api/routes/observability.py', doraise=True)
except Exception as e:
    print("COMPILE ERROR:")
    print(e)
