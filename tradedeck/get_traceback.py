import io

try:
    with open('backend7.log', 'r', encoding='utf-16le') as f:
        lines = f.readlines()
        
    traceback_lines = []
    in_traceback = False
    
    for line in lines:
        if "Traceback (most recent call last):" in line:
            in_traceback = True
            traceback_lines.append(line)
        elif in_traceback:
            traceback_lines.append(line)
            if not line.strip().startswith("File ") and not line.strip().startswith("line ") and "Error:" in line and not line.startswith(" "):
                # Stop after the error description
                in_traceback = False
                
    if traceback_lines:
        with open('traceback.txt', 'w', encoding='utf-8') as f:
            f.writelines(traceback_lines)
        print("Traceback found and saved to traceback.txt")
    else:
        # If no explicit traceback, maybe just an ERROR line
        errors = [l for l in lines[-100:] if "ERROR" in l or "500" in l]
        with open('traceback.txt', 'w', encoding='utf-8') as f:
            f.writelines(errors)
        print("No traceback found, saved recent errors to traceback.txt")
except Exception as e:
    print(f"Error: {e}")
