import os
import re

dir_path = "frontend/src/pages"

replacements = {
    r"text-\[\#F5F7FA\]": "text-stone-800",
    r"text-\[\#8B95A5\]": "text-stone-600",
    r"text-\[\#596273\]": "text-stone-500",
    r"bg-\[\#0B0A0F\]": "bg-white/50",
    r"bg-\[\#120F17\]": "bg-white/60",
    r"bg-\[\#120F17\]/80": "bg-white/80",
    r"text-\[\#D9A441\]": "text-purple-600",
    r"bg-\[\#D9A441\]": "bg-purple-600",
    r"text-\[\#4ADE80\]": "text-emerald-600",
    r"bg-\[\#4ADE80\]": "bg-emerald-500",
    r"text-\[\#F87171\]": "text-red-600",
    r"bg-\[\#F87171\]": "bg-red-500",
    r"border-white/\[0\.06\]": "border-[var(--border)]",
    r"bg-white/\[0\.03\]": "bg-white/50",
    r"bg-white/\[0\.04\]": "bg-white/60",
    r"border-white/\[0\.15\]": "border-purple-200",
    r"border-white/\[0\.08\]": "border-[var(--border)]",
    r"border-white/\[0\.1\]": "border-stone-200",
    r"bg-\[\#F87171\]/\[0\.04\]": "bg-red-50",
    r"border-\[\#F87171\]/30": "border-red-200",
    r"bg-\[\#4ADE80\]/10": "bg-emerald-50",
    r"bg-\[\#F87171\]/10": "bg-red-50",
    r"hover:bg-\[\#E0B24E\]": "hover:bg-purple-700",
    r"border-\[\#D9A441\]/30": "border-purple-300",
    r"border-t-\[\#D9A441\]": "border-t-purple-500",
    r"bg-\[\#D9A441\]/10": "bg-purple-50",
    r"border-\[\#D9A441\]/25": "border-purple-200",
    r"hover:border-\[\#D9A441\]/50": "hover:border-purple-300",
    r"bg-\[\#0B0A0F\]/80": "bg-white/60 backdrop-blur-md",
    r"border-white/\[0\.2\]": "border-purple-200",
    r"bg-white/\[0\.05\]": "bg-white/50",
    r"hover:border-\[\#e945f5\]/40": "hover:border-purple-300",
    r"hover:bg-white/\[0\.08\]": "hover:bg-white/70",
    r"bg-\[\#120F17\]/60": "bg-white/70",
    r"text-\[\#F5B942\]": "text-amber-500",
    r"bg-\[\#F5B942\]": "bg-amber-500",
    r"bg-\[\#1A1722\]": "bg-white/80",
    r"border-\[\#D9A441\]": "border-purple-500",
    r"border-\[\#4ADE80\]/20": "border-emerald-200",
    r"border-\[\#F87171\]/20": "border-red-200",
    r"fill-\[\#D9A441\]": "fill-purple-500",
    r"stroke-\[\#D9A441\]": "stroke-purple-500",
}

for root, _, files in os.walk(dir_path):
    for file in files:
        if file.endswith(".tsx"):
            # skip Home.tsx and Dashboard.tsx as we already edited them manually or rewrote them
            if file in ["Home.tsx", "Dashboard.tsx"]:
                continue
            path = os.path.join(root, file)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            for pattern, rep in replacements.items():
                content = re.sub(pattern, rep, content)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
                
print("Done replacements in pages/")
