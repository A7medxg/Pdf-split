import sys, traceback, importlib
sys.path.insert(0, "tests")
m = importlib.import_module("test_core")
fails = 0
for name in sorted(dir(m)):
    if name.startswith("test_"):
        try:
            getattr(m, name)()
            print("PASS", name)
        except Exception:
            fails += 1
            print("FAIL", name)
            traceback.print_exc()
print("failures:", fails)
