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
    newchat=sub.add_parser("browser-project-new-chat-test",help="open a new chat from a ChatGPT Project without sending a message")
    newchat.add_argument("--cdp",default="http://127.0.0.1:9222")
    newchat.add_argument("--project-name",required=True)
    newchat.add_argument("--project-url",default="")
    newchat.add_argument("--selector",default="")
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

def browser_project_diagnose_command(args):
    session=BrowserSession(Path("."))
    try:
        context=session.connect_over_cdp(args.cdp)
        page=next((p for p in context.pages if p.url.startswith("https://chatgpt.com/")),None)
        if page is None: raise RuntimeError("No ChatGPT page is attached")
        print(f"URL: {page.url}")
        print(f"Title: {page.title()}")
        if args.project_name:
            needle=f"Open project options for {args.project_name}"
            loc=page.locator(f'button[aria-label="{needle}"]')
            if loc.count():
                print("--- project-options-html ---")
                print(loc.first.evaluate("(el) => el.outerHTML"))
                print("--- project-menu-diagnostic ---")
                try:
                    loc.first.click()
                    page.wait_for_timeout(300)
                    menus=page.locator('[role="menu"]')
                    print(f"menu count={menus.count()}")
                    for i in range(min(menus.count(),10)):
                        menu=menus.nth(i)
                        if not menu.is_visible():
                            continue
                        print(f"menu[{i}] text={menu.inner_text()!r}")
                        print(f"menu[{i}] html={menu.evaluate('(el) => el.outerHTML.slice(0,5000)')}")
                except Exception as exc:
                    print(f"menu diagnostic error: {exc}")
            else:
                print(f"Project options button not found: {needle}")
        print("--- composer-diagnostic ---")
        selectors=("textarea", '[contenteditable="true"]', '[role="textbox"]')
        seen=set()
        for selector in selectors:
            loc=page.locator(selector)
            print(f"selector: {selector} count={loc.count()}")
            for i in range(min(loc.count(),20)):
                item=loc.nth(i)
                try:
                    outer=item.evaluate("(el) => el.outerHTML.slice(0,1200)")
                    if outer in seen:
                        continue
                    seen.add(outer)
                    disabled=item.is_disabled() if selector == "textarea" else False
                    print(f"  [{i}] visible={item.is_visible()} hidden={item.is_hidden()} disabled={disabled}")
                    print(f"  [{i}] aria-label={item.get_attribute('aria-label')!r} placeholder={item.get_attribute('placeholder')!r} role={item.get_attribute('role')!r} contenteditable={item.get_attribute('contenteditable')!r} data-testid={item.get_attribute('data-testid')!r}")
                    print(f"  [{i}] outerHTML={outer}")
                except Exception as exc:
                    print(f"  [{i}] diagnostic error: {exc}")
        for selector in ("button","a"):
            loc=page.locator(selector)
            print(f"--- {selector} ---")
            for i in range(min(loc.count(),200)):
                item=loc.nth(i)
                try:
                    text=item.inner_text(timeout=500).strip().replace("\\n"," ")
                    aria=item.get_attribute("aria-label") or ""
                    title=item.get_attribute("title") or ""
                    testid=item.get_attribute("data-testid") or ""
                    href=item.get_attribute("href") or ""
                    combined=" | ".join(x for x in (text,aria,title,testid,href) if x)
                    if combined and any(k in combined.casefold() for k in ("project","new chat","chat")):
                        print(f"{i}: {combined[:300]}")
                except Exception:
                    continue
    finally:
        session.close()

def browser_project_new_chat_test_command(args):
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
        chat.assert_ready()
        if not chat.project_context_present(args.project_name):
            raise RuntimeError(f"required ChatGPT Project context not detected: {args.project_name}")
        before_url=page.url
        chat.start_new_project_chat(
            project_name=args.project_name,
            project_url=args.project_url or None,
            selector=args.selector or None,
        )
        print("Project new-chat navigation completed.")
        print(f"Previous URL: {before_url}")
        print(f"New URL: {page.url}")
        print(f"Title: {page.title()}")
        print(f"Message input detected: {chat.status().has_input}")
        print(f"Project context present: {chat.project_context_present(args.project_name)}")
        print("No message was sent.")
    finally:
        session.close()

def browser_attach_command(args):
    print(f"Attaching to existing Chromium: {args.cdp}")
    print("LabOS-Agent will not launch the remote Chromium.")
    session=BrowserSession(Path("."))
    try:
        context=session.connect_over_cdp(args.cdp)
        pages=context.pages
        chat_pages=[p for p in pages if p.url.startswith("https://chatgpt.com/")]
        page=chat_pages[0] if chat_pages else (pages[0] if pages else context.new_page())
        chat=ChatGPTPage(page); status=chat.status()
        print(f"URL: {status.url}"); print(f"Title: {status.title}"); print(f"Message input detected: {status.has_input}")
        if status.is_challenge: print("Browser is showing a verification challenge. Stop here and complete it manually."); return
        if not status.is_chatgpt: print("No ChatGPT page is currently attached. Open ChatGPT in the existing Chromium first."); return
        if not status.has_input: print("ChatGPT message input was not detected. Do not enable autonomous mode."); return
        if args.test_message:
            print("Sending one controlled test message.")
            response=chat.send_and_wait_for_response(args.test_message)
            print("Test message completed."); print("Assistant response:"); print(response)
        if args.keep_open:
            print("Attached session kept alive. Press Ctrl+C to detach.")
            try:
                import time
                while True: time.sleep(1)
            except KeyboardInterrupt: print("Detaching from Chromium.")
    finally: session.close()

def browser_smoke_command(args):
    import os,time
    from .browser.session import BrowserSession
    profile=Path(args.profile_dir).expanduser().resolve()
    if args.display: os.environ["DISPLAY"]=args.display
    with BrowserSession(profile,headless=args.headless) as session:
        page=session.context.pages[0] if session.context.pages else session.context.new_page()
        chat=ChatGPTPage(page); status=chat.open()
        print(f"URL: {status.url}"); print(f"Title: {status.title}"); print(f"Message input detected: {status.has_input}")
        if args.test_message and status.has_input:
            print("Sending one controlled test message."); print(chat.send_and_wait_for_response(args.test_message))
        if args.keep_open:
            try:
                while True: time.sleep(1)
            except KeyboardInterrupt: pass

def main():
    args=build_parser().parse_args()
    if args.command=="run": run_command(args)
    elif args.command=="continue": continue_command(args)
    elif args.command=="browser-project-diagnose": browser_project_diagnose_command(args)
    elif args.command=="browser-project-new-chat-test": browser_project_new_chat_test_command(args)
    elif args.command=="browser-attach": browser_attach_command(args)
    elif args.command=="browser-smoke": browser_smoke_command(args)
