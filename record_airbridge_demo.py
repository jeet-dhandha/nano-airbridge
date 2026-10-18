#!/usr/bin/env python3
"""record_airbridge_demo.py — Drives ChatGPT in Chrome profile 'ignis' via agent-webbridge
to filter local dataset customers_orders.csv using nano-airbridge, recording a pristine video
with ZERO other tabs or apps visible.
"""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request

TAB_ID = 521872927
FRAMES_DIR = Path("/tmp/airbridge_frames")
OUTPUT_VIDEO = Path("/Users/jeetdhandha/Work/nano-airbridge/chatgpt_data_filter_demo.mp4")

TASK_PROMPT = """Access our local project file `customers_orders.csv` via nano-airbridge at https://habilimental-lia-reapply.ngrok-free.dev/read/customers_orders.csv.

Read the file content, verify its SHA-256 hash, and filter all orders where status is 'Delivered' and total_amount >= $250.00. Present the matching transactions, category summary, and the verified nano-airbridge execution receipt."""


frame_index = 0

def call_awb(action: str, args: dict) -> dict:
    payload = {"profile": "ignis", "session": "chatgpt-demo", "action": action, "args": args}
    req = urllib.request.Request(
        "http://127.0.0.1:10086/command",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())

def capture_frame(label: str = ""):
    global frame_index
    frame_index += 1
    filename = FRAMES_DIR / f"frame_{frame_index:04d}.png"
    res = call_awb("screenshot", {"_tabId": TAB_ID, "path": str(filename)})
    if frame_index % 5 == 0 or label:
        print(f"Frame {frame_index:04d}: {label} (ok={res.get('ok')})")

def set_prompt_text(text: str) -> dict:
    code = f"""(() => {{
        const el = document.querySelector("#prompt-textarea, [contenteditable=true]");
        if (!el) return {{ ok: false }};
        el.focus();
        document.execCommand("selectAll", false, null);
        document.execCommand("insertText", false, {json.dumps(text)});
        const btn = document.querySelector("button[aria-label='Send']");
        return {{ ok: true, btnFound: !!btn, btnDisabled: btn ? btn.disabled : true }};
    }})()"""
    return call_awb("evaluate", {"_tabId": TAB_ID, "code": code})

def click_send() -> dict:
    code = """(() => {
        const btn = document.querySelector("button[aria-label='Send']");
        if (btn) {
            btn.click();
            return { clicked: true };
        }
        return { clicked: false };
    })()"""
    return call_awb("evaluate", {"_tabId": TAB_ID, "code": code})

def is_generating() -> bool:
    code = """(() => {
        const stopBtn = document.querySelector("button[aria-label*='Stop'], button[data-testid='stop-button']");
        const sendBtn = document.querySelector("button[aria-label='Send']");
        return {
            hasStop: !!stopBtn,
            hasSend: !!sendBtn,
            sendDisabled: sendBtn ? sendBtn.disabled : true
        };
    })()"""
    res = call_awb("evaluate", {"_tabId": TAB_ID, "code": code})
    val = res.get("data", {}).get("value", {})
    return val.get("hasStop", False)

def main():
    if FRAMES_DIR.exists():
        shutil.rmtree(FRAMES_DIR)
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)

    print("Step 1: Navigating ChatGPT to a clean new chat session...")
    call_awb("navigate", {"_tabId": TAB_ID, "url": "https://chatgpt.com/"})
    time.sleep(3.5)
    for _ in range(4):
        capture_frame("initial_blank_state")
        time.sleep(0.3)

    print("Step 2: Entering nano-airbridge prompt...")
    set_prompt_text(TASK_PROMPT)
    time.sleep(0.5)
    for _ in range(4):
        capture_frame("prompt_entered")
        time.sleep(0.3)

    print("Step 3: Clicking Send...")
    click_res = click_send()
    print("Click result:", click_res)
    time.sleep(0.5)
    capture_frame("clicked_send")

    print("Step 4: Recording ChatGPT generation...")
    start_time = time.time()
    max_wait = 60
    while time.time() - start_time < max_wait:
        capture_frame("generating_response")
        time.sleep(0.4)
        if time.time() - start_time > 6 and not is_generating():
            print("Generation completed!")
            break

    # Smooth scroll down to reveal full table and receipt
    scroll_steps = [
        "window.scrollBy({ top: 300, behavior: 'smooth' });",
        "window.scrollBy({ top: 400, behavior: 'smooth' });",
        "window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });"
    ]
    for step in scroll_steps:
        call_awb("evaluate", {"_tabId": TAB_ID, "code": step})
        time.sleep(0.6)
        capture_frame("scrolling")

    # Hold on final clean state
    for _ in range(10):
        capture_frame("final_result_hold")
        time.sleep(0.3)

    print(f"Total frames captured: {frame_index}")

    print("Step 5: Encoding video with ffmpeg...")
    cmd = [
        "ffmpeg", "-y",
        "-framerate", "3",
        "-i", str(FRAMES_DIR / "frame_%04d.png"),
        "-vf", "fps=30,format=yuv420p",
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "medium",
        str(OUTPUT_VIDEO)
    ]
    subprocess.run(cmd, check=True)
    print(f"Video saved to: {OUTPUT_VIDEO}")

if __name__ == "__main__":
    main()
