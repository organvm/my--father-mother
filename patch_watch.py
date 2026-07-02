import re

with open("main.py", "r") as f:
    content = f.read()

# We want to find the body of cmd_watch's while True: and wrap it
# "        while True:\n            paused = is_paused(conn)"
# ...
# "                    last_digest = digest\n            time.sleep(interval)"

old_snippet = """        while True:
            paused = is_paused(conn)"""

new_snippet = """        while True:
            try:
                paused = is_paused(conn)"""

if old_snippet in content:
    content = content.replace(old_snippet, new_snippet)
else:
    print("Could not find start")

# Now indent all lines until time.sleep(interval)
# Actually, a regex might be better, or just iterate lines.

lines = content.split('\n')
inside_watch = False
in_try = False
indent_amount = 0

for i, line in enumerate(lines):
    if line == "    def cmd_watch(args: argparse.Namespace) -> None:" or "def cmd_watch(" in line:
        inside_watch = True
    
    if inside_watch and line == "        while True:":
        in_try = True
        continue
        
    if in_try:
        if line == "            time.sleep(interval)":
            lines[i] = "            except Exception as e:\n                logging.exception(f\"Error in watcher loop: {e}\")\n" + line
            in_try = False
            inside_watch = False
        elif line.startswith("            ") and line != "            try:":
            # Add 4 spaces
            lines[i] = "    " + line

with open("main.py", "w") as f:
    f.write('\n'.join(lines))
print("Patched main.py")
