from datetime import timedelta
import re

# Adjust this to your filename
input_file = "subtitles.txt"
output_file = "subtitles_shifted.txt"

def parse_time(t):
    h, m, s_ms = t.split(":")
    s, ms = s_ms.split(",")
    return timedelta(hours=int(h), minutes=int(m), seconds=int(s), milliseconds=int(ms))

def format_time(td):
    if td.total_seconds() < 0:
        td = timedelta(0)
    total_ms = int(td.total_seconds() * 1000)
    h = total_ms // 3_600_000
    total_ms %= 3_600_000
    m = total_ms // 60_000
    total_ms %= 60_000
    s = total_ms // 1000
    ms = total_ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

with open(input_file, "r", encoding="utf-8") as f:
    lines = f.readlines()

shifted_lines = []
time_pattern = re.compile(r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})")

for line in lines:
    match = time_pattern.search(line)
    if match:
        start = parse_time(match.group(1)) + timedelta(seconds=24.3)
        end = parse_time(match.group(2)) + timedelta(seconds=24.3)
        new_line = f"{format_time(start)} --> {format_time(end)}\n"
        shifted_lines.append(new_line)
    else:
        shifted_lines.append(line)

with open(output_file, "w", encoding="utf-8") as f:
    f.writelines(shifted_lines)

print(f"Shifted subtitles saved to {output_file}")
