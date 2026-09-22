"""Command-line entry point for LabOS-Agent."""
from __future__ import annotations
import argparse
from datetime import datetime
from pathlib import Path
import uuid

from .browser.chatgpt import ChatGPTPage
from .browser.session import BrowserSession
from .config import load_config
from .loop import run_loop,run_once,state_path
from .rollover import rollover
from .controller import Controller
from .safety import SafetyLimits
from .state import AgentState,save_state

def build_parser():
    parser=argparse.ArgumentParser(prog="lab-agent")
    sub=parser.add_subparsers(dest="command",required=True)
    run=sub.add_parser("run",help="run autonomous project loop")
    run.add_argument("project"); run.add_argument("--config",default="config.yaml")
    run.add_argument("--until",help="local deadline, HH:MM")
    run.add_argument("--max-iterations",type=int,default=50)
    run.add_argument("--max-rollovers",type=int,default=10)
    cont=sub.add_parser("continue",help="perform exactly one controlled project iteration")
    cont.add_argument("project"); cont.add_argument("--config",default="config.yaml")
    browser=sub.add_parser("browser-smoke",help="open persistent ChatGPT browser for manual smoke testing")
    browser.add_argument("--profile-dir",default="./browser-profile"); browser.add_argument("--headless",action="store_true")
    browser.add_argument("--display"); browser.add_argument("--test-message"); browser.add_argument("--keep-open",action="store_true")
    diag=sub.add_parser("browser-project-diagnose",help="inspect Project/new-chat UI without clicking")
    diag.add_argument("--cdp",default="http://127.0.0.1:9222")
    diag.add_argument("--project-name",default="")
    newchat=sub.add_parser("browser-project-new-chat-test",help="prepare the Project home composer for a new chat without sending a message")
    newchat.add_argument("--cdp",default="http://127.0.0.1:9222")
    newchat.add_argument("--project-name",required=True)
    newchat.add_argument("--project-url",default="")
    newchat.add_argument("--selector",default="")
    newchat.add_argument("--diagnose",action="store_true",help="click Project Home and print post-navigation composer diagnostics")
    newchat.add_argument("--test-message",default="",help="send one controlled message after reaching the Project composer and wait for the assistant response")
    rollover=sub.add_parser("browser-project-rollover-test",help="perform a controlled same-Project rollover using a supplied handoff")
    rollover.add_argument("--cdp",default="http://127.0.0.1:9222")
    rollover.add_argument("--project-name",required=True)
    rollover.add_argument("--handoff-message",required=True)
    rollover.add_argument("--require-rollover",action="store_true",help="require ChatGPT's explicit maximum-length UI before rolling over")
    handoff=sub.add_parser("browser-project-handoff-rollover-test",help="generate a real handoff, open a fresh Project chat, and resume from it")
    handoff.add_argument("--cdp",default="http://127.0.0.1:9222")
    handoff.add_argument("--project-name",required=True)
    handoff.add_argument("--project-url",default="")
    handoff.add_argument("--state-dir",default="")
    attach=sub.add_parser("browser-attach",help="attach to an existing Chromium over CDP")
    attach.add_argument("--cdp",default="http://127.0.0.1:9222"); attach.add_argument("--test-message"); attach.add_argument("--keep-open",action="store_true")
    return parser

def parse_deadline(value,now):
    if value is None: return None
    try: hour,minute=(int(x) for x in value.split(":",1))
    except ValueError as exc: raise ValueError("--until must be HH:MM") from exc
    if not 0<=hour<=23 or not 0<=minute<=59: raise ValueError("--until must be HH:MM")
    deadline=now.replace(hour=hour,minute=minute,second=0,microsecond=0)
    if deadline<=now: raise ValueError("--until must be later today")
    return deadline

def run_command(args):
    config=load_config(Path(args.config))
    deadline=parse_deadline(args.until,datetime.now().astimezone())
    result=run_loop(config,args.project,deadline=deadline,max_iterations=args.max_iterations,max_rollovers=args.max_rollovers)
    print(f"LabOS-Agent finished: state={result.state.state.value} iteration={result.state.iteration} rollovers={result.state.rollover_count}")
    if result.state.reason: print(f"Reason: {result.state.reason}")

def continue_command(args):
    config=load_config(Path(args.config))
    result=run_once(config,args.project)
    print(f"LabOS-Agent iteration finished: state={result.state.state.value} iteration={result.state.iteration}")
    if result.state.reason: print(f"Reason: {result.state.reason}")
    if result.response: print(result.response)

def browser_project_handoff_rollover_test_command(args):
    print(f"Attaching to existing Chromium: {args.cdp}")
    print("LabOS-Agent will not launch the remote Chromium.")
    session=BrowserSession(Path("."))
    try:
        context=session.connect_over_cdp(args.cdp)
        pages=[p for p in context.pages if p.url.startswith("https://chatgpt.com/")]
        page=pages[0] if pages else None
        if page is None:
            raise RuntimeError("No ChatGPT page is attached")
        chat=ChatGPTPage(page)
        status=chat.status()
        if status.is_challenge:
            raise RuntimeError("ChatGPT verification challenge is active")
        if not status.is_chatgpt:
            raise RuntimeError("attached page is not ChatGPT")
        if status.rollover_required:
            raise RuntimeError("attached conversation is already at the maximum length; use the emergency rollover path")
        if not chat.project_context_present(args.project_name):
            raise RuntimeError(f"required ChatGPT Project context not detected: {args.project_name}")
        state_dir_path=Path(args.state_dir) if args.state_dir else Path("state") / args.project_name
        before_url=page.url
        print(f"Current URL: {before_url}")
        print("Generating a real handoff from the current conversation.")
        project=type("Project", (), {
            "name": args.project_name,
            "repository": "",
            "project_root": Path("."),
            "continuation_message": "",
            "project_name": args.project_name,
            "project_url": args.project_url or None,
            "new_chat_selector": None,
        })()
        _,path,response=rollover(
            chat,
            project,
            state_dir_path,
            timeout_seconds=300,
            quiet_seconds=3,
        )
        print("Real handoff rollover completed.")
        print(f"Handoff path: {path}")
        print(f"New URL: {page.url}")
        print(f"Project context present: {chat.project_context_present(args.project_name)}")
        print(f"Conversation changed: {page.url != before_url}")
        print("Resume prompt sent: True")
        print("Resume assistant response:")
        print(response)
    finally:
        session.close()

def browser_project_diagnose_command(args):
    raise NotImplementedError("browser-project-diagnose remains available in the previous release")

def browser_project_new_chat_test_command(args):
    raise NotImplementedError("browser-project-new-chat-test remains available in the previous release")

def browser_project_rollover_test_command(args):
    raise NotImplementedError("browser-project-rollover-test remains available in the previous release")

def browser_attach_command(args):
    raise NotImplementedError("browser-attach remains available in the previous release")

def browser_smoke_command(args):
    raise NotImplementedError("browser-smoke remains available in the previous release")

def main():
    args=build_parser().parse_args()
    if args.command=="run": run_command(args)
    elif args.command=="continue": continue_command(args)
    elif args.command=="browser-project-handoff-rollover-test": browser_project_handoff_rollover_test_command(args)
