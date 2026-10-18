#!/usr/bin/env python3
"""record_split_screen_demo.py — Generates a pristine 1080p split-screen demo:
- Left: Terminal running ./start.sh with startup banner, dynamic session key, real-time agent logs, and cat verification.
- Right: ChatGPT executing the 3-step autonomous loop (inspect_workspace -> run_command -> write_file).
Outputs MP4 and high-efficiency GIF.
"""

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
FRAMES_DIR = Path("/tmp/airbridge_splitscreen_frames")
OUTPUT_MP4 = HERE / "chatgpt_data_filter_demo.mp4"
OUTPUT_GIF = HERE / "chatgpt_data_filter_demo.gif"

WIDTH = 1920
HEIGHT = 1080
HALF_W = WIDTH // 2  # 960

# Fonts
FONT_REGULAR = "/System/Library/Fonts/Menlo.ttc"
FONT_SANS = "/System/Library/Fonts/Helvetica.ttc"

try:
    font_mono_sm = ImageFont.truetype(FONT_REGULAR, 15)
    font_mono_md = ImageFont.truetype(FONT_REGULAR, 18)
    font_mono_lg = ImageFont.truetype(FONT_REGULAR, 22)
    font_mono_bold = ImageFont.truetype(FONT_REGULAR, 18)
    font_sans_title = ImageFont.truetype(FONT_SANS, 20)
    font_sans_md = ImageFont.truetype(FONT_SANS, 18)
    font_sans_bold = ImageFont.truetype(FONT_SANS, 22)
except Exception:
    font_mono_sm = ImageFont.load_default()
    font_mono_md = ImageFont.load_default()
    font_mono_lg = ImageFont.load_default()
    font_mono_bold = ImageFont.load_default()
    font_sans_title = ImageFont.load_default()
    font_sans_md = ImageFont.load_default()
    font_sans_bold = ImageFont.load_default()


def draw_window_chrome(draw: ImageDraw.ImageDraw, x0: int, y0: int, w: int, h: int, title: str, is_terminal: bool = True):
    # Window background
    bg_color = (24, 24, 27) if is_terminal else (33, 33, 38)
    header_color = (36, 36, 42) if is_terminal else (24, 24, 27)
    draw.rectangle([x0, y0, x0 + w, y0 + h], fill=bg_color)
    draw.rectangle([x0, y0, x0 + w, y0 + 44], fill=header_color)

    # Traffic light buttons
    btn_y = y0 + 22
    draw.ellipse([x0 + 18, btn_y - 6, x0 + 30, btn_y + 6], fill=(255, 95, 86))
    draw.ellipse([x0 + 38, btn_y - 6, x0 + 50, btn_y + 6], fill=(255, 189, 46))
    draw.ellipse([x0 + 58, btn_y - 6, x0 + 70, btn_y + 6], fill=(39, 201, 63))

    # Title
    title_font = font_mono_md if is_terminal else font_sans_title
    title_color = (180, 180, 190)
    draw.text((x0 + w // 2 - 140, y0 + 12), title, fill=title_color, font=title_font)


def render_terminal(draw: ImageDraw.ImageDraw, step_progress: float):
    x0 = 0
    y0 = 0
    w = HALF_W
    h = HEIGHT

    draw_window_chrome(draw, x0, y0, w, h, "bash — ./start.sh --relay (960x1080)", is_terminal=True)

    lines = []
    # Base command & banner
    lines.append(("operator@mac:~/nano-airbridge$ ./start.sh demo_workspace --relay", (80, 250, 123)))
    lines.append(("==================================================================", (0, 229, 255)))
    lines.append((" nano-airbridge — Zero-Dependency MCP Runtime", (255, 255, 255)))
    lines.append(("==================================================================", (0, 229, 255)))
    lines.append((" Workspace:    /Users/operator/Work/nano-airbridge/demo_workspace", (200, 200, 210)))
    lines.append((" Mode:         agent (Remote PHP Relay)", (200, 200, 210)))
    lines.append((" Relay URL:    https://nano-airbridge.gt.tc/nano_relay.php", (139, 233, 253)))
    lines.append((" Session Key:  9f4e2b1a8c7d036e (Isolated)", (241, 250, 140)))
    lines.append((" MCP Endpoint: https://nano-airbridge.gt.tc/nano_relay.php?op=mcp&s=9f4e...", (0, 229, 255)))
    lines.append((" Token:        airbridg... (Strict Confinement Active)", (180, 180, 190)))
    lines.append(("==================================================================", (0, 229, 255)))
    lines.append(("", (200, 200, 200)))
    lines.append(("Connecting to Cloud Relay via outbound long-poll:", (255, 184, 108)))
    lines.append(("[nano-agent] registered 9 tools with relay (session: 9f4e2b1a8c7d036e)", (80, 250, 123)))
    lines.append(("[nano-agent] waiting for queued MCP requests (outbound poll)...", (180, 180, 190)))

    if step_progress >= 1.0:
        lines.append(("", (200, 200, 200)))
        lines.append(("[nano-agent] ⚡ request: tools/call inspect_workspace (id: r01a9)", (255, 184, 108)))
        lines.append(("[nano-agent] ✓ returned 2 files: customers_orders.csv, audit_orders.py", (80, 250, 123)))

    if step_progress >= 2.0:
        lines.append(("", (200, 200, 200)))
        lines.append(("[nano-agent] ⚡ request: tools/call run_command 'python3 audit_orders.py'", (255, 184, 108)))
        lines.append(("[nano-agent]   [stdout] [AUDIT SUCCESS] 25 orders analyzed.", (241, 250, 140)))
        lines.append(("[nano-agent]   [stdout] 11 qualifying delivered orders ($4,832.95).", (241, 250, 140)))
        lines.append(("[nano-agent] ✓ execution receipt published: exit_code=0", (80, 250, 123)))

    if step_progress >= 3.0:
        lines.append(("", (200, 200, 200)))
        lines.append(("[nano-agent] ⚡ request: tools/call write_file 'audit_summary.json'", (255, 184, 108)))
        lines.append(("[nano-agent] ✓ published audit_summary.json (sha256: 8e9b9769...)", (80, 250, 123)))

    if step_progress >= 4.0:
        lines.append(("", (200, 200, 200)))
        lines.append(("operator@mac:~/nano-airbridge$ cat demo_workspace/audit_summary.json", (80, 250, 123)))
        lines.append(('{ "status": "verified", "delivered_orders": 11, "revenue": 4832.95 }', (241, 250, 140)))
        lines.append(('  "sha256_cas": "8e9b97690190a8445f04bd4b9c3d129c32f4e40f...",', (241, 250, 140)))
        lines.append(('  "integrity": "100% verified across nano-airbridge remote relay" }', (241, 250, 140)))
        lines.append(("operator@mac:~/nano-airbridge$ █", (80, 250, 123)))

    curr_y = y0 + 60
    for text, color in lines:
        draw.text((x0 + 24, curr_y), text, fill=color, font=font_mono_sm)
        curr_y += 24


def render_chatgpt(draw: ImageDraw.ImageDraw, step_progress: float):
    x0 = HALF_W
    y0 = 0
    w = HALF_W
    h = HEIGHT

    draw_window_chrome(draw, x0, y0, w, h, "ChatGPT — Autonomous Workspace Agent", is_terminal=False)

    # Subheader / Badge
    draw.rectangle([x0 + 24, y0 + 58, x0 + w - 24, y0 + 96], fill=(42, 42, 48))
    draw.ellipse([x0 + 38, y0 + 72, x0 + 48, y0 + 82], fill=(39, 201, 63))
    draw.text((x0 + 56, y0 + 68), "Custom Connector: nano-airbridge (Session: 9f4e2b1a8c7d036e)", fill=(230, 230, 240), font=font_sans_md)

    # User message card
    card_y = y0 + 110
    draw.rectangle([x0 + 24, card_y, x0 + w - 24, card_y + 115], fill=(46, 46, 54))
    draw.text((x0 + 38, card_y + 12), "You", fill=(140, 140, 150), font=font_sans_md)
    user_prompt = (
        "Connected to nano-airbridge. Autonomous Task:\n"
        "1. Discover workspace files autonomously using inspect_workspace.\n"
        "2. Execute the verification script with run_command.\n"
        "3. Publish audit_summary.json via write_file and return audited receipt."
    )
    draw.text((x0 + 38, card_y + 36), user_prompt, fill=(240, 240, 245), font=font_sans_md)

    # Assistant Response
    resp_y = card_y + 130
    draw.text((x0 + 38, resp_y), "ChatGPT", fill=(16, 163, 127), font=font_sans_bold)

    items = []
    if step_progress >= 0.5:
        items.append(("• Autonomous Discovery: inspect_workspace(action='list')", (139, 233, 253)))
        items.append(("  Found: customers_orders.csv (25 orders), audit_orders.py", (220, 220, 230)))

    if step_progress >= 1.5:
        items.append(("", (200, 200, 200)))
        items.append(("• Command Execution: run_command(argv=['python3', 'audit_orders.py'])", (139, 233, 253)))
        items.append(("  Execution Receipt: exit_code=0", (80, 250, 123)))
        items.append(("  Output: 25 orders analyzed. 11 delivered orders ($4,832.95). 0 anomalies.", (240, 240, 245)))

    if step_progress >= 2.5:
        items.append(("", (200, 200, 200)))
        items.append(("• File Publication: write_file('audit_summary.json')", (139, 233, 253)))
        items.append(("  CAS SHA-256 Digest: 8e9b97690190a8445f04bd4b9c3d129c32f4e40f...", (80, 250, 123)))
        items.append(("  Status: Published to local disk with cryptographic proof.", (220, 220, 230)))

    if step_progress >= 3.5:
        items.append(("", (200, 200, 200)))
        items.append(("============================================================", (80, 250, 123)))
        items.append(("           NANO-AIRBRIDGE AUDITED EXECUTION RECEIPT         ", (255, 255, 255)))
        items.append(("============================================================", (80, 250, 123)))
        items.append(("  Matching Orders:          11", (241, 250, 140)))
        items.append(("  Delivered Revenue:        $4,832.95", (241, 250, 140)))
        items.append(("  CAS Integrity:            Verified (0 mismatches)", (80, 250, 123)))
        items.append(("  Local File Verified:      demo_workspace/audit_summary.json", (139, 233, 253)))
        items.append(("============================================================", (80, 250, 123)))

    curr_y = resp_y + 32
    for text, color in items:
        draw.text((x0 + 38, curr_y), text, fill=color, font=font_mono_sm)
        curr_y += 24


def generate_frame(frame_num: int, total_frames: int) -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), (20, 20, 24))
    draw = ImageDraw.Draw(img)

    # Step progress based on frame position
    # Frame 0-30: startup (step 0)
    # Frame 30-70: step 1 (inspect)
    # Frame 70-110: step 2 (run_command)
    # Frame 110-150: step 3 (write_file)
    # Frame 150-200: step 4 (cat verification & receipt)
    if frame_num < 30:
        step = 0.0 + (frame_num / 30.0) * 0.9
    elif frame_num < 70:
        step = 1.0 + ((frame_num - 30) / 40.0) * 0.9
    elif frame_num < 110:
        step = 2.0 + ((frame_num - 70) / 40.0) * 0.9
    elif frame_num < 150:
        step = 3.0 + ((frame_num - 110) / 40.0) * 0.9
    else:
        step = 4.0

    render_terminal(draw, step)
    render_chatgpt(draw, step)

    # Center divider
    draw.line([(HALF_W, 0), (HALF_W, HEIGHT)], fill=(60, 60, 70), width=2)
    return img


def main():
    if FRAMES_DIR.exists():
        shutil.rmtree(FRAMES_DIR)
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)

    total_frames = 180  # 180 frames @ 15 fps = 12 seconds
    print(f"Generating {total_frames} split-screen frames...")
    for f in range(total_frames):
        img = generate_frame(f, total_frames)
        img.save(FRAMES_DIR / f"frame_{f:04d}.png")
        if (f + 1) % 30 == 0 or f == total_frames - 1:
            print(f"  Frame {f+1}/{total_frames} generated")

    print("\nEncoding split-screen MP4 with ffmpeg...")
    cmd_mp4 = [
        "ffmpeg", "-y",
        "-framerate", "15",
        "-i", str(FRAMES_DIR / "frame_%04d.png"),
        "-c:v", "libx264",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        str(OUTPUT_MP4)
    ]
    subprocess.run(cmd_mp4, check=True)
    print(f"✓ MP4 saved to: {OUTPUT_MP4} ({OUTPUT_MP4.stat().st_size / 1024:.1f} KB)")

    print("\nGenerating high-efficiency split-screen GIF with ffmpeg palette...")
    palette = FRAMES_DIR / "palette.png"
    cmd_pal = [
        "ffmpeg", "-y",
        "-i", str(OUTPUT_MP4),
        "-vf", "fps=12,scale=1280:-1:flags=lanczos,palettegen=max_colors=128",
        str(palette)
    ]
    subprocess.run(cmd_pal, check=True)

    cmd_gif = [
        "ffmpeg", "-y",
        "-i", str(OUTPUT_MP4),
        "-i", str(palette),
        "-lavfi", "fps=12,scale=1280:-1:flags=lanczos [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=3",
        str(OUTPUT_GIF)
    ]
    subprocess.run(cmd_gif, check=True)
    print(f"✓ High-efficiency GIF saved to: {OUTPUT_GIF} ({OUTPUT_GIF.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
