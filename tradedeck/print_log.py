import os
try:
    with open("backend7.log", "r", encoding="utf-16le") as f:
        text = f.read()
        if "Traceback" in text:
            parts = text.split("Traceback (most recent call last):")
            last_traceback = "Traceback (most recent call last):" + parts[-1]
            with open("traceback_backend7.txt", "w", encoding="utf-8") as out:
                out.write(last_traceback)
            print("Saved to traceback_backend7.txt")
        else:
            print("No Traceback found in backend7.log")
except Exception as e:
    print(f"Failed to read: {e}")
