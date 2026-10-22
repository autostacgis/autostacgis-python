def combine(results):
    return "fail" if "fail" in results else ("unknown" if "unknown" in results else "pass")
